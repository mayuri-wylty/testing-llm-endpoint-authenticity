#!/usr/bin/env python3
"""Self-test for scripts/benchmark.py (pure data, no network, reads only local fixtures/profiles).

  python tests/selftest_benchmark.py
"""
import copy
import io
import json
import math
import os
import sys
from contextlib import redirect_stderr

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import benchmark  # noqa: E402
import spec_checks  # noqa: E402

fails = []
checks = 0


def check(cond, msg):
    global checks
    checks += 1
    if not cond:
        fails.append(msg)
        print("FAIL", msg)


def approx(got, want, msg, tol=1e-9):
    ok = got is not None and want is not None and math.isclose(got, want, rel_tol=tol, abs_tol=tol)
    check(ok, "%s: got %r, want ~%r" % (msg, got, want))


def eq(got, want, msg):
    check(got == want, "%s: got %r, want %r" % (msg, got, want))


def load_json(*parts):
    with open(os.path.join(ROOT, *parts), "r", encoding="utf-8") as f:
        return json.load(f)


def find(rows, group, metric, pctl):
    hit = [r for r in rows if r["group"] == group and r["metric"] == metric and r["pctl"] == pctl]
    check(len(hit) == 1, "row %s/%s/%s found exactly once (got %d)" % (group, metric, pctl, len(hit)))
    return hit[0] if hit else {}


FIELDS = {"group", "metric", "pctl", "unit", "official", "measured", "ratio", "delta_pct", "direction",
          "threshold", "threshold_kind", "verdict", "source"}

spec = load_json("tests", "fixtures", "sample_spec.json")
prof = load_json("profiles", "kimi-k3.1-pro.json")
items = {i["key"]: i["status"] for i in spec["spec"]["items"]}

# ---------------------------------------------------------------- shape on the real fixture
rows = benchmark.build_rows(spec, prof)
eq(len(rows), 16, "row count (12 perf + 1 ttft + 3 cache)")
check(all(set(r) == FIELDS for r in rows), "every row has exactly the documented fields")
check(all(r["verdict"] in ("ok", "warn", "bad", "unknown") for r in rows), "verdict enum")
check(all(r["direction"] in ("lower", "higher") for r in rows), "direction enum")
check(all(isinstance(r["source"], str) and r["source"] for r in rows), "source present")
check(all(r["official"] is not None and r["measured"] is not None for r in rows), "fixture is fully populated")
eq([r["group"] for r in rows].count("perf"), 12, "perf rows")

# ttft p50 (lower-better, ratio_max)
r = find(rows, "perf", "ttft_s", "p50")
approx(r["official"], 2.459, "ttft p50 official")
approx(r["measured"], 3.101, "ttft p50 measured")
approx(r["ratio"], 3.101 / 2.459, "ttft p50 ratio")
approx(r["delta_pct"], (3.101 - 2.459) / 2.459 * 100, "ttft p50 delta_pct")
approx(r["threshold"], 2.0, "ttft p50 threshold")
eq(r["threshold_kind"], "ratio_max", "ttft p50 threshold_kind")
eq((r["unit"], r["direction"], r["verdict"]), ("s", "lower", "ok"), "ttft p50 unit/direction/verdict")
eq(r["source"], "profile.baseline.percentiles.ttft_s.p50 vs spec.load.percentiles.ttft_s.p50", "ttft p50 source")

# p90 uses the p90 ratio gate; p95 has no gate
r = find(rows, "perf", "latency_s", "p90")
approx(r["threshold"], 2.5, "latency p90 threshold")
approx(r["ratio"], 10.104 / 8.982, "latency p90 ratio")
r = find(rows, "perf", "tpot_ms", "p95")
eq((r["threshold"], r["threshold_kind"], r["verdict"]), (None, None, "unknown"), "p95 ungated -> unknown")
check(r["ratio"] is not None, "p95 ratio still computed")

