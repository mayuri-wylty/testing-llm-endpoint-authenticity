"""Sections 01, 03, 04: core standards, all metrics, official-baseline comparison.

Every visible number goes through html_components.fmt / val_html / ov (float -> 2 decimals), every visible
English word through lab()/bi() (``English（中文）``), free text through txt(). Official-vs-measured data comes
from benchmark.build_rows(spec_doc, profile); with no profile the official side reads 未提供 and the measured
side is still shown.
"""
from benchmark import baseline_source, build_rows
from html_components import (
    CORE_TEXT,
    dig,
    empty_panel,
    esc,
    esc_raw,
    find_item,
    FOLD_END,
    fold_start,
    fmt,
    is_num,
    items_of,
    lab,
    ov,
    pill,
    section,
    status_of,
    table,
    txt,
    val_html,
)
from common import fmt_num
from i18n_zh import bi

# --------------------------------------------------------------------------- scoped CSS (spliced into <style>)
# Uses only the theme variables (--surface --surface-2 --text --muted --line --accent --ok --warn --bad
# --radius --mono); no colours, dark/print rules or external resources here.

CSS_EXTRA = r"""
/* ---- 01 core standard cards ---- */
.kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(15.5rem,1fr));gap:1rem}
.kpi{display:flex;flex-direction:column;gap:.75rem;min-width:0;padding:1.25rem 1.25rem 1rem;
  background:var(--surface);border:1px solid var(--line);border-top:4px solid var(--muted);
  border-radius:var(--radius)}
.kpi.pass{border-top-color:var(--ok)}
.kpi.partial{border-top-color:var(--warn)}
.kpi.fail{border-top-color:var(--bad)}
.kpi-head{display:flex;flex-direction:column;gap:.25rem}
.kpi-title{margin:0;font-size:1.125rem;font-weight:600;line-height:1.3}
.kpi-sub{font-size:.875rem;line-height:1.5;color:var(--muted)}
.kpi-value{font-size:2.5rem;font-weight:600;line-height:1.1;font-variant-numeric:tabular-nums;
  overflow-wrap:anywhere}
.kpi.fail .kpi-value{color:var(--bad)}
.kpi.partial .kpi-value{color:var(--warn)}
.kpi-value small{margin-left:.25rem;font-size:1.125rem;font-weight:400;color:var(--muted)}
.kpi-status{display:flex;flex-wrap:wrap;gap:.25rem .5rem;align-items:center}
.kpi-ovline{padding-top:.75rem;border-top:1px dashed var(--line);font-size:.9375rem;line-height:1.6}
.kpi-ovlabel{display:block;margin-bottom:.25rem;font-size:.8125rem;color:var(--muted)}
.kpi-mini{display:flex;flex-wrap:wrap;gap:.25rem .75rem;font-size:.875rem;line-height:1.6;color:var(--muted)}
.kpi-mini b{font-weight:600;color:var(--text)}
.kpi-meta{display:grid;grid-template-columns:auto minmax(0,1fr);gap:.25rem .75rem;margin:auto 0 0;
  padding-top:.75rem;border-top:1px solid var(--line);font-size:.875rem;line-height:1.6}
.kpi-meta dt{color:var(--muted)}
.kpi-meta dd{margin:0}
.kpi .na,.bm-table .na{color:var(--muted)}

/* ---- 03 all metrics ---- */
.sc-quant td.r,.sc-quant th.r,.bm-table td.r,.bm-table th.r{text-align:right}
.bm-table td.c,.bm-table th.c{text-align:center}
.mt-name{display:block;font-weight:600}
.mt-en{display:block;font-size:.8125rem;line-height:1.5;color:var(--muted)}
.mt-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(11.5rem,1fr));gap:.75rem;margin-top:1rem}
.mt-tile{display:flex;flex-direction:column;gap:.375rem;min-width:0;padding:1rem 1.125rem;
  background:var(--surface);border:1px solid var(--line);border-radius:var(--radius)}
.mt-tile.bad{border-color:var(--bad)}
.mt-label{font-size:.875rem;line-height:1.5;color:var(--muted)}
.mt-value{font-size:1.5rem;font-weight:600;line-height:1.2;font-variant-numeric:tabular-nums;
  overflow-wrap:anywhere}
.mt-value small{margin-left:.25rem;font-size:.875rem;font-weight:400;color:var(--muted)}
.mt-note{font-size:.8125rem;color:var(--muted)}
.mt-tile.bad .mt-note{color:var(--bad);font-weight:600}
.sc-subhead{display:flex;flex-wrap:wrap;gap:.25rem 1rem;align-items:baseline;margin:1.75rem 0 .75rem;
  font-size:1rem;font-weight:600}
.sc-subhead span{font-size:.8125rem;font-weight:400;color:var(--muted)}
.fr{display:grid;gap:.625rem;padding:1rem 1.25rem;background:var(--surface);
  border:1px solid var(--line);border-radius:var(--radius)}
.fr-row{display:grid;grid-template-columns:minmax(9rem,15rem) minmax(0,1fr) minmax(7rem,auto);gap:.75rem;
  align-items:center}
.fr-label{font-size:.875rem;line-height:1.5}
.fr-track{height:.75rem;overflow:hidden;background:var(--surface-2);border-radius:999px}
.fr-fill{display:block;height:100%;background:var(--accent)}
.fr-fill.warn{background:var(--bad)}
.fr-val{text-align:right;font-size:.9375rem;font-weight:600;font-variant-numeric:tabular-nums}
.fr-val small{margin-left:.25rem;font-weight:400;color:var(--muted)}
@media (max-width:40rem){
  .fr-row{grid-template-columns:minmax(0,1fr) auto}
  .fr-label{grid-column:1 / -1}
}

/* ---- 04 official baseline ---- */
.bm-note{margin:0 0 .75rem;font-size:.875rem;line-height:1.7;color:var(--muted)}
.bm-legend{display:flex;flex-wrap:wrap;gap:.25rem 1.25rem;align-items:center;margin:0 0 1rem;
  font-size:.875rem;color:var(--muted)}
.bm-legend span{display:inline-flex;gap:.5rem;align-items:center}
.bm-legend .barpair{display:inline-block;width:3rem}
.bm-group{margin-top:1.5rem}
.bm-group:first-of-type{margin-top:0}
.bm-table caption{caption-side:top;padding:.5rem .25rem .75rem;text-align:left;font-size:1rem;
  font-weight:600;color:var(--text)}
.bm-metric{display:flex;flex-direction:column;gap:.125rem;font-weight:600}
.bm-dir{font-size:.875rem;font-weight:400;color:var(--muted)}
.bm-gate{white-space:nowrap}
.bm-bar{min-width:9rem}
.bm-bar .barpair{display:block}
.bm-evidence{margin-top:1.25rem;padding:1rem 1.25rem;background:var(--surface);
  border:1px solid var(--line);border-radius:var(--radius);font-size:.9375rem;line-height:1.7}
.bm-evidence ul{margin:.375rem 0 0;padding-left:1.25rem}
.bm-evidence p{margin:.5rem 0 0;color:var(--muted)}
"""

