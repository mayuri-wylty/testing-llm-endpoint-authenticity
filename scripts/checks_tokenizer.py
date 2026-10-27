"""Tokenizer fingerprint: does the endpoint's prompt-token accounting look like the CLAIMED model family's tokenizer?

Method (black box, uses only usage.prompt via Resp.norm, never wire JSON):
  for each of 4 fixed texts (en / zh / code / digits, data/tokenizer_bands.json) send unit*a and unit*b with a tiny
  max_tokens; slope = (p_b - p_a) / (len_b - len_a) tok/char. The difference cancels the fixed overhead (chat template,
  hidden system prompt), so the slope depends on the tokenizer only.
Verdict logic is COMPATIBILITY, not nearest-family: is the measured slope inside the claimed family's [lo, hi] band
(+/-3 %)? digits separates three clusters (~0.33 openai/deepseek/kimi/llama, ~0.64 glm, ~1.0 qwen/gemini); en/code are
weak and only support. The check can only lower the score, never set hard_fail.
Limits: bands come from public tokenizers (claude / gemini are low-confidence approximations); a relay may recompute
usage with its own tokenizer, in which case the result reflects the relay.
"""
import json, os, re
from concurrent.futures import ThreadPoolExecutor

NAME = "tokenizer"
BANDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tokenizer_bands.json")
TOL = 0.03                 # relative tolerance added to each side of a band
MAX_TOKENS = 16
PARTIAL_LO, PARTIAL_HI = 0.3, 0.9   # partial compatibility maps linearly onto this range
LOW_CONF_CAP = 0.7         # best possible score when the claimed family's band is low-confidence
LOW_CONF_FLOOR = 0.3       # ... and the worst (a low-confidence band alone may not drive a 0)
DIGITS_FAIL_CAP = 0.2      # digits is the cluster-defining class: failing it caps the score even if en/zh/code fit
TAIL = "usage may be recomputed by the relay; result reflects the relay tokenizer if so"

# (earliest match in vendor, then model, wins) pattern -> family key in tokenizer_bands.json
INFER = [
    (r"moonshot|kimi", "kimi"),
    (r"openai|gpt|chatgpt", "openai"),
    (r"deepseek", "deepseek"),
    (r"qwen|qwq|alibaba|tongyi", "qwen"),
    (r"glm|zhipu|(?<![a-z])zai(?![a-z])", "glm"),
    (r"anthropic|claude|(?<![a-z])(?:sonnet|opus|haiku)", "claude"),
    (r"google|gemini|gemma", "gemini"),
    (r"llama|(?<![a-z])meta(?![a-z])", "llama"),
]


def _load_bands():
    with open(BANDS_PATH, encoding="utf-8") as f:
        return json.load(f)


def infer_family(vendor, model, explicit=""):
    """-> family key (not validated against the table) or None. --tokenizer-family wins; then vendor; then model."""
    for src in (explicit, vendor, model):
        s = (src or "").strip().lower()
        if not s:
            continue
        best = None
        for rx, fam in INFER:
            m = re.search(rx, s)
            if m and (best is None or m.start() < best[0]):
                best = (m.start(), fam)
        if best:
            return best[1]
        if src is explicit:
            return s  # explicit but not an alias: use verbatim (table lookup decides)
    return None


def measure(audit, texts):
    """-> {type: {"slope","overhead","pa","pb"}} for the types whose two requests both returned usable usage."""
    jobs = [(t, which, spec["unit"] * spec["reps"][i]) for t, spec in texts.items() for i, which in enumerate("ab")]

    def one(job):
        t, which, text = job
        r = audit.chat([{"role": "user", "content": text}], max_tokens=MAX_TOKENS)
        u = r.norm.usage if r.status == 200 and r.norm is not None else None
        p = u.get("prompt") if isinstance(u, dict) else None
        return t, which, len(text), (p if isinstance(p, (int, float)) and not isinstance(p, bool) else None)

    with ThreadPoolExecutor(min(8, len(jobs))) as ex:
        got = list(ex.map(one, jobs))
    pts = {}
    for t, which, n, p in got:
        pts.setdefault(t, {})[which] = (n, p)
    out = {}
    for t, d in pts.items():
        if "a" not in d or "b" not in d or d["a"][1] is None or d["b"][1] is None:
            continue
        (la, pa), (lb, pb) = d["a"], d["b"]
        if lb <= la or pb - pa <= 0:
            continue
        slope = (pb - pa) / (lb - la)
        out[t] = {"slope": slope, "overhead": pa - slope * la, "pa": pa, "pb": pb}
    return out


def _compat(slope, band, tol=TOL):
    return band[0] * (1 - tol) <= slope <= band[1] * (1 + tol)


def _dist(slope, band):
    """Normalised distance of a slope to a band (0 inside the tolerance-widened band)."""
    lo, hi = band[0] * (1 - TOL), band[1] * (1 + TOL)
    mid = (band[0] + band[1]) / 2
    return 0.0 if lo <= slope <= hi else ((lo - slope) if slope < lo else (slope - hi)) / mid