# output_tps: higher-better, p50 floor 0.5 only
r = find(rows, "perf", "output_tps", "p50")
approx(r["official"], 36.238, "tps p50 official")
approx(r["ratio"], 33.104 / 36.238, "tps p50 ratio")
approx(r["delta_pct"], (33.104 - 36.238) / 36.238 * 100, "tps p50 delta_pct (negative = slower)")
check(r["delta_pct"] < 0, "tps delta is signed")
approx(r["threshold"], 0.5, "tps p50 threshold")
eq((r["unit"], r["direction"], r["threshold_kind"], r["verdict"]),
   ("tok/s", "higher", "ratio_min", "ok"), "tps p50 meta")
eq(find(rows, "perf", "output_tps", "p90")["verdict"], "unknown", "tps p90 ungated")

# ttft group: absolute p90 ceiling (item 2)
r = find(rows, "ttft", "ttft_s", "p90")
approx(r["official"], 4.879, "ttft group official (baseline p90)")
approx(r["measured"], 6.402, "ttft group measured p90")
approx(r["threshold"], 10.0, "ttft group threshold")
eq((r["threshold_kind"], r["verdict"]), ("abs_max", "ok"), "ttft group kind/verdict")

# cache: higher-better, pct_min
r = find(rows, "cache", "cache_identical_20k", None)
approx(r["official"], 99.5, "cache 20k official")
approx(r["measured"], 99.6, "cache 20k measured")
approx(r["ratio"], 99.6 / 99.5, "cache 20k ratio")
approx(r["delta_pct"], (99.6 - 99.5) / 99.5 * 100, "cache 20k delta_pct")
approx(r["threshold"], 85.0, "cache threshold")
eq((r["unit"], r["direction"], r["threshold_kind"], r["verdict"]),
   ("%", "higher", "pct_min", "ok"), "cache 20k meta")
approx(find(rows, "cache", "cache_short_1k", None)["measured"], 93.1, "cache 1k measured")
approx(find(rows, "cache", "cache_multiturn_12k", None)["official"], 98.4, "cache 12k official")

# ---------------------------------------------------------------- verdicts agree with spec items 1/2/3
doc = {k: v for k, v in spec.items() if k != "spec"}


def consistent(rows_, doc_, label):
    """pass -> no gated row bad; fail -> at least one gated row bad (partial: mixed or all-bad-but-not-majority)."""
    for group, key, fn in (("perf", "perf_baseline", spec_checks.check_perf_baseline),
                           ("ttft", "ttft", spec_checks.check_ttft),
                           ("cache", "cache_hit", spec_checks.check_cache_hit)):
        status = fn(doc_, prof)["status"]
        gated = [x for x in rows_ if x["group"] == group and x["verdict"] != "unknown"]
        bad = [x for x in gated if x["verdict"] == "bad"]
        if status == "pass":
            check(not bad, "%s: item %s pass but %d bad rows" % (label, key, len(bad)))
        elif status in ("partial", "fail"):
            check(bad, "%s: item %s %s but no bad row" % (label, key, status))


eq((items["perf_baseline"], items["ttft"], items["cache_hit"]), ("pass", "pass", "pass"), "fixture items 1/2/3 pass")
check(all(x["verdict"] == "ok" for x in rows if x["verdict"] != "unknown"), "fixture: every gated row ok")
consistent(rows, doc, "fixture")

# degraded measurement: slow everything x3, tps /3, cache collapses
bad_spec = copy.deepcopy(spec)
for m, p in bad_spec["load"]["percentiles"].items():
    for k in ("p50", "p90", "p95"):
        p[k] = p[k] / 3.0 if m == "output_tps" else p[k] * 3.0
for sc in bad_spec["cache"]["scenarios"].values():
    sc["hit_rate_pct"] = 10.0
bad_rows = benchmark.build_rows(bad_spec, prof)
bad_doc = {k: v for k, v in bad_spec.items() if k != "spec"}
eq(find(bad_rows, "perf", "ttft_s", "p50")["verdict"], "bad", "degraded ttft p50 bad")
eq(find(bad_rows, "perf", "output_tps", "p50")["verdict"], "bad", "degraded tps p50 bad")
eq(find(bad_rows, "ttft", "ttft_s", "p90")["verdict"], "bad", "degraded ttft abs bad")
eq(find(bad_rows, "cache", "cache_short_1k", None)["verdict"], "bad", "degraded cache bad")
consistent(bad_rows, bad_doc, "degraded")

