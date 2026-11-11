#!/usr/bin/env python3
"""Frozen 15-item conformance scorer for spec_audit.py output (pure functions, no I/O).

The single contract is profiles/README.md. Each check_<key>(doc, profile) receives the spec_audit
document WITHOUT its "spec" key and returns one item dict. Checks only read their inputs and never
mutate doc or profile.

Status values are pass / partial / fail / skip. skip means "not measured" and is excluded from the
denominator; a run whose measured weight is below 60% of 100 gets score=None instead of a number.
score = round(100 * sum(weight * value) / sum(weight of non-skip items)), pass=1, partial=0.5, fail=0.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402
import perf_stats  # noqa: E402
import profile as profile_mod  # noqa: E402

# (id, key, title, weight); weights are frozen by profiles/README.md and add up to 100.
ITEM_SPECS = [
    (1, "perf_baseline", "符合官方基准 p50/p90/p95", 10),
    (2, "ttft", "TTFT 小于 10s", 6),
    (3, "cache_hit", "缓存命中 ≥ 85%", 8),
    (4, "usage_schema", "usage 计费字段和官网一样", 8),
    (5, "thinking_params", "思考档位与固定参数", 10),
    (6, "finish_reason", "finish_reason 正常", 6),
    (7, "timeout", "超时 3600s", 3),
    (8, "video", "视频 base64 / 公网 URL（100MB）", 4),
    (9, "param_matrix", "参数尽量和官网保持一致", 8),
    (10, "reasoning_content", "reasoning_content 与官方一致", 6),
    (11, "formula_tools", "官方工具通过 Formula 接入", 5),
    (12, "cache_ttl", "支持 5m / 1h 缓存与缓存写入", 8),
    (13, "tools_choice", "支持自定义工具与 tool_choice", 6),
    (14, "structured_output", "标准结构化输出 reasoning_content", 5),
    (15, "hidden_prompt", "隐藏提示词注入与身份", 7),
]

_SPEC_BY_KEY = {key: (ident, key, title, weight) for ident, key, title, weight in ITEM_SPECS}
_STATUS_VALUE = {"pass": 1.0, "partial": 0.5, "fail": 0.0}
MIN_COVERAGE_PCT = 60.0

# The five fixed sampling knobs whose non-official values must be rejected (HTTP 400).
KEY_MATRIX_CASES = ("P:temp_0.6", "P:top_p_0.8", "P:n_2", "P:presence_0.5",
                    "P:frequency_0.5", "P:mct_131072", "P:mct_1048576")
UNTESTED_MARKERS = ("", "未测", "n/a", "na", "skip", "skipped", "not tested", "unknown")


# --------------------------------------------------------------------------- small helpers
def _dict(value):
    return value if isinstance(value, dict) else {}


def _num(value):
    """float(value) for real numbers; bool is deliberately not a number here."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _ratio(measured, baseline):
    m, b = _num(measured), _num(baseline)
    if m is None or b is None or b == 0:
        return None
    return m / b


def _r3(value):
    return None if value is None else round(value, 3)


def _fmt(value, digits=3):
    n = _num(value)
    if n is None:
        return "—"
    text = ("%.*f" % (digits, n)).rstrip("0").rstrip(".")
    return text or "0"


def _is_untested(value):
    """True for None / 未测 / n/a / skipped and dicts that carry no tested result."""
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, str):
        return value.strip().lower() in UNTESTED_MARKERS
    if isinstance(value, dict):
        if value.get("tested") is False:
            return True
        if "tested" in value and value.get("tested") is True:
            return False
        if "status" in value:
            return _is_untested(value.get("status"))
        if "result" in value:
            return _is_untested(value.get("result"))
        return True
    return False


