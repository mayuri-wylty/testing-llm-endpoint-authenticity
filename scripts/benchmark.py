#!/usr/bin/env python3
"""Official-vs-measured benchmark rows for the HTML / md reports (pure data, no formatting, no I/O but load_profile).

build_rows(spec, profile) joins the frozen official baseline in a profile (baseline.percentiles with a
per-model override, baseline.cache, targets) with the measured numbers of a spec_audit.py JSON
(load.percentiles, cache.scenarios) and returns one dict per comparable metric. Values stay full precision;
rounding, units text and HTML/markdown are the renderers' job. Nothing here talks to any endpoint.

Row fields
  group           'perf' | 'ttft' | 'cache'
  metric          ttft_s / latency_s / tpot_ms / output_tps | cache_identical_20k / cache_multiturn_12k / cache_short_1k
  pctl            'p50' | 'p90' | 'p95' | None (cache rows)
  unit            's' | 'ms' | 'tok/s' | '%'
  official        float | None     frozen baseline value
  measured        float | None     value from the spec JSON
  ratio           measured/official | None
  delta_pct       (measured-official)/official*100 | None   (signed; same formula for lower/higher-better)
  direction       'lower' | 'higher'    which way is better
  threshold       float | None     gate value; threshold_kind says how to read it:
                    'ratio_max' ratio <= threshold   'ratio_min' ratio >= threshold
                    'abs_max'   measured <= threshold   'pct_min' measured >= threshold   (None when ungated)
  verdict         'ok' | 'warn' | 'bad' | 'unknown'
  source          where the numbers come from, e.g.
                  'profile.baseline.percentiles.ttft_s.p50 vs spec.load.percentiles.ttft_s.p50'

Verdict policy (mirrors spec_checks.check_perf_baseline / check_ttft / check_cache_hit). Thresholds are read from
the spec item's own `expected` block first (so the badge matches the card status whatever --profile is passed
here), then profile.targets, then the same defaults spec_checks uses. A cache scenario that only has a `pass`
flag and no hit rate takes its verdict from the flag (pass -> ok, otherwise bad):
  gated row within its limit -> 'ok'; beyond it -> 'bad'; no gate (p95 rows, p90/p95 of output_tps) or missing
  data (no profile, no spec, no official/measured value) -> 'unknown'. Row level has no 'partial', so 'warn' is
  reserved for renderers; an item-level partial shows up as some rows 'ok' and some 'bad'.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import profile as profile_mod  # noqa: E402
import spec_checks  # noqa: E402  (pure functions; reused for the cache hit-rate extraction)

PERF_METRICS = (("ttft_s", "s", "lower"), ("latency_s", "s", "lower"),
                ("tpot_ms", "ms", "lower"), ("output_tps", "tok/s", "higher"))
PERCENTILES = ("p50", "p90", "p95")
CACHE_ROWS = (("A_identical_20k", "identical_20k", "cache_identical_20k"),
              ("B_multiturn_12k", "multiturn_12k", "cache_multiturn_12k"),
              ("C_short_1k", "short_1k", "cache_short_1k"))

# Same fallbacks spec_checks uses when the profile omits a target.
DEFAULT_P50_RATIO_MAX = 2.0
DEFAULT_P90_RATIO_MAX = 2.5
DEFAULT_TPS_P50_RATIO_MIN = 0.5
DEFAULT_TTFT_P90_MAX_S = 10.0
DEFAULT_CACHE_HIT_MIN_PCT = 85.0


def _dict(v):
    return v if isinstance(v, dict) else {}


def _num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _ratio_delta(measured, official):
    if measured is None or official is None or official == 0:
        return None, None
    ratio = measured / official
    return ratio, (measured - official) / official * 100.0


def _row(group, metric, pctl, unit, direction, official, measured, source,
         threshold=None, threshold_kind=None, verdict="unknown"):
    ratio, delta = _ratio_delta(measured, official)
    return {
        "group": group, "metric": metric, "pctl": pctl, "unit": unit,
        "official": official, "measured": measured, "ratio": ratio, "delta_pct": delta,
        "direction": direction, "threshold": threshold, "threshold_kind": threshold_kind,
        "verdict": verdict, "source": source,
    }


def _judge(kind, threshold, ratio, measured):
    """ok/bad against a gate; unknown when the needed number is missing."""
    if threshold is None:
        return "unknown"
    if kind == "ratio_max":
        return "unknown" if ratio is None else ("ok" if ratio <= threshold else "bad")
    if kind == "ratio_min":
        return "unknown" if ratio is None else ("ok" if ratio >= threshold else "bad")
    if kind == "abs_max":
        return "unknown" if measured is None else ("ok" if measured <= threshold else "bad")
    if kind == "pct_min":
        return "unknown" if measured is None else ("ok" if measured >= threshold else "bad")
    return "unknown"


def _target(targets, key, default):
    v = _num(targets.get(key))
    return default if v is None else v


def _item_expected(spec, key):
    """The `expected` block of the spec item with this key (spec.spec.items[]); {} when absent or malformed."""
    items = _dict(_dict(spec).get("spec")).get("items")
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict) and it.get("key") == key:
            return _dict(it.get("expected"))
    return {}


def _gate(item_exp, item_key, targets, target_key, default):
    """Threshold precedence: the spec item's own expected value (what produced the card status) > profile.targets
    > built-in default, so the badge (this module) and the card (spec item status) cannot disagree on the gate."""
    v = _num(item_exp.get(item_key))
    return _target(targets, target_key, default) if v is None else v


def _baseline_block(profile, model):
    """(percentile block, source prefix) honouring the per-model override like profile.model_baseline."""
    bl = _dict(profile.get("baseline"))
    by_model = _dict(bl.get("percentiles_by_model"))
    if model and model in by_model:
        return _dict(by_model[model]), "profile.baseline.percentiles_by_model[%s]" % model
    return _dict(bl.get("percentiles")), "profile.baseline.percentiles"


def build_rows(spec, profile):
    """Official-vs-measured rows; see module docstring. Never raises on missing or malformed inputs."""
    spec = spec if isinstance(spec, dict) else None
    profile = profile if isinstance(profile, dict) else None
    have_profile = profile is not None
    prof = profile or {}
    targets = _dict(prof.get("targets"))
    model = (spec or {}).get("model") or prof.get("id")
    if not isinstance(model, str):  # a list/dict model would raise TypeError on the dict lookup below
        model = None
    base, base_src = _baseline_block(prof, model) if have_profile else ({}, "profile.baseline.percentiles")
    load_pcts = _dict(_dict((spec or {}).get("load")).get("percentiles"))
    scenarios = _dict(_dict((spec or {}).get("cache")).get("scenarios"))

    exp_perf, exp_ttft, exp_cache = (_item_expected(spec, k) for k in ("perf_baseline", "ttft", "cache_hit"))
    p50_max = _gate(exp_perf, "p50_ratio_max", targets, "perf_p50_ratio_max", DEFAULT_P50_RATIO_MAX) if have_profile else None
    p90_max = _gate(exp_perf, "p90_ratio_max", targets, "perf_p90_ratio_max", DEFAULT_P90_RATIO_MAX) if have_profile else None
    tps_min = _num(exp_perf.get("output_tps_p50_ratio_min"))
    tps_min = DEFAULT_TPS_P50_RATIO_MIN if tps_min is None else tps_min
    ttft_max = _gate(exp_ttft, "p90_max_s", targets, "ttft_p90_max_s", DEFAULT_TTFT_P90_MAX_S) if have_profile else None
    cache_min = _gate(exp_cache, "min_pct", targets, "cache_hit_min_pct", DEFAULT_CACHE_HIT_MIN_PCT) if have_profile else None

    rows = []
    # ---- performance: ttft/latency/tpot/output_tps x p50/p90/p95
    for metric, unit, direction in PERF_METRICS:
        for p in PERCENTILES:
            official = _num(_dict(base.get(metric)).get(p))
            measured = _num(_dict(load_pcts.get(metric)).get(p))
            source = "%s.%s.%s vs spec.load.percentiles.%s.%s" % (base_src, metric, p, metric, p)
            if direction == "lower":
                kind, thr = ("ratio_max", p50_max) if p == "p50" else \
                            ("ratio_max", p90_max) if p == "p90" else (None, None)
            else:  # output_tps: only the p50 floor is gated (spec_checks: ratio < 0.5 fails)
                kind, thr = ("ratio_min", tps_min if have_profile else None) \
                    if p == "p50" else (None, None)
            ratio, _ = _ratio_delta(measured, official)
            rows.append(_row("perf", metric, p, unit, direction, official, measured, source,
                             threshold=thr, threshold_kind=kind if thr is not None else None,
                             verdict=_judge(kind, thr, ratio, measured) if have_profile else "unknown"))

    # ---- ttft: item 2 gates the measured p90 against an absolute ceiling (targets.ttft_p90_max_s)
    official = _num(_dict(base.get("ttft_s")).get("p90"))
    measured = _num(_dict(load_pcts.get("ttft_s")).get("p90"))
    rows.append(_row("ttft", "ttft_s", "p90", "s", "lower", official, measured,
                     "%s.ttft_s.p90 vs spec.load.percentiles.ttft_s.p90; threshold spec item ttft.expected.p90_max_s, else profile.targets.ttft_p90_max_s"
                     % base_src,
                     threshold=ttft_max, threshold_kind="abs_max" if ttft_max is not None else None,
                     verdict=_judge("abs_max", ttft_max, None, measured) if have_profile else "unknown"))

    # ---- cache: three core scenarios, hit rate vs targets.cache_hit_min_pct
    base_cache = _dict(_dict(prof.get("baseline")).get("cache"))
    for scen, bkey, metric in CACHE_ROWS:
        official = _num(base_cache.get(bkey))
        sc = scenarios.get(scen)
        measured = _num(spec_checks._scenario_hit(sc)) if isinstance(sc, dict) else None
        verdict = _judge("pct_min", cache_min, None, measured) if have_profile else "unknown"
        if have_profile and measured is None and isinstance(sc, dict) and isinstance(sc.get("pass"), bool):
            # a scenario that only reports a pass flag (no hit rate): spec_checks.check_cache_hit uses the flag as
            # its state, so the row follows it too (official stays displayable, measured stays None)
            verdict = "ok" if sc["pass"] else "bad"
        rows.append(_row("cache", metric, None, "%", "higher", official, measured,
                         "profile.baseline.cache.%s vs spec.cache.scenarios.%s.hit_rate_pct; "
                         "threshold spec item cache_hit.expected.min_pct, else profile.targets.cache_hit_min_pct" % (bkey, scen),
                         threshold=cache_min, threshold_kind="pct_min" if cache_min is not None else None,
                         verdict=verdict))
    return rows


def baseline_source(profile):
    """Archive date of the frozen baseline (e.g. '2026-11-09') for report footers; None when the profile has none.

    Reads profile.baseline_source.collected (dict form), falling back to a plain string value or derived_from.
    """
    if not isinstance(profile, dict):
        return None
    src = profile.get("baseline_source")
    if isinstance(src, str):
        return src.strip() or None
    if isinstance(src, dict):
        for key in ("collected", "date", "archived_at", "derived_from"):
            v = src.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def load_profile(id_or_path):
    """Thin wrapper over profile.load: built-in id or a .json path. Returns None (and warns on stderr) on failure."""
    if not id_or_path:
        return None
    try:
        return profile_mod.load(id_or_path)
    except (OSError, ValueError) as e:
        sys.stderr.write("warning: cannot load profile %r: %s\n" % (id_or_path, e))
        return None
