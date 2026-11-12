"""Sections 04-06 (spec conformance, billing and cache, authenticity audit) and totals.

Conventions (see html_components.py): numbers go through ``fmt`` (float -> 2 decimals), free text from JSON goes
through ``txt`` (round + gloss + escape), fixed labels go through ``lab``/``bi`` (``English（中文）``), tables go
through ``table``.  Section 06 notes are free English sentences the glossary can not cover, so the main column
shows a fixed Chinese summary per check and keeps the original sentence in a folded ``<pre>`` (escaped, key-masked,
numbers rounded, never glossed).
"""
import re

from benchmark import build_rows
from common import fmt_num, round_text
from html_components import (
    NO_SPEC_TEXT,
    STATUS_TEXT,
    is_num,
    fmt,
    esc_raw,
    val_html,
    dig,
    items_of,
    find_item,
    pretty,
    status_of,
    pill,
    verdict_class,
    empty_panel,
    fold_start,
    FOLD_END,
    section,
    table,
    txt,
    lab,
    ov,
    bar_pair,
)
from i18n_zh import bi, zh

# --------------------------------------------------------------------------- local helpers

_SK_RE = re.compile(r"sk-[A-Za-z0-9_-]{8,}")


def _mask(s):
    """Mask key-like tokens again at render time (load_json already did; this is defence in depth)."""
    return _SK_RE.sub("sk-***", str(s))


