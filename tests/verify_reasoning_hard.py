#!/usr/bin/env python3
"""Independently re-derives every hard reasoning answer in scripts/data/reasoning_hard.json and checks checks_reasoning.py logic.

Part 1: each hard question is solved by a program that does not read the stored answer (brute force + a second method), then compared.
Part 2: the stored answer_regex accepts the right answer in common formats and rejects wrong/ambiguous replies.
Part 3: checks_reasoning.run() against a fake audit (all right, hard wrong, hard file missing, reasoning budget exhausted,
        429 retry, 5xx/timeout/length questions excluded from the denominator, everything excluded -> None).
Run:  python tests/verify_reasoning_hard.py     (stdlib only, no network; exits 1 on any failure)
"""
import collections
import itertools
import json
import os
import re
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, SCRIPTS)
import checks_reasoning as cr  # noqa: E402


# ---------------------------------------------------------------- independent solvers (parameters repeated from the question text)
def solve_binary_runs(n=18, ones=9):
    """No three equal consecutive characters, exactly `ones` ones. Brute force, then a DP over (ones used, last symbol, run length)."""
    brute = 0
    for pos in itertools.combinations(range(n), ones):
        s = [0] * n
        for p in pos:
            s[p] = 1
        if all(not (s[i] == s[i + 1] == s[i + 2]) for i in range(n - 2)):
            brute += 1
    dp = collections.Counter({(1, 1, 1): 1, (0, 0, 1): 1})  # (ones, last, run) after the first character
    for _ in range(n - 1):
        nxt = collections.Counter()
        for (o, last, run), c in dp.items():
            for b in (0, 1):
                r = run + 1 if b == last else 1
                if r <= 2:
                    nxt[(o + b, b, r)] += c
        dp = nxt
    dp_total = sum(c for (o, _, _), c in dp.items() if o == ones)
    assert brute == dp_total, "binary runs: brute %d != dp %d" % (brute, dp_total)
    return brute


def solve_mod_sequence(a1=5, upto=14, mod=211):
    a = a1
    for n in range(2, upto + 1):
        a = (a * a + 2 * n + 1) % mod
    b = a1

    def rec(n):  # recursive definition, pow() instead of multiplication
        return a1 if n == 1 else (pow(rec(n - 1), 2, mod) + 2 * n + 1) % mod

    b = rec(upto)
    assert a == b, "mod sequence: loop %d != recursion %d" % (a, b)
    return a


def solve_circle(n=58, k=6):
    people = list(range(1, n + 1))  # 1) list simulation
    i = 0
    while len(people) > 1:
        i = (i + k - 1) % len(people)
        people.pop(i)
    sim = people[0]
    dq = collections.deque(range(1, n + 1))  # 2) rotation simulation
    while len(dq) > 1:
        dq.rotate(-(k - 1))
        dq.popleft()
    rot = dq[0]
    j = 0  # 3) recurrence on 0-based survivor position, J(1)=0, J(m)=(J(m-1)+k) mod m
    for m in range(2, n + 1):
        j = (j + k) % m
    assert sim == rot == j + 1, "circle: sim %d rot %d rec %d" % (sim, rot, j + 1)
    return sim


SOLVERS = {"hard-binary-runs": solve_binary_runs, "hard-mod-sequence": solve_mod_sequence, "hard-circle-elimination": solve_circle}
# pytype for the regex test: id -> (should match, should not match)
FORMATS = {
    "hard-binary-runs": (["3254", "The answer is 3254.", "**3254**", "3,254", "Total: 3254\n"], ["3253", "13254", "32540", "3254 or maybe 1000", "3254.5", "3254 or maybe (1000) x"]),
    "hard-mod-sequence": (["166", "a_14 = 166", "After 13 steps the result is 166."], ["16", "1660", "166 then 12", "0.166"]),
    "hard-circle-elimination": (["41", "Person 41", "**41**."], ["4", "141", "410", "41 or 14", "x 4.41"]),
}


def part1_and_2():
    fails = []
    with open(cr.HARD_PATH, encoding="utf-8") as f:
        doc = json.load(f)
    ids = [h["id"] for h in doc["hard"]]
    if len(ids) != 3 or set(ids) != set(SOLVERS):
        fails.append("expected exactly the 3 hard ids %s, got %s" % (sorted(SOLVERS), ids))
    for h in doc["hard"]:
        solver = SOLVERS.get(h["id"])
        if not solver:
            continue
        got = str(solver())
        ok = got == h["answer"]
        print("%-5s %-26s computed=%-5s stored=%-5s" % ("PASS" if ok else "FAIL", h["id"], got, h["answer"]))
        if not ok:
            fails.append("%s: computed %s != stored %s" % (h["id"], got, h["answer"]))
        if not h.get("how_verified"):
            fails.append("%s: how_verified is empty" % h["id"])
        good, bad = FORMATS[h["id"]]
        for t in good:
            if not re.search(h["answer_regex"], cr.norm_text(t, True), re.S):
                fails.append("%s: regex should accept %r" % (h["id"], t))
        for t in bad:
            if re.search(h["answer_regex"], cr.norm_text(t, True), re.S):
                fails.append("%s: regex should reject %r" % (h["id"], t))
        if re.search(r"(?i)answer\s*(is|:)\s*\d", h["q"]) or h["answer"] in h["q"].split():
            fails.append("%s: question leaks the answer" % h["id"])
    return fails, doc