# ---------------------------------------------------------------- per-model override
hs_spec = copy.deepcopy(spec)
hs_spec["model"] = "kimi-k3.1-pro-highspeed"
hs_rows = benchmark.build_rows(hs_spec, prof)
r = find(hs_rows, "perf", "ttft_s", "p50")
approx(r["official"], 2.485, "highspeed ttft p50 official comes from percentiles_by_model")
check("percentiles_by_model[kimi-k3.1-pro-highspeed]" in r["source"], "override source labelled")
approx(find(hs_rows, "perf", "output_tps", "p50")["official"], 196.231, "highspeed tps p50 official")
approx(find(rows, "perf", "ttft_s", "p50")["official"], 2.459, "primary model keeps primary block")
unk = copy.deepcopy(spec)
unk["model"] = "some-alias-not-in-profile"
approx(find(benchmark.build_rows(unk, prof), "perf", "ttft_s", "p50")["official"], 2.459,
       "unknown model falls back to primary block")

# ---------------------------------------------------------------- degradations
# profile=None: no official, nothing gated, measured kept
nop = benchmark.build_rows(spec, None)
eq(len(nop), 16, "profile=None keeps the row skeleton")
check(all(x["official"] is None and x["ratio"] is None and x["delta_pct"] is None for x in nop),
      "profile=None: official/ratio/delta None")
check(all(x["verdict"] == "unknown" and x["threshold"] is None for x in nop), "profile=None: unknown, no threshold")
approx(find(nop, "perf", "ttft_s", "p50")["measured"], 3.101, "profile=None keeps measured")
approx(find(nop, "cache", "cache_short_1k", None)["measured"], 93.1, "profile=None keeps cache measured")

# spec=None: no measured, official kept
nos = benchmark.build_rows(None, prof)
eq(len(nos), 16, "spec=None keeps the row skeleton")
check(all(x["measured"] is None and x["ratio"] is None and x["delta_pct"] is None for x in nos),
      "spec=None: measured/ratio/delta None")
check(all(x["verdict"] == "unknown" for x in nos), "spec=None: unknown")
approx(find(nos, "perf", "ttft_s", "p50")["official"], 2.459, "spec=None keeps official")
approx(find(nos, "cache", "cache_identical_20k", None)["threshold"], 85.0, "spec=None keeps threshold")

# both None, garbage inputs: no exception
eq(len(benchmark.build_rows(None, None)), 16, "both None")
eq(len(benchmark.build_rows("x", [1])), 16, "non-dict inputs tolerated")

# partial profile: drop percentiles.output_tps, cache.short_1k, targets.perf_p50_ratio_max
part = copy.deepcopy(prof)
del part["baseline"]["percentiles"]["output_tps"]
del part["baseline"]["cache"]["short_1k"]
del part["targets"]["perf_p50_ratio_max"]
prow = benchmark.build_rows(spec, part)
r = find(prow, "perf", "output_tps", "p50")
check(r["official"] is None and r["ratio"] is None and r["delta_pct"] is None and r["verdict"] == "unknown",
      "missing official percentile -> None/unknown")
approx(r["measured"], 33.104, "missing official keeps measured")
r = find(prow, "cache", "cache_short_1k", None)
check(r["official"] is None and r["delta_pct"] is None, "missing cache official -> None")
eq(r["verdict"], "ok", "cache verdict still judged from measured (93.1) vs target (85)")
r = find(prow, "perf", "ttft_s", "p50")
approx(r["threshold"], 2.0, "missing target falls back to spec_checks default")
eq(r["verdict"], "ok", "missing target default still judged")
# spec partially missing
pspec = copy.deepcopy(spec)
del pspec["load"]["percentiles"]["tpot_ms"]
del pspec["cache"]["scenarios"]["B_multiturn_12k"]
q = benchmark.build_rows(pspec, prof)
check(find(q, "perf", "tpot_ms", "p50")["measured"] is None, "missing load metric -> measured None")
eq(find(q, "perf", "tpot_ms", "p50")["verdict"], "unknown", "missing load metric -> unknown")
check(find(q, "cache", "cache_multiturn_12k", None)["measured"] is None, "missing cache scenario -> None")
# zero / bool / string numbers never divide or crash
z = copy.deepcopy(prof)
z["baseline"]["percentiles"]["ttft_s"]["p50"] = 0
z["baseline"]["cache"]["identical_20k"] = True
zr = benchmark.build_rows(spec, z)
r = find(zr, "perf", "ttft_s", "p50")
check(r["official"] == 0.0 and r["ratio"] is None and r["delta_pct"] is None, "official=0 -> no ratio")
check(find(zr, "cache", "cache_identical_20k", None)["official"] is None, "bool official rejected")
# rows must not mutate inputs
check(spec == load_json("tests", "fixtures", "sample_spec.json") and prof == load_json("profiles", "kimi-k3.1-pro.json"),
      "inputs untouched")