def _fraction(value):
    """'2/6' -> 0.333, 0.35 -> 0.35, None -> None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*", value)
        if m and float(m.group(2)):
            return float(m.group(1)) / float(m.group(2))
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _first_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        m = re.search(r"-?\d+(?:\.\d+)?", value)
        if m:
            return float(m.group(0))
    return None


def _flag(value):
    return value if isinstance(value, bool) else None


def _item(key, status, measured=None, expected=None, delta=None, evidence=None, note=""):
    ident, key, title, weight = _SPEC_BY_KEY[key]
    if status not in _STATUS_VALUE:
        status = "skip"
    return {
        "id": ident,
        "key": key,
        "title": title,
        "weight": weight,
        "status": status,
        "measured": measured if measured is not None else {},
        "expected": expected if expected is not None else {},
        "delta": delta if delta is not None else {},
        "evidence": [str(x) for x in (evidence or [])],
        "note": note,
    }


def _skip(key, why, measured=None, expected=None, evidence=None):
    return _item(key, "skip", measured=measured, expected=expected,
                 evidence=(evidence or [why]), note=why)


def _targets(profile):
    return _dict(profile.get("targets"))


def _baseline(profile, doc):
    model = doc.get("model") or profile.get("id")
    try:
        return _dict(profile_mod.model_baseline(profile, model))
    except Exception:
        return {}


def _load_percentiles(doc):
    return _dict(_dict(doc.get("load")).get("percentiles"))


# --------------------------------------------------------------------------- item 1: performance
def check_perf_baseline(doc, profile):
    pcts = _load_percentiles(doc)
    base = _baseline(profile, doc)
    targets = _targets(profile)
    p50_max = _num(targets.get("perf_p50_ratio_max"))
    p90_max = _num(targets.get("perf_p90_ratio_max"))
    p50_max = 2.0 if p50_max is None else p50_max
    p90_max = 2.5 if p90_max is None else p90_max
    expected = {
        "p50_ratio_max": p50_max,
        "p90_ratio_max": p90_max,
        "output_tps_p50_ratio_min": 0.5,
    }

    measured = {}
    ratios_p50, ratios_p90 = [], []
    failures = []
    for metric, short in (("ttft_s", "ttft"), ("latency_s", "latency"), ("tpot_ms", "tpot")):
        mblock, bblock = _dict(pcts.get(metric)), _dict(base.get(metric))
        for p in ("p50", "p90"):
            ratio = _ratio(mblock.get(p), bblock.get(p))
            measured["%s_%s_ratio" % (short, p)] = _r3(ratio)
            if ratio is None:
                continue
            (ratios_p50 if p == "p50" else ratios_p90).append(ratio)
            limit = p50_max if p == "p50" else p90_max
            if ratio > limit:
                failures.append("%s.%s=%.3f×>%.2f×" % (metric, p, ratio, limit))

    speed_ratio = _ratio(_dict(pcts.get("output_tps")).get("p50"), _dict(base.get("output_tps")).get("p50"))
    measured["output_tps_p50_ratio"] = _r3(speed_ratio)
    if speed_ratio is not None and speed_ratio < 0.5:
        failures.append("output_tps.p50=%.3f×<0.5×" % speed_ratio)

    n_checks = len(ratios_p50) + len(ratios_p90) + (1 if speed_ratio is not None else 0)
    evidence = [
        "load.percentiles.ttft_s p50=%s p90=%s" % (_fmt(_dict(pcts.get("ttft_s")).get("p50")),
                                                   _fmt(_dict(pcts.get("ttft_s")).get("p90"))),
        "画像 baseline.percentiles.ttft_s p50=%s p90=%s" % (_fmt(_dict(base.get("ttft_s")).get("p50")),
                                                           _fmt(_dict(base.get("ttft_s")).get("p90"))),
    ]
    if n_checks == 0:
        return _skip("perf_baseline", "负载测试未提供可用分位数，本项不计分。", measured=measured,
                     expected=expected, evidence=["load.percentiles 缺失或 n=0"])

    delta = {
        "p50_margin": _r3(p50_max - max(ratios_p50)) if ratios_p50 else None,
        "p90_margin": _r3(p90_max - max(ratios_p90)) if ratios_p90 else None,
    }
    if not failures:
        status = "pass"
        note = "四个分位数指标均落在官方基准的 p50≤2×、p90≤2.5× 范围内。"
    elif len(failures) >= max(1, (n_checks + 1) // 2):
        status = "fail"
        note = "超过一半的性能指标超出官方基准容差。"
    else:
        status = "partial"
        note = "部分性能指标超出官方基准容差。"
    evidence.append("输出速度 p50 比值=%s（下限 0.5×）" % _fmt(speed_ratio))
    evidence.extend(failures)
    return _item("perf_baseline", status, measured=measured, expected=expected, delta=delta,
                 evidence=evidence, note=note)


# --------------------------------------------------------------------------- item 2: TTFT
def check_ttft(doc, profile):
    block = _dict(_load_percentiles(doc).get("ttft_s"))
    p90 = _num(block.get("p90"))
    if p90 is None:
        return _skip("ttft", "未测得 TTFT 分位数，本项不计分。", evidence=["load.percentiles.ttft_s 缺失"])
    limit = _num(_targets(profile).get("ttft_p90_max_s"))
    limit = 10.0 if limit is None else limit
    measured = {"p90_s": _r3(p90), "p95_s": _r3(_num(block.get("p95")))}
    expected = {"p90_max_s": limit}
    delta = {"margin_s": _r3(limit - p90)}
    n = _num(block.get("n")) or _num(_dict(doc.get("load")).get("samples"))
    evidence = ["%s 次流式请求首 delta 统计" % (int(n) if n else "若干")]
    if p90 <= limit:
        note = "P90 首字 %.1fs，低于 %.0fs 目标；P95 仅展示不设阈值。" % (p90, limit)
        return _item("ttft", "pass", measured=measured, expected=expected, delta=delta,
                     evidence=evidence, note=note)
    return _item("ttft", "fail", measured=measured, expected=expected, delta=delta,
                 evidence=evidence, note="P90 首字 %.1fs，超过 %.0fs 目标。" % (p90, limit))


# --------------------------------------------------------------------------- item 3: cache hit
def _scenario_hit(scenario):
    hit = _num(scenario.get("hit_rate_pct"))
    if hit is not None:
        return hit
    rounds = scenario.get("rounds")
    if isinstance(rounds, list):
        values = [_num(r.get("hit_rate_pct")) for r in rounds if isinstance(r, dict)]
        values = [v for v in values if v is not None]
        if values:
            return values[-1]
    cached, prompt = _num(scenario.get("cached_tokens")), _num(scenario.get("prompt_tokens"))
    if cached is not None and prompt:
        return 100.0 * cached / prompt
    return None


def check_cache_hit(doc, profile):
    scenarios = _dict(_dict(doc.get("cache")).get("scenarios"))
    min_pct = _num(_targets(profile).get("cache_hit_min_pct"))
    min_pct = 85.0 if min_pct is None else min_pct
    core = (
        ("A_identical_20k", "identical_20k_pct"),
        ("B_multiturn_12k", "multiturn_12k_pct"),
        ("C_short_1k", "short_1k_pct"),
    )
    measured, evidence, states, hits = {}, [], [], []
    for name, key in core:
        scenario = scenarios.get(name)
        if not isinstance(scenario, dict):
            continue
        hit = _scenario_hit(scenario)
        state = None
        if hit is not None:
            state = hit >= min_pct
            hits.append(hit)
            measured[key] = _r3(hit)
            evidence.append("%s 命中 %s%%（目标 ≥%s%%）" % (name, _fmt(hit), _fmt(min_pct)))
        elif isinstance(scenario.get("pass"), bool):
            state = scenario["pass"]
            measured[key] = None
            evidence.append("%s pass=%s（无命中率数值）" % (name, scenario["pass"]))
        if state is not None:
            states.append(state)
    expected = {"min_pct": min_pct}
    if not states:
        return _skip("cache_hit", "缓存场景未测得命中率，本项不计分。", measured=measured,
                     expected=expected, evidence=["cache.scenarios 缺失"])
    if all(states) and len(states) >= len(core):
        status, note = "pass", "三类缓存场景均高于 %s%% 目标。" % _fmt(min_pct)
    elif any(states):
        status, note = "partial", "部分缓存场景缺失或未达标。"
    else:
        status, note = "fail", "三类缓存场景均未达到 %s%% 目标。" % _fmt(min_pct)
    delta = {"margin_pct": _r3(min(hits) - min_pct)} if hits else {}
    return _item("cache_hit", status, measured=measured, expected=expected, delta=delta,
                 evidence=evidence, note=note)


# --------------------------------------------------------------------------- item 4: usage schema
def check_usage_schema(doc, profile):
    usage = doc.get("probes", {}).get("usage") if isinstance(doc.get("probes"), dict) else None
    if not isinstance(usage, dict):
        return _skip("usage_schema", "未采集 usage 探测，本项不计分。",
                     evidence=["probes.usage 缺失"])
    schema = _dict(profile.get("usage_schema"))
    required_present = _flag(usage.get("required_present"))
    total_matches = _flag(usage.get("total_matches"))
    details = _flag(usage.get("details_present"))
    if details is None:
        details = _flag(usage.get("detail_keys_present"))
    cache_write = _flag(usage.get("cache_write_present"))
    if cache_write is None:
        cache_write = _flag(usage.get("cache_write_tokens_present"))
    ratio = _num(usage.get("token_ratio"))
    rng = _dict(schema.get("token_ratio_range"))
    lo, hi = _num(rng.get("min")), _num(rng.get("max"))
    lo = 0.15 if lo is None else lo
    hi = 0.6 if hi is None else hi
    ratio_ok = ratio is not None and lo <= ratio <= hi
    headers_required = bool(schema.get("headers"))

    measured = {
        "required_present": required_present,
        "total_matches": total_matches,
        "detail_keys_present": details,
        "cache_write_tokens_present": cache_write,
        "token_ratio": ratio,
    }
    expected = {
        "required": schema.get("required") or [],
        "details": schema.get("details") or {},
        "token_ratio_range": [lo, hi],
    }
    delta = {"missing_keys": usage.get("missing_keys") or []}
    evidence = []
    if required_present:
        evidence.append("usage 三段式字段齐备")
    if total_matches:
        evidence.append("total_tokens 与 prompt+completion 一致")
    if details:
        evidence.append("prompt/completion 明细子键齐备")
    if cache_write:
        evidence.append("缓存写入响应头存在")
    if ratio is not None:
        evidence.append("token_ratio=%s，合理区间 %s–%s" % (_fmt(ratio), _fmt(lo), _fmt(hi)))

    if required_present is False or total_matches is False:
        status, note = "fail", "usage 核心计费字段缺失或 total_tokens 不自洽。"
    elif required_present is True and total_matches is True and details is True \
            and (cache_write is True or not headers_required) and ratio_ok:
        status, note = "pass", "计费字段结构与官方一致，token/char 落在合理区间。"
    else:
        status, note = "partial", "usage 基本可用，但明细字段或 token_ratio 未能完全验证。"
    return _item("usage_schema", status, measured=measured, expected=expected, delta=delta,
                 evidence=evidence, note=note)


# --------------------------------------------------------------------------- item 5: thinking/params
def check_thinking_params(doc, profile):
    thinking = doc.get("probes", {}).get("thinking") if isinstance(doc.get("probes"), dict) else None
    if not isinstance(thinking, dict):
        return _skip("thinking_params", "未采集思考档位探测，本项不计分。",
                     evidence=["probes.thinking 缺失"])
    cfg = _dict(_dict(profile.get("params")).get("thinking"))
    required_tiers = [str(t) for t in (cfg.get("tiers") or [])]
    actual_tiers = thinking.get("tiers_ok")
    tiers_all = isinstance(actual_tiers, list) and all(t in actual_tiers for t in required_tiers)
    fixed_ok = _flag(thinking.get("fixed_ok"))
    mct_ok = _flag(thinking.get("mct_ok"))
    default_reasoning = _flag(thinking.get("default_reasoning"))
    echo_ok = _flag(thinking.get("assistant_echo_ok"))
    disabled_ct = _num(thinking.get("disabled_completion_tokens"))
    disabled_limit = _num(cfg.get("disabled_completion_tokens_max"))
    disabled_ok = None
    if disabled_ct is not None and disabled_limit is not None:
        disabled_ok = disabled_ct <= disabled_limit
    default_expected = bool(cfg.get("always_on", True))
    default_ok = None if default_reasoning is None else (default_reasoning == default_expected)

    core_bad = (isinstance(actual_tiers, list) and not tiers_all) or fixed_ok is False or mct_ok is False
    core_ok = tiers_all and fixed_ok is True and mct_ok is True
    # Every auxiliary behavior the profile specifies must be actually verified:
    # one passing aux check must not stand in for the others.
    aux_complete = (default_ok is True and echo_ok is True
                    and (disabled_limit is None or disabled_ok is True))

    mct_cfg = _dict(_dict(profile.get("params")).get("max_completion_tokens"))
    measured = {
        "tiers_ok": actual_tiers if isinstance(actual_tiers, list) else [],
        "default_tier": cfg.get("default"),
        "default_has_reasoning": default_reasoning,
        "disabled_completion_tokens": int(disabled_ct) if disabled_ct is not None else None,
        "disabled_within_limit": disabled_ok,
        "fixed_params_ok": fixed_ok,
        "mct_ok": mct_ok,
        "assistant_echo_ok": echo_ok,
    }
    expected = {
        "tiers": required_tiers,
        "default": cfg.get("default"),
        "disabled_completion_tokens_max": disabled_limit,
        "max_completion_tokens": [mct_cfg.get("default"), mct_cfg.get("max")],
        "fixed": _dict(_dict(profile.get("params")).get("fixed")),
    }
    evidence = []
    if tiers_all:
        evidence.append("%s 均返回 200" % "/".join(required_tiers))
    if default_reasoning is True:
        evidence.append("默认请求带 reasoning_content")
    if disabled_ct is not None:
        evidence.append("thinking.disabled 时 completion_tokens=%d" % int(disabled_ct))
    if echo_ok is True:
        evidence.append("多轮完整回传 assistant message 后上下文保留")
    if fixed_ok is True:
        evidence.append("五个固定采样参数均被接受")
    if mct_ok is True:
        evidence.append("max_completion_tokens 默认值与上限均被接受")

    if core_bad:
        status, note = "fail", "档位、固定参数或 token 上限与官方行为不一致。"
    elif core_ok and aux_complete:
        status, note = "pass", "档位、固定参数、token 上限与完整回传均符合官方行为。"
    else:
        status, note = "partial", "核心档位可用，但部分辅助行为未测或未通过。"
    return _item("thinking_params", status, measured=measured, expected=expected, evidence=evidence, note=note)


# --------------------------------------------------------------------------- item 6: finish_reason
def check_finish_reason(doc, profile):
    load = _dict(doc.get("load"))
    finish = load.get("finish")
    if not isinstance(finish, dict):
        return _skip("finish_reason", "未采集 finish_reason 直方图，本项不计分。",
                     evidence=["load.finish 缺失"])
    keys = ("stop", "length", "tool_calls", "empty", "other")
    counts = {}
    for key in keys:
        n = _num(finish.get(key))
        counts[key] = int(n) if n is not None else 0
    total = sum(counts.values())
    if total == 0:
        return _skip("finish_reason", "finish_reason 直方图为空，本项不计分。",
                     measured=counts, evidence=["load.finish 全为 0"])
    raw = load.get("raw") if isinstance(load.get("raw"), list) else []
    raw_empty = [r for r in raw if isinstance(r, dict) and not r.get("finish")]
    samples = _num(load.get("samples"))
    measured = counts
    expected = {"allowed": ["stop", "length", "tool_calls"], "empty_is_failure": True}
    histogram = " ".join("%s=%d" % (key, counts[key]) for key in keys if counts[key])
    evidence = ["%d 次请求 finish_reason 分布：%s" % (total, histogram)]
    if raw:
        evidence.append("load.raw 尾部样本无空 finish" if not raw_empty else "load.raw 尾部存在空 finish")
    if counts["empty"] or counts["other"] or raw_empty:
        status, note = "fail", "存在空串或非标准 finish_reason。"
    elif samples is not None and total < samples and not raw:
        status, note = "partial", "finish_reason 覆盖数少于请求数，且无原始样本可复核。"
    else:
        status, note = "pass", "无空串或缺失的 finish_reason。"
    return _item("finish_reason", status, measured=measured, expected=expected,
                 evidence=evidence, note=note)


# --------------------------------------------------------------------------- item 7: timeout
def _survival_ok(value, min_s):
    if isinstance(value, bool):
        return value
    n = _num(value)
    if n is not None:
        return n >= min_s
    if isinstance(value, str):
        low = value.lower()
        if any(token in low for token in ("surviv", "存活", "通过", "ok")):
            return True
    return False


def check_timeout(doc, profile):
    probe = doc.get("probes", {}).get("timeout") if isinstance(doc.get("probes"), dict) else None
    if not isinstance(probe, dict):
        return _skip("timeout", "未采集超时探测，本项不计分。", evidence=["probes.timeout 缺失"])
    configured = _num(probe.get("configured_s"))
    expected_s = None
    try:
        expected_s = float(common.parse_duration(_dict(profile.get("params")).get("timeout_s")))
    except (TypeError, ValueError):
        expected_s = 3600.0
    survival = probe.get("long_generation_survival")
    measured = {
        "configured_s": int(configured) if configured is not None else None,
        "long_generation_survival": "未测" if _is_untested(survival) else survival,
    }
    expected = {"configured_s": int(expected_s), "survive_min_s": 600}
    evidence = []
    if configured is not None:
        evidence.append("客户端默认超时已按画像设为 %ds" % int(configured))
    if _is_untested(survival):
        evidence.append("长生成存活探测：未测")

    if configured is None:
        status, note = "partial", "超时配置未记录；长生成存活未测，不能给满分。"
    elif configured != expected_s:
        status, note = "fail", "客户端超时配置与画像不一致（期望 %ds）。" % int(expected_s)
    elif _is_untested(survival):
        status, note = "partial", "客户端配置已满足；长连接实际存活未测，因此不给满分。"
    elif _survival_ok(survival, 600):
        status, note = "pass", "客户端超时配置与长生成存活均符合要求。"
    else:
        status, note = "fail", "长生成未能在 600s 内保持连接。"
    return _item("timeout", status, measured=measured, expected=expected,
                 evidence=evidence, note=note)


# --------------------------------------------------------------------------- item 8: video
def _entry_ok(entry):
    if not isinstance(entry, dict):
        return False
    if entry.get("ok") is True:
        return True
    return _num(entry.get("status")) == 200


def check_video(doc, profile):
    mm = doc.get("multimodal")
    if not isinstance(mm, dict):
        return _skip("video", "未采集多模态探测，本项不计分。", evidence=["multimodal 缺失"])
    image = _entry_ok(mm.get("image_b64"))
    video_b64 = _entry_ok(mm.get("video_b64_1mb"))
    video_url = _entry_ok(mm.get("video_url"))
    hundred = mm.get("video_100mb")
    h_untested = _is_untested(hundred) if not isinstance(hundred, dict) else _is_untested(hundred.get("status"))
    h_ok = _entry_ok(hundred)
    core_ok = image and video_b64 and video_url
    measured = {
        "image_b64_ok": image,
        "video_b64_1mb_ok": video_b64,
        "video_url_ok": video_url,
        "video_100mb": "未测" if h_untested else (hundred.get("status") if isinstance(hundred, dict) else hundred),
    }
    expected = {"video_100mb": "base64 与公网 URL 均可"}
    evidence = []
    for name, entry in (("image_b64", mm.get("image_b64")), ("video_b64_1mb", mm.get("video_b64_1mb")),
                        ("video_url", mm.get("video_url"))):
        if isinstance(entry, dict):
            evidence.append("%s: %s" % (name, entry.get("detail") or ("HTTP %s" % entry.get("status"))))
    evidence.append("video_100mb: 未测" if h_untested else "video_100mb: %s" % measured["video_100mb"])

    if core_ok and h_ok and not h_untested:
        status, note = "pass", "1MB base64 视频、公网 URL 与 100MB 路径均通过。"
    elif core_ok:
        status, note = "partial", "100MB 路径未实测，按规则最高只能判 partial。"
    elif video_b64 or video_url or image:
        status, note = "partial", "部分多模态路径可用，但小样本未全部通过。"
    else:
        status, note = "fail", "图片/视频小样本均未通过。"
    return _item("video", status, measured=measured, expected=expected,
                 evidence=evidence, note=note)


# --------------------------------------------------------------------------- item 9: param matrix
def _case_match(case):
    match = case.get("match")
    if isinstance(match, bool):
        return match
    exp, act = case.get("expected_status"), case.get("actual_status")
    if exp is not None and act is not None:
        return exp == act
    return False


def check_param_matrix(doc, profile):
    matrix = _dict(doc.get("params")).get("matrix")
    if not isinstance(matrix, list) or not matrix:
        return _skip("param_matrix", "未采集参数矩阵，本项不计分。", evidence=["params.matrix 缺失"])
    rows = [c for c in matrix if isinstance(c, dict)]
    total = len(rows)
    matched = sum(1 for c in rows if _case_match(c))
    pct = round(100.0 * matched / total, 1) if total else 0.0
    by_case = {str(c.get("case")): _case_match(c) for c in rows}
    missing_keys = [k for k in KEY_MATRIX_CASES if k not in by_case]
    key_cases_ok = not missing_keys and all(by_case.get(k) is True for k in KEY_MATRIX_CASES)
    mismatches = [str(c.get("case")) for c in rows if not _case_match(c)]

    measured = {"cases": total, "matched": matched, "match_pct": pct, "key_cases_ok": key_cases_ok}
    expected = {"match_pct_min": 95, "key_cases_required": True}
    evidence = []
    if matched == total:
        evidence.append("%d 例参数矩阵与画像 expected_probes 全部一致" % total)
    else:
        evidence.append("%d/%d 例一致；不一致：%s" % (matched, total, ", ".join(mismatches[:8])))
    if missing_keys:
        evidence.append("缺少关键用例：%s" % ", ".join(missing_keys))

    if pct >= 95 and key_cases_ok:
        status, note = "pass", "固定参数非法值正确 400，合法值与 token 上限正确 200。"
    elif pct < 80 or any(by_case.get(k) is False for k in KEY_MATRIX_CASES):
        status, note = "fail", "参数矩阵一致率过低或关键参数用例不匹配。"
    else:
        status, note = "partial", "参数矩阵大体一致，但未达到 95% 或缺少关键用例。"
    return _item("param_matrix", status, measured=measured, expected=expected,
                 evidence=evidence, note=note)


# --------------------------------------------------------------------------- item 10: reasoning_content
def check_reasoning_content(doc, profile):
    probe = doc.get("probes", {}).get("reasoning") if isinstance(doc.get("probes"), dict) else None
    if not isinstance(probe, dict):
        return _skip("reasoning_content", "未采集 reasoning_content 探测，本项不计分。",
                     evidence=["probes.reasoning 缺失"])
    present = _flag(probe.get("present"))
    is_string = _flag(probe.get("is_string"))
    independent = _flag(probe.get("independent"))
    order = probe.get("stream_order")
    order_ok = isinstance(order, list) and len(order) >= 2 and order[0] == "reasoning" and order[1] == "content"
    tokens = _num(probe.get("reasoning_tokens"))
    measured = {
        "field_present": present,
        "string_type": is_string,
        "independent_from_content": independent,
        "stream_order": order if isinstance(order, list) else [],
        "reasoning_tokens": int(tokens) if tokens is not None else None,
    }
    expected = {"field": "reasoning_content", "type": "string",
                "stream_order": ["reasoning", "content"], "reasoning_tokens_positive": True}
    evidence = []
    if present is True:
        evidence.append("响应包含 reasoning_content")
    if is_string is True:
        evidence.append("reasoning_content 为字符串")
    if independent is True:
        evidence.append("reasoning_content 与 content 独立")
    if order:
        evidence.append("流式顺序：%s" % " → ".join(str(x) for x in order))
    if tokens is not None:
        evidence.append("completion_tokens_details.reasoning_tokens=%d" % int(tokens))

    if present is False or is_string is False:
        status, note = "fail", "reasoning_content 缺失或类型不符合官方。"
    elif present is True and is_string is True and independent is True and order_ok and tokens is not None and tokens > 0:
        status, note = "pass", "推理通道结构与官方一致，计费上也有对应记账。"
    else:
        status, note = "partial", "reasoning_content 存在，但独立性、流式顺序或计费记账未完全验证。"
    return _item("reasoning_content", status, measured=measured, expected=expected,
                 evidence=evidence, note=note)


# --------------------------------------------------------------------------- item 11: formula tools
def _formula_ok(entry):
    if not isinstance(entry, dict):
        return None
    if entry.get("ok") is True:
        return True
    if _num(entry.get("status")) == 200:
        return True
    if "ok" in entry or entry.get("status") is not None:
        return False
    return None


def _first_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        m = re.search(r"\d+", value)
        if m:
            return int(m.group(0))
    return None


def check_formula_tools(doc, profile):
    formula = doc.get("formula")
    if not isinstance(formula, dict):
        return _skip("formula_tools", "未采集 Formula 探测，本项不计分。", evidence=["formula 缺失"])
    tools_entry = formula.get("tools")
    fibers_entry = formula.get("fibers")
    tools_ok = _formula_ok(tools_entry)
    fibers_ok = _formula_ok(fibers_entry)
    if tools_ok is None and fibers_ok is None:
        return _skip("formula_tools", "Formula tools/fibers 均未测得，本项不计分。",
                     evidence=["formula.tools 与 formula.fibers 缺失"])
    no_v1 = formula.get("no_v1") if isinstance(formula.get("no_v1"), dict) else {}
    measured = {
        "tools_status": tools_entry.get("status") if isinstance(tools_entry, dict) else None,
        "tools_count": _first_int(tools_entry.get("detail")) if isinstance(tools_entry, dict) else None,
        "fibers_ok": fibers_ok,
        "no_v1_status": no_v1.get("status"),
    }
    expected = {"tools_status": 200, "fibers_ok": True, "no_v1_note": "网关差异"}
    evidence = []
    if isinstance(tools_entry, dict):
        evidence.append("GET /formulas/%s/date:latest/tools -> %s" %
                        (profile.get("vendor") or "vendor", tools_entry.get("detail") or tools_entry.get("status")))
    if isinstance(fibers_entry, dict):
        evidence.append("POST /formulas/%s/date:latest/fibers -> %s" %
                        (profile.get("vendor") or "vendor", fibers_entry.get("detail") or fibers_entry.get("status")))
    if no_v1:
        evidence.append("无 /v1 前缀返回 %s，仅记为网关差异" % no_v1.get("status"))

    if tools_ok is True and fibers_ok is True:
        status, note = "pass", "Formula 接入可用；无 /v1 前缀 404 仅记为网关差异。"
    elif tools_ok is True or fibers_ok is True:
        status, note = "partial", "Formula 仅部分能力可用。"
    else:
        status, note = "fail", "Formula tools 与 fibers 均不可用。"
    return _item("formula_tools", status, measured=measured, expected=expected,
                 evidence=evidence, note=note)


# --------------------------------------------------------------------------- item 12: cache TTL
def _ttl_state(entry, min_pct):
    if not isinstance(entry, dict):
        return None
    explicit = _flag(entry.get("pass"))
    write_tokens = _num(entry.get("write_tokens"))
    header = entry.get("header")
    read_back = _num(entry.get("read_back_hit_pct"))
    has_data = (write_tokens is not None or header not in (None, "") or read_back is not None
                or explicit is not None)
    if not has_data:
        return None
    if explicit is not None:
        return explicit
    ok = write_tokens is not None and write_tokens > 0 and header not in (None, "")
    if read_back is not None:
        ok = ok and read_back >= min_pct
    return ok


def check_cache_ttl(doc, profile):
    cache = doc.get("cache")
    if not isinstance(cache, dict):
        return _skip("cache_ttl", "未采集缓存 TTL 探测，本项不计分。", evidence=["cache 缺失"])
    t5 = cache.get("ttl_5m")
    t1 = cache.get("ttl_1h")
    if not isinstance(t5, dict) and not isinstance(t1, dict):
        return _skip("cache_ttl", "5m/1h 缓存写入均未测得，本项不计分。",
                     evidence=["cache.ttl_5m 与 cache.ttl_1h 缺失"])
    min_pct = _num(_targets(profile).get("cache_hit_min_pct"))
    min_pct = 85.0 if min_pct is None else min_pct
    state5 = _ttl_state(t5, min_pct)
    state1 = _ttl_state(t1, min_pct)
    t1 = t1 if isinstance(t1, dict) else {}
    rb_wait = _num(t1.get("read_back_after_s"))
    rb58_raw = t1.get("read_back_58m")
    rb58 = _num(rb58_raw)
    if rb58 is None and not _is_untested(rb58_raw):
        rb58 = _first_number(rb58_raw)
    untested58 = rb58 is None and _is_untested(rb58_raw)

    measured = {
        "ttl_5m_write_tokens": t5.get("write_tokens") if isinstance(t5, dict) else None,
        "ttl_1h_write_tokens": t1.get("write_tokens"),
        "ttl_1h_header": t1.get("header"),
        "read_back_after_s": t1.get("read_back_after_s"),
        "read_back_hit_pct": t1.get("read_back_hit_pct"),
        "read_back_58m": rb58_raw if rb58_raw is not None else "未测",
    }
    expected = {"write_tokens_positive": True, "headers_present": True,
                "read_back_hit_pct_min": min_pct, "read_back_58m": "可选"}
    evidence = []
    if isinstance(t5, dict):
        evidence.append("ttl_5m write_tokens=%s header=%s" % (t5.get("write_tokens"), t5.get("header")))
    if isinstance(t1, dict):
        evidence.append("ttl_1h write_tokens=%s header=%s" % (t1.get("write_tokens"), t1.get("header")))
    if t1.get("read_back_hit_pct") is not None:
        if rb_wait is not None:
            evidence.append("回读等待 %ss 后命中 %s%%" % (int(rb_wait), _fmt(t1.get("read_back_hit_pct"))))
        else:
            evidence.append("回读等待时间未记录，命中 %s%%" % _fmt(t1.get("read_back_hit_pct")))
        if rb_wait is not None and rb_wait < 300:
            evidence.append("回读间隔短于 5 分钟")
    evidence.append("58 分钟回读：未测" if untested58 else "58 分钟回读：%s" % measured["read_back_58m"])

    if state5 is False and state1 is False:
        status, note = "fail", "5m 与 1h 缓存写入均未通过。"
    elif state5 is False or state1 is False:
        status, note = "partial", "5m/1h 缓存写入仅部分通过。"
    elif state5 is None or state1 is None:
        status, note = "partial", "部分缓存写入计数缺失，无法完整判断。"
    elif untested58:
        wait_label = "%ds 回读" % int(rb_wait) if rb_wait is not None else "短间隔回读"
        status, note = "partial", "5m 与 1h 写入、响应头、%s均通过；58 分钟回读未测。" % wait_label
    elif rb58 is not None and rb58 >= min_pct:
        status, note = "pass", "5m/1h 缓存写入与 58 分钟回读均通过。"
    else:
        status, note = "partial", "5m/1h 写入通过，但 58 分钟回读未达标。"
    return _item("cache_ttl", status, measured=measured, expected=expected,
                 evidence=evidence, note=note)


# --------------------------------------------------------------------------- item 13: tools choice
def check_tools_choice(doc, profile):
    probe = doc.get("probes", {}).get("tools") if isinstance(doc.get("probes"), dict) else None
    if not isinstance(probe, dict):
        return _skip("tools_choice", "未采集工具调用探测，本项不计分。",
                     evidence=["probes.tools 缺失"])
    auto = _flag(probe.get("auto_calls"))
    none_ok = _flag(probe.get("none_no_calls"))
    required = _flag(probe.get("required_calls"))
    specified_status = probe.get("specified_fn_status")
    expected_probes = _dict(profile.get("expected_probes"))
    specified_exp = _dict(expected_probes.get("T:choice_specified"))
    expected_status = specified_exp.get("status", 400)
    core_ok = auto is True and none_ok is True and required is True
    core_bad = auto is False or required is False
    specified_ok = specified_status is not None and specified_status == expected_status

    measured = {
        "auto_tool_calls": auto,
        "none_no_calls": none_ok,
        "required_calls": required,
        "specified_fn_status": specified_status,
        "tool_call_id_shape": probe.get("id_shape"),
    }
    expected = {"auto": "tool_calls", "none": "no calls", "required": "calls", "specified_fn": "按官方行为"}
    evidence = []
    if auto is True:
        evidence.append("auto 触发工具调用")
    if none_ok is True:
        evidence.append("none 不调用工具")
    if required is True:
        evidence.append("required 必调用")
    if specified_status is not None:
        evidence.append("指定函数返回 HTTP %s（官方 %s）" % (specified_status, expected_status))
    if probe.get("id_shape"):
        evidence.append("tool_call id=%s" % probe.get("id_shape"))

    if core_bad:
        status, note = "fail", "工具自动调用或 required 行为不符合官方。"
    elif core_ok and specified_ok:
        status, note = "pass", "自定义工具与 tool_choice 行为符合官方；指定函数组合按官方返回 %s。" % expected_status
    elif core_ok:
        status, note = "partial", "auto/none/required 通过，但指定函数状态未验证或与官方不同。"
    else:
        status, note = "partial", "工具调用行为未完整验证。"
    return _item("tools_choice", status, measured=measured, expected=expected,
                 evidence=evidence, note=note)


# --------------------------------------------------------------------------- item 14: structured output
def check_structured_output(doc, profile):
    probe = doc.get("probes", {}).get("structured") if isinstance(doc.get("probes"), dict) else None
    if not isinstance(probe, dict):
        return _skip("structured_output", "未采集结构化输出探测，本项不计分。",
                     evidence=["probes.structured 缺失"])
    reasoning_string = _flag(probe.get("reasoning_is_string"))
    content_string = _flag(probe.get("content_is_string"))
    nested = _flag(probe.get("nested_parts"))
    tool_standard = _flag(probe.get("tool_calls_standard"))
    known = [v for v in (reasoning_string, content_string, nested, tool_standard) if v is not None]
    if not known:
        return _skip("structured_output", "结构化输出字段均未测得，本项不计分。",
                     evidence=["probes.structured 无有效字段"])
    measured = {
        "reasoning_is_string": reasoning_string,
        "content_is_string": content_string,
        "nested_parts": nested,
        "tool_calls_standard": tool_standard,
    }
    expected = {"reasoning": "string", "content": "string", "nested_parts": False}
    bad = reasoning_string is False or content_string is False or nested is True or tool_standard is False
    good = reasoning_string is True and content_string is True and nested is False and tool_standard is True
    evidence = []
    if reasoning_string is True:
        evidence.append("reasoning_content 为字符串")
    if content_string is True:
        evidence.append("content 为字符串")
    if nested is False:
        evidence.append("无嵌套 parts")
    if tool_standard is True:
        evidence.append("tool_calls 结构标准")
    if bad:
        status, note = "fail", "message 结构不符合官方标准。"
    elif good:
        status, note = "pass", "输出结构与官方标准一致。"
    else:
        status, note = "partial", "部分结构化字段未验证。"
    return _item("structured_output", status, measured=measured, expected=expected,
                 evidence=evidence, note=note)


# --------------------------------------------------------------------------- item 15: hidden prompt
def _extraction_state(value):
    """-> (tested, leaked). Missing / 未测 -> (False, False)."""
    if value is None:
        return False, False
    if isinstance(value, dict):
        if value.get("tested") is False:
            return False, False
        tested = value.get("tested") is True or any(k in value for k in ("leaked", "result", "hit"))
        leaked = value.get("leaked") is True
        if not leaked:
            text = str(value.get("result") or value.get("detail") or "")
            leaked = bool(re.search(r"泄漏|leaked|injected|发现", text, re.I))
        return tested, leaked
    if isinstance(value, bool):
        return True, value
    if isinstance(value, str):
        if _is_untested(value):
            return False, False
        return True, bool(re.search(r"泄漏|leaked|injected|发现", value, re.I))
    return False, False


def check_hidden_prompt(doc, profile):
    probe = doc.get("hidden_prompt")
    if not isinstance(probe, dict):
        return _skip("hidden_prompt", "未采集隐藏提示词探测，本项不计分。",
                     evidence=["hidden_prompt 缺失"])
    overhead = _num(probe.get("fixed_overhead"))
    if overhead is None:
        overhead = _num(probe.get("fixed_overhead_tokens"))
    leak_raw = probe.get("leak_rate")
    leak = _fraction(leak_raw)
    baseline = _dict(profile.get("baseline"))
    mentions = _dict(baseline.get("hidden_prompt_mentions"))
    model = doc.get("model") or profile.get("id")
    official_raw = mentions.get(model)
    if official_raw is None:
        official_raw = mentions.get(profile.get("id"))
    official = _fraction(official_raw)
    identity = probe.get("identity")
    identity_ok = None
    identity_detail = ""
    if isinstance(identity, dict):
        identity_ok = _flag(identity.get("ok"))
        identity_detail = str(identity.get("detail") or "")
    tested, leaked = _extraction_state(probe.get("extraction_attempt"))
    probe_successes = _num(probe.get("probe_successes"))
    probe_total = _num(probe.get("probe_total"))
    probe_complete = (probe_successes is not None and probe_total is not None
                      and probe_total > 0 and probe_successes >= probe_total)
    max_overhead = 40.0
    overhead_ok = overhead is not None and overhead <= max_overhead
    tolerance = 1.0 / 6.0
    leak_ok = None
    if leak is not None and official is not None:
        leak_ok = leak <= official + tolerance

    measured = {
        "fixed_overhead_tokens": int(overhead) if overhead is not None else None,
        "leak_rate": leak_raw if leak_raw is not None else "未测",
        "official_leak_rate": official_raw if official_raw is not None else "未知",
        "identity_match": identity_ok,
        "extraction_attempt": "未测" if not tested else ("泄漏" if leaked else "未发现泄漏"),
        "probe_successes": int(probe_successes) if probe_successes is not None else None,
        "probe_total": int(probe_total) if probe_total is not None else None,
    }
    expected = {
        "overhead_max_tokens": int(max_overhead),
        "leak_rate_max": "官方比率 + 1/6",
        "identity": _dict(profile.get("audit_defaults")).get("identity_regex") or "",
    }
    delta = {}
    if leak is not None and official is not None:
        delta["leak_rate_vs_official"] = round(leak - official, 3)
    evidence = []
    if overhead is not None:
        evidence.append("固定开销估算 %s tokens（上限 %s）" % (_fmt(overhead, 1), _fmt(max_overhead, 0)))
    if leak is not None and official is not None:
        evidence.append("推理泄漏 %s，官方对照 %s" % (leak_raw, official_raw))
    if identity_detail:
        evidence.append("身份自述：%s" % identity_detail[:120])
    if probe_successes is not None:
        evidence.append("泄漏探针成功 %d/%s 次" % (
            int(probe_successes), int(probe_total) if probe_total is not None else "?"))
    evidence.append("复述探测：未测" if not tested else ("发现泄漏" if leaked else "未发现泄漏"))

    hard_fail = identity_ok is False or leaked or (leak_ok is False)
    if hard_fail:
        status, note = "fail", "身份不符、泄漏超出官方容差或复述探测发现注入。"
    elif identity_ok is True and overhead_ok and leak_ok is True and tested and not leaked and probe_complete:
        status, note = "pass", "固定开销、身份与泄漏率符合官方，复述探测未发现注入。"
    elif identity_ok is True and overhead_ok and leak_ok is True:
        if not tested:
            note = "未发现强注入迹象；复述探测未测，故判 partial。"
        elif not probe_complete:
            note = "未发现强注入迹象；泄漏探针仅 %d/%s 次成功，证据不完整，故判 partial。" % (
                int(probe_successes) if probe_successes is not None else 0,
                int(probe_total) if probe_total is not None else "?")
        else:
            note = "未发现强注入迹象；证据不完整，故判 partial。"
        status = "partial"
    else:
        status, note = "partial", "隐藏提示词证据不完整，无法给出满分。"
    return _item("hidden_prompt", status, measured=measured, expected=expected, delta=delta,
                 evidence=evidence, note=note)


ORDER = [
    ("perf_baseline", check_perf_baseline),
    ("ttft", check_ttft),
    ("cache_hit", check_cache_hit),
    ("usage_schema", check_usage_schema),
    ("thinking_params", check_thinking_params),
    ("finish_reason", check_finish_reason),
    ("timeout", check_timeout),
    ("video", check_video),
    ("param_matrix", check_param_matrix),
    ("reasoning_content", check_reasoning_content),
    ("formula_tools", check_formula_tools),
    ("cache_ttl", check_cache_ttl),
    ("tools_choice", check_tools_choice),
    ("structured_output", check_structured_output),
    ("hidden_prompt", check_hidden_prompt),
]


def evaluate(doc, profile):
    """Run all 15 checks and apply the frozen scoring formula. Never mutates doc/profile."""
    doc = doc if isinstance(doc, dict) else {}
    items = []
    for key, fn in ORDER:
        try:
            item = fn(doc, profile)
            if not isinstance(item, dict):
                item = _skip(key, "检查返回了非字典结果，按 skip 处理。")
        except Exception as exc:  # one malformed input must not lose the whole spec
            item = _skip(key, "检查执行异常：%s: %s" % (type(exc).__name__, str(exc)[:120]),
                         evidence=["检查执行异常，按 skip 处理"])
        if item.get("status") not in _STATUS_VALUE:
            item["status"] = "skip"
        items.append(item)

    total_weight = sum(int(i.get("weight") or 0) for i in items)
    measured_weight = sum(int(i.get("weight") or 0) for i in items if i.get("status") != "skip")
    coverage = (100.0 * measured_weight / total_weight) if total_weight else 0.0
    score = None
    if total_weight and coverage >= MIN_COVERAGE_PCT and measured_weight:
        weighted = sum(int(i.get("weight") or 0) * _STATUS_VALUE[i["status"]]
                       for i in items if i.get("status") != "skip")
        score = round(100 * weighted / measured_weight)
    return {"version": 1, "score": score, "items": items}
