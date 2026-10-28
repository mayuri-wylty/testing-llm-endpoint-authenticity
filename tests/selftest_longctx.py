#!/usr/bin/env python3
"""Self-test for scripts/checks_longctx.py (no network, no mock server): window parsing, tier planning, haystack layout,
cost guard, timeout scaling, scoring/diagnosis with a fake Audit. Exit 0 = all pass.
  python selftest_longctx.py
"""
import argparse, os, random, sys, types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
import checks_longctx as lc

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
        print("FAIL", msg)


def ns(**kw):
    d = dict(records=None, needle_pos=None, claimed_window="", deep=False)
    d.update(kw)
    return argparse.Namespace(**d)


# 1. parse_window
for s, want in [("", None), (None, None), ("32k", 32000), ("128K", 128000), ("200k", 200000), ("1M", 1_000_000), ("1m", 1_000_000),
                ("1.5M", 1_500_000), ("131072", 131072), ("128,000 tokens", 128000), ("huge", None), ("0", None), ("-5k", None), ("k", None)]:
    check(lc.parse_window(s) == want, "parse_window(%r) = %r, want %r" % (s, lc.parse_window(s), want))

# 2. plan(): tiers, back-compat, deep
p = lc.plan(ns())
check(p["mode"] == "triple" and p["target"] == 32000 and p["records"] == round(32000 / 17), "default tier %r" % p)
p = lc.plan(ns(claimed_window="32k"))
check(p["target"] == 19200 and "32k" in p["tier"], "32k claim must shrink the haystack below the window: %r" % p)
p = lc.plan(ns(claimed_window="8k"))
check(p["target"] == 4800, "8k claim: %r" % p)
p = lc.plan(ns(claimed_window="128k"))
check(p["target"] == 76800 and p["records"] == 4518, "128k tier %r" % p)
p = lc.plan(ns(claimed_window="200k"))
check(p["target"] == 100000, "200k tier %r" % p)
p = lc.plan(ns(claimed_window="1M"))
check(p["target"] == 100000 and "1M tier needs --deep" in p["tier"], "1M without --deep stays 100k and says so: %r" % p)
p = lc.plan(ns(claimed_window="1M", deep=True))
check(p["target"] == 600000 and p["records"] == round(600000 / 17), "1M --deep -> 0.6*window: %r" % p)
for w in ("8k", "32k", "64k", "128k", "200k", "512k", "1M", "2M"):  # a 0.44 tok/char tokenizer must still fit the window
    pw = lc.plan(ns(claimed_window=w, deep=True))
    check(pw["target"] * 0.44 / 0.33 <= lc.parse_window(w), "%s: target %d overflows at 0.44 tok/char" % (w, pw["target"]))
p = lc.plan(ns(claimed_window="2M", deep=True))
check(p["target"] == 900000, "2M --deep capped at 900k: %r" % p)
p = lc.plan(ns(claimed_window="200k", deep=True))
check(p["target"] == 100000, "--deep does not enlarge sub-1M claims: %r" % p)
p = lc.plan(ns(records=200))
check(p["records"] == 200 and p["mode"] == "triple" and "explicit" in p["tier"], "explicit --records %r" % p)
p = lc.plan(ns(records=200, claimed_window="128k"))
check(p["records"] == 200, "explicit --records beats the tier %r" % p)
p = lc.plan(ns(records=0))
check(p["skip"], "--records 0 skips")
p = lc.plan(ns(records=0, claimed_window="1M", deep=True))
check(p["skip"], "--records 0 skips even with a claim")
p = lc.plan(ns(needle_pos=0.1))
check(p["mode"] == "single" and p["pos"] == 0.1 and p["records"] == round(32000 / 17), "explicit --needle-pos -> single needle %r" % p)
p = lc.plan(ns(needle_pos=1.7))
check(p["pos"] == 1.0, "needle-pos clamped %r" % p)
p = lc.plan(ns(claimed_window="huge"))
check("unparseable" in p["tier"] and p["target"] == 32000, "garbage claim %r" % p)