# ---------------------------------------------------------------- card (item.status) and badge (row verdict) share one gate
# The spec JSON is evaluated with profile X (targets overridden) but may be rendered with another --profile: the rows must
# read the thresholds from item.expected first, so "达标 vs 偏离" can never disagree between the card and the badge.
def agree(rows_, spec_doc, label):
    st = {i["key"]: i["status"] for i in spec_doc["spec"]["items"]}
    for group, key in (("perf", "perf_baseline"), ("ttft", "ttft"), ("cache", "cache_hit")):
        gated = [x for x in rows_ if x["group"] == group and x["verdict"] != "unknown"]
        bad = [x for x in gated if x["verdict"] == "bad"]
        check(gated, "%s: %s has gated rows" % (label, key))
        check((st[key] == "pass") == (not bad),
              "%s: item %s=%s but %d bad rows (card/badge disagree)" % (label, key, st[key], len(bad)))


def evaluated(measured_spec, targets):
    p = copy.deepcopy(prof)
    p["targets"].update(targets)
    base = {k: v for k, v in measured_spec.items() if k != "spec"}
    out = dict(base)
    out["spec"] = spec_checks.evaluate(base, p)
    return out


strict = evaluated(spec, {"perf_p50_ratio_max": 1.1, "perf_p90_ratio_max": 1.2,
                          "ttft_p90_max_s": 5.0, "cache_hit_min_pct": 99.9})
check(all(i["status"] != "pass" for i in strict["spec"]["items"][:3]), "strict targets fail items 1-3")
agree(benchmark.build_rows(strict, prof), strict, "strict spec, default profile")
approx(find(benchmark.build_rows(strict, prof), "perf", "ttft_s", "p50")["threshold"], 1.1,
       "row threshold comes from item.expected.p50_ratio_max, not profile.targets")
approx(find(benchmark.build_rows(strict, prof), "cache", "cache_short_1k", None)["threshold"], 99.9,
       "cache row threshold comes from item.expected.min_pct")
approx(find(benchmark.build_rows(strict, prof), "ttft", "ttft_s", "p90")["threshold"], 5.0,
       "ttft row threshold comes from item.expected.p90_max_s")

lax_spec = copy.deepcopy(bad_spec)
lax_spec["load"]["percentiles"]["output_tps"] = copy.deepcopy(spec["load"]["percentiles"]["output_tps"])  # the 0.5 tps floor is fixed
lax = evaluated(lax_spec, {"perf_p50_ratio_max": 50.0, "perf_p90_ratio_max": 50.0,
                           "ttft_p90_max_s": 1000.0, "cache_hit_min_pct": 1.0})
check(all(i["status"] == "pass" for i in lax["spec"]["items"][:3]), "lax targets pass the degraded measurements")
agree(benchmark.build_rows(lax, prof), lax, "lax spec on degraded data, default profile")

mixed = evaluated(spec, {"cache_hit_min_pct": 95.0, "perf_p50_ratio_max": 1.2})
agree(benchmark.build_rows(mixed, prof), mixed, "mixed spec, default profile")
eq(find(benchmark.build_rows(mixed, prof), "cache", "cache_short_1k", None)["verdict"], "bad", "mixed: 93.1 < 95 bad")
eq(find(benchmark.build_rows(mixed, prof), "cache", "cache_identical_20k", None)["verdict"], "ok", "mixed: 99.6 >= 95 ok")
# item.expected missing -> falls back to profile.targets (then defaults)
noexp = copy.deepcopy(strict)
for i in noexp["spec"]["items"][:3]:
    i["expected"] = {}
