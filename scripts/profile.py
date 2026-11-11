#!/usr/bin/env python3
"""Model profile loading, validation, CLI overrides and the official-endpoint guard.

A profile is a static JSON file under profiles/ that freezes what the official endpoint looked like
when it was measured: fixed sampling params, thinking tiers, token caps, expected probe outcomes,
usage schema, baseline percentiles, pass targets and load-test parameters. Profiles contain no secrets.

Every entry point that can send traffic (model_audit.py --profile, spec_audit.py, load_test.py) must call
guard_base() before its first request. There is deliberately no override switch: the standing rule is
that the official baseline endpoint is never called again.

CLI:
  python profile.py --list
  python profile.py kimi-k3.1-pro                  # print the resolved profile as JSON
  python profile.py kimi-k3.1-pro --check-base https://relay.example/v1
  python profile.py kimi-k3.1-pro --override targets.spec_score_min=85
"""
import argparse
import copy
import json
import os
import sys
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402

PROFILE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "profiles")
REQUIRED_AUDIT_DEFAULTS = ("identity_regex", "facts", "claimed_window", "tokenizer_family")
REQUIRED_FIXED = ("temperature", "top_p", "n", "presence_penalty", "frequency_penalty")
REQUIRED_TIERS = ("low", "high", "max")
REQUIRED_METRICS = ("ttft_s", "latency_s", "tpot_ms", "output_tps")
REQUIRED_TARGETS = ("ttft_p90_max_s", "cache_hit_min_pct", "perf_p50_ratio_max",
                    "perf_p90_ratio_max", "spec_score_min")


def list_names():
    """Built-in profile ids, sorted (files under profiles/*.json minus README)."""
    if not os.path.isdir(PROFILE_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(PROFILE_DIR) if f.endswith(".json"))


def resolve_path(spec):
    """'kimi-k3.1-pro' -> profiles/kimi-k3.1-pro.json; a path or a *.json name is used as given."""
    if not spec:
        raise ValueError("profile name is required")
    s = str(spec)
    if s.endswith(".json") or os.sep in s or "/" in s or os.path.isabs(s):
        path = os.path.abspath(os.path.expanduser(s))
    else:
        path = os.path.join(PROFILE_DIR, s + ".json")
    if not os.path.isfile(path):
        raise FileNotFoundError("profile not found: %s (built-in: %s)" % (path, ", ".join(list_names()) or "none"))
    return path


def _set_path(doc, dotted, value):
    keys = dotted.split(".")
    cur = doc
    for k in keys[:-1]:
        nxt = cur.get(k)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[k] = nxt
        cur = nxt
    cur[keys[-1]] = value


def parse_overrides(items):
    """['a.b=1', 'c=x'] -> {'a': {'b': 1}, 'c': 'x'}; values are JSON when they parse, else strings."""
    out = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError("override must be KEY=VALUE, got %r" % item)
        key, raw = item.split("=", 1)
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw
        _set_path(out, key.strip(), value)
    return out


def _deep_merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = copy.deepcopy(v)
    return dst


def _err(errors, cond, msg):
    if not cond:
        errors.append(msg)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_percentile_block(errors, block, where):
    if not isinstance(block, dict):
        errors.append("%s must be an object" % where)
        return
    for metric in REQUIRED_METRICS:
        ms = block.get(metric)
        if not isinstance(ms, dict):
            errors.append("%s.%s missing or not an object" % (where, metric))
            continue
        for p in ("p50", "p90", "p95"):
            _err(errors, _is_num(ms.get(p)), "%s.%s.%s must be a number" % (where, metric, p))


