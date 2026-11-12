#!/usr/bin/env python3
"""Self-test for the display-layer number formatters in scripts/common.py (pure, no network).

  python tests/selftest_fmt.py

Covers fmt_num / round_half_up / round_text / round_obj. These helpers are for the rendering boundary only
(HTML / md); audit/spec JSON keeps full precision.

round_text trade-offs pinned down here (see its docstring in common.py):
  * only decimals with >= 3 fractional digits are touched (-> 2 digits, HALF_UP on the literal text, no float error);
  * a number is left alone when it is glued to an identifier (letter/digit/underscore before it, or `name-` before it),
    belongs to a multi-dot token (1.2.3 / 127.0.0.1 / v0.149.0), a time (12:30:45.123), a URL, an sk- token,
    scientific notation (1.5e-5), hex/hash, or is followed by letters that are not a known unit (1.234a);
  * a known unit directly after the number (x % s ms us ns tok tps MB ...) does not protect it (3.141ms -> 3.14ms).
Known limitations are asserted too so a behaviour change is a conscious decision.
"""
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import time  # noqa: E402

from common import fmt_num, round_half_up, round_obj, round_text, scrub  # noqa: E402

fails = []
checks = 0


def check(cond, msg):
    global checks
    checks += 1
    if not cond:
        fails.append(msg)
        print("FAIL", msg)


def eq(got, want, msg):
    check(got == want and type(got) is type(want), "%s: got %r, want %r" % (msg, got, want))