NO_SPEC = f"未提供 {bi('spec')} {bi('JSON')}"
MARK = {"pass": "✓", "partial": "!", "fail": "✕", "skip": "–"}
METRIC_NAME = {"ttft_s": "TTFT", "latency_s": "Latency", "tpot_ms": "TPOT", "output_tps": "Output TPS"}
RATIO_BASE = {"ttft": "ttft_s", "latency": "latency_s", "tpot": "tpot_ms", "output_tps": "output_tps"}
CACHE_KEY = {"identical_20k_pct": "cache_identical_20k", "multiturn_12k_pct": "cache_multiturn_12k",
             "short_1k_pct": "cache_short_1k"}
CACHE_LABEL = {v: k for k, v in CACHE_KEY.items()}


# --------------------------------------------------------------------------- shared bits

def _safe_rows(spec_doc, profile):
    try:
        return build_rows(spec_doc, profile)
    except Exception:  # noqa: BLE001  (report rendering must never fail on odd profile/spec shapes)
        return []


def _index(rows):
    return {(r.get("group"), r.get("metric"), r.get("pctl")): r for r in rows}


def _status_pill(st, text=None):
    st = status_of(st)
    return pill(st, f"{MARK[st]} {text or CORE_TEXT[st]}")


def _ov_row(row, measured=None):
    """``官方 / 实测`` cell of a benchmark row (numbers only)."""
    if not row:
        return ov(None, measured) if measured is not None else ""
    return ov(row.get("official"), row.get("measured") if row.get("measured") is not None else measured,
              row.get("unit", ""))


