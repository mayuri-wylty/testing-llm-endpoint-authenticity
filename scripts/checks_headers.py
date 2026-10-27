"""Vendor / channel provenance from response headers, driven by what the channel CLAIMS to be (--claim).

Headers are collected from non-stream 200 responses into audit.headers (lowercase names).
Scoring (None = n/a: not penalised, weight renormalised over the applicable checks):
  contradiction  claim X but headers that clearly belong to another provider/channel are present     -> 0.0  ("CLAIM CONTRADICTED: ...")
  hit            headers consistent with the claim present (vendor prefix, or channel headers for bedrock/vertex/azure) -> 1.0
  miss           claim=official and a vendor/prefix was given -> 0.2 (an official endpoint should send them)
                 bedrock/vertex/azure/relay/unknown          -> None ("absence not penalised for claim=X": resellers legitimately strip headers)
  nothing to compare (no vendor/prefix and claim unknown/relay/official; channel claims always have their own table) -> None
Relay markers (x-oneapi-request-id ...) are only recorded in audit.notes.
"""
import re

NAME = "vendor headers"

# --vendor -> expected vendor header prefixes (unchanged from v1)
VENDOR_HEADERS = {
    "moonshot|kimi": ["msh-", "x-msh-"],
    "openai|gpt": ["openai-organization", "openai-processing-ms"],
    "anthropic|claude": ["anthropic-ratelimit", "anthropic-organization-id"],
    "google|gemini": ["x-goog-"],
    "deepseek": ["x-ds-"],
}
RELAY_HEADERS = {"x-oneapi-request-id": "one-api/new-api relay", "x-new-api-version": "new-api relay", "x-litellm": "LiteLLM relay",
                 "helicone": "Helicone proxy", "x-openrouter": "OpenRouter"}

# Headers expected from the cloud channel a --claim names (hit evidence for bedrock/vertex/azure).
CHANNEL_HEADERS = {
    "bedrock": ["x-amzn-requestid", "x-amzn-bedrock-"],
    "vertex": ["x-goog-", "x-vertex-"],
    "azure": ["x-ms-region", "apim-request-id", "x-ms-deployment-name", "azureml-"],
}
# claim -> provider family whose headers are "its own". Families are the keys of SIGNATURES.
CLAIM_FAMILY = {"bedrock": "bedrock", "vertex": "google", "azure": "azure"}

# Headers that clearly identify one provider/channel (used to detect a contradicted claim). Deliberately narrow: only headers a
# reseller would not add by itself (e.g. x-amzn-trace-id and openai-processing-ms are NOT here: ALB / Azure OpenAI also emit them).
SIGNATURES = {
    "openai": ["openai-organization", "openai-project"],
    "anthropic": ["anthropic-ratelimit", "anthropic-organization-id"],
    "google": ["x-goog-", "x-vertex-"],
    "moonshot": ["msh-", "x-msh-"],
    "deepseek": ["x-ds-"],
    "bedrock": ["x-amzn-requestid", "x-amzn-bedrock-", "x-amzn-errortype"],
    "azure": ["x-ms-region", "apim-request-id", "x-ms-deployment-name", "azureml-"],
}
VENDOR_FAMILIES = {"moonshot|kimi": "moonshot", "openai|gpt": "openai", "anthropic|claude": "anthropic",
                   "google|gemini": "google", "deepseek": "deepseek"}
CLOUD_FAMILIES = ("bedrock", "azure")  # channel gateways: never legitimate on a direct vendor endpoint


def _match(names, prefixes):
    return sorted(h for h in names if any(h.startswith(p) for p in prefixes))


def _family_of_prefix(p):
    for fam, sigs in SIGNATURES.items():
        if any(p.startswith(s) or s.startswith(p) for s in sigs):
            return fam
    return None


def _foreign(names, claim, own_fams, user_prefixes):
    """-> {header: family} for headers of a provider that the claim does not allow (explicit --header-prefix are never foreign)."""
    if claim in CLAIM_FAMILY:
        forbidden = [f for f in SIGNATURES if f != CLAIM_FAMILY[claim]]
    elif claim == "official":
        # direct vendor endpoint: cloud-gateway headers never fit; with a known vendor every other vendor's exclusive headers don't either
        forbidden = [f for f in SIGNATURES if f not in own_fams] if own_fams else list(CLOUD_FAMILIES)
        if claim == "official" and "google" in own_fams:
            forbidden = [f for f in forbidden if f != "google"]
    else:  # relay / unknown: a reseller may legitimately forward anything
        return {}
    out = {}
    for h in names:
        if user_prefixes and any(h.startswith(p) for p in user_prefixes):
            continue
        for fam in forbidden:
            if any(h.startswith(s) for s in SIGNATURES[fam]):
                out[h] = fam
                break
    return out


def run(audit):
    a = audit.a
    claim = getattr(a, "claim", "unknown") or "unknown"
    names = sorted(audit.headers)
    user_prefixes = [p.lower() for p in (a.header_prefix or [])]
    prefixes = list(user_prefixes)
    own_fams = set()
    for k, fam in VENDOR_FAMILIES.items():
        if a.vendor and re.search(k, a.vendor, re.I):
            own_fams.add(fam)
            if not user_prefixes:
                prefixes = list(VENDOR_HEADERS[k])
    own_fams |= {f for f in (_family_of_prefix(p) for p in user_prefixes) if f}

    relay = sorted({v for hk in names for rk, v in RELAY_HEADERS.items() if rk in hk})
    if relay:
        audit.notes.append("relay markers: %s" % ", ".join(relay))
        if claim == "official":
            audit.notes.append("claim=official but relay markers present")

    foreign = _foreign(names, claim, own_fams, user_prefixes)
    if foreign:
        listing = ", ".join("%s (%s)" % (h, f) for h, f in sorted(foreign.items()))
        msg = "CLAIM CONTRADICTED: claim=%s but response carries other providers' headers: %s" % (claim, listing)
        audit.notes.append(msg)
        audit.add(NAME, 0.0, msg)
        return

    if claim in CHANNEL_HEADERS:  # bedrock / vertex / azure: channel headers (plus explicit prefixes) are the evidence
        want = CHANNEL_HEADERS[claim] + user_prefixes
        hit = _match(names, want)
        if hit:
            audit.add(NAME, 1.0, "matched %s headers: %s" % (claim, hit))
        else:
            audit.add(NAME, None, "no %s channel headers (%s); absence not penalised for claim=%s (resellers may strip headers)"
                      % (claim, ", ".join(CHANNEL_HEADERS[claim]), claim))
        return

    if not prefixes:
        if claim == "official":
            audit.add(NAME, None, "claim=official but no --vendor/--header-prefix given and no cloud-gateway headers; nothing to verify")
        else:
            audit.add(NAME, None, "no vendor/prefix given; skipped")
        return
    hit = _match(names, prefixes)
    if hit:
        audit.add(NAME, 1.0, "matched: %s" % hit)
    elif claim == "official":
        audit.add(NAME, 0.2, "claim=official but no vendor headers (%s) seen; an official endpoint should send them" % ", ".join(prefixes))
    else:
        audit.add(NAME, None, "no vendor headers seen; absence not penalised for claim=%s (relays may strip headers)" % claim)