class FakeAudit:
    """Answers each question by id from a dict. reasoning: ids whose answer goes into norm.reasoning_text only.
    status: id -> HTTP status always returned; first: id -> status returned only on the first request (then the normal answer);
    length: ids answered with empty text and finish=length."""

    def __init__(self, answers, reasoning=(), status=None, first=None, length=()):
        self.answers, self.reasoning, self.added = answers, set(reasoning), []
        self.status, self.first, self.length = dict(status or {}), dict(first or {}), set(length)
        self.calls = collections.Counter()
        self.qmap = {q: i for i, q, _ in cr.EASY}
        self.qmap.update({q: i for i, q, _ in cr.load_hard()[0]})

    def ask(self, q, **kw):
        qid = self.qmap[q]
        self.calls[qid] += 1
        st = self.status.get(qid) or (self.first.get(qid) if self.calls[qid] == 1 else None)
        if st:
            return types.SimpleNamespace(status=st, raw="error", norm=types.SimpleNamespace(text="", reasoning_text="", finish=None)), "error"
        ans = self.answers.get(qid, "I do not know")
        txt, rt = ("", "so the answer is " + ans) if qid in self.reasoning or qid in self.length else (ans, "")
        fin = "length" if qid in self.length else "stop"
        return types.SimpleNamespace(status=200, norm=types.SimpleNamespace(text=txt, reasoning_text=rt, finish=fin)), txt

    def add(self, name, score, note="", weight=None):
        self.added.append((name, score, note))


def part3(doc):
    fails = []
    right = {"easy-pool": "5", "easy-letters": "3 and 4", "easy-batball": "5", "easy-mult": "391"}
    hard_right = {h["id"]: h["answer"] for h in doc["hard"]}
    hard_wrong = {k: str(int(v) + 1) for k, v in hard_right.items()}

    def expect(label, audit, lo, hi, note_rx):
        cr.run(audit)
        (name, score, note), = audit.added
        ok = name == "reasoning" and (score is None if lo is None else score is not None and lo <= score <= hi) and re.search(note_rx, note)
        print("%-5s selftest %-28s score=%s note=%s" % ("PASS" if ok else "FAIL", label, score if score is None else "%.3f" % score, note))
        if not ok:
            fails.append("selftest %s: score %s note %r (want %s..%s, ~%s)" % (label, score, note, lo, hi, note_rx))

    expect("all right", FakeAudit({**right, **hard_right}), 1.0, 1.0, r"^easy 4/4, hard 3/3$")
    expect("hard wrong", FakeAudit({**right, **hard_wrong}), 0.4, 0.4, r"^easy 4/4, hard 0/3; wrong: hard-")
    first = next(iter(hard_right))
    expect("one hard right", FakeAudit({**right, **hard_wrong, first: hard_right[first]}), 0.6, 0.6, r"^easy 4/4, hard 1/3")
    expect("easy wrong hard right", FakeAudit(hard_right), 0.6, 0.6, r"^easy 0/4, hard 3/3")
    expect("reasoning exhausted", FakeAudit({**right, **hard_right}, reasoning=[first]), 0.8, 0.8, r"hard 2/3.*raise --max-tokens")
    ids = list(hard_right)
    cr.RETRY_SLEEP = 0
    full = {**right, **hard_right}
    a = FakeAudit(full, first={i: 429 for i in full})
    expect("429 once then ok (retry)", a, 1.0, 1.0, r"^easy 4/4, hard 3/3$")
    if set(a.calls.values()) != {2}:
        fails.append("429 retry: each question must be asked exactly twice, got %s" % dict(a.calls))
    a = FakeAudit(full, status={i: 429 for i in ids})
    expect("hard 429 twice -> not counted", a, 1.0, 1.0, r"^easy 4/4, hard 0/0.*not counted: hard-binary-runs \(HTTP 429\)")
    if a.calls[ids[0]] != 2:
        fails.append("persistent 429 must be retried exactly once, got %d calls" % a.calls[ids[0]])
    expect("one 503 not counted", FakeAudit(full, status={ids[1]: 503}), 1.0, 1.0, r"hard 2/2.*hard-mod-sequence \(HTTP 503\)")
    expect("timeout not counted", FakeAudit(full, status={ids[2]: -1}), 1.0, 1.0, r"hard 2/2.*network/timeout")
    expect("503 + wrong keeps wrong", FakeAudit({**right, **hard_wrong}, status={ids[1]: 503}), 0.4, 0.4, r"hard 0/2; wrong: hard-binary-runs, hard-circle-elimination; not counted")
    expect("length+empty not counted", FakeAudit(full, length=[ids[0]]), 1.0, 1.0, r"hard 2/2.*not counted: hard-binary-runs \(reasoning exhausted max_tokens\).*raise --max-tokens")
    expect("HTTP 400 stays wrong", FakeAudit({**right, **hard_right}, status={ids[0]: 400}), 0.8, 0.8, r"hard 2/3; wrong: hard-binary-runs")
    expect("easy 429 hard right", FakeAudit(full, status={i: 429 for i in right}), 1.0, 1.0, r"^easy 0/0, hard 3/3")
    expect("everything excluded -> None", FakeAudit(full, status={i: 429 for i in full}), None, None, r"no gradable question")
    saved = cr.HARD_PATH
    try:
        cr.HARD_PATH = os.path.join(HERE, "no-such-file.json")
        expect("hard file missing", FakeAudit(right), 1.0, 1.0, r"^easy 4/4, hard tier skipped")
        bad = os.path.join(HERE, "_bad_reasoning_hard.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("{not json")
        cr.HARD_PATH = bad
        try:
            expect("hard file corrupt", FakeAudit(right), 1.0, 1.0, r"hard tier skipped")
        finally:
            os.remove(bad)
    finally:
        cr.HARD_PATH = saved
    return fails


def main():
    fails, doc = part1_and_2()
    fails += part3(doc)
    if fails:
        print("\nFAILED:")
        for f in fails:
            print("  - " + f)
        sys.exit(1)
    print("\nPASS: 3 hard answers independently verified, regexes and check logic OK")


if __name__ == "__main__":
    main()