# ---------------------------------------------------------------- scrub (linear-time masks; no ReDoS)
_K = "sk-abcdefghijklmnop"
eq(scrub("k=" + _K + " end", _K), "k=[KEY] end", "scrub literal key")
eq(scrub("x sk-abc***mnop y", _K), "x [KEY] y", "scrub prefix+mask echo")
eq(scrub("x ***mnop y", _K), "x [KEY] y", "scrub mask+suffix echo")
eq(scrub("x ……mnop y", _K), "x [KEY] y", "scrub ellipsis mask echo")
eq(scrub("a**b..c", _K), "a**b..c", "scrub leaves masks without key suffix alone")
eq(scrub("", _K), "", "scrub empty")
for _n in (100000, 1000000):
    for _s in ("*" * _n, "." * _n, "*" * _n + "mnop", "ab" + "*." * (_n // 2), ("**x") * (_n // 3)):
        _t = time.time()
        _r = scrub(_s, _K)
        _dt = time.time() - _t
        check(_dt < 0.5, "scrub %d chars took %.2fs (quadratic ReDoS?)" % (len(_s), _dt))
    eq(scrub("*" * _n + "mnop", _K), "[KEY]", "scrub %d-run + suffix collapses to one mask" % _n)

# ---------------------------------------------------------------- fmt_num
eq(fmt_num(12345), "12,345", "fmt_num int thousands")
eq(fmt_num(12345, thousands=False), "12345", "fmt_num int no thousands")
eq(fmt_num(0), "0", "fmt_num zero int")
eq(fmt_num(-1234567), "-1,234,567", "fmt_num negative int")
eq(fmt_num(999), "999", "fmt_num small int")
eq(fmt_num(True), "True", "fmt_num bool True verbatim")
eq(fmt_num(False), "False", "fmt_num bool False verbatim")
eq(fmt_num(None), "", "fmt_num None -> empty")
eq(fmt_num(2.675), "2.68", "fmt_num 2.675 HALF_UP (round() gives 2.67)")
eq(fmt_num(100.0), "100.00", "fmt_num 100.0 fixed 2dp")
eq(fmt_num(0.5), "0.50", "fmt_num 0.5")
eq(fmt_num(-1.005), "-1.01", "fmt_num -1.005 half away from zero")
eq(fmt_num(1234.567), "1,234.57", "fmt_num float thousands")
eq(fmt_num(1234.567, thousands=False), "1234.57", "fmt_num float no thousands")
eq(fmt_num(0.0), "0.00", "fmt_num 0.0")
eq(fmt_num(-0.0), "0.00", "fmt_num -0.0 has no sign")
eq(fmt_num(-0.001), "0.00", "fmt_num -0.001 rounds to unsigned zero")
eq(fmt_num(0.004), "0.00", "fmt_num 0.004")
eq(fmt_num(0.005), "0.01", "fmt_num 0.005")
eq(fmt_num(0.999), "1.00", "fmt_num carry")
eq(fmt_num(1e-7), "0.00", "fmt_num tiny float, no sci notation")
eq(fmt_num(1.5e22), "15,000,000,000,000,000,000,000.00", "fmt_num huge float, no sci notation")
eq(fmt_num(1e22, thousands=False), "10000000000000000000000.00", "fmt_num huge float no thousands")
check("e" not in fmt_num(1.7976931348623157e308).lower(), "fmt_num float max has no exponent")
eq(fmt_num(float("nan")), "—", "fmt_num nan")
eq(fmt_num(float("inf")), "—", "fmt_num inf")
eq(fmt_num(float("-inf")), "—", "fmt_num -inf")
eq(fmt_num(10 ** 30), "1,000,000,000,000,000,000,000,000,000,000", "fmt_num huge int")
eq(fmt_num("abc"), "abc", "fmt_num str passes through")

# ---------------------------------------------------------------- round_half_up
eq(round_half_up(2.675, 2), 2.68, "round_half_up 2.675")
eq(round_half_up(1.005, 2), 1.01, "round_half_up 1.005")
eq(round_half_up(-1.005, 2), -1.01, "round_half_up -1.005")
eq(round_half_up(100.0, 2), 100.0, "round_half_up keeps float")
eq(round_half_up(0.1 + 0.2, 2), 0.3, "round_half_up 0.30000000000000004")
eq(round_half_up(-0.001, 2), 0.0, "round_half_up -0.001 -> 0.0 (no -0.0)")
check(math.copysign(1, round_half_up(-0.001, 2)) == 1, "round_half_up no negative zero")
check(math.isnan(round_half_up(float("nan"), 2)), "round_half_up nan passthrough")
eq(round_half_up(float("inf"), 2), float("inf"), "round_half_up inf passthrough")
eq(round_half_up(1.2345, 3), 1.235, "round_half_up nd=3")

# ---------------------------------------------------------------- round_text: rounds
ROUNDS = [
    ("1.261", "1.26"),
    ("0.7777", "0.78"),
    ("2.500", "2.50"),
    ("2.675", "2.68"),
    ("1.005", "1.01"),
    ("0.999", "1.00"),
    ("9.9999", "10.00"),
    ("-1.005", "-1.01"),
    ("delta -0.001", "delta 0.00"),
    ("ttft 1.261s", "ttft 1.26s"),
    ("ttft 3.141ms", "ttft 3.14ms"),
    ("ratio 1.261x", "ratio 1.26x"),
    ("ratio 1.261×", "ratio 1.26×"),
    ("ratio 1.261 x", "ratio 1.26 x"),
    ("hit 12.345%", "hit 12.35%"),
    ("12.345 ms total", "12.35 ms total"),
    ("speed 33.3333tok/s", "speed 33.33tok/s"),
    ("33.3333 tps", "33.33 tps"),
    ("size 1.234MB", "size 1.23MB"),
    ("p50=3.101 p90=6.402", "p50=3.10 p90=6.40"),
    (".ttft_s p50=3.101 p90=6.402", ".ttft_s p50=3.10 p90=6.40"),
    ("range 1.234-5.678", "range 1.23-5.68"),
    ("ends 1.2345.", "ends 1.23."),
    ("(0.1234)", "(0.12)"),
    ("[0.1234, 0.5678]", "[0.12, 0.57]"),
    ("0.123/0.456", "0.12/0.46"),
    ("1,234.567 tok", "1,234.57 tok"),
    ('"temperature":0.7777', '"temperature":0.78'),
    ("a=1.261;b=2.5", "a=1.26;b=2.5"),
    ("两次 1.2345 秒", "两次 1.23 秒"),
    ("1.261秒", "1.26秒"),
    ("P:temp_0.6 ttft=1.261s", "P:temp_0.6 ttft=1.26s"),
    ("1.2345\n9.8765", "1.23\n9.88"),
    ("12345.678901", "12345.68"),
    ("1.2345678901234567890123", "1.23"),
]
for src, want in ROUNDS:
    eq(round_text(src), want, "round_text %r" % src)

# ---------------------------------------------------------------- round_text: must NOT touch
KEEP = [
    "1.2", "1.26", "0.5", "100", "12345", "0.60", "",
    "1.2.3", "1.2.3.4", "v0.149.0", "0.149.0", "v1.234.567", "1.234.567",
    "127.0.0.1", "192.168.100.255", "10.0.0.1:8080",
    "2026-11-09", "2026-11-09T12:30:45Z", "12:30:45.123", "2026-11-09T12:30:45.123Z", "2026-11-09 12:30:45.123456",
    "1:23.456", "09.11.2026",
    "kimi-k3.1-pro", "gpt-4.1-mini", "claude-opus-4.5", "model-0.7777", "gpt-4.1234", "temp-0.6789",
    "P:temp_0.6", "P:top_p_0.95", "P:top_p_0.9500", "x_1.2345",
    "sk-abc1.2345xyz", "sk-ABCdef0123.4567",
    "5f4dcc3b5aa765d61d8327deb882cf99", "0x1.8p3", "0x1.2345p3", "deadbeef1.2345",
    "1.5e-5", "1.2345e-5", "6.0221e23", "1.2345E+5",
    "v1.234a", "1.234a", "1.234abc", "1.234x86", "1.2345-pro", "3.1415_turbo",
    "https://api.example.com/v1/1.2345", "http://127.0.0.1:8080/x?y=0.12345",
    "host-1.example.com", "api.v2.1234.example.com",
]
for s in KEEP:
    eq(round_text(s), s, "round_text keeps %r" % s)

# mixed: protected tokens untouched, plain numbers rounded in the same sentence
eq(round_text("kimi-k3.1-pro v0.149.0 2026-11-09 127.0.0.1 ttft=1.261s 0.9876"),
   "kimi-k3.1-pro v0.149.0 2026-11-09 127.0.0.1 ttft=1.26s 0.99", "round_text mixed sentence")
eq(round_text("see https://a.b/c/1.2345 then 2.3456"), "see https://a.b/c/1.2345 then 2.35", "round_text URL then number")

# non-str / idempotence
eq(round_text(None), None, "round_text None passthrough")
eq(round_text(5), 5, "round_text int passthrough")
for src, _ in ROUNDS:
    once = round_text(src)
    eq(round_text(once), once, "round_text idempotent %r" % src)
for s in KEEP:
    eq(round_text(round_text(s)), round_text(s), "round_text idempotent keep %r" % s)

# ---------------------------------------------------------------- round_obj
orig = {"a": 1.23456, "b": [1.005, 2, True, None, "ttft 1.2345s"], "c": (0.1 + 0.2, {"d": 100.0, "e": -0.0004}),
        "f": "1.23456", "g": float("nan")}
snap = json.dumps(orig, default=str, sort_keys=True)
out = round_obj(orig)
eq(out["a"], 1.23, "round_obj float")
eq(out["b"][0], 1.01, "round_obj float in list (HALF_UP)")
eq(out["b"][1], 2, "round_obj int unchanged")
check(out["b"][2] is True, "round_obj bool unchanged")
check(out["b"][3] is None, "round_obj None unchanged")
eq(out["b"][4], "ttft 1.2345s", "round_obj str untouched (round_text is separate)")
eq(out["f"], "1.23456", "round_obj numeric-looking str untouched")
check(isinstance(out["c"], tuple), "round_obj keeps tuple type")
eq(out["c"][0], 0.3, "round_obj 0.1+0.2")
eq(out["c"][1]["d"], 100.0, "round_obj integral float stays float")
eq(out["c"][1]["e"], 0.0, "round_obj tiny negative -> 0.0")
check(math.isnan(out["g"]), "round_obj nan passthrough")
check(json.dumps(orig, default=str, sort_keys=True) == snap, "round_obj does not mutate input")
check(out is not orig and out["b"] is not orig["b"], "round_obj returns new containers")
eq(json.dumps(round_obj({"x": 0.30000000000000004, "y": 1e-9})), '{"x": 0.3, "y": 0.0}', "round_obj json.dumps shape")
eq(round_obj(1.0), 1.0, "round_obj scalar float")
eq(round_obj("s"), "s", "round_obj scalar str")
eq(round_obj([]), [], "round_obj empty list")

# ---------------------------------------------------------------- fixtures: every string through round_text
DEC3 = re.compile(r"\d+\.\d{3,}")
# Independent protection test for the fixture sweep: what is allowed to still look like >=3 decimals after round_text.
PROTECTED = [
    re.compile(r"\d+(?:\.\d+){2,}"),                  # versions / IPs
    re.compile(r"\d{1,2}:\d{2}[:.]\d+\.?\d*"),        # times
    re.compile(r"[A-Za-z_][\w\-]*-?\d+\.\d+[\w\-.]*"),  # identifier-glued: kimi-k3.1-pro, temp_0.6789, deadbeef1.234
    re.compile(r"[A-Za-z_]-\d+\.\d+"),                # name-0.1234
    re.compile(r"https?://\S+"),
    re.compile(r"\d+\.\d+[eE][+-]?\d+"),              # scientific
    re.compile(r"\d+\.\d+[A-Za-z_]\w*"),              # number glued to a non-unit word
]


def walk_strings(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from walk_strings(k)
            yield from walk_strings(v)
    elif isinstance(o, (list, tuple)):
        for v in o:
            yield from walk_strings(v)
    elif isinstance(o, str):
        yield o


def walk_floats(o):
    if isinstance(o, dict):
        for v in o.values():
            yield from walk_floats(v)
    elif isinstance(o, (list, tuple)):
        for v in o:
            yield from walk_floats(v)
    elif isinstance(o, float):
        yield o


strings, floats = set(), []
for name in ("sample_spec.json", "sample_audit.json"):
    data = json.load(open(os.path.join(HERE, "fixtures", name), encoding="utf-8"))
    strings |= set(walk_strings(data))
    floats += list(walk_floats(data))
check(len(strings) > 100, "fixtures yielded strings (%d)" % len(strings))
check(len(floats) > 0, "fixtures yielded floats (%d)" % len(floats))

preserved = []
for s in sorted(strings):
    r = round_text(s)
    eq(round_text(r), r, "fixture idempotent %r" % s[:60])
    rest = r
    for p in PROTECTED:
        rest = p.sub(" ", rest)
    left = DEC3.findall(rest)
    check(not left, "fixture string still has >=3 decimals after round_text: %r -> %r" % (s[:80], left))
    for m in DEC3.finditer(r):
        preserved.append(r[max(0, m.start() - 12):m.end() + 8])

# every fixture float: fmt_num is 2dp, round_obj is a float with <= 2 decimals, no sci notation
for f in floats:
    t = fmt_num(f, thousands=False)
    check(re.fullmatch(r"-?\d+\.\d{2}", t) is not None, "fmt_num(%r)=%r is fixed 2dp" % (f, t))
    r = round_half_up(f, 2)
    check(isinstance(r, float) and re.fullmatch(r"-?\d+\.\d{1,2}", repr(r)) is not None, "round_half_up(%r)=%r" % (f, r))
rd = round_obj({"v": floats})
check(not DEC3.search(json.dumps(rd)), "round_obj(fixture floats) json has no >=3 decimals")

print()
print("fixture strings swept: %d, floats: %d" % (len(strings), len(floats)))
print("protected samples preserved with >=3 decimals (%d):" % len(preserved))
for p in sorted(set(preserved))[:40]:
    print("   keep:", repr(p))

print()
if fails:
    print("FAIL: %d of %d checks failed" % (len(fails), checks))
    sys.exit(1)
print("PASS: %d checks" % checks)