# 3. haystack layout: reproducible, three distinct secrets at ~10/50/90% depth, v1 single-needle format kept
doc, sec = lc.build_haystack(1000, lc.DEPTHS, random.Random(7))
doc2, sec2 = lc.build_haystack(1000, lc.DEPTHS, random.Random(7))
check(doc == doc2 and sec == sec2, "haystack must be reproducible (Random(7))")
check(len(set(sec)) == 3 and all(len(s) == 10 and s.isalnum() and s.upper() == s for s in sec), "secrets %r" % sec)
lines = doc.split("\n")
check(len(lines) == 1003, "1000 records + 3 needles, got %d" % len(lines))
for k, d in enumerate(lc.DEPTHS):
    idx = next(i for i, l in enumerate(lines) if sec[k] in l)
    check(abs(idx / len(lines) - d) < 0.01, "needle %d at %.3f, want ~%.2f" % (k + 1, idx / len(lines), d))
    check(lines[idx] == "[IMPORTANT] The hidden passphrase #%d is %s." % (k + 1, sec[k]), "needle line %r" % lines[idx])
d1, s1 = lc.build_haystack(100, (0.5,), random.Random(7))
check(len(s1) == 1 and ("[IMPORTANT] The hidden passphrase is %s." % s1[0]) in d1.split("\n"), "single needle keeps the v1 sentence")
check(d1.split("\n")[50].startswith("[IMPORTANT]"), "single needle at int(100*0.5)=50")

# 4. estimates / timeout
check(lc.estimate_tokens(100000) == 33000, "estimate")
check(lc.timeout_for(30000) == 240 and lc.timeout_for(100000) == 666 and lc.timeout_for(800000) == 900, "timeout scaling %r" % [lc.timeout_for(x) for x in (30000, 100000, 800000)])


# 5. run() with a fake Audit
class FakeAudit:
    def __init__(self, a, reply_fn, prompt_tokens_fn=None, status=200, raw="boom"):
        self.a, self.timeout, self.usage_inflated, self.out, self.sent, self.timeouts = a, 240, False, [], [], []
        self.reply_fn, self.pt_fn, self.status, self.raw = reply_fn, prompt_tokens_fn or (lambda doc: int(len(doc) * 0.33)), status, raw

    def chat(self, messages, timeout=None, **kw):
        doc = messages[0]["content"]
        self.sent.append(doc)
        self.timeouts.append(timeout)
        norm = types.SimpleNamespace(text=self.reply_fn(doc), usage={"prompt": self.pt_fn(doc)})
        return types.SimpleNamespace(status=self.status, norm=norm, t=1.0, raw=self.raw)

    def add(self, name, score, note="", weight=None):
        self.out.append((name, score, note))


def all_found(doc):
    import re
    return "\n".join("#%s: %s" % (n, c) for n, c in re.findall(r"hidden passphrase #(\d) is ([A-Z0-9]+)\.", doc))


def only(depth_ids):
    import re
    return lambda doc: "\n".join("#%s: %s" % (n, c) for n, c in re.findall(r"hidden passphrase #(\d) is ([A-Z0-9]+)\.", doc) if n in depth_ids)


def go(a, reply=all_found, **kw):
    au = FakeAudit(a, reply, **kw)
    lc.run(au)
    check(len(au.out) == 1, "run() must call audit.add exactly once, got %d" % len(au.out))
    return au


au = go(ns(records=300))
name, score, note = au.out[0]
check(name == "long context" and abs(score - 1.0) < 1e-9 and "needle found 3/3" in note, "good: %r" % (au.out,))
check("effective-window lower bound" in note and "est " in note, "note lacks lower bound / estimate: %s" % note)
check(au.timeouts == [240], "timeout passed %r" % au.timeouts)
check(au.sent[0].endswith(lc.QUESTION_TRIPLE) and len(lc.QUESTION_TRIPLE) < 500, "question must trail the haystack (and stay <500 chars for the mock's truncation)")
for ids, exp, rx in [(("1", "2", "3"), 1.0, "needle found 3/3"), (("1", "2"), 0.7 * 2 / 3 + 0.3, "MISSED at 90%.*tail truncation suspected"),
                     (("2", "3"), 0.7 * 2 / 3 + 0.3, "MISSED at 10%.*head truncation suspected"), (("1", "3"), 0.7 * 2 / 3 + 0.3, "MISSED at 50%.*middle lost"),
                     (("1",), 0.7 / 3 + 0.3, "MISSED at 50%,90%.*tail truncation"), (("3",), 0.7 / 3 + 0.3, "MISSED at 10%,50%.*head truncation"),
                     ((), 0.3, "MISSED at 10%,50%,90%.*all needles missing")]:
    au = go(ns(records=300), only(ids))
    sc, nt = au.out[0][1], au.out[0][2]
    import re as _re
    check(abs(sc - exp) < 1e-9 and _re.search(rx, nt), "found %r -> %.3f (want %.3f) %s" % (ids, sc, exp, nt[:140]))