def _pq(q):
    return lab(str(q).upper())


def _metric_label(row):
    if row.get("group") == "cache":
        return lab(CACHE_LABEL.get(row.get("metric"), row.get("metric")))
    return lab(METRIC_NAME.get(row.get("metric"), row.get("metric")))


# --------------------------------------------------------------------------- key findings (L1, above the cards)

def _clip(v, n=48):
    t = str(v or "").strip()
    return t if len(t) <= n else t[:n].rstrip() + "…"


def render_findings(audit, spec_doc, profile=None):
    """At most 3 problems, most severe first, each linking to its section; one calm line when nothing is wrong."""
    cands = []  # (severity, pill_status, pill_text, text_html, anchor)
    audit = audit if isinstance(audit, dict) else {}
    if audit.get("hard_fail"):
        cands.append((0, "fail", "✕ 硬性失败", lab(audit.get("hard_fail")), "06"))
    if audit.get("usage_inflated") is True:
        cands.append((1, "fail", "✕ 用量虚高", lab("usage_inflated"), "06"))
    items = items_of(spec_doc)
    for it in items:
        if it.get("id") in (1, 2, 3) or it.get("key") in ("perf_baseline", "ttft", "cache_hit"):
            continue  # perf items: shown as numbers only, no pass/fail
        st = it.get("status")
        if st in ("fail", "partial"):
            note = it.get("note") or ""
            title = it.get("title") or it.get("key") or "未命名项"
            body = txt(_clip(title, 40)) + (f" — {txt(_clip(note, 36))}" if note and st == "fail" else "")
            cands.append((1 if st == "fail" else 3, st, "✕ 未通过" if st == "fail" else "! 部分通过", body, "02"))
    load = dig(spec_doc, "load", default={}) or {}
    sr = load.get("success_rate")
    if is_num(sr) and float(sr) < 100.0:
        cands.append((2, "fail", "✕ 请求失败", f"请求成功率 {val_html(sr, '%')}", "03"))
    empty = dig(load, "finish", "empty")
    if is_num(empty) and empty > 0:
        cands.append((2, "fail", "✕ 空响应", f"出现 {fmt(empty)} 次空响应", "03"))
    cands.sort(key=lambda c: c[0])
    rows = []
    for _, st, ptxt, body, anchor in cands[:3]:
        rows.append(f'<li><a href="#sec-{anchor}">{pill(st, ptxt)}<span>{body}</span></a></li>')
    rest = len(cands) - 3
    if rest > 0:
        rows.append(f'<li class="more"><a href="#sec-02"><span>另有 {fmt(rest)} 项，见各章节</span></a></li>')
    if not cands:
        text = NO_SPEC if spec_doc is None else "未发现异常：规范、基准与请求成功率均无需处理"
        rows.append(f'<li class="ok"><span>{text}</span></li>')
    return ('<section class="findings" aria-label="关键发现"><h2 class="findings-title">关键发现</h2>'
            f'<ol>{"".join(rows)}</ol></section>')


# --------------------------------------------------------------------------- section 01

def ratio_groups(item):
    groups = {"p50": [], "p90": [], "p95": []}
    measured = dig(item, "measured", default={}) or {}
    if isinstance(measured, dict):
        for k, v in measured.items():
            if is_num(v) and k.endswith("_ratio"):
                for q in groups:
                    if f"_{q}_" in k:
                        groups[q].append((k, float(v)))
    return groups


def _ratio_key_metric(key):
    """'ttft_p90_ratio' -> 'ttft_s' (None when the base is unknown)."""
    base = key[:-6] if key.endswith("_ratio") else key
    for q in ("p50", "p90", "p95"):
        base = base.replace(f"_{q}", "")
    return RATIO_BASE.get(base)


