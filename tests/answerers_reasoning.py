"""Mock hook for the two-tier reasoning check (scripts/checks_reasoning.py).

Knob `rzn` (URL param, e.g. /s/good:rzn=paren/v1; failure modes live in mock_proto_reasoning.py): "paren" appends a trailing remark
"(approx., double-checked 2 ways)" to every hard answer, which the check must still accept.
good           : every easy and hard question answered correctly.
weak_reasoner  : easy tier right, hard tier wrong (a flagship vs a cheap substitute). The core mock's own weak logic would also fail
                 three of the easy questions, so this hook answers the easy ones itself. Hard answers come from scripts/data/reasoning_hard.json.
"""
import json
import os
import re

_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "data", "reasoning_hard.json")
# distinctive phrase in the question -> hard id
_HARD_RX = [
    (r"binary strings of length 18", "hard-binary-runs"),
    (r"a_1 = 5 and, for n >= 2", "hard-mod-sequence"),
    (r"58 people numbered 1 to 58", "hard-circle-elimination"),
]
_EASY = [
    (r"inlet\s+A", "5"),
    (r"strawberry", "3 and 4"),
    (r"bat and a ball|bat and ball", "5"),
    (r"what is 17 \* 23", "391"),
]
_answers = None
KNOBS = {"rzn": ""}


def _hard_answers():
    global _answers
    if _answers is None:
        with open(_DATA, encoding="utf-8") as f:
            _answers = {h["id"]: h["answer"] for h in json.load(f)["hard"]}
    return _answers


def answer(ctx, user_text):
    weak = bool(ctx.knob("weak", 0))
    for rx, hid in _HARD_RX:
        if re.search(rx, user_text):
            ans = _hard_answers()[hid]
            out = str(int(ans) + 13) if weak else ans
            return out + " (approx., double-checked 2 ways)" if ctx.knob("rzn", "") == "paren" else out
    if weak:  # keep the easy tier correct even though the core mock would fail "hard-looking" easy questions
        for rx, ans in _EASY:
            if re.search(rx, user_text, re.I):
                return ans
    return None