# tok/char sanity unchanged: too low -> 0.3 lost (not usage_inflated); too high -> usage_inflated + 0.3 lost
au = go(ns(records=300), prompt_tokens_fn=lambda d: int(len(d) * 0.10))
check(abs(au.out[0][1] - 0.7) < 1e-9 and not au.usage_inflated, "low tok/char: %r" % (au.out,))
au = go(ns(records=300), prompt_tokens_fn=lambda d: int(len(d) * 0.9))
check(abs(au.out[0][1] - 0.7) < 1e-9 and au.usage_inflated and "USAGE INFLATED" in au.out[0][2], "high tok/char: %r" % (au.out,))
au = go(ns(records=300), prompt_tokens_fn=lambda d: 0)
check(abs(au.out[0][1] - 0.7) < 1e-9, "missing usage -> not sane (as v1): %r" % (au.out,))
# HTTP failure
for st, raw, want_none, phrase in [
        (400, "This model's MAXIMUM context length is 8192", True, "request rejected as too long; claimed window may be smaller than stated"),
        (413, "Request Entity Too Large", True, "request rejected as too long"), (400, "prompt exceeds limit", True, "rejected as too long"),
        (504, "Gateway Timeout", True, "gateway/timeout on large request; inconclusive"), (524, "timeout", True, "gateway/timeout on large request; inconclusive"),
        (408, "", True, "inconclusive"), (-1, "timed out", True, "inconclusive"), (None, None, True, "inconclusive"),
        (400, "Invalid value for temperature", False, "HTTP 400"), (401, "Invalid token", False, "HTTP 401"), (403, "forbidden", False, "HTTP 403"),
        (500, "context length exceeded", False, "HTTP 500"), (502, "bad gateway", False, "HTTP 502"), (429, "rate", False, "HTTP 429")]:
    au = go(ns(records=300), status=st, raw=raw)
    sc, nt = au.out[0][1], au.out[0][2]
    check((sc is None) == want_none and (want_none or sc == 0.0) and phrase in nt, "HTTP %s %r -> %r %s" % (st, raw, sc, nt[:100]))
# single needle mode == v1 scoring
au = go(ns(records=300, needle_pos=0.1), lambda d: "ABC")
check(abs(au.out[0][1] - 0.3) < 1e-9 and "needle MISSED at 10% depth" in au.out[0][2], "single missed: %r" % (au.out,))
au = go(ns(records=300, needle_pos=0.9), lambda d: d.split("passphrase is ")[1][:10])
check(abs(au.out[0][1] - 1.0) < 1e-9 and "needle found 1/1" in au.out[0][2], "single found: %r" % (au.out,))
# --records 0
au = go(ns(records=0))
check(au.out[0][1] is None and not au.sent, "--records 0 sends nothing")
# cost guard: ~165k tokens without --deep is refused and nothing is sent; with --deep it is sent with a scaled timeout
au = go(ns(records=10000))
check(au.out[0][1] is None and "refused: est ~" in au.out[0][2] and "> 150k, pass --deep" in au.out[0][2] and not au.sent, "guard: %r" % (au.out,))
au = go(ns(records=10000, deep=True))
check(au.out[0][1] is not None and len(au.sent) == 1 and au.timeouts[0] >= 240, "--deep lifts the guard: %r" % (au.out,))
est = lc.estimate_tokens(len(au.sent[0]))
check(150_000 < est < 200_000 and au.timeouts[0] == lc.timeout_for(est), "est %d timeout %r" % (est, au.timeouts))
# 1M without --deep stays under the guard (~100k); 1M with --deep would be refused only if --deep missing -> plan est check
p = lc.plan(ns(claimed_window="1M"))
check(lc.estimate_tokens(p["records"] * 50) < lc.GUARD_TOKENS, "1M without --deep must stay under the guard")
p = lc.plan(ns(claimed_window="1M", deep=True))
check(lc.estimate_tokens(p["records"] * 50) > lc.GUARD_TOKENS, "1M --deep plan is above the guard (needs --deep)")

if fails:
    print("%d FAILED" % len(fails))
    sys.exit(1)
print("selftest_longctx: all passed")