def core_card(cls, title, sub_html, value_html_str, status_html, ov_html, ov_label, mini, target_html, delta_html):
    """Slim L2 card: title, big value, optional status pill, one 官方 / 实测 line.
    ``sub_html`` / ``mini`` / ``target_html`` / ``delta_html`` are accepted for call-site stability but not rendered."""
    ovline = f'<div class="kpi-ovline">官方 / 实测 {ov_html}</div>' if ov_html else ""
    status = f'<div class="kpi-status">{status_html}</div>' if status_html else ""
    return f"""
<article class="kpi {esc_raw(cls)}">
  <h3 class="kpi-title">{title}</h3>
  <div class="kpi-value">{value_html_str}</div>
  {status}
  {ovline}
</article>"""


def render_core(spec_doc, profile=None):
    items = items_of(spec_doc)
    item1 = find_item(items, ident=1, key="perf_baseline")
    item2 = find_item(items, ident=2, key="ttft")
    item3 = find_item(items, ident=3, key="cache_hit")
    load = dig(spec_doc, "load", default={}) or {}
    idx = _index(_safe_rows(spec_doc, profile))

    cards = []

    # 01 baseline ratios
    if item1:
        groups = ratio_groups(item1)
        worst = {q: max((v for _, v in pairs), default=None) for q, pairs in groups.items()}
        main_q = "p90" if worst.get("p90") is not None else "p50"
        main = worst.get(main_q)
        value = val_html(main, "×") if main is not None else "未测"
        expected = dig(item1, "expected", default={}) or {}
        tbits = []
        if is_num(expected.get("p50_ratio_max")):
            tbits.append(f"{_pq('p50')} ≤ {esc(expected.get('p50_ratio_max'))}×")
        if is_num(expected.get("p90_ratio_max")):
            tbits.append(f"{_pq('p90')} ≤ {esc(expected.get('p90_ratio_max'))}×")
        if is_num(expected.get("output_tps_p50_ratio_min")):
            tbits.append(f"{lab('TPS')} ≥ {esc(expected.get('output_tps_p50_ratio_min'))}×")
        target = "；".join(tbits) if tbits else "未测"
        delta = dig(item1, "delta", default={}) or {}
        dbits = []
        if is_num(delta.get("p50_margin")):
            dbits.append(f"{_pq('p50')} 余量 {esc(delta.get('p50_margin'))}×")
        if is_num(delta.get("p90_margin")):
            dbits.append(f"{_pq('p90')} 余量 {esc(delta.get('p90_margin'))}×")
        delta_html = "；".join(dbits) if dbits else "未测"
        mini = "".join(
            f"<span>{_pq(q)} <b>{val_html(worst.get(q), '×') if worst.get(q) is not None else '未测'}</b></span>"
            for q in ("p50", "p90", "p95"))
        ov_html, ov_label = "", ""
        if main is not None:
            key = next((k for k, v in groups[main_q] if v == main), "")
            metric = _ratio_key_metric(key)
            row = idx.get(("perf", metric, main_q)) if metric else None
            if row:
                ov_html = _ov_row(row)
                ov_label = f"{lab(METRIC_NAME.get(metric, metric))} {_pq(main_q)}"
        st = status_of(item1.get("status"))
        cards.append(core_card(
            "skip", "官方基准对标", f"实测 ÷ 官方 · 最差 {_pq(main_q)} 比率",
            value, "", ov_html, ov_label, mini, target, delta_html))
    else:
        cards.append(core_card(
            "skip", "官方基准对标", f"实测 ÷ 官方 · {_pq('p50')} / {_pq('p90')} / {_pq('p95')}",
            "未测", "", "", "",
            f"<span>{_pq('p50')} <b>未测</b></span><span>{_pq('p90')} <b>未测</b></span>"
            f"<span>{_pq('p95')} <b>未测</b></span>", "未测", "未测"))

    # 02 TTFT P90
    ttft_title = f"{lab('TTFT')} {_pq('p90')}"
    ttft_sub = lab("time to first token")
    if item2:
        measured = dig(item2, "measured", default={}) or {}
        expected = dig(item2, "expected", default={}) or {}
        p90 = measured.get("p90_s")
        value = val_html(p90, "s") if is_num(p90) else "未测"
        target = f"≤ {esc(expected.get('p90_max_s'))} s" if is_num(expected.get("p90_max_s")) else "未测"
        margin = dig(item2, "delta", "margin_s")
        delta_html = f"余量 {esc(margin)} s" if is_num(margin) else "未测"
        st = status_of(item2.get("status"))
        p95 = measured.get("p95_s")
        mini = f"<span>{_pq('p95')} <b>{val_html(p95, 's') if is_num(p95) else '未测'}</b></span>"
        row = idx.get(("ttft", "ttft_s", "p90"))
        cards.append(core_card(
            "skip", ttft_title, ttft_sub, value, "", _ov_row(row, p90 if is_num(p90) else None),
            f"{lab('TTFT')} {_pq('p90')}", mini, target, delta_html))
    else:
        cards.append(core_card("skip", ttft_title, ttft_sub, "未测", "", "", "",
                               f"<span>{_pq('p95')} <b>未测</b></span>", "未测", "未测"))

    # 03 cache hit
    cache_sub = f"{lab('KV cache')} · 三类场景最低值"
    if item3:
        measured = dig(item3, "measured", default={}) or {}
        expected = dig(item3, "expected", default={}) or {}
        pairs = [(k, v) for k, v in measured.items() if is_num(v) and "pct" in k]
        low = min(pairs, key=lambda kv: kv[1]) if pairs else (None, None)
        value = val_html(low[1], "%") if low[1] is not None else "未测"
        target = f"≥ {esc(expected.get('min_pct'))} %" if is_num(expected.get("min_pct")) else "未测"
        margin = dig(item3, "delta", "margin_pct")
        delta_html = f"余量 {esc(margin)} pp" if is_num(margin) else "未测"
        mini = "".join(f"<span>{lab(k)} <b>{val_html(v, '%')}</b></span>" for k, v in pairs) or "未测"
        st = status_of(item3.get("status"))
        row = idx.get(("cache", CACHE_KEY.get(low[0]), None)) if low[0] else None
        ov_html = _ov_row(row, low[1]) if (row or low[1] is not None) else ""
        ov_label = lab(low[0]) if low[0] else ""
        cards.append(core_card("skip", "缓存命中", cache_sub, value, "", ov_html, ov_label,
                               mini, target, delta_html))
    else:
        cards.append(core_card("skip", "缓存命中", cache_sub, "未测", "", "", "",
                               "未测", "未测", "未测"))

    # 04 request success rate
    sr = load.get("success_rate")
    if is_num(sr):
        if float(sr) == 100.0:
            st = "pass"
        elif float(sr) >= 95.0:
            st = "partial"
        else:
            st = "fail"
        value, status_html = val_html(sr, "%"), _status_pill(st)
    else:
        value, status_html, st = "未测", _status_pill("skip", "未测"), "skip"
    empty = dig(load, "finish", "empty")
    mini = (f"<span>成功 <b>{esc(load.get('success'))}</b> / 样本 <b>{esc(load.get('samples'))}</b></span>"
            + (f"<span>{lab('finish')} {lab('empty')} <b>{esc(empty)}</b></span>" if is_num(empty) else ""))
    cards.append(core_card(st, "请求成功率", f"{lab('Request success rate')} · 负载样本", value, status_html,
                           "", "", mini, f"= {fmt(100.0)} %", "未测"))

    # 05 TPOT P90 (display only, no gate)
    node = dig(load, "percentiles", "tpot_ms", default={}) or {}
    p90 = node.get("p90")
    value = val_html(p90, "ms") if is_num(p90) else "未测"
    mini = f"<span>{_pq('p50')} <b>{val_html(node.get('p50'), 'ms') if is_num(node.get('p50')) else '未测'}</b></span>"
    if is_num(node.get("p95")):
        mini += f"<span>{_pq('p95')} <b>{val_html(node.get('p95'), 'ms')}</b></span>"
    row = idx.get(("perf", "tpot_ms", "p90"))
    cards.append(core_card("skip", f"{lab('TPOT')} {_pq('p90')}", lab("time per output token"), value,
                           "",
                           _ov_row(row, p90 if is_num(p90) else None) if (row or is_num(p90)) else "",
                           f"{lab('TPOT')} {_pq('p90')}", mini, "", ""))

    note = NO_SPEC if spec_doc is None else "核心指标 · 5 项"
    return section("01", "核心标准", note) + '<section class="kpi-grid">' + "".join(cards) + "</section>"