def _overlap(b1, b2):
    return b1[0] * (1 - TOL) <= b2[1] * (1 + TOL) and b2[0] * (1 - TOL) <= b1[1] * (1 + TOL)


def _usable_ratios(fam):
    r = fam.get("ratios") or {}
    return {t: v for t, v in r.items() if isinstance(v, (list, tuple)) and len(v) == 2}


def score_family(meas, claimed, families):
    """Pure scoring. meas {type: {"slope",..}}; claimed family key (present in families, ratios usable).
    -> (score|None, note_without_tail)."""
    fam = families[claimed]
    bands = _usable_ratios(fam)
    types = [t for t in ("en", "zh", "code", "digits") if t in meas and t in bands]
    if not types:
        return None, "no usable categories for family %s" % claimed
    low = fam.get("confidence") == "low"
    ok = {t: _compat(meas[t]["slope"], bands[t]) for t in types}
    k, n = sum(ok.values()), len(types)
    detail = "; ".join("%s %.3f %s [%.3f-%.3f]" % (t, meas[t]["slope"], "ok" if ok[t] else "OUT", bands[t][0], bands[t][1]) for t in types)
    parts = []
    if k == n:
        score = 1.0
        verdict = "compatible with %s (%d/%d categories)" % (claimed, k, n)
    elif k == 0:
        score = 0.0 if n >= 2 else PARTIAL_LO
        verdict = "NOT compatible with %s (0/%d categories)%s" % (claimed, n, "" if n >= 2 else "; only one category usable, score floored at %.1f" % PARTIAL_LO)
    else:
        score = PARTIAL_LO + (PARTIAL_HI - PARTIAL_LO) * k / n
        verdict = "partially compatible with %s (%d/%d categories)" % (claimed, k, n)
    if "digits" in ok and not ok["digits"] and n >= 2 and score > DIGITS_FAIL_CAP:
        score = DIGITS_FAIL_CAP
        parts.append("digits slope outside the claimed band: different digit-splitting tokenizer cluster, score capped at %.1f" % DIGITS_FAIL_CAP)
    if n < 2 and score > LOW_CONF_CAP:
        score = LOW_CONF_CAP
        parts.append("only %d category measured, score capped at %.1f" % (n, LOW_CONF_CAP))
    if low:
        score = min(max(score, LOW_CONF_FLOOR), LOW_CONF_CAP)
        parts.append("low-confidence band (%s tokenizer is approximated), score kept within %.1f-%.1f" % (claimed, LOW_CONF_FLOOR, LOW_CONF_CAP))
    # nearest high-confidence families (hint only) and families this measurement cannot exclude
    others = []
    for name, f in families.items():
        if name == claimed or f.get("confidence") != "high":
            continue
        b = _usable_ratios(f)
        ts = [t for t in types if t in b]
        if ts:
            others.append((sum(_dist(meas[t]["slope"], b[t]) for t in ts), name, ts))
    if others:
        dmin = min(d for d, _, _ in others)
        near = sorted(nm for d, nm, _ in others if d == dmin)
        if k < n:
            parts.append("closest high-confidence %s by measured slopes: %s (distance %.2f)" % ("family" if len(near) == 1 else "families (tie)", ", ".join(near), dmin))
        elif dmin == 0:
            parts.append("measured values also fit: " + ", ".join(near))
    indist = sorted(name for name, f in families.items() if name != claimed and f.get("confidence") == "high"
                    and all(t in _usable_ratios(f) and _overlap(bands[t], _usable_ratios(f)[t]) for t in types))
    if indist:
        parts.append("cannot discriminate from " + ", ".join(indist) + " (bands overlap on all measured categories)")
    ov = sorted(round(m["overhead"]) for m in meas.values())
    return round(score, 3), "%s: %s; fixed overhead ~%s tok" % (verdict, detail, "%d-%d" % (ov[0], ov[-1]) if ov[0] != ov[-1] else ov[0]) + ("; " + "; ".join(parts) if parts else "")


def run(audit):
    a = audit.a
    bands = _load_bands()
    families = bands["families"]
    fam_key = infer_family(getattr(a, "vendor", ""), getattr(a, "model", ""), getattr(a, "tokenizer_family", ""))
    if not fam_key:
        return audit.add(NAME, None, "no claimed family (use --tokenizer-family or --vendor/--model). " + TAIL)
    fam = families.get(fam_key)
    if not fam or not _usable_ratios(fam) or fam.get("confidence") == "none":
        return audit.add(NAME, None, "claimed family %r has no usable band in the table (n/a). %s" % (fam_key, TAIL))
    meas = measure(audit, bands["texts"])
    if not meas:
        return audit.add(NAME, None, "no usable prompt-token usage (missing, or no growth with length); cannot fingerprint. " + TAIL)
    score, note = score_family(meas, fam_key, families)
    audit.add(NAME, score, "%s. %s" % (note, TAIL))
