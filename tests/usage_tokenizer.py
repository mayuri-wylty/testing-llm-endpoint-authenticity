"""usage hook for the tokenizer check: simulates 'fixed overhead + slope * chars' prompt_tokens per tokenizer family.

Only requests whose text is one of the 4 fixed fingerprint texts (data/tokenizer_bands.json: en/zh/code/digits, repeated)
are handled; everything else falls through to mock_llm's default estimate (returns None).
Knobs: family (existing; mock names, gpt -> openai), tokvariant (substring of a tokenizer name in `measured`, e.g.
"cl100k"; default = first tokenizer), overhead (existing), tokflat=1 (prompt_tokens independent of length -> every
category is skipped), ratio (existing; forces the default estimate), usage_mult (existing; applied by mock_llm).
Slopes come from the family's `measured` values (first tokenizer, or tokvariant); families without `measured` (claude)
use the band midpoint.
"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
BANDS_PATH = os.path.join(os.path.dirname(HERE), "scripts", "data", "tokenizer_bands.json")
TEMPLATE_OVERHEAD = 7   # chat-template tokens added on top of the text, cancelled by the differential slope
FLAT_TOKENS = 100
ALIASES = {"gpt": "openai", "moonshot": "kimi", "gemma": "gemini"}
KNOBS = {"tokvariant": "", "tokflat": 0}
SCENARIOS = {"tokflat": {"tokflat": 1}}
_cache = {}


def _bands():
    if "b" not in _cache:
        with open(BANDS_PATH, encoding="utf-8") as f:
            _cache["b"] = json.load(f)
    return _cache["b"]


def _classify(text):
    if len(text) < 1000:
        return None
    for t, spec in _bands()["texts"].items():
        unit = spec["unit"]
        if text[:40] in unit * 2:
            return t
    return None


def _slope(family, t, variant):
    fam = _bands()["families"].get(ALIASES.get(family, family))
    if not fam:
        return None
    measured = fam.get("measured")
    if measured:
        names = list(measured)
        pick = next((n for n in names if variant and variant.lower() in n.lower()), names[0])
        return measured[pick].get(t)
    band = (fam.get("ratios") or {}).get(t)
    return (band[0] + band[1]) / 2 if band else None


def estimate(ctx, text):
    if ctx.knob("ratio", "") not in ("", None):
        return None
    t = _classify(text)
    if t is None:
        return None
    base = TEMPLATE_OVERHEAD + ctx.knob("overhead", 0)
    if ctx.knob("tokflat", 0):
        return base + FLAT_TOKENS
    s = _slope(str(ctx.knob("family", "kimi")), t, str(ctx.knob("tokvariant", "") or ""))
    if s is None:
        return None
    return base + s * len(text)