def _t(v):
    """Free text (masked, rounded, glossed, escaped); empty -> 未测."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return "未测"
    return txt(_mask(v))


def _gl(s):
    """Known glossary term -> ``English（中文）``; anything else is treated as free text."""
    if isinstance(s, str) and zh(s) is not None:
        return lab(s)
    return _t(s)


def _pre(v):
    """Escaped ``<pre>`` payload: rounded and key-masked, never glossed (raw data)."""
    return esc_raw(round_text(_mask(pretty(v))))


def _pill(st):
    """Status pill with ``pass（通过）``-style text (never colour-only)."""
    st = status_of(st)
    return pill(st, bi(st))


def _bool_state(v):
    return "pass" if v is True else ("fail" if v is False else "skip")


# --------------------------------------------------------------------------- section 04

def evidence_html(ev):
    if isinstance(ev, list) and ev:
        return "<ul>" + "".join(f"<li>{_t(x)}</li>" for x in ev) + "</ul>"
    if isinstance(ev, str) and ev.strip():
        return f"<p>{_t(ev)}</p>"
    return "<p>未测</p>"


def render_spec_items(spec_doc, profile=None):
    items = items_of(spec_doc)
    if not items:
        return section("04", "规范符合度", NO_SPEC_TEXT) + \
            empty_panel(NO_SPEC_TEXT, "15 项规范清单不可用。")

    counts = {s: sum(1 for it in items if it.get("status") == s) for s in STATUS_TEXT}
    score = dig(spec_doc, "spec", "score")
    out = [section("04", "规范符合度",
                   f"{counts['pass']} 通过 · {counts['partial']} 部分 · "
                   f"{counts['fail']} 未通过 · {counts['skip']} 跳过")]
    marks = {"pass": "✓", "partial": "!", "fail": "✕", "skip": "–"}
    cells = "".join(
        f'<span class="sp-count {st}"><span aria-hidden="true">{marks[st]}</span> {lab(st)} '
        f'<b class="sp-count-num">{fmt(counts[st])}</b></span>'
        for st in ("pass", "partial", "fail", "skip")
    )
    out.append(
        f'<div class="sp-counts" role="group" aria-label="规范项状态统计">{cells}'
        f'<span class="sp-count sp-score">规范分 <b class="sp-count-num">{fmt(score)}</b> / 100</span></div>'
    )
    attention, calm = [], []
    for pos, it in enumerate(items, 1):
        (attention if status_of(it.get("status")) in ("fail", "partial") else calm).append((pos, it))
    out.append('<div class="sp-list">')
    for pos, it in attention:
        st = status_of(it.get("status"))
        raw_title = it.get("title") or it.get("key") or "未命名项"
        key = it.get("key")
        key_html = f'<span class="sp-key">{lab(key)}</span>' if key else ""
        note = it.get("note") or "未提供一句话结论"
        out.append(
            f'<details class="sp-item {st}"{" open" if st == "fail" else ""}>'
            f'<summary><span class="sp-idx">{pos:02d}</span>'
            f'<span class="sp-main"><span class="sp-title" title="{esc_raw(raw_title)}">{txt(raw_title)}</span>'
            f'{key_html}</span>'
            f'<span class="sp-weight">权重 {fmt(it.get("weight"))}</span>{_pill(st)}</summary>'
            '<div class="sp-body">'
            f'<p class="sp-note"><b>{lab("note")}：</b>{txt(note)}</p>'
            f'<div class="sp-evidence"><h4>{lab("evidence")}</h4>{evidence_html(it.get("evidence"))}</div>'
            '<details class="au-raw"><summary>实测 / 预期 / 差值（原始数据）</summary><div class="sp-kv">'
            f'<div><h4>{lab("measured")}</h4><pre tabindex="0">{_pre(it.get("measured"))}</pre></div>'
            f'<div><h4>{lab("expected")}</h4><pre tabindex="0">{_pre(it.get("expected"))}</pre></div>'
            f'<div><h4>{lab("delta")}</h4><pre tabindex="0">{_pre(it.get("delta"))}</pre></div>'
            '</div></details></div></details>'
        )
    out.append("</div>")
    if calm:
        lis = "".join(
            f'<li><span class="sp-idx">{pos:02d}</span>'
            f'<span class="sp-title" title="{esc_raw(it.get("title") or it.get("key") or "未命名项")}">'
            f'{txt(it.get("title") or it.get("key") or "未命名项")}</span>{_pill(it.get("status"))}</li>'
            for pos, it in calm)
        out.append(f'<details class="sp-passed"><summary>通过 / 跳过 {fmt(len(calm))} 项（点击展开）</summary>'
                   f'<ul>{lis}</ul></details>')

    matrix = dig(spec_doc, "params", "matrix", default=[]) or []
    if isinstance(matrix, list) and matrix:
        matched = sum(1 for m in matrix if isinstance(m, dict) and m.get("match") is True)
        rows = []
        for m in matrix:
            if not isinstance(m, dict):
                continue
            mst = _bool_state(m.get("match"))
            rows.append([
                _t(m.get("case")), fmt(m.get("expected_status")), fmt(m.get("actual_status")),
                pill(mst, {"pass": "一致", "fail": "不一致", "skip": "未测"}[mst]),
                _t(m.get("detail")),
            ])
        out.append(
            f'<details class="sp-matrix"><summary>{lab("param_matrix")}明细 · {fmt(len(matrix))} 例 · '
            f'一致 {fmt(matched)}</summary>'
            + table(["用例", "预期状态", "实测状态", "比对", "说明"], rows, aligns=["", "r", "r", "c", ""])
            + "</details>"
        )
    return "".join(out)


# --------------------------------------------------------------------------- section 05

_SCENARIO_LABEL = {
    "A_identical_20k": "A · 相同 20k", "B_multiturn_12k": "B · 多轮 12k",
    "C_short_1k": "C · 短文本 1k",
}
_SCENARIO_ROW = {  # benchmark.build_rows cache metric -> spec scenario key
    "cache_identical_20k": "A_identical_20k",
    "cache_multiturn_12k": "B_multiturn_12k",
    "cache_short_1k": "C_short_1k",
}


def _scenario_label(name):
    if name in _SCENARIO_LABEL:
        return esc_raw(_SCENARIO_LABEL[name])
    if name == "E_cache_key":
        return "E · " + lab("cache key")
    return _gl(name)


def _cache_rows(spec_doc, profile):
    """scenario key -> benchmark row (cache group only)."""
    try:
        rows = build_rows(spec_doc, profile)
    except Exception:  # rendering must never fail because of the benchmark layer
        return {}
    return {_SCENARIO_ROW[r["metric"]]: r for r in rows
            if r.get("group") == "cache" and r.get("metric") in _SCENARIO_ROW}


def _threshold_html(row):
    thr = row.get("threshold") if row else None
    if is_num(thr) and row.get("threshold_kind") == "pct_min":
        return f"≥ {val_html(thr, '%')}"
    return '<span class="na">未提供</span>'


def render_billing(spec_doc, profile=None):
    out = [fold_start("05", "计费与缓存", f"{bi('usage')}字段 · 5m / 1h 写入 · 缓存场景")]
    if spec_doc is None:
        out.append(empty_panel(NO_SPEC_TEXT, f"{bi('usage')}、缓存写入与场景命中不可用。"))
        out.append(FOLD_END)
        return "".join(out)

    usage = dig(spec_doc, "probes", "usage", default={}) or {}
    item4 = find_item(items_of(spec_doc), ident=4, key="usage_schema")
    exp = dig(item4, "expected", default={}) or {}

    required = exp.get("required") if isinstance(exp.get("required"), list) else []
    details = exp.get("details") if isinstance(exp.get("details"), dict) else {}
    detail_text = "；".join(
        f"{k}: {', '.join(str(x) for x in v)}" for k, v in details.items() if isinstance(v, list)
    )
    ratio_range = exp.get("token_ratio_range") if isinstance(exp.get("token_ratio_range"), list) else []

    def bool_row(label_html, key, expected_html):
        v = usage.get(key)
        if isinstance(v, bool):
            st, result = ("pass" if v else "fail"), ("是" if v else "否")
        else:
            st, result = "skip", "未测"
        return [label_html, result, expected_html, _pill(st)]

    rows = [
        bool_row("必需字段", "required_present", _t(", ".join(str(x) for x in required)) if required else "—"),
        bool_row(f"{lab('total_tokens')} 一致性", "total_matches", "等于分项之和"),
        bool_row("明细子键", "details_present", _t(detail_text) if detail_text else "—"),
        bool_row("缓存写入字段", "cache_write_present", lab("cache_write_tokens")),
    ]
    tr = usage.get("token_ratio")
    if is_num(tr):
        if len(ratio_range) == 2 and all(is_num(x) for x in ratio_range):
            ok = float(ratio_range[0]) <= float(tr) <= float(ratio_range[1])
            st, exp_text = ("pass" if ok else "fail"), f"{fmt(ratio_range[0])} – {fmt(ratio_range[1])}"
        else:
            st, exp_text = "skip", "未提供区间"
        rows.append(["token / char 比率", fmt(tr), esc_raw(exp_text), _pill(st)])
    else:
        rows.append(["token / char 比率", "未测", "—", _pill("skip")])
    out.append(table(["检查项", "结果", "官方期望", "判定"], rows, aligns=["", "", "", "c"],
                     caption=f"{bi('usage')}字段一致性"))

    cache = dig(spec_doc, "cache", default={}) or {}
    out.append('<div class="bl-ttl-grid">')
    for key, title in (("ttl_5m", "5 分钟缓存写入"), ("ttl_1h", "1 小时缓存写入")):
        ttl = cache.get(key) or {}
        if not isinstance(ttl, dict) or not ttl:
            out.append(
                f'<div class="bl-ttl na"><div class="bl-ttl-head"><h3>{esc_raw(title)}</h3>{_pill("skip")}</div>'
                f'<dl class="bl-dl"><dt>状态</dt><dd>未测</dd></dl></div>')
            continue
        st = _bool_state(ttl.get("pass"))
        rows_dl = [
            ("写入 tokens", val_html(ttl.get("write_tokens")) if is_num(ttl.get("write_tokens")) else "未测"),
            ("响应头值", _t(ttl.get("header"))),
            ("回读命中", val_html(ttl.get("read_back_hit_pct"), "%")
             if is_num(ttl.get("read_back_hit_pct")) else "未测"),
        ]
        if key == "ttl_1h":
            rows_dl.insert(2, ("回读延迟", val_html(ttl.get("read_back_after_s"), "s")
                               if is_num(ttl.get("read_back_after_s")) else "未测"))
            rows_dl.append(("58 分钟回读", _t(ttl.get("read_back_58m"))))
        dl = "".join(f"<dt>{esc_raw(k)}</dt><dd>{v}</dd>" for k, v in rows_dl)
        out.append(
            f'<div class="bl-ttl"><div class="bl-ttl-head"><h3>{esc_raw(title)}</h3>{_pill(st)}</div>'
            f'<dl class="bl-dl">{dl}</dl></div>'
        )
    out.append("</div>")

    scenarios = cache.get("scenarios") or {}
    if isinstance(scenarios, dict) and scenarios:
        official_rows = _cache_rows(spec_doc, profile)
        rows, details_blocks = [], []
        for name, sc in scenarios.items():
            if not isinstance(sc, dict):
                continue
            st = _bool_state(sc.get("pass"))
            hit = sc.get("hit_rate_pct")
            rounds = sc.get("rounds") if isinstance(sc.get("rounds"), list) else []
            row = official_rows.get(name)
            measured = row["measured"] if row and row.get("measured") is not None else (hit if is_num(hit) else None)
            official = row.get("official") if row else None
            if official is not None:
                cell = ov(official, measured, "%", row.get("delta_pct"), row.get("verdict")) + \
                    " " + bar_pair(official, measured, "缓存命中率")
            else:
                cell = ov(None, measured, "%")
            rows.append([_scenario_label(name), _pill(st), cell, _threshold_html(row), fmt(len(rounds))])
            rrows = [
                [fmt(r.get("i")), fmt(r.get("cached_tokens")), fmt(r.get("prompt_tokens")),
                 val_html(r.get("hit_rate_pct"), "%") if is_num(r.get("hit_rate_pct")) else "未测"]
                for r in rounds if isinstance(r, dict)
            ]
            if rrows:
                details_blocks.append(
                    f'<details class="bl-rounds"><summary>{_scenario_label(name)} · 逐轮明细</summary>'
                    + table(["轮次", bi("cached_tokens"), bi("prompt_tokens"), "命中率"], rrows,
                            aligns=["r", "r", "r", "r"])
                    + "</details>"
                )
        out.append('<div class="bl-subhead">缓存场景<span>固定前缀重复请求 · 官方 / 实测命中率</span></div>')
        out.append(table(["场景", "判定", "官方 / 实测", "门槛", "轮次"], rows,
                         aligns=["", "c", "", "r", "r"], caption="缓存场景命中率"))
        out.extend(details_blocks)
    else:
        out.append('<div class="bl-subhead">缓存场景</div>')
        out.append(empty_panel("缓存场景未测", f"{bi('cache.scenarios')} 为空。"))
    out.append(FOLD_END)
    return "".join(out)


# --------------------------------------------------------------------------- section 06

# Fixed Chinese point per check name (scoring.WEIGHTS keys). The free English note of a check is data, shown raw.
CHECK_SUMMARY = {
    "vendor headers": "核对响应头中的厂商或网关标记是否与声称的模型一致。",
    "format+stream": "核对响应结构与流式事件是否符合接口规范。",
    "identity+facts": "询问模型自称的身份，并核对常识题答案是否正确。",
    "reasoning": "核对推理内容字段的有无、类型与推理 token 计数。",
    "long context": "在长上下文的不同位置埋入暗号，核对能否找回。",
    "tools+json": "核对工具调用与 JSON（结构化数据）模式输出是否有效。",
    "instruction following": "核对模型是否遵守格式类指令。",
    "error format": "发送畸形请求，核对错误响应是否为官方格式。",
    "tokenizer": "按 token 与字符比率核对是否属于声称的模型家族。",
}
CHECK_SUMMARY_FALLBACK = "详见原始说明。"

_VERDICT_MARK = {"green": "✓", "amber": "!", "red": "✕", "": "–"}


def render_audit(audit):
    out = [section("06", "真实性与渠道", f"{bi('model_audit')} 加权检查 · 独立于规范符合度")]
    score = audit.get("score")
    verdict = audit.get("verdict") or "未测"
    hard_fail = audit.get("hard_fail")
    vcls = "red" if hard_fail else verdict_class(audit.get("verdict"))
    notes = audit.get("notes") if isinstance(audit.get("notes"), list) else []
    crashed = audit.get("crashed_checks") if isinstance(audit.get("crashed_checks"), list) else []
    usage_inflated = audit.get("usage_inflated")

    hard_html = f'<span class="au-alert">{_gl(hard_fail)}</span>' if hard_fail else "未触发"
    if usage_inflated is True:
        infl_html = '<span class="au-alert">是</span>'
    else:
        infl_html = "否" if usage_inflated is False else "未测"
    crashed_html = "、".join(_gl(x) for x in crashed) if crashed else "无"
    perf = audit.get("performance")
    perf_html = f'{fmt(perf)} / 100' if is_num(perf) else "未测"
    meta = [
        f"{lab('hard_fail')}：<b>{hard_html}</b>",
        f"{lab('usage_inflated')}：<b>{infl_html}</b>",
        f"{lab('crashed_checks')}：<b>{crashed_html}</b>",
        f"性能分：<b>{perf_html}</b> · {_t(audit.get('performance_detail'))}",
    ]
    verdict_html = _gl(verdict) if isinstance(verdict, str) else _t(verdict)
    out.append(
        f'<div class="au-banner {vcls}" role="group" aria-label="真实性审计结论">'
        f'<div class="au-head"><div class="au-score">{fmt(score) if is_num(score) else "未测"}'
        f'<small>/ 100</small></div>'
        f'<div class="au-verdict"><span aria-hidden="true">{_VERDICT_MARK.get(vcls, "–")}</span> '
        f'{verdict_html}</div></div>'
        f'<ul class="au-meta">{"".join(f"<li>{m}</li>" for m in meta)}</ul></div>'
    )

    checks = audit.get("checks") if isinstance(audit.get("checks"), list) else []
    if checks:
        rows, raws = [], []
        for c in checks:
            if not isinstance(c, dict):
                continue
            w, s = c.get("weight"), c.get("score")
            name = c.get("name")
            if is_num(s) and is_num(w):
                weighted = f"{fmt_num(float(s) * float(w))} / {fmt(w)}"
                width = max(0.0, min(100.0, float(s) * 100.0))
                bar = f'<span class="au-bar" aria-hidden="true"><span style="width:{width:.1f}%"></span></span>'
                score_txt = f"{fmt(float(s))}{bar}"
            elif s is None:
                weighted = score_txt = lab("n/a")
            else:
                weighted, score_txt = "未测", _t(s)
            summary = CHECK_SUMMARY.get(str(name).lower(), CHECK_SUMMARY_FALLBACK)
            name_html = _gl(name) if name else "未命名检查"
            rows.append([f'<span class="mt-name">{name_html}</span><span class="mt-en">{esc_raw(summary)}</span>',
                         fmt(w), score_txt, weighted])
            raws.append(f'<h4>{name_html}</h4><pre tabindex="0">'
                        f'{esc_raw(round_text(_mask(c.get("note") or "未测")))}</pre>')
        out.append(table(["检查项", "权重", "得分", "加权得分"], rows,
                         cls="table check-table", aligns=["", "r", "r", "r"],
                         caption="真实性检查明细"))
        out.append('<details class="au-raw"><summary>raw note（原始说明）· 逐项</summary>'
                   f'<div class="au-raws">{"".join(raws)}</div></details>')
    else:
        out.append(empty_panel("未提供 checks", f"{bi('model_audit')} 输出中没有检查项。"))

    if notes:
        raw_notes = "\n".join(round_text(_mask(n)) for n in notes)
        out.append(
            f'<div class="au-notes"><b>审计备注</b> · 共 {fmt(len(notes))} 条'
            f'<details class="au-raw"><summary>raw notes（原始备注）</summary>'
            f'<pre tabindex="0">{esc_raw(raw_notes)}</pre></details></div>'
        )
    return "".join(out)


# --------------------------------------------------------------------------- totals + footer

def render_totals(spec_doc, profile=None):
    load = dig(spec_doc, "load", default={}) or {}
    totals = load.get("totals") or {}
    cells = [
        ("输入 tokens 合计", totals.get("prompt_tokens")),
        ("输出 tokens 合计", totals.get("completion_tokens")),
        ("缓存命中 tokens", totals.get("cached_tokens")),
        ("缓存写入 tokens", totals.get("cache_write_tokens")),
    ]
    out = ['<section class="tt-totals" aria-label="用量合计">']
    for label, v in cells:
        out.append(
            f'<article class="tt-card"><div class="tt-label">{esc_raw(label)}</div>'
            f'<div class="tt-value">{val_html(v) if is_num(v) else "未测"}</div></article>'
        )
    out.append("</section>")
    return "".join(out)


# --------------------------------------------------------------------------- CSS (spliced into <style> by html_theme)

CSS_EXTRA = r"""
/* ---- sections 04-06 + totals (html_sec_spec.py); theme variables only ---- */
.sp-counts{display:grid;grid-template-columns:repeat(auto-fit,minmax(9rem,1fr));gap:var(--space-3,.75rem);margin:var(--space-4,1rem) 0}
.sp-count{background:var(--surface);border:1px solid var(--line);border-left:4px solid var(--line);border-radius:var(--radius);padding:var(--space-3,.75rem) var(--space-4,1rem);display:flex;flex-direction:column;gap:.25rem}
.sp-count-label{color:var(--muted);font-size:.9rem}
.sp-count-num{font-size:1.9rem;line-height:1.1;font-variant-numeric:tabular-nums}
.sp-count.pass{border-left-color:var(--ok)}
.sp-count.partial{border-left-color:var(--warn);background:var(--warn-bg)}
.sp-count.fail{border-left-color:var(--bad);background:var(--bad-bg)}
.sp-count.skip{border-left-color:var(--muted)}
.sp-count.pass .sp-count-num{color:var(--ok)}
.sp-count.partial .sp-count-num{color:var(--warn)}
.sp-count.fail .sp-count-num{color:var(--bad)}
.sp-score{border-left-color:var(--accent)}
.sp-list{display:flex;flex-direction:column;gap:var(--space-2,.5rem)}
.sp-item,.sp-matrix,.bl-rounds,.au-raw{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius)}
.sp-item{border-left:4px solid var(--line)}
.sp-item.pass{border-left-color:var(--ok)}
.sp-item.partial{border-left-color:var(--warn)}
.sp-item.fail{border-left-color:var(--bad)}
.sp-item[open].partial{background:var(--warn-bg)}
.sp-item[open].fail{background:var(--bad-bg)}
.sp-item>summary,.sp-matrix>summary,.bl-rounds>summary,.au-raw>summary{list-style:none;cursor:pointer}
.sp-item>summary::-webkit-details-marker,.sp-matrix>summary::-webkit-details-marker,.bl-rounds>summary::-webkit-details-marker,.au-raw>summary::-webkit-details-marker{display:none}
.sp-item>summary{display:flex;flex-wrap:wrap;align-items:center;gap:var(--space-2,.5rem) var(--space-3,.75rem);padding:var(--space-3,.75rem) var(--space-4,1rem)}
.sp-item>summary::before,.sp-matrix>summary::before,.bl-rounds>summary::before,.au-raw>summary::before{content:"▸";color:var(--muted);flex:none}
.sp-item[open]>summary::before,.sp-matrix[open]>summary::before,.bl-rounds[open]>summary::before,.au-raw[open]>summary::before{content:"▾"}
.sp-item>summary:focus-visible,.sp-matrix>summary:focus-visible,.bl-rounds>summary:focus-visible,.au-raw>summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.sp-idx{font-family:var(--mono);color:var(--muted);font-variant-numeric:tabular-nums}
.sp-main{flex:1 1 16rem;min-width:0;display:flex;flex-direction:column;gap:.15rem}
.sp-title{font-weight:600;overflow-wrap:anywhere}
.sp-key{color:var(--muted);font-size:.85rem;overflow-wrap:anywhere}
.sp-weight{color:var(--muted);font-size:.9rem;white-space:nowrap}
.sp-body{padding:var(--space-3,.75rem) var(--space-4,1rem) var(--space-4,1rem);border-top:1px solid var(--line);display:flex;flex-direction:column;gap:var(--space-3,.75rem)}
.sp-body h4{margin:0 0 .25rem;font-size:.9rem;color:var(--muted)}
.sp-note{margin:0;overflow-wrap:anywhere}
.sp-evidence ul{margin:0;padding-left:1.25rem}
.sp-evidence li,.sp-evidence p{overflow-wrap:anywhere;margin:.15rem 0}
.sp-kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(14rem,1fr));gap:var(--space-3,.75rem)}
.sp-kv>div{min-width:0}
.sp-kv pre,.au-raw pre{margin:0;padding:var(--space-2,.5rem) var(--space-3,.75rem);background:var(--surface-2);border:1px solid var(--line);border-radius:var(--radius);font-family:var(--mono);font-size:.85rem;white-space:pre-wrap;overflow-wrap:anywhere;max-height:20rem;overflow:auto}
.sp-matrix,.bl-rounds{margin-top:var(--space-3,.75rem)}
.sp-matrix>summary,.bl-rounds>summary{display:flex;gap:.5rem;padding:var(--space-2,.5rem) var(--space-4,1rem);font-weight:600}
.sp-matrix>.table-wrap,.bl-rounds>.table-wrap{margin:0 var(--space-3,.75rem) var(--space-3,.75rem)}
.bl-ttl-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(18rem,1fr));gap:var(--space-3,.75rem);margin:var(--space-4,1rem) 0}
.bl-ttl{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:var(--space-3,.75rem) var(--space-4,1rem)}
.bl-ttl-head{display:flex;justify-content:space-between;align-items:center;gap:var(--space-2,.5rem)}
.bl-ttl-head h3{margin:0;font-size:1rem}
.bl-dl{display:grid;grid-template-columns:max-content 1fr;gap:.25rem var(--space-3,.75rem);margin:var(--space-3,.75rem) 0 0}
.bl-dl dt{color:var(--muted)}
.bl-dl dd{margin:0;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}
.bl-subhead{margin:var(--space-5,1.5rem) 0 var(--space-2,.5rem);font-weight:600}
.bl-subhead span{margin-left:.75rem;color:var(--muted);font-weight:400;font-size:.9rem}
.au-banner{background:var(--surface);border:1px solid var(--line);border-left:6px solid var(--muted);border-radius:var(--radius);padding:var(--space-4,1rem);display:flex;flex-wrap:wrap;gap:var(--space-4,1rem) var(--space-5,1.5rem);align-items:center;margin:var(--space-4,1rem) 0}
.au-banner.green{border-left-color:var(--ok);background:var(--ok-bg)}
.au-banner.amber{border-left-color:var(--warn);background:var(--warn-bg)}
.au-banner.red{border-left-color:var(--bad);background:var(--bad-bg)}
.au-score{font-size:2.6rem;font-weight:700;line-height:1;font-variant-numeric:tabular-nums}
.au-score small{font-size:1rem;font-weight:400;color:var(--muted);margin-left:.25rem}
.au-verdict{font-size:1.15rem;font-weight:600;margin-top:.35rem;overflow-wrap:anywhere}
.au-meta{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:.25rem;flex:1 1 18rem;min-width:0;overflow-wrap:anywhere}
.au-alert{color:var(--bad);font-weight:700}
.au-alert::before{content:"✕ "}
.au-bar{display:block;height:.4rem;margin-top:.25rem;background:var(--surface-2);border-radius:.2rem;overflow:hidden}
.au-bar>span{display:block;height:100%;background:var(--accent)}
.au-sum{font-size:.875rem;margin:0 0 .35rem;overflow-wrap:anywhere}
.au-raw{margin-top:.25rem}
.au-raw>summary{padding:.25rem var(--space-3,.75rem);font-size:.85rem;color:var(--muted);display:flex;gap:.5rem}
.au-raw>pre{margin:0 var(--space-3,.75rem) var(--space-3,.75rem)}
.au-notes{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:var(--space-3,.75rem) var(--space-4,1rem);margin-top:var(--space-4,1rem)}
.tt-totals{display:grid;grid-template-columns:repeat(auto-fit,minmax(12rem,1fr));gap:var(--space-3,.75rem);margin:var(--space-5,1.5rem) 0}
.tt-card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:var(--space-3,.75rem) var(--space-4,1rem)}
.tt-label{color:var(--muted);font-size:.9rem}
.tt-value{font-size:1.5rem;font-weight:600;font-variant-numeric:tabular-nums;margin-top:.25rem}
.tt-value small{font-size:.85rem;font-weight:400;color:var(--muted);margin-left:.2rem}
"""