# --------------------------------------------------------------------------- section 02

def _metrics_summary(load):
    pct = load.get("percentiles") or {}
    bits = []
    ttft = (pct.get("ttft_s") or {}).get("p90")
    if is_num(ttft):
        bits.append(f"{_pq('p90')} {lab('TTFT')} {fmt(ttft)} s")
    rpm = (load.get("throughput") or {}).get("rpm")
    if is_num(rpm):
        bits.append(f"{lab('RPM')} {fmt(rpm)}")
    return " · ".join(bits) or "分位数 · 吞吐 · token 合计"


def render_metrics(spec_doc, profile=None):
    from html_sec_spec import render_totals  # local import: spec module is a sibling, no cycle at load time
    load = dig(spec_doc, "load", default={}) or {}
    note = NO_SPEC if spec_doc is None else _metrics_summary(load)
    out = [fold_start("03", "全部指标", note)]
    if not load:
        out.append(empty_panel(NO_SPEC, "负载分位数与吞吐指标无法展示。"))
        out.append(FOLD_END)
        return "".join(out)

    pct = load.get("percentiles") or {}
    rows = []
    for key, label, en, unit in (
        ("latency_s", "Latency", "total wall time", "s"),
        ("ttft_s", "TTFT", "time to first token", "s"),
        ("tpot_ms", "TPOT", "time per output token", "ms"),
        ("output_tps", "Output TPS", "tokens per second", "tok/s"),
    ):
        node = pct.get(key) or {}
        cells = [f'<span class="mt-name">{lab(label)}</span>']
        for q in ("p50", "p90", "p95"):
            v = node.get(q)
            cells.append(val_html(v, unit) if is_num(v) else "未测")
        cells.append(fmt(node.get("n")) if is_num(node.get("n")) else "未测")
        rows.append(cells)
    out.append(table(["指标", bi("P50"), bi("P90"), bi("P95"), "样本数 n"], rows,
                     cls="table sc-quant", aligns=["", "r", "r", "r", "r"],
                     caption="延迟与吞吐分位数"))

    throughput = load.get("throughput") or {}
    finish = load.get("finish") or {}
    tiles = [
        (lab("RPM"), throughput.get("rpm"), "req/min"),
        (f"总 {lab('TPM')}", throughput.get("tpm"), "tok/min"),
        (lab("input_tpm"), throughput.get("input_tpm"), "tok/min"),
        (lab("output_tpm"), throughput.get("output_tpm"), "tok/min"),
    ]
    out.append('<div class="mt-grid">')
    for label_html, v, unit in tiles:
        out.append(f'<div class="mt-tile"><div class="mt-label">{label_html}</div>'
                   f'<div class="mt-value">{val_html(v, unit) if is_num(v) else "未测"}</div></div>')
    out.append("</div>")

    # finish_reason: one stacked bar + one legend line (counts and shares as text)
    fkeys = ("stop", "length", "tool_calls", "empty", "other")
    vals = {k: finish.get(k) for k in fkeys if is_num(finish.get(k))}
    total = sum(vals.values())
    out.append(f'<div class="sc-subhead">{lab("finish_reason")} 分布</div>')
    if total:
        segs, legend = [], []
        for key in fkeys:
            v = vals.get(key)
            if not v:
                continue
            warn = " warn" if key == "empty" else ""
            segs.append(f'<span class="fr-seg s-{key}{warn}" style="width:{float(v) / total * 100.0:.1f}%"></span>')
            legend.append(f'<span class="fr-leg"><i class="fr-dot s-{key}{warn}"></i>{lab(key)} '
                          f'<b>{fmt(v)}</b> <small>（{fmt_num(float(v) / total * 100.0)}%）</small></span>')
        out.append(f'<div class="fr-stack" role="img" aria-label="{esc_raw(bi("finish_reason"))} 分布">'
                   f'{"".join(segs)}</div><div class="fr-legend">{"".join(legend)}</div>')
    else:
        out.append('<p class="bm-note">未测</p>')
    out.append(render_totals(spec_doc, profile))
    out.append(FOLD_END)
    return "".join(out)


