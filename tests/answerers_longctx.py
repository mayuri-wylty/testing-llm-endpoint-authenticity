"""Mock answerer for the three-needle long-context question (checks_longctx.py). Reads the passphrases it can still SEE
(after the scenario's truncation) and reports the others as MISSING, like a model that never received them."""
import re

NEEDLE_RE = re.compile(r"hidden passphrase #([1-3]) is ([A-Za-z0-9]+)\.")


def answer(ctx, user_text):
    if "hides exactly three passphrases" not in user_text:
        return None
    seen = {int(n): code for n, code in NEEDLE_RE.findall(user_text)}
    if ctx.knob("lc_blind", 0):  # optional knob: the model never finds anything (answer is "not found")
        seen = {}
    return "\n".join("#%d: %s" % (i, seen.get(i, "MISSING")) for i in (1, 2, 3))


KNOBS = {"lc_blind": 0}