def validate(p, source="<profile>"):
    """Raise ValueError listing every structural problem; return p unchanged on success."""
    errors = []
    if not isinstance(p, dict):
        raise ValueError("%s: profile must be a JSON object" % source)
    _err(errors, p.get("schema_version") == 1, "schema_version must be 1")
    for k in ("id", "vendor", "protocol"):
        _err(errors, isinstance(p.get(k), str) and p.get(k), "%s must be a non-empty string" % k)
    aliases = p.get("aliases", [])
    _err(errors, isinstance(aliases, list) and all(isinstance(x, str) and x for x in aliases),
         "aliases must be a list of non-empty strings")
    hosts = p.get("official_hosts")
    _err(errors, isinstance(hosts, list) and hosts and all(isinstance(x, str) and x for x in hosts),
         "official_hosts must be a non-empty list of host names")

    ad = p.get("audit_defaults")
    if not isinstance(ad, dict):
        errors.append("audit_defaults missing or not an object")
    else:
        for k in REQUIRED_AUDIT_DEFAULTS:
            _err(errors, k in ad, "audit_defaults.%s missing" % k)
        _err(errors, isinstance(ad.get("facts"), list), "audit_defaults.facts must be a list")

    params = p.get("params")
    if not isinstance(params, dict):
        errors.append("params missing or not an object")
    else:
        fixed = params.get("fixed")
        if not isinstance(fixed, dict):
            errors.append("params.fixed missing or not an object")
        else:
            for k in REQUIRED_FIXED:
                _err(errors, _is_num(fixed.get(k)), "params.fixed.%s must be a number" % k)
        th = params.get("thinking")
        if not isinstance(th, dict):
            errors.append("params.thinking missing or not an object")
        else:
            tiers = th.get("tiers")
            _err(errors, isinstance(tiers, list) and all(t in tiers for t in REQUIRED_TIERS),
                 "params.thinking.tiers must include low, high, max")
            _err(errors, th.get("default") in (tiers or []), "params.thinking.default must be one of the tiers")
        mct = params.get("max_completion_tokens")
        if not isinstance(mct, dict):
            errors.append("params.max_completion_tokens missing or not an object")
        else:
            _err(errors, _is_num(mct.get("default")) and mct["default"] > 0,
                 "params.max_completion_tokens.default must be a positive number")
            _err(errors, _is_num(mct.get("max")) and mct["max"] >= (mct.get("default") or 0),
                 "params.max_completion_tokens.max must be >= default")
        try:
            _err(errors, common.parse_duration(params.get("timeout_s")) > 0,
                 "params.timeout_s must parse to a positive duration")
        except (TypeError, ValueError):
            errors.append("params.timeout_s missing or unparseable (e.g. 3600s)")

    probes = p.get("expected_probes")
    if not isinstance(probes, dict) or not probes:
        errors.append("expected_probes must be a non-empty object")
    else:
        for name, exp in probes.items():
            if not isinstance(exp, dict) or not ("status" in exp or "ok" in exp):
                errors.append("expected_probes.%s must be an object with status or ok" % name)

    usage = p.get("usage_schema")
    if not isinstance(usage, dict):
        errors.append("usage_schema missing or not an object")
    else:
        _err(errors, isinstance(usage.get("required"), list) and usage["required"],
             "usage_schema.required must be a non-empty list")
        _err(errors, isinstance(usage.get("headers"), list), "usage_schema.headers must be a list")

    bl = p.get("baseline")
    if not isinstance(bl, dict):
        errors.append("baseline missing or not an object")
    else:
        _check_percentile_block(errors, bl.get("percentiles"), "baseline.percentiles")
        by_model = bl.get("percentiles_by_model") or {}
        _err(errors, isinstance(by_model, dict), "baseline.percentiles_by_model must be an object")
        if isinstance(by_model, dict):
            for model, block in by_model.items():
                _check_percentile_block(errors, block, "baseline.percentiles_by_model.%s" % model)
        cache = bl.get("cache")
        _err(errors, isinstance(cache, dict) and cache and all(_is_num(v) for v in cache.values()),
             "baseline.cache must be a non-empty object of numbers")

    targets = p.get("targets")
    if not isinstance(targets, dict):
        errors.append("targets missing or not an object")
    else:
        for k in REQUIRED_TARGETS:
            _err(errors, _is_num(targets.get(k)), "targets.%s must be a number" % k)

    lt = p.get("load_test")
    if not isinstance(lt, dict):
        errors.append("load_test missing or not an object")
    else:
        for k in ("requests", "concurrency", "prompt_chars", "max_completion_tokens"):
            _err(errors, _is_num(lt.get(k)) and lt[k] > 0, "load_test.%s must be a positive number" % k)
        _err(errors, _is_num(lt.get("cache_repeats_20k")) and lt["cache_repeats_20k"] >= 2,
             "load_test.cache_repeats_20k must be >= 2")

    if errors:
        raise ValueError("%s: invalid profile:\n  - %s" % (source, "\n  - ".join(errors)))
    return p


