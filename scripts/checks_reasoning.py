"""Reasoning check, two tiers: an easy tier (any competent model) and a hard tier (flagships answer, cheap/downgraded models often do not).

score = 0.4 * easy_rate + 0.6 * hard_rate. Hard questions live in data/reasoning_hard.json (answers verified by tests/verify_reasoning_hard.py);
if that file cannot be read the check runs the easy tier only and says so in the note. All questions are sent in parallel with --max-tokens.
Hard answers must be the LAST number of the reply (thousands commas stripped, trailing "(...)" remarks removed first).
Questions that could not be answered for infrastructure reasons (429 after one retry, 5xx, timeout, empty text with finish=length) are
excluded from the denominator; if nothing is gradable the check is n/a (None).
"""
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

NAME = "reasoning"
EASY_WEIGHT, HARD_WEIGHT = 0.4, 0.6
RETRY_SLEEP = 2.0  # seconds to wait before the single retry of a 429
HARD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "reasoning_hard.json")
EASY = [
    ("easy-pool", "A pool has inlet A (fills in 6h), inlet B (fills in 10h) and drain C (empties in 15h). All open: hours to fill? Answer number only.", r"\b5\b"),
    ("easy-letters", "How many letter r in 'strawberry', and how many letter s in 'Mississippi'? Reply as two numbers.", r"3\D+4"),
    ("easy-batball", "A bat and a ball cost $1.10 total; the bat costs $1.00 more than the ball. Ball price in cents? Number only.", r"\b5\b"),
    ("easy-mult", "What is 17 * 23? Number only.", r"391"),
]
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_CLOSE = {")": "(", "）": "（"}


def strip_trailing_notes(text):
    """Drop trailing parenthesised remarks, e.g. '3254 (approx)' -> '3254' (nested ok; never strips down to nothing-with-digits)."""
    t = (text or "").rstrip()
    while t and t[-1] in _CLOSE:
        depth, j = 0, len(t) - 1
        while j >= 0:
            if t[j] in _CLOSE:
                depth += 1
            elif t[j] in _CLOSE.values():
                depth -= 1
                if depth == 0:
                    break
            j -= 1
        if j < 0 or not re.search(r"\d", t[:j]):
            break
        t = t[:j].rstrip()
    return t


def norm_text(text, strip_notes=False):
    """Strip thousands separators (1,234 -> 1234) so numeric answers match regardless of formatting; optionally drop trailing '(...)' notes."""
    t = _THOUSANDS.sub("", text or "")
    return strip_trailing_notes(t) if strip_notes else t


def load_hard(path=None):
    """-> (questions [(id, q, regex)], error or None). Never raises."""
    path = path or HARD_PATH
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        out = []
        for item in doc["hard"]:
            re.compile(item["answer_regex"])
            out.append((str(item["id"]), str(item["q"]), item["answer_regex"]))
        if not out:
            raise ValueError("no hard questions")
        return out, None
    except Exception as e:
        detail = os.path.basename(path) if isinstance(e, OSError) else str(e)[:100]
        return [], "%s: %s" % (type(e).__name__, detail)


def _grade(audit, q, rx, tier):
    """-> (verdict, why). verdict: 'ok' | 'wrong' | 'skip' (not counted: rate limit/5xx/timeout/max_tokens exhausted) ; why = short reason for notes."""
    r, txt = audit.ask(q)
    if r.status == 429:  # one backoff retry
        time.sleep(RETRY_SLEEP)
        r, txt = audit.ask(q)
    st = r.status
    if st == 429 or st == 408 or st >= 500 or st < 0:
        return "skip", "network/timeout" if st < 0 else "HTTP %d" % st
    if st != 200:
        return "wrong", "HTTP %d" % st
    empty = not (txt or "").strip()
    if empty and getattr(r.norm, "finish", None) == "length":
        return "skip", "length"
    if re.search(rx, norm_text(txt, tier == "hard"), re.S):
        return "ok", ""
    reasoning = getattr(r.norm, "reasoning_text", "") or ""
    if empty and reasoning and re.search(rx, norm_text(reasoning, tier == "hard"), re.S):
        return "wrong", "reasoning_only"  # the model answered, but only inside reasoning_text: budget used up by thinking
    return "wrong", ""


def run(audit):
    hard, err = load_hard()
    qs = [(i, q, rx, "easy") for i, q, rx in EASY] + [(i, q, rx, "hard") for i, q, rx in hard]
    with ThreadPoolExecutor(len(qs)) as ex:
        res = list(ex.map(lambda x: _grade(audit, x[1], x[2], x[3]), qs))
    tiers = {"easy": [], "hard": []}  # tier -> [(id, verdict)]
    skipped, length_ids, reasoning_only = [], [], []
    for (qid, _, _, tier), (v, why) in zip(qs, res):
        tiers[tier].append((qid, v))
        if v == "skip":
            skipped.append("%s (%s)" % (qid, "reasoning exhausted max_tokens" if why == "length" else why))
            if why == "length":
                length_ids.append(qid)
        if why == "reasoning_only":
            reasoning_only.append(qid)

    def tally(tier):
        counted = [v for _, v in tiers[tier] if v != "skip"]
        return sum(v == "ok" for v in counted), len(counted)

    (e_ok, e_n), (h_ok, h_n) = tally("easy"), tally("hard")
    parts = []
    if e_n:
        parts.append((EASY_WEIGHT, e_ok / e_n))
    if h_n:
        parts.append((HARD_WEIGHT, h_ok / h_n))
    note = "easy %d/%d" % (e_ok, e_n)
    if hard:
        note += ", hard %d/%d" % (h_ok, h_n)
    else:
        note += ", hard tier skipped (data/reasoning_hard.json unreadable: %s; score = easy tier only)" % err
    wrong = [i for t in ("easy", "hard") for i, v in tiers[t] if v == "wrong"]
    if wrong:
        note += "; wrong: " + ", ".join(wrong)
    if skipped:
        note += "; not counted: " + ", ".join(skipped)
    if length_ids or reasoning_only:
        note += "; reasoning exhausted max_tokens; raise --max-tokens"
    if not parts:
        audit.add(NAME, None, note + "; no gradable question (n/a)")
        return
    w = sum(x for x, _ in parts)
    audit.add(NAME, sum(x * v for x, v in parts) / w, note)