# --------------------------------------------------------------------------- section 03

GROUPS = (
    ("perf", "性能分位"),
    ("ttft", f"{bi('TTFT')} 首字延迟"),
    ("cache", "缓存命中率"),
)


def _direction_html(row):
    return "↓ 越低越好" if row.get("direction") == "lower" else "↑ 越高越好"


def _delta_plain(delta_pct):
    """Signed deviation as plain numbers: ▲ +24.80% / ▼ -3.10% / ■ 0.00% / — (missing)."""
    if not is_num(delta_pct):
        return '<span class="na">—</span>'
    d = float(delta_pct)
    arrow = "▲" if d > 0 else ("▼" if d < 0 else "■")
    sign = "+" if d > 0 else ""
    return f'<span class="dlt">{arrow} {sign}{fmt_num(d)}%</span>'


def _bm_groups(rows):
    headers = ["指标", "分位", "官方 / 实测", "偏差"]
    aligns = ["", "", "r", ""]
    out = []
    for gkey, gtitle in GROUPS:
        grows = [r for r in rows if r.get("group") == gkey]
        if not grows:
            continue
        body = []
        for r in grows:
            name = _metric_label(r)
            pctl = r.get("pctl")
            body.append([
                f'<span class="bm-metric">{name}<span class="bm-dir">{esc_raw(_direction_html(r))}</span></span>',
                _pq(pctl) if pctl else '<span class="na">—</span>',
                ov(r.get("official"), r.get("measured"), r.get("unit", "")),
                _delta_plain(r.get("delta_pct")),
            ])
        out.append(f'<div class="bm-group">{table(headers, body, cls="table bm-table", aligns=aligns, caption=gtitle)}</div>')
    return "".join(out)