def load(spec, overrides=None):
    """Load, deep-merge CLI overrides, validate, return the profile dict."""
    path = resolve_path(spec)
    with open(path, "r", encoding="utf-8") as f:
        try:
            prof = json.load(f)
        except ValueError as e:
            raise ValueError("%s: not valid JSON (%s)" % (path, e))
    if overrides:
        _deep_merge(prof, parse_overrides(overrides))
    return validate(prof, source=path)


def _host(base):
    """Lower-cased hostname of an absolute http(s) URL, or None."""
    try:
        parts = urlsplit(str(base))
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return parts.hostname.lower().rstrip(".")


def guard_base(prof, base):
    """Refuse (SystemExit) to run against a host the profile marks as an official baseline endpoint.

    Returns the normalised host on success. No override switch exists by design.
    """
    host = _host(base)
    if host is None:
        raise ValueError("--base must be an absolute http(s) URL, got %r" % base)
    for official in prof.get("official_hosts", []):
        h = str(official).lower().rstrip(".")
        if host == h or host.endswith("." + h):
            sys.stderr.write("profile %s declares %s as an official baseline host\n" % (prof.get("id"), h))
            sys.exit("refusing to call the official baseline endpoint; use a channel URL")
    return host


def apply_timeout(prof):
    """Push params.timeout_s into common's module default; returns the effective seconds."""
    secs = common.parse_duration((prof.get("params") or {}).get("timeout_s", common.DEFAULT_TIMEOUT))
    return common.set_default_timeout(secs)


def model_baseline(prof, model):
    """Frozen percentile block for a model id: per-model override first, then the profile's primary block."""
    bl = prof.get("baseline") or {}
    by_model = bl.get("percentiles_by_model") or {}
    if model and model in by_model:
        return by_model[model]
    if model and model == prof.get("id"):
        return bl.get("percentiles") or {}
    # unknown alias: fall back to the primary block so item 1 still has a reference, not a crash
    return bl.get("percentiles") or {}


def short_samples(prof, model):
    """Recorded baseline samples for a model, as [{ttft,total,ct,fin,tps}] (empty list when absent).

    Accepts either {"records": [...]} or the compact parallel-array form used by the built-in profile:
    {"ttft": [...], "total": [...], "tps": [...], "ct": 120 | [...], "fin": "length" | [...]}.
    """
    sl = (prof.get("baseline") or {}).get("short_latency_samples") or {}
    entry = sl.get(model) or sl.get(prof.get("id")) or {}
    if entry.get("records"):
        return entry["records"]
    ttft, total, tps = entry.get("ttft") or [], entry.get("total") or [], entry.get("tps") or []
    ct, fin = entry.get("ct"), entry.get("fin")
    out = []
    for i in range(max(len(ttft), len(total), len(tps))):
        pick = lambda seq: (seq[i] if isinstance(seq, list) and i < len(seq) else seq)  # noqa: E731
        out.append({"ttft": pick(ttft), "total": pick(total), "tps": pick(tps),
                    "ct": pick(ct), "fin": pick(fin)})
    return out


def main():
    ap = argparse.ArgumentParser(description="Load and inspect a model profile.")
    ap.add_argument("name", nargs="?", help="profile id (profiles/<name>.json) or a path to a JSON file")
    ap.add_argument("--list", action="store_true", help="list built-in profiles")
    ap.add_argument("--check-base", default="", help="run the official-endpoint guard against this base URL")
    ap.add_argument("--override", action="append", default=[], metavar="KEY=VALUE",
                    help="override a profile field (dotted path, JSON value); repeatable")
    a = ap.parse_args()
    if a.list:
        for name in list_names():
            print(name)
        return
    if not a.name:
        ap.error("profile name is required (or use --list)")
    try:
        prof = load(a.name, a.override)
    except (OSError, ValueError) as e:
        sys.exit("error: %s" % e)
    if a.check_base:
        try:
            host = guard_base(prof, a.check_base)
        except ValueError as e:
            sys.exit("error: %s" % e)
        print("ok: %s is not an official baseline host for profile %s" % (host, prof.get("id")))
        return
    print(json.dumps(prof, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
