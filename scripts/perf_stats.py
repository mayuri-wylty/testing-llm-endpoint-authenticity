#!/usr/bin/env python3
"""Performance statistics for load/cache tests (stdlib only, pure functions).

Percentiles come from common.percentile(), the same linear-interpolation formula as the official
baseline (baseline_official/scripts/run_compare.py: pct()). Nothing here does I/O or loads a profile,
so tests/selftest_perf_stats.py can exercise every function with plain literals.

Sample dicts (as produced by load_test.py) may carry: ok, ttft (s), total (s), completion_tokens,
prompt_tokens, cached_tokens, cache_write_tokens, finish. Missing / None fields are tolerated
everywhere and yield None rather than an exception.

Definitions:
  gen_s      = total - ttft                      generation window (first token excluded)
  output_tps = completion_tokens / gen_s
  tpot_ms    = gen_s * 1000 / max(completion_tokens - 1, 1)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import percentile  # noqa: E402

PCTS = (50, 90, 95)
METRICS = ("latency_s", "ttft_s", "tpot_ms", "output_tps")


def _num(v):
    """float(v) for real numbers, else None. bool is deliberately not a number here."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def pct_set(values):
    """{'p50','p90','p95','n','min','max'} over the numeric entries; all None (n=0) on empty input."""
    xs = [x for x in (_num(v) for v in values) if x is not None]
    out = {"n": len(xs), "min": min(xs) if xs else None, "max": max(xs) if xs else None}
    for p in PCTS:
        out["p%d" % p] = percentile(xs, p)
    return out


def gen_seconds(total_s, ttft_s):
    """Generation window in seconds; None when total is missing or the window is not positive."""
    total, ttft = _num(total_s), _num(ttft_s)
    if total is None:
        return None
    gap = total - (ttft if ttft is not None else 0.0)
    return gap if gap > 0 else None


def output_tps(total_s, ttft_s, completion_tokens):
    """Completion tokens per generation second (first-token latency excluded)."""
    gen, ct = gen_seconds(total_s, ttft_s), _num(completion_tokens)
    if gen is None or ct is None or ct <= 0:
        return None
    return ct / gen


def tpot_ms(total_s, ttft_s, completion_tokens):
    """Time per output token, ms: (total - ttft) * 1000 / max(ct - 1, 1)."""
    gen, ct = gen_seconds(total_s, ttft_s), _num(completion_tokens)
    if gen is None or ct is None or ct <= 0:
        return None
    return gen * 1000.0 / max(ct - 1, 1)


def sample_metrics(sample):
    """Copy of one sample with output_tps and tpot_ms filled in."""
    out = dict(sample)
    out["output_tps"] = output_tps(sample.get("total"), sample.get("ttft"), sample.get("completion_tokens"))
    out["tpot_ms"] = tpot_ms(sample.get("total"), sample.get("ttft"), sample.get("completion_tokens"))
    return out


def finish_histogram(rows):
    """Counts by finish_reason; None and '' both count as 'empty' (the upstream defect we report)."""
    hist = {"stop": 0, "length": 0, "tool_calls": 0, "empty": 0, "other": 0}
    for r in rows:
        f = r.get("finish")
        if f is None or f == "":
            hist["empty"] += 1
        elif f in ("stop", "length", "tool_calls"):
            hist[f] += 1
        else:
            hist["other"] += 1
    return hist


def token_totals(rows, ok_only=False):
    """Sum of prompt/completion/cached/cache_write tokens over rows (all rows by default)."""
    tot = {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "cache_write_tokens": 0}
    for r in rows:
        if ok_only and not r.get("ok"):
            continue
        for k in tot:
            v = _num(r.get(k))
            if v:
                tot[k] += int(v)
    return tot


def summarize(samples):
    """Aggregate a load-test batch. Percentiles use successful requests only; counts use all of them."""
    rows = [sample_metrics(s) for s in samples]
    ok = [r for r in rows if r.get("ok")]
    n = len(rows)
    return {
        "samples": n,
        "success": len(ok),
        "success_rate": (100.0 * len(ok) / n) if n else None,
        "latency_s": pct_set([r.get("total") for r in ok]),
        "ttft_s": pct_set([r.get("ttft") for r in ok]),
        "tpot_ms": pct_set([r.get("tpot_ms") for r in ok]),
        "output_tps": pct_set([r.get("output_tps") for r in ok]),
        "finish": finish_histogram(rows),
        "totals": token_totals(rows),
    }


def throughput(elapsed_s, requests, prompt_tokens=0, completion_tokens=0):
    """Requests/tokens per minute over the whole batch window (wall clock, including queueing)."""
    el = _num(elapsed_s)
    if el is None or el <= 0:
        return {"rpm": None, "tpm": None, "input_tpm": None, "output_tpm": None}
    req = _num(requests) or 0.0
    pt = _num(prompt_tokens) or 0.0
    ct = _num(completion_tokens) or 0.0
    return {
        "rpm": 60.0 * req / el,
        "tpm": 60.0 * (pt + ct) / el,
        "input_tpm": 60.0 * pt / el,
        "output_tpm": 60.0 * ct / el,
    }


def cache_hit_rate(cached_tokens, prompt_tokens):
    """Percentage of the prompt served from cache, or None when prompt_tokens is missing/zero."""
    cached, prompt = _num(cached_tokens), _num(prompt_tokens)
    if prompt is None or prompt <= 0 or cached is None:
        return None
    return 100.0 * cached / prompt


def compare(measured, baseline):
    """measured vs baseline -> {measured, baseline, delta, ratio, deviation_pct}; ratio None when baseline is 0."""
    m, b = _num(measured), _num(baseline)
    if m is None or b is None:
        return {"measured": m, "baseline": b, "delta": None, "ratio": None, "deviation_pct": None}
    return {
        "measured": m,
        "baseline": b,
        "delta": m - b,
        "ratio": (m / b) if b else None,
        "deviation_pct": ((m - b) / b * 100.0) if b else None,
    }


def compare_percentiles(measured_map, baseline_map, metrics=METRICS):
    """Nested {metric: {p50: compare(...), p90: ..., p95: ...}} for every metric present on both sides."""
    out = {}
    for metric in metrics:
        mset = (measured_map or {}).get(metric) or {}
        bset = (baseline_map or {}).get(metric) or {}
        row = {}
        for p in PCTS:
            key = "p%d" % p
            row[key] = compare(mset.get(key), bset.get(key))
        out[metric] = row
    return out