def render_baseline(spec_doc, profile=None):
    date = baseline_source(profile)
    has_profile = isinstance(profile, dict)
    item1 = find_item(items_of(spec_doc), ident=1, key="perf_baseline")
    if spec_doc is None:
        return (fold_start("04", "官方基准对标", NO_SPEC, open_=True)
                + empty_panel(NO_SPEC, "缺少规范审计结果，无法展示官方基准对标。") + FOLD_END)

    rows = [r for r in _safe_rows(spec_doc, profile)
            if r.get("official") is not None or r.get("measured") is not None]
    if not rows:
        return (fold_start("04", "官方基准对标", "无可对标数据", open_=True)
                + empty_panel("无可对标数据", f"{bi('spec')} {bi('JSON')} 中没有负载分位数或缓存命中率，且未提供官方画像。")
                + FOLD_END)

    if not has_profile:
        note = "官方基准未提供，仅展示实测"
    else:
        note = f"{fmt(len(rows))} 项对标" + (f" · 基准存档 {date}" if date else "")
    out = [fold_start("04", "官方基准对标", note, open_=False)]
    out.append('<p class="bm-note">偏差 = （实测 − 官方）÷ 官方；延迟类越低越好，吞吐与缓存类越高越好。'
               + ("" if has_profile else f" 未指定 {bi('profile')} 官方画像文件，官方列显示「未提供」。")
               + "</p>")
    out.append(_bm_groups(rows))

    evidence = dig(item1, "evidence", default=[]) or []
    note_txt = dig(item1, "note", default="")
    if evidence or note_txt:
        body = ""
        if evidence:
            body += "<ul>" + "".join(f"<li>{txt(line)}</li>" for line in evidence) + "</ul>"
        if note_txt:
            body += f"<p>{txt(note_txt)}</p>"
        out.append(f'<details class="bl-rounds"><summary>证据原文</summary><div class="bm-evidence">{body}</div></details>')
    out.append(FOLD_END)
    return "".join(out)
