#!/usr/bin/env python3
"""Input normalisation for the report renderers (HTML / md): degrade, never crash.

audit / spec JSON can come from older versions, hand edits or other tools, and json.loads happily yields NaN /
Infinity / 1e999, lists where dicts are expected, None list items, ... The renderers dig with .get() chains, so one
wrong container type used to raise AttributeError / TypeError deep inside a section and kill the whole report.

normalize_audit(doc) / normalize_spec(doc) return a COPY (the caller's object is untouched) in which
  * a non-dict root becomes {} (spec: None stays None = "not provided");
  * non-finite numbers (NaN / +-inf / 1e999) are removed (dict key dropped, list slot -> None) = "missing";
  * every field the renderers treat as a container has the right type: wrong-typed dict/list fields are DROPPED so
    the section falls back to its 未测 / 未提供 wording, None / non-dict items of an item list are dropped;
  * string fields (model, status, name, ...) must be str: numbers are str()-ed, anything else dropped;
  * numeric fields (score, weight, ...) must be finite non-bool numbers, otherwise dropped.
Unknown keys and free-form leaves are passed through untouched: the renderers already stringify and escape those.
Valid documents come back structurally identical (key order kept), so output for well-formed input is unchanged.
"""
import math

_DROP = object()


def _sch_dict(**kids):
    return ("dict", kids)


def _sch_map(child):
    return ("map", child)


def _sch_list(item=None):
    return ("list", item)


_S = ("str", None)
_N = ("num", None)
_D = _sch_dict()
_L = _sch_list()
_DICTS = _sch_map(_D)

_PERCENTILE_METRICS = _sch_dict(**{k: _D for k in ("latency_s", "ttft_s", "tpot_ms", "output_tps")})

AUDIT_SCHEMA = _sch_dict(
    model=_S, profile=_S, protocol=_S, verdict=_S, score=_N, performance=_N, performance_detail=_S,
    scoring_version=_N, notes=_L, crashed_checks=_L,
    checks=_sch_list(_sch_dict(name=_S, weight=_N, score=_N, note=_S)),
)

SPEC_SCHEMA = _sch_dict(
    model=_S, profile=_S, protocol=_S, host=_S, tier=_S, tested_at=_S, notes=_L,
    spec=_sch_dict(
        version=_N, score=_N,
        items=_sch_list(_sch_dict(id=_N, key=_S, title=_S, weight=_N, status=_S,
                                  measured=_D, expected=_D, delta=_D, evidence=_L, note=_S)),
    ),
    params=_sch_dict(matrix=_sch_list(_sch_dict(case=_S, detail=_S))),
    probes=_sch_dict(
        usage=_D, structured=_D, tools=_D, timeout=_D,
        thinking=_sch_dict(tiers_ok=_L), reasoning=_sch_dict(stream_order=_L),
    ),
    load=_sch_dict(
        samples=_N, success=_N, success_rate=_N, percentiles=_PERCENTILE_METRICS, throughput=_D, totals=_D,
        finish=_D, raw=_sch_list(_sch_dict()),
    ),
    cache=_sch_dict(scenarios=_sch_map(_sch_dict(rounds=_sch_list(_sch_dict()))), ttl_5m=_D, ttl_1h=_D),
    multimodal=_DICTS, formula=_DICTS,
    hidden_prompt=_sch_dict(identity=_D, extraction_attempt=_D, verdict=_S),
)


def _finite_only(o):
    """Copy of o without NaN / inf: dict values dropped, list slots -> None."""
    if isinstance(o, dict):
        return {k: _finite_only(v) for k, v in o.items()
                if not (isinstance(v, float) and not math.isfinite(v))}
    if isinstance(o, list):
        return [None if isinstance(v, float) and not math.isfinite(v) else _finite_only(v) for v in o]
    return o


def _norm(v, sch):
    kind, arg = sch
    if kind == "dict":
        if not isinstance(v, dict):
            return _DROP
        out = {}
        for k, val in v.items():
            child = arg.get(k)
            if child is None:
                out[k] = val
                continue
            r = _norm(val, child)
            if r is not _DROP:
                out[k] = r
        return out
    if kind == "map":
        if not isinstance(v, dict):
            return _DROP
        out = {}
        for k, val in v.items():
            r = _norm(val, arg)
            if r is not _DROP:
                out[k] = r
        return out
    if kind == "list":
        if not isinstance(v, list):
            return _DROP
        if arg is None:
            return list(v)
        out = []
        for e in v:
            r = _norm(e, arg)
            if r is not _DROP:
                out.append(r)
        return out
    if kind == "str":
        if isinstance(v, str):
            return v
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return str(v)
        return _DROP
    if kind == "num":
        ok = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        return v if ok else _DROP
    return v


def _normalize(doc, schema):
    r = _norm(_finite_only(doc) if isinstance(doc, (dict, list)) else doc, schema)
    return {} if r is _DROP else r


def normalize_audit(doc):
    """model_audit JSON -> well-typed copy; anything that is not an object becomes {}."""
    return _normalize(doc, AUDIT_SCHEMA)


def normalize_spec(doc):
    """spec_audit JSON -> well-typed copy; None (not provided) stays None, any other non-object becomes {}."""
    if doc is None:
        return None
    return _normalize(doc, SPEC_SCHEMA)