approx(find(benchmark.build_rows(noexp, prof), "perf", "ttft_s", "p50")["threshold"], 2.0, "no item.expected -> profile target")
pt = copy.deepcopy(prof)
pt["targets"]["cache_hit_min_pct"] = 50.0
approx(find(benchmark.build_rows(noexp, pt), "cache", "cache_short_1k", None)["threshold"], 50.0,
       "no item.expected -> the --profile cache target")

# cache scenarios that only carry a pass flag (no hit rate): row verdict follows the flag, official still shown
flag = copy.deepcopy(spec)
for name, passed in (("A_identical_20k", True), ("B_multiturn_12k", False), ("C_short_1k", True)):
    flag["cache"]["scenarios"][name] = {"pass": passed}
frows = benchmark.build_rows(flag, prof)
eq([find(frows, "cache", m, None)["verdict"] for m in ("cache_identical_20k", "cache_multiturn_12k", "cache_short_1k")],
   ["ok", "bad", "ok"], "pass-only cache scenarios: verdict follows the flag (not unknown)")
check(all(find(frows, "cache", m, None)["measured"] is None for m in ("cache_identical_20k", "cache_multiturn_12k")),
      "pass-only cache scenarios keep measured=None")
approx(find(frows, "cache", "cache_identical_20k", None)["official"], 99.5, "pass-only scenario still shows official")
flag_doc = {k: v for k, v in flag.items() if k != "spec"}
eq(spec_checks.check_cache_hit(flag_doc, prof)["status"], "partial", "card: partial for flags pass/fail/pass")
check(any(x["verdict"] == "bad" for x in frows if x["group"] == "cache"), "badge: bad row where the card is partial")
eq([x["verdict"] for x in benchmark.build_rows(flag, None) if x["group"] == "cache"], ["unknown"] * 3,
   "pass-only scenarios without a profile stay unknown")

# a list / dict model must not raise (spec says "never raises")
for bad_model in (["a"], {"a": 1}, 7):
    ms = copy.deepcopy(spec)
    ms["model"] = bad_model
    eq(len(benchmark.build_rows(ms, prof)), 16, "non-string model %r tolerated" % (bad_model,))
    approx(find(benchmark.build_rows(ms, prof), "perf", "ttft_s", "p50")["official"], 2.459,
           "non-string model %r falls back to the primary block" % (bad_model,))

# ---------------------------------------------------------------- baseline_source
eq(benchmark.baseline_source(prof), "2026-11-09", "baseline_source collected date")
eq(benchmark.baseline_source({"baseline_source": "2026-01-02"}), "2026-01-02", "baseline_source string form")
eq(benchmark.baseline_source({"baseline_source": {"derived_from": "x.json"}}), "x.json", "baseline_source fallback")
eq(benchmark.baseline_source({}), None, "baseline_source missing")
eq(benchmark.baseline_source(None), None, "baseline_source None")

# ---------------------------------------------------------------- load_profile
p1 = benchmark.load_profile("kimi-k3.1-pro")
check(isinstance(p1, dict) and p1.get("id") == "kimi-k3.1-pro", "load_profile by id")
p2 = benchmark.load_profile(os.path.join(ROOT, "profiles", "kimi-k3.1-pro.json"))
check(isinstance(p2, dict) and p2.get("id") == "kimi-k3.1-pro", "load_profile by path")
buf = io.StringIO()
with redirect_stderr(buf):
    miss = benchmark.load_profile("no-such-profile-xyz")
eq(miss, None, "load_profile missing -> None")
check("no-such-profile-xyz" in buf.getvalue(), "load_profile warns on stderr")
eq(benchmark.load_profile(None), None, "load_profile(None)")
eq(benchmark.load_profile(""), None, "load_profile('')")

if fails:
    print("FAIL: %d of %d checks failed" % (len(fails), checks))
    sys.exit(1)
print("PASS: %d checks" % checks)
