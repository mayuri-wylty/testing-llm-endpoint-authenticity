#!/usr/bin/env python3
"""Self-test for perf_stats (pure math, no network). Exit 0 = all checks pass.

  python tests/selftest_perf_stats.py
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import perf_stats  # noqa: E402
from common import percentile  # noqa: E402

fails = []
checks = 0


def check(cond, msg):
    global checks
    checks += 1
    if not cond:
        fails.append(msg)
        print("FAIL", msg)


def near(got, want, tol=1e-9):
    return got is not None and want is not None and math.isclose(got, want, rel_tol=tol, abs_tol=tol)


def eq(got, want, msg):
    check(got == want, "%s: got %r, want %r" % (msg, got, want))


def approx(got, want, msg):
    check(near(got, want), "%s: got %r, want ~%r" % (msg, got, want))


def none(got, msg):
    check(got is None, "%s: got %r, want None" % (msg, got))


# ---------------------------------------------------------------- percentile: linear interpolation
xs = list(range(1, 11))
approx(percentile(xs, 50), 5.5, "percentile [1..10] p50")
approx(percentile(xs, 10), 1.9, "percentile [1..10] p10")
approx(percentile(xs, 90), 9.1, "percentile [1..10] p90")
approx(percentile(xs, 95), 9.55, "percentile [1..10] p95")
eq(percentile([7], 50), 7, "percentile single element")
none(percentile([], 50), "percentile empty list")
approx(percentile([None, 1, "x", 3], 50), 2.0, "percentile drops None/non-numeric")
eq(percentile([1, 2], 0), 1, "percentile p0")
eq(percentile([1, 2], 100), 2, "percentile p100")

# pct_set: shape, n/min/max, empty handling, bool rejection
ps = perf_stats.pct_set([1, 2, 3, 4])
eq(ps["n"], 4, "pct_set n")
eq(ps["min"], 1, "pct_set min")
eq(ps["max"], 4, "pct_set max")
approx(ps["p50"], 2.5, "pct_set p50")
approx(ps["p90"], 3.7, "pct_set p90")
approx(ps["p95"], 3.85, "pct_set p95")
empty = perf_stats.pct_set([])
eq(empty["n"], 0, "pct_set empty n")
check(all(empty[k] is None for k in ("p50", "p90", "p95", "min", "max")), "pct_set empty all None")
eq(perf_stats.pct_set([True, "x", None, 5])["n"], 1, "pct_set rejects bool/string/None")

# ---------------------------------------------------------------- generation window: boundary cases
approx(perf_stats.gen_seconds(10, 2), 8.0, "gen_seconds normal")
approx(perf_stats.gen_seconds(10, None), 10.0, "gen_seconds no ttft")
none(perf_stats.gen_seconds(None, 2), "gen_seconds missing total")
none(perf_stats.gen_seconds(2, 2), "gen_seconds zero window")
none(perf_stats.gen_seconds(2, 3), "gen_seconds negative window")
none(perf_stats.gen_seconds(0, 0), "gen_seconds total 0")
approx(perf_stats.gen_seconds(5, 0), 5.0, "gen_seconds ttft 0")

# ---------------------------------------------------------------- TPOT / output speed formulas
approx(perf_stats.tpot_ms(10, 2, 11), 800.0, "tpot_ms normal")
approx(perf_stats.tpot_ms(10, 2, 1), 8000.0, "tpot_ms single token divides by 1")
none(perf_stats.tpot_ms(10, 2, 0), "tpot_ms zero completion tokens")
none(perf_stats.tpot_ms(10, 2, None), "tpot_ms missing completion tokens")
none(perf_stats.tpot_ms(10, 10, 5), "tpot_ms zero generation window")
approx(perf_stats.output_tps(10, 2, 10), 1.25, "output_tps normal")
none(perf_stats.output_tps(10, 2, 0), "output_tps zero completion tokens")
none(perf_stats.output_tps(10, 2, None), "output_tps missing completion tokens")
none(perf_stats.output_tps(2, 2, 5), "output_tps zero generation window")

# sample_metrics fills values without mutating the input
raw = {"ttft": 2, "total": 10, "completion_tokens": 11}
filled = perf_stats.sample_metrics(raw)
approx(filled["tpot_ms"], 800.0, "sample_metrics tpot_ms")
approx(filled["output_tps"], 1.375, "sample_metrics output_tps")
check("tpot_ms" not in raw, "sample_metrics must not mutate input")

# ---------------------------------------------------------------- finish_histogram
rows = [{"finish": "stop"}, {"finish": "length"}, {"finish": "tool_calls"},
        {"finish": None}, {"finish": ""}, {"finish": "weird"}]
eq(perf_stats.finish_histogram(rows),
   {"stop": 1, "length": 1, "tool_calls": 1, "empty": 2, "other": 1},
   "finish_histogram None/empty -> empty, unknown -> other")
eq(perf_stats.finish_histogram([]), {"stop": 0, "length": 0, "tool_calls": 0, "empty": 0, "other": 0},
   "finish_histogram empty input")

# ---------------------------------------------------------------- summarize: ok-only percentiles, all-row counts
samples = [
    {"ok": True, "ttft": 2.0, "total": 10.0, "completion_tokens": 11, "prompt_tokens": 100,
     "cached_tokens": 0, "cache_write_tokens": 100, "finish": "stop"},
    {"ok": True, "ttft": 4.0, "total": 14.0, "completion_tokens": 21, "prompt_tokens": 200,
     "cached_tokens": 150, "cache_write_tokens": 50, "finish": "length"},
    {"ok": False, "ttft": None, "total": 999.0, "completion_tokens": None, "prompt_tokens": None,
     "cached_tokens": None, "cache_write_tokens": None, "finish": None},
]
agg = perf_stats.summarize(samples)
eq(agg["samples"], 3, "summarize samples counts all rows")
eq(agg["success"], 2, "summarize success counts ok rows")
approx(agg["success_rate"], 200.0 / 3.0, "summarize success_rate")
eq(agg["latency_s"]["n"], 2, "summarize latency n ignores failures")
approx(agg["latency_s"]["min"], 10.0, "summarize latency min ignores failed 999s")
approx(agg["latency_s"]["max"], 14.0, "summarize latency max ignores failed 999s")
approx(agg["latency_s"]["p50"], 12.0, "summarize latency p50")
approx(agg["latency_s"]["p90"], 13.6, "summarize latency p90")
approx(agg["ttft_s"]["p50"], 3.0, "summarize ttft p50")
approx(agg["tpot_ms"]["p50"], 650.0, "summarize tpot p50")
approx(agg["output_tps"]["p50"], 1.7375, "summarize output_tps p50")
eq(agg["finish"], {"stop": 1, "length": 1, "tool_calls": 0, "empty": 1, "other": 0},
   "summarize finish counts failed row as empty")
eq(agg["totals"], {"prompt_tokens": 300, "completion_tokens": 32, "cached_tokens": 150, "cache_write_tokens": 150},
   "summarize token totals over all rows")

agg0 = perf_stats.summarize([])
eq(agg0["samples"], 0, "summarize empty samples")
eq(agg0["success"], 0, "summarize empty success")
none(agg0["success_rate"], "summarize empty success_rate")
eq(agg0["latency_s"]["n"], 0, "summarize empty percentile n")
none(agg0["latency_s"]["p50"], "summarize empty percentile p50")
eq(agg0["totals"], {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "cache_write_tokens": 0},
   "summarize empty totals")

# ---------------------------------------------------------------- throughput: four values and invalid windows
tp = perf_stats.throughput(60, 6, 600, 1200)
approx(tp["rpm"], 6.0, "throughput rpm")
approx(tp["tpm"], 1800.0, "throughput tpm")
approx(tp["input_tpm"], 600.0, "throughput input_tpm")
approx(tp["output_tpm"], 1200.0, "throughput output_tpm")
for bad in (0, -1, None, "x"):
    got = perf_stats.throughput(bad, 6, 600, 1200)
    check(all(got[k] is None for k in ("rpm", "tpm", "input_tpm", "output_tpm")),
          "throughput invalid elapsed %r must be all None, got %r" % (bad, got))

# ---------------------------------------------------------------- cache_hit_rate
approx(perf_stats.cache_hit_rate(500, 1000), 50.0, "cache_hit_rate 50%")
approx(perf_stats.cache_hit_rate(0, 1000), 0.0, "cache_hit_rate zero cached")
none(perf_stats.cache_hit_rate(100, 0), "cache_hit_rate zero prompt")
none(perf_stats.cache_hit_rate(None, 100), "cache_hit_rate missing cached")
none(perf_stats.cache_hit_rate(100, None), "cache_hit_rate missing prompt")
none(perf_stats.cache_hit_rate(500, -10), "cache_hit_rate negative prompt")

# ---------------------------------------------------------------- compare / compare_percentiles
c = perf_stats.compare(110, 100)
approx(c["measured"], 110.0, "compare measured")
approx(c["baseline"], 100.0, "compare baseline")
approx(c["delta"], 10.0, "compare delta")
approx(c["ratio"], 1.1, "compare ratio")
approx(c["deviation_pct"], 10.0, "compare deviation_pct")
c0 = perf_stats.compare(5, 0)
approx(c0["delta"], 5.0, "compare zero baseline delta")
none(c0["ratio"], "compare zero baseline ratio")
none(c0["deviation_pct"], "compare zero baseline deviation")
for a, b in ((None, 100), (100, None)):
    got = perf_stats.compare(a, b)
    check(all(got[k] is None for k in ("delta", "ratio", "deviation_pct")),
          "compare missing input %r/%r must be all None" % (a, b))

measured = {"ttft_s": {"p50": 2.0, "p90": 4.0, "p95": 5.0},
            "latency_s": {"p50": 10.0, "p90": 14.0, "p95": 15.0},
            "tpot_ms": {"p50": 800.0, "p90": 1000.0, "p95": 1100.0},
            "output_tps": {"p50": 1.0, "p90": 2.0, "p95": 2.5}}
baseline = {"ttft_s": {"p50": 1.0, "p90": 2.0, "p95": 2.5}}
cmp_all = perf_stats.compare_percentiles(measured, baseline)
eq(set(cmp_all), set(perf_stats.METRICS), "compare_percentiles covers all metrics")
eq(set(cmp_all["ttft_s"]), {"p50", "p90", "p95"}, "compare_percentiles percentile keys")
approx(cmp_all["ttft_s"]["p50"]["ratio"], 2.0, "compare_percentiles ttft p50 ratio")
approx(cmp_all["ttft_s"]["p90"]["deviation_pct"], 100.0, "compare_percentiles ttft p90 deviation")
none(cmp_all["latency_s"]["p50"]["baseline"], "compare_percentiles missing baseline metric")
approx(cmp_all["latency_s"]["p95"]["measured"], 15.0, "compare_percentiles measured passthrough")
cmp_one = perf_stats.compare_percentiles(measured, baseline, metrics=("ttft_s",))
eq(set(cmp_one), {"ttft_s"}, "compare_percentiles metrics subset")

# ---------------------------------------------------------------- report
if fails:
    print("perf_stats: FAILED (%d/%d checks)" % (len(fails), checks))
    sys.exit(1)
print("perf_stats: %d checks passed" % checks)
