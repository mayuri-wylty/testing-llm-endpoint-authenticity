#!/usr/bin/env python3
"""Render a single-file HTML model-conformance report from spec_audit / model_audit JSON.

Usage:
  python scripts/report_html.py --audit <path> [--spec <path>] --out <path> [--title TEXT]

The report is fully self-contained: inline CSS, no JavaScript, no external resources.
Every dynamic string is HTML-escaped; all numbers come straight from the JSON inputs
(only pass counts and bar widths are derived for presentation).
"""
import argparse
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import scrub  # noqa: E402

SK_RE = re.compile(r"sk-[A-Za-z0-9_-]{8,}")
STATUS_TEXT = {"pass": "通过", "partial": "部分通过", "fail": "未通过", "skip": "跳过"}
CORE_TEXT = {"pass": "达标", "partial": "部分达标", "fail": "未达标", "skip": "跳过"}


# --------------------------------------------------------------------------- data helpers

def load_json(path, key):
    """Read JSON text, scrub the API key, mask sk-* tokens, then parse."""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    text = scrub(text, key)
    text = SK_RE.sub("sk-***", text)
    return json.loads(text)


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def fmt(v):
    """Human number formatting with thousands separators; missing data is never zero-filled."""
    if v is None:
        return "未测"
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        return format(v, ",")
    s = str(v).strip()
    return s if s else "未测"


def esc(v):
    return html.escape(fmt(v), quote=True)


def esc_raw(v):
    """Escape a plain string (used for values that may legitimately be empty text)."""
    return html.escape("" if v is None else str(v), quote=True)


def val_html(v, unit=""):
    """Value presentation: numeric + unit suffix, otherwise escaped text."""
    if is_num(v):
        suffix = f'<small>{esc_raw(unit)}</small>' if unit else ""
        return f"{fmt(v)}{suffix}"
    return esc(v)


def dig(obj, *path, default=None):
    cur = obj
    for k in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return default if cur is None else cur


def items_of(spec_doc):
    v = dig(spec_doc, "spec", "items", default=[])
    return v if isinstance(v, list) else []


def find_item(items, ident=None, key=None):
    for it in items:
        if ident is not None and it.get("id") == ident:
            return it
    if key is not None:
        for it in items:
            if it.get("key") == key:
                return it
    return None


def pretty(v):
    """Text for a details block; dict/list are pretty-printed JSON."""
    if v is None or v == "":
        return "未测"
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False, indent=1)


def status_of(v):
    return v if v in STATUS_TEXT else "skip"


def pill(status, text=None):
    st = status_of(status)
    return f'<span class="pill {st}">{esc_raw(text or STATUS_TEXT[st])}</span>'


def status_label(v):
    return STATUS_TEXT.get(status_of(v))


def perf_flags(spec_doc):
    """Five explicit booleans from the report contract; None when no spec was supplied."""
    if spec_doc is None:
        return None
    items = items_of(spec_doc)
    flags = []
    for ident in (1, 2, 3):
        it = find_item(items, ident=ident)
        flags.append(bool(it and it.get("status") == "pass"))
    load = dig(spec_doc, "load", default={}) or {}
    sr = load.get("success_rate")
    flags.append(is_num(sr) and float(sr) == 100.0)
    empty = dig(load, "finish", "empty")
    flags.append(is_num(empty) and empty == 0)
    return sum(1 for f in flags if f)


def verdict_class(verdict):
    s = (verdict or "").lower()
    if "highly" in s or "credible" in s or "plausible" in s:
        return "green"
    if "suspect" in s or "mixed" in s or "partial" in s or "doubt" in s:
        return "amber"
    if "fake" in s or "fail" in s or "implausible" in s:
        return "red"
    return ""


def empty_panel(msg="未提供 spec JSON", detail=""):
    extra = f'<span class="empty-detail">{esc_raw(detail)}</span>' if detail else ""
    return f'<div class="empty"><b>{esc_raw(msg)}</b>{extra}</div>'


def section(index, title, note=""):
    return (
        '<div class="section-heading"><div class="heading-left">'
        f'<span class="section-index">{esc_raw(index)}</span><h2>{esc_raw(title)}</h2>'
        f'</div><span class="section-note">{esc_raw(note)}</span></div>'
    )


def table(headers, rows, cls="table", aligns=None):
    aligns = aligns or []
    head = []
    for i, h in enumerate(headers):
        al = aligns[i] if i < len(aligns) else ""
        head.append(f'<th class="{al}">{esc_raw(h)}</th>')
    body = "".join(
        "<tr>" + "".join(
            f'<td class="{aligns[i] if i < len(aligns) else ""}">{cell}</td>'
            for i, cell in enumerate(row)
        ) + "</tr>"
        for row in rows
    )
    return f'<table class="{esc_raw(cls)}"><thead><tr>{"".join(head)}</tr></thead><tbody>{body}</tbody></table>'


# --------------------------------------------------------------------------- CSS

CSS = r"""
:root{
  --bg:#f4f4f4; --text:#161616; --blue:#0f62fe; --green:#198038; --red:#da1e28;
  --line:#e0e0e0; --dark:#262626; --g700:#525252; --g600:#6f6f6f; --g500:#8d8d8d; --g300:#c6c6c6;
  --amber:#8e6a00; --amber-bg:#fcf4d6; --amber-line:#f1c21b;
  --font:"IBM Plex Sans","Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0;background:var(--bg);color:var(--text);font-family:var(--font);
  font-variant-numeric:tabular-nums;-webkit-font-smoothing:antialiased}
h1,h2,h3,h4,p,ul,dl,dd{margin:0}
ul{padding-left:17px}
.shell{max-width:1440px;margin:0 auto}
.masthead{background:var(--dark);color:#fff;border-top:4px solid var(--blue)}
.mast-inner{max-width:1440px;margin:0 auto;padding:14px 40px;display:flex;justify-content:space-between;
  align-items:center;gap:24px}
.wordmark{font-size:19px;font-weight:300;letter-spacing:.2px}
.wordmark strong{font-weight:600}
.masthead-label{font-size:11px;letter-spacing:2.6px;color:#e0e0e0;text-align:right}
.masthead-meta{font-size:11px;color:#a8a8a8;margin-top:4px;text-align:right;line-height:1.5}
.page{max-width:1440px;margin:0 auto;padding:34px 40px 28px}
.hero{display:grid;grid-template-columns:minmax(0,1fr) 470px;gap:40px;align-items:stretch;margin-bottom:30px}
.eyebrow{font-size:11px;letter-spacing:1.9px;color:var(--g600);text-transform:uppercase}
.hero h1{font-size:42px;font-weight:300;line-height:1.22;letter-spacing:-.6px;margin-top:8px}
.model-line{display:flex;flex-wrap:wrap;gap:13px;align-items:center;margin-top:15px;font-size:14px;color:var(--g700)}
.model{font-size:22px;font-weight:500;color:var(--text);letter-spacing:-.2px}
.divider{width:1px;height:17px;background:var(--g300)}
.chips{display:flex;flex-wrap:wrap;gap:7px;margin-top:16px}
.chip{background:#fff;border:1px solid var(--line);color:var(--g600);font-size:11px;padding:4px 9px;line-height:1.5}
.chip b{color:var(--text);font-weight:500;margin-left:5px}
.scoreboard{background:#fff;border-top:3px solid var(--blue);display:grid;grid-template-columns:1fr 1fr}
.score{padding:17px 19px 14px;border-right:1px solid var(--line);border-bottom:1px solid var(--line)}
.score:nth-child(2n){border-right:0}
.score:nth-child(n+3){border-bottom:0}
.score-label{font-size:11px;color:var(--g600);letter-spacing:.4px}
.score-value{font-size:37px;font-weight:300;line-height:1.1;margin-top:7px;letter-spacing:-1px}
.score-value small{font-size:13px;color:var(--g600);margin-left:5px;letter-spacing:0}
.score-caption{font-size:10px;color:var(--g600);margin-top:6px;line-height:1.45}
.score-caption.green{color:var(--green)}
.score-caption.amber{color:var(--amber)}
.score-caption.red{color:var(--red)}
.section-heading{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;
  margin:28px 0 13px;padding-bottom:9px;border-bottom:1px solid var(--g300)}
.heading-left{display:flex;align-items:center;gap:13px}
.section-index{font-size:11px;letter-spacing:1.4px;color:var(--blue);font-weight:500}
.section-heading h2{font-size:23px;font-weight:400;line-height:1.2}
.section-note{font-size:11px;color:var(--g600);text-align:right}
.core-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:1px;
  background:var(--line);border:1px solid var(--line)}
.core{background:#fff;padding:17px 17px 13px;position:relative;min-height:246px}
.core:before{content:"";position:absolute;left:0;right:0;top:0;height:3px;background:var(--g300)}
.core.pass:before{background:var(--green)}
.core.partial:before{background:var(--amber-line)}
.core.fail:before{background:var(--red)}
.core.skip:before{background:var(--g500)}
.core-title{font-size:16px;font-weight:500;line-height:1.3}
.core-en{font-size:10px;color:var(--g500);margin-top:4px;line-height:1.35;min-height:26px}
.core-value{font-size:38px;font-weight:300;letter-spacing:-1.1px;margin:10px 0 5px;line-height:1.1;white-space:nowrap}
.core-value small{font-size:18px;margin-left:4px;letter-spacing:0}
.core.fail .core-value{color:var(--red)}
.core.partial .core-value{color:var(--amber)}
.core-status{display:flex;align-items:center;gap:6px;font-size:12px;font-weight:500}
.core.pass .core-status{color:var(--green)}
.core.partial .core-status{color:var(--amber)}
.core.fail .core-status{color:var(--red)}
.core.skip .core-status{color:var(--g600)}
.dot{width:7px;height:7px;border-radius:50%;background:currentColor;display:inline-block;flex:none}
.core-mini{display:flex;flex-wrap:wrap;gap:3px 9px;font-size:10px;color:var(--g600);margin-top:7px;min-height:15px}
.core-mini b{color:var(--text);font-weight:500}
.core-target{font-size:10px;color:var(--g600);border-top:1px solid var(--line);padding-top:7px;margin-top:8px;line-height:1.55}
.core-delta{font-size:10px;color:var(--g600);margin-top:2px;line-height:1.55}
.quant-table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);font-size:12px}
.quant-table th,.quant-table td{text-align:right;padding:9px 13px;border-bottom:1px solid var(--line)}
.quant-table th{font-size:10px;font-weight:500;color:var(--g600);letter-spacing:.5px;text-transform:uppercase;
  border-bottom:1px solid var(--g300)}
.quant-table td:first-child,.quant-table th:first-child{text-align:left}
.quant-table tr:last-child td{border-bottom:0}
.metric-name{font-weight:500;font-size:13px}
.metric-en{font-size:9px;color:var(--g500);font-weight:400;margin-left:6px;letter-spacing:.2px}
.quant-table small{font-size:9px;color:var(--g600);margin-left:2px}
.data-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1px;
  background:var(--line);border:1px solid var(--line);margin-top:13px}
.metric{background:#fff;padding:13px 16px;min-height:82px}
.metric-label{font-size:10px;color:var(--g600);letter-spacing:.2px}
.metric-value{font-size:23px;font-weight:500;letter-spacing:-.4px;margin-top:6px;white-space:nowrap;line-height:1.1}
.metric-value small{font-size:11px;font-weight:400;color:var(--g700);margin-left:3px}
.metric-note{font-size:9px;color:var(--g500);margin-top:3px}
.subhead{display:flex;align-items:baseline;gap:10px;margin:18px 0 0;font-size:12px;font-weight:500}
.subhead span{font-size:10px;color:var(--g500);font-weight:400}
.bars{background:#fff;border:1px solid var(--line);padding:6px 15px 9px;margin-top:10px}
.bar-row{display:grid;grid-template-columns:135px minmax(0,1fr) 95px;gap:12px;align-items:center;padding:5px 0}
.bar-label{font-size:11px;color:var(--g700);white-space:nowrap}
.bar-track{height:9px;background:#e8e8e8;overflow:hidden}
.bar-fill{height:100%;background:var(--blue)}
.bar-fill.warn{background:var(--red)}
.bar-value{font-size:11px;text-align:right;font-weight:500}
.totals{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1px;background:var(--g700);margin-top:16px}
.total{background:var(--dark);color:#fff;padding:15px 19px}
.total .metric-label{color:var(--g300);font-size:10px}
.total .metric-value{font-size:25px;font-weight:300;margin-top:7px}
.total .metric-value small{color:#c6c6c6}
.baseline-layout{display:grid;grid-template-columns:minmax(0,1.06fr) minmax(0,.94fr);gap:20px;align-items:start}
.table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);font-size:11px}
.table th,.table td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
.table th{background:#f7f7f7;color:var(--g600);font-weight:500;font-size:9px;letter-spacing:.6px;text-transform:uppercase}
.table tr:last-child td{border-bottom:0}
.table .r{text-align:right}
.table .c{text-align:center}
.chart{background:#fff;border:1px solid var(--line);padding:14px 16px}
.chart-title{font-size:13px;font-weight:500}
.chart-sub{font-size:10px;color:var(--g600);margin:3px 0 9px;line-height:1.5}
.cbar-row{display:grid;grid-template-columns:96px minmax(0,1fr) 54px;gap:9px;align-items:center;padding:4px 0}
.cbar-label{font-size:10px;color:var(--g700);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cbar-track{height:11px;background:#f0f0f0;position:relative}
.cbar-fill{height:100%;background:var(--green)}
.cbar-fill.bad{background:var(--red)}
.cbar-fill.na{background:var(--g500)}
.cbar-marker{position:absolute;top:-3px;bottom:-3px;width:1px;background:var(--text)}
.cbar-val{font-size:10px;text-align:right;font-weight:500}
.chart-legend{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:9px;color:var(--g600);
  margin-top:9px;border-top:1px solid var(--line);padding-top:7px;line-height:1.6}
.chart-evidence{margin-top:10px;font-size:10px;color:var(--g700);line-height:1.65}
.chart-evidence b{font-weight:500}
.chart-evidence ul{margin-top:3px}
.baseline-note{font-size:10px;color:var(--g600);margin:0 0 9px;line-height:1.6}
.spec-overview{display:grid;grid-template-columns:repeat(4,minmax(0,1fr)) 1.3fr;gap:1px;
  background:var(--line);border:1px solid var(--line);margin-bottom:12px}
.spec-overview>div{background:#fff;padding:10px 14px;display:flex;align-items:baseline;justify-content:space-between;gap:8px}
.spec-overview span{font-size:10px;color:var(--g600)}
.spec-overview b{font-size:19px;font-weight:500}
.spec-overview .spec-score-cell{background:var(--dark);color:#fff}
.spec-overview .spec-score-cell span{color:var(--g300)}
.spec-list{border:1px solid var(--line);background:#fff}
.spec-item{border-bottom:1px solid var(--line)}
.spec-item:last-child{border-bottom:0}
.spec-item summary{display:grid;grid-template-columns:38px minmax(0,1fr) 72px 84px;gap:12px;
  align-items:center;padding:11px 15px;cursor:pointer;list-style:none}
.spec-item summary::-webkit-details-marker{display:none}
.spec-item summary:hover{background:#f7f7f7}
.spec-idx{font-size:10px;color:var(--g500);letter-spacing:.6px}
.spec-main{min-width:0;display:flex;align-items:baseline;gap:10px;flex-wrap:nowrap}
.spec-title{font-size:13px;font-weight:500;white-space:nowrap}
.spec-title:before{content:"+ ";color:var(--g500);font-weight:400}
.spec-item[open] .spec-title:before{content:"− "}
.spec-note{font-size:10px;color:var(--g600);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.spec-weight{font-size:9px;color:var(--g500);text-align:right;white-space:nowrap}
.spec-body{padding:3px 15px 15px 65px;background:#fafafa;border-top:1px solid var(--line)}
.kv-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:11px;margin-top:9px}
.kv h4,.evidence h4{font-size:9px;letter-spacing:.6px;color:var(--g600);text-transform:uppercase;margin-bottom:4px}
pre{background:#fff;border:1px solid var(--line);padding:7px 9px;font-size:10px;line-height:1.55;
  white-space:pre-wrap;word-break:break-word;margin:0;max-height:250px;overflow:auto;font-family:var(--font)}
.evidence{margin-top:11px}
.evidence ul{font-size:11px;color:var(--g700);line-height:1.65}
.note-line{font-size:11px;color:var(--g700);line-height:1.65;margin-top:9px}
.note-line b{color:var(--text);font-weight:500}
.pill{display:inline-flex;align-items:center;justify-content:center;font-size:9px;font-weight:500;
  padding:2px 7px;letter-spacing:.3px;white-space:nowrap;border:1px solid transparent}
.pill.pass{color:var(--green);background:#defbe6;border-color:#a7f0ba}
.pill.partial{color:var(--amber);background:var(--amber-bg);border-color:var(--amber-line)}
.pill.fail{color:var(--red);background:#fff1f1;border-color:#ffb3b8}
.pill.skip{color:var(--g600);background:#f4f4f4;border-color:var(--line)}
.empty{background:#fff;border:1px solid var(--line);border-left:3px solid var(--g500);
  padding:16px 18px;font-size:12px;color:var(--g700);line-height:1.6}
.empty b{color:var(--text)}
.empty-detail{display:block;color:var(--g600);font-size:11px;margin-top:3px}
.ttl-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:13px}
.ttl-card{background:#fff;border:1px solid var(--line);border-top:3px solid var(--green);padding:13px 15px}
.ttl-card.na{border-top-color:var(--g500)}
.ttl-head{display:flex;justify-content:space-between;align-items:center;gap:10px}
.ttl-head h3{font-size:14px;font-weight:500}
.dl{display:grid;grid-template-columns:120px minmax(0,1fr);gap:5px 11px;margin-top:9px;font-size:11px}
.dl dt{color:var(--g600)}
.dl dd{font-weight:500;overflow-wrap:anywhere}
.rounds{margin-top:10px;border:1px solid var(--line);background:#fff}
.rounds summary{padding:8px 12px;font-size:11px;color:var(--g700);cursor:pointer;list-style:none}
.rounds summary::-webkit-details-marker{display:none}
.rounds summary:before{content:"+ ";color:var(--g500)}
.rounds[open] summary:before{content:"− "}
.rounds .table{border:0;border-top:1px solid var(--line)}
.matrix{margin-top:12px;border:1px solid var(--line);background:#fff}
.matrix summary{padding:10px 14px;font-size:12px;font-weight:500;cursor:pointer;list-style:none}
.matrix summary::-webkit-details-marker{display:none}
.matrix summary:before{content:"+ ";color:var(--g500);font-weight:400}
.matrix[open] summary:before{content:"− "}
.matrix .table{border:0;border-top:1px solid var(--line)}
.verdict-banner{background:#fff;border:1px solid var(--line);border-left:3px solid var(--green);
  padding:15px 19px;display:flex;justify-content:space-between;gap:24px;align-items:center}
.verdict-banner.red{border-left-color:var(--red)}
.verdict-banner.amber{border-left-color:var(--amber-line)}
.verdict-score{font-size:36px;font-weight:300;letter-spacing:-1px;line-height:1.1}
.verdict-score small{font-size:13px;color:var(--g600);letter-spacing:0;margin-left:4px}
.verdict-word{font-size:15px;font-weight:500;margin-top:4px}
.verdict-word .zh{font-size:11px;color:var(--g600);font-weight:400;margin-left:8px}
.verdict-meta{font-size:11px;color:var(--g700);text-align:right;line-height:1.75}
.verdict-meta b{font-weight:500;color:var(--text)}
.verdict-meta .alert{color:var(--red)}
.check-table td.note{color:var(--g700);font-size:10px;line-height:1.55}
.mini-bar{height:6px;background:#e8e8e8;width:58px;display:inline-block;vertical-align:middle;margin-left:5px}
.mini-bar span{display:block;height:100%;background:var(--blue)}
.notes-box{margin-top:12px;background:#fff;border:1px solid var(--line);padding:11px 15px;
  font-size:11px;color:var(--g700);line-height:1.7}
.notes-box b{color:var(--text);font-weight:500}
.notes-box ul{margin-top:3px}
.footer{display:flex;justify-content:space-between;gap:20px;align-items:center;
  border-top:1px solid var(--g300);padding:13px 0 0;margin-top:24px;font-size:10px;color:var(--g600)}
.footer .right{color:var(--g500);white-space:nowrap}
@media (max-width:1200px){
  .hero{grid-template-columns:1fr}
  .core-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
  .data-grid{grid-template-columns:repeat(3,minmax(0,1fr))}
  .baseline-layout{grid-template-columns:1fr}
  .kv-grid{grid-template-columns:1fr}
  .spec-overview{grid-template-columns:repeat(2,minmax(0,1fr)) 1.3fr}
}
@media (max-width:760px){
  .mast-inner{flex-direction:column;align-items:flex-start;gap:6px;padding:12px 18px}
  .masthead-label,.masthead-meta{text-align:left}
  .page{padding:22px 16px}
  .core-grid,.totals,.ttl-grid,.data-grid{grid-template-columns:1fr}
  .spec-item summary{grid-template-columns:30px minmax(0,1fr) auto}
  .spec-weight{display:none}
  .spec-body{padding-left:15px}
  .footer{flex-direction:column;align-items:flex-start}
}
"""


# --------------------------------------------------------------------------- section 1-3

def render_masthead(audit, spec_doc, title):
    host = dig(spec_doc, "host") or "未测"
    tested_at = dig(spec_doc, "tested_at") or "未测"
    return f"""
<header class="masthead">
  <div class="mast-inner">
    <div class="wordmark"><strong>ENDPOINT</strong> / Conformance Report</div>
    <div>
      <div class="masthead-label">MODEL CONFORMANCE ASSESSMENT</div>
      <div class="masthead-meta">数据来源：{esc_raw(host)} 实测 · 测试日期：{esc_raw(tested_at)}</div>
    </div>
  </div>
</header>"""


def render_hero(audit, spec_doc, title):
    model = dig(spec_doc, "model") or dig(audit, "model") or "未测"
    host = dig(spec_doc, "host") or "未测"
    tested_at = dig(spec_doc, "tested_at") or "未测"
    protocol = dig(spec_doc, "protocol") or dig(audit, "protocol") or "未测"
    profile = dig(spec_doc, "profile") or dig(audit, "profile") or "未测"
    tier = dig(spec_doc, "tier") or "未测"

    item5 = find_item(items_of(spec_doc), ident=5)
    fixed = dig(item5, "expected", "fixed", default={}) or {}
    chips = [
        f'<span class="chip">协议<b>{esc_raw(protocol)}</b></span>',
        f'<span class="chip">画像<b>{esc_raw(profile)}</b></span>',
        f'<span class="chip">测试档位<b>{esc_raw(tier)}</b></span>',
    ]
    if isinstance(fixed, dict) and fixed:
        for k, v in fixed.items():
            chips.append(f'<span class="chip">{esc_raw(k)}<b>{esc(v)}</b></span>')
    else:
        chips.append('<span class="chip">固定参数<b>未测</b></span>')

    score = audit.get("score")
    verdict = audit.get("verdict") or "未测"
    vcls = verdict_class(audit.get("verdict"))

    items = items_of(spec_doc)
    pass_n = sum(1 for it in items if it.get("status") == "pass")
    partial_n = sum(1 for it in items if it.get("status") == "partial")
    spec_score = dig(spec_doc, "spec", "score")
    if spec_doc is None:
        spec_value, spec_unit, spec_cap = "—", "", "未提供 spec JSON"
    else:
        spec_value, spec_unit = fmt(pass_n), f"/ {fmt(len(items))} 项通过"
        spec_cap = f"规范分 {fmt(spec_score)} / 100 · {fmt(partial_n)} 项 partial"

    perf = perf_flags(spec_doc)
    perf_value = "—" if perf is None else fmt(perf)
    perf_cap = "未提供 spec JSON" if perf is None else "官方基准 · TTFT · 缓存 · 成功率 · finish"

    load = dig(spec_doc, "load", default={}) or {}
    sr = load.get("success_rate")
    samples = load.get("samples")
    success = load.get("success")
    sr_cap = "未提供 spec JSON" if spec_doc is None else f"成功 {fmt(success)} / 样本 {fmt(samples)}"

    return f"""
<section class="hero">
  <div>
    <div class="eyebrow">API 实测 · 规范符合度 · 渠道真实性</div>
    <h1>{esc_raw(title)}</h1>
    <div class="model-line">
      <span class="model">{esc_raw(model)}</span><span class="divider"></span>
      <span>{esc_raw(host)}</span><span class="divider"></span>
      <span>{esc_raw(tested_at)}</span>
    </div>
    <div class="chips">{''.join(chips)}</div>
  </div>
  <div class="scoreboard">
    <div class="score">
      <div class="score-label">真实性分</div>
      <div class="score-value">{fmt(score) if is_num(score) else '未测'}<small>/ 100</small></div>
      <div class="score-caption {vcls}">{esc_raw(verdict)} · model_audit v{esc(dig(audit, "scoring_version") or "2")}</div>
    </div>
    <div class="score">
      <div class="score-label">规范符合度</div>
      <div class="score-value">{spec_value}<small>{spec_unit}</small></div>
      <div class="score-caption">{spec_cap}</div>
    </div>
    <div class="score">
      <div class="score-label">性能达标</div>
      <div class="score-value">{perf_value}<small>/ 5</small></div>
      <div class="score-caption">{perf_cap}</div>
    </div>
    <div class="score">
      <div class="score-label">请求成功率</div>
      <div class="score-value">{val_html(sr, "%") if is_num(sr) else '未测'}</div>
      <div class="score-caption">{sr_cap}</div>
    </div>
  </div>
</section>"""


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


def core_card(cls, title, en, value_html_str, status_text, mini, target_html, delta_html):
    return f"""
<article class="core {cls}">
  <div class="core-title">{esc_raw(title)}</div>
  <div class="core-en">{esc_raw(en)}</div>
  <div class="core-value">{value_html_str}</div>
  <div class="core-status"><span class="dot"></span>{esc_raw(status_text)}</div>
  <div class="core-mini">{mini}</div>
  <div class="core-target">目标：{target_html}</div>
  <div class="core-delta">差值：{delta_html}</div>
</article>"""


def render_core(spec_doc):
    items = items_of(spec_doc)
    item1 = find_item(items, ident=1, key="perf_baseline")
    item2 = find_item(items, ident=2, key="ttft")
    item3 = find_item(items, ident=3, key="cache_hit")
    load = dig(spec_doc, "load", default={}) or {}

    cards = []

    # 01 baseline ratios
    if item1:
        groups = ratio_groups(item1)
        worst = {}
        for q, pairs in groups.items():
            worst[q] = max((v for _, v in pairs), default=None)
        main = worst.get("p90")
        if main is None:
            main = worst.get("p50")
        value = val_html(main, "×") if main is not None else "未测"
        expected = dig(item1, "expected", default={}) or {}
        tbits = []
        if is_num(expected.get("p50_ratio_max")):
            tbits.append(f"P50 ≤ {esc(expected.get('p50_ratio_max'))}×")
        if is_num(expected.get("p90_ratio_max")):
            tbits.append(f"P90 ≤ {esc(expected.get('p90_ratio_max'))}×")
        if is_num(expected.get("output_tps_p50_ratio_min")):
            tbits.append(f"TPS ≥ {esc(expected.get('output_tps_p50_ratio_min'))}×")
        target = " · ".join(tbits) if tbits else "未测"
        delta = dig(item1, "delta", default={}) or {}
        dbits = []
        if is_num(delta.get("p50_margin")):
            dbits.append(f"P50 余量 {fmt(delta.get('p50_margin'))}×")
        if is_num(delta.get("p90_margin")):
            dbits.append(f"P90 余量 {fmt(delta.get('p90_margin'))}×")
        delta_html = " · ".join(esc_raw(x) for x in dbits) if dbits else "未测"
        mini = " · ".join(
            f"{q.upper()} <b>{val_html(worst.get(q), '×') if worst.get(q) is not None else '未测'}</b>"
            for q in ("p50", "p90", "p95")
        )
        st = status_of(item1.get("status"))
        cards.append(core_card(st, "官方基准对标", "实测 ÷ 官方 · 最差 P90 比率",
                               value, CORE_TEXT[st], mini, target, delta_html))
    else:
        cards.append(core_card("skip", "官方基准对标", "实测 ÷ 官方 · P50 / P90 / P95",
                               "未测", "未测", "P50 <b>未测</b> · P90 <b>未测</b> · P95 <b>未测</b>",
                               "未测", "未测"))

    # 02 TTFT P90
    if item2:
        measured = dig(item2, "measured", default={}) or {}
        expected = dig(item2, "expected", default={}) or {}
        p90 = measured.get("p90_s")
        value = val_html(p90, "s") if is_num(p90) else "未测"
        target = f"≤ {esc(expected.get('p90_max_s'))} s" if is_num(expected.get("p90_max_s")) else "未测"
        margin = dig(item2, "delta", "margin_s")
        delta_html = f"余量 {esc(margin)} s" if is_num(margin) else "未测"
        st = status_of(item2.get("status"))
        mini = f"P95 <b>{val_html(measured.get('p95_s'), 's') if is_num(measured.get('p95_s')) else '未测'}</b>"
        cards.append(core_card(st, "TTFT P90", "首 token 延迟 · P90",
                               value, CORE_TEXT[st], mini, target, delta_html))
    else:
        cards.append(core_card("skip", "TTFT P90", "首 token 延迟 · P90",
                               "未测", "未测", "P95 <b>未测</b>", "未测", "未测"))

    # 03 cache hit
    if item3:
        measured = dig(item3, "measured", default={}) or {}
        expected = dig(item3, "expected", default={}) or {}
        pairs = [(k, v) for k, v in measured.items() if is_num(v) and "pct" in k]
        low = min(pairs, key=lambda kv: kv[1]) if pairs else (None, None)
        value = val_html(low[1], "%") if low[1] is not None else "未测"
        target = f"≥ {esc(expected.get('min_pct'))} %" if is_num(expected.get("min_pct")) else "未测"
        margin = dig(item3, "delta", "margin_pct")
        delta_html = f"余量 {esc(margin)} pp" if is_num(margin) else "未测"
        mini = " · ".join(f"{esc_raw(k.replace('_pct', ''))} <b>{val_html(v)}%</b>" for k, v in pairs) or "未测"
        st = status_of(item3.get("status"))
        cards.append(core_card(st, "缓存命中", "KV cache · 三类场景最低值",
                               value, CORE_TEXT[st], mini, target, delta_html))
    else:
        cards.append(core_card("skip", "缓存命中", "KV cache · 三类场景最低值",
                               "未测", "未测", "未测", "未测", "未测"))

    # 04 request success rate
    sr = load.get("success_rate")
    if is_num(sr):
        if float(sr) == 100.0:
            st, text = "pass", "达标"
        elif float(sr) >= 95.0:
            st, text = "partial", "部分达标"
        else:
            st, text = "fail", "未达标"
        value = val_html(sr, "%")
    else:
        st, text, value = "skip", "未测", "未测"
    empty = dig(load, "finish", "empty")
    mini = (f"成功 <b>{esc(load.get('success'))}</b> / 样本 <b>{esc(load.get('samples'))}</b>"
            + (f" · finish empty <b>{esc(empty)}</b>" if is_num(empty) else ""))
    cards.append(core_card(st, "请求成功率", "Request success rate · 负载样本",
                           value, text, mini, "= 100 %", "未测"))

    # 05 TPOT P90
    node = dig(load, "percentiles", "tpot_ms", default={}) or {}
    p90 = node.get("p90")
    value = val_html(p90, "ms") if is_num(p90) else "未测"
    mini = f"P50 <b>{val_html(node.get('p50'), 'ms') if is_num(node.get('p50')) else '未测'}</b>"
    if is_num(node.get("p95")):
        mini += f" · P95 <b>{val_html(node.get('p95'), 'ms')}</b>"
    cards.append(core_card("skip", "TPOT P90", "每输出 token 耗时 · P90",
                           value, "仅展示（无阈值）", mini, "未提供阈值", "未测"))

    note = "未提供 spec JSON" if spec_doc is None else f"核心验收卡 · {fmt(perf_flags(spec_doc))} / 5 项达标"
    return section("01", "核心标准", note) + '<section class="core-grid">' + "".join(cards) + "</section>"


# --------------------------------------------------------------------------- section 02

def render_metrics(spec_doc):
    load = dig(spec_doc, "load", default={}) or {}
    note = "p50 / p90 / p95 · 吞吐 · token 合计 · finish 分布"
    if spec_doc is None:
        note = "未提供 spec JSON"
    out = [section("02", "全部指标", note)]
    if not load:
        out.append(empty_panel("未提供 spec JSON", "负载分位数与吞吐指标无法展示。"))
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
        cells = [f'<span class="metric-name">{esc_raw(label)}</span><span class="metric-en">{esc_raw(en)}</span>']
        for q in ("p50", "p90", "p95"):
            v = node.get(q)
            cells.append(val_html(v, unit) if is_num(v) else "未测")
        cells.append(fmt(node.get("n")) if is_num(node.get("n")) else "未测")
        rows.append(cells)
    out.append(table(["指标", "P50", "P90", "P95", "样本 n"], rows,
                     cls="quant-table", aligns=["", "r", "r", "r", "r"]))

    throughput = load.get("throughput") or {}
    finish = load.get("finish") or {}
    out.append('<div class="data-grid">')
    cards = [
        ("RPM", throughput.get("rpm"), "req/min"),
        ("总 TPM", throughput.get("tpm"), "tok/min"),
        ("输入 TPM", throughput.get("input_tpm"), "tok/min"),
        ("输出 TPM", throughput.get("output_tpm"), "tok/min"),
        ("样本数", load.get("samples"), ""),
        ("成功数", load.get("success"), ""),
        ("成功率", load.get("success_rate"), "%"),
        ("finish empty", finish.get("empty"), ""),
    ]
    for label, v, unit in cards:
        out.append(
            f'<div class="metric"><div class="metric-label">{esc_raw(label)}</div>'
            f'<div class="metric-value">{val_html(v, unit) if is_num(v) else "未测"}</div></div>'
        )
    out.append("</div>")

    # finish distribution bars
    out.append('<div class="subhead">finish_reason 分布<span>条长按最大值缩放</span></div>')
    fdefs = (("stop", "stop · 正常结束"), ("length", "length · 长度截断"),
             ("tool_calls", "tool_calls · 工具调用"), ("empty", "empty · 空响应"),
             ("other", "other · 其它"))
    vals = [finish.get(k) for k, _ in fdefs if is_num(finish.get(k))]
    vmax = max(vals) if vals else None
    out.append('<div class="bars">')
    for key, label in fdefs:
        v = finish.get(key)
        if is_num(v) and vmax:
            width = max(0.0, min(100.0, float(v) / float(vmax) * 100.0))
            fill = f'<div class="bar-fill{" warn" if key == "empty" and v > 0 else ""}" style="width:{width:.1f}%"></div>'
        else:
            fill = ""
        shown = fmt(v) if is_num(v) else "未测"
        out.append(
            f'<div class="bar-row"><div class="bar-label">{esc_raw(label)}</div>'
            f'<div class="bar-track">{fill}</div><div class="bar-value">{shown}</div></div>'
        )
    out.append("</div>")
    return "".join(out)


# --------------------------------------------------------------------------- section 03

def metric_parts(key):
    base = key[:-6] if key.endswith("_ratio") else key
    q = ""
    for token in ("p50", "p90", "p95"):
        if f"_{token}" in base:
            q = token.upper()
            base = base.replace(f"_{token}", "")
            break
    names = {
        "ttft": "TTFT", "latency": "Latency", "tpot": "TPOT",
        "output_tps": "Output TPS", "input_tps": "Input TPS",
    }
    label = names.get(base, " ".join(w.capitalize() for w in base.split("_")) or base)
    return label, q or "—"


def render_baseline(spec_doc):
    out = [section("03", "官方基准对标", "实测 ÷ 官方 · 目标取自规范第 1 项")]
    item1 = find_item(items_of(spec_doc), ident=1, key="perf_baseline")
    if not item1:
        out.append(empty_panel("未提供 spec JSON", "缺少第 1 项 perf_baseline，无法展示基准对标。"))
        return "".join(out)

    measured = dig(item1, "measured", default={}) or {}
    expected = dig(item1, "expected", default={}) or {}
    rows, bars = [], []
    values_for_scale = []
    for key, v in measured.items():
        if not (is_num(v) and key.endswith("_ratio")):
            continue
        label, q = metric_parts(key)
        q_low = q.lower() if q != "—" else ""
        direction = "min" if "output_tps" in key else "max"
        if direction == "min":
            target = expected.get(f"{q_low}_ratio_min") if q_low else None
            if not is_num(target):
                target = expected.get("output_tps_p50_ratio_min")
        else:
            target = expected.get(f"{q_low}_ratio_max") if q_low else None
        verdict = "skip"
        if is_num(target):
            verdict = "pass" if (float(v) >= float(target) if direction == "min" else float(v) <= float(target)) else "fail"
        if is_num(target):
            target_text = (f"≥ {fmt(target)}×" if direction == "min" else f"≤ {fmt(target)}×")
            values_for_scale.append(float(target))
        else:
            target_text = "未评"
        values_for_scale.append(abs(float(v)))
        dev = (float(v) - 1.0) * 100.0
        dev_text = f"{dev:+.1f}%"
        rows.append([
            esc_raw(label), esc_raw(q), f"{esc(str(v))}×", esc_raw(dev_text),
            esc_raw(target_text), pill(verdict, {"pass": "达标", "fail": "超限", "skip": "未评"}[verdict]),
        ])
        bars.append({"label": f"{label} {q}".strip(), "ratio": float(v),
                     "target": float(target) if is_num(target) else None,
                     "verdict": verdict})

    out.append('<div class="baseline-note">spec JSON 未提供官方绝对值，本表以「实测 ÷ 官方」比率与判定呈现；'
               '延迟类比率越低越好，吞吐类比率越高越好。数值不做二次计算。</div>')
    out.append('<div class="baseline-layout"><div>')
    out.append(table(["指标", "分位", "比率（实测 ÷ 官方）", "偏差", "目标", "判定"], rows,
                     aligns=["", "r", "r", "r", "r", "c"]))
    out.append("</div><div class=\"chart\">")
    out.append('<div class="chart-title">比率与目标阈值</div>'
               '<div class="chart-sub">条形 = 实测 ÷ 官方；竖线 = 目标阈值。超出阈值显示红色。</div>')
    scale = max(values_for_scale + [1.0]) * 1.08
    for b in bars:
        width = max(0.0, min(100.0, b["ratio"] / scale * 100.0))
        cls = {"pass": "", "fail": " bad", "skip": " na"}[b["verdict"]]
        marker = ""
        if b["target"] is not None:
            mleft = max(0.0, min(100.0, b["target"] / scale * 100.0))
            marker = f'<span class="cbar-marker" style="left:{mleft:.1f}%"></span>'
        out.append(
            f'<div class="cbar-row"><div class="cbar-label">{esc_raw(b["label"])}</div>'
            f'<div class="cbar-track"><div class="cbar-fill{cls}" style="width:{width:.1f}%"></div>{marker}</div>'
            f'<div class="cbar-val">{esc(b["ratio"])}×</div></div>'
        )
    out.append('<div class="chart-legend"><span>绿色：达标</span><span>红色：超限</span>'
               '<span>灰色：无目标</span><span>竖线：阈值</span></div>')
    evidence = dig(item1, "evidence", default=[]) or []
    if evidence:
        out.append('<div class="chart-evidence"><b>证据原文</b><ul>')
        for line in evidence:
            out.append(f"<li>{esc_raw(line)}</li>")
        out.append("</ul></div>")
    out.append("</div></div>")
    return "".join(out)


# --------------------------------------------------------------------------- section 04

def evidence_html(ev):
    if isinstance(ev, list) and ev:
        return "<ul>" + "".join(f"<li>{esc_raw(x)}</li>" for x in ev) + "</ul>"
    if isinstance(ev, str) and ev.strip():
        return f"<p>{esc_raw(ev)}</p>"
    return "<p>未测</p>"


def render_spec_items(spec_doc):
    items = items_of(spec_doc)
    if not items:
        return section("04", "规范符合度", "未提供 spec JSON") + \
            empty_panel("未提供 spec JSON", "15 项规范清单不可用。")

    counts = {s: sum(1 for it in items if it.get("status") == s) for s in STATUS_TEXT}
    score = dig(spec_doc, "spec", "score")
    out = [section("04", "规范符合度",
                   f"{counts['pass']} 通过 · {counts['partial']} 部分 · "
                   f"{counts['fail']} 未通过 · {counts['skip']} 跳过")]
    out.append(
        '<div class="spec-overview">'
        f'<div><span>通过 pass</span><b style="color:var(--green)">{counts["pass"]}</b></div>'
        f'<div><span>部分 partial</span><b style="color:var(--amber)">{counts["partial"]}</b></div>'
        f'<div><span>未通过 fail</span><b style="color:var(--red)">{counts["fail"]}</b></div>'
        f'<div><span>跳过 skip</span><b>{counts["skip"]}</b></div>'
        f'<div class="spec-score-cell"><span>规范分 / 100</span><b>{fmt(score)}</b></div>'
        '</div>'
    )
    out.append('<div class="spec-list">')
    for pos, it in enumerate(items, 1):
        st = status_of(it.get("status"))
        title = it.get("title") or it.get("key") or "未命名项"
        note = it.get("note") or "未提供一句话结论"
        body = f"""
<div class="spec-body">
  <div class="kv-grid">
    <div class="kv"><h4>实测 measured</h4><pre>{html.escape(pretty(it.get("measured")), quote=True)}</pre></div>
    <div class="kv"><h4>预期 expected</h4><pre>{html.escape(pretty(it.get("expected")), quote=True)}</pre></div>
    <div class="kv"><h4>差值 delta</h4><pre>{html.escape(pretty(it.get("delta")), quote=True)}</pre></div>
  </div>
  <div class="evidence"><h4>证据 evidence</h4>{evidence_html(it.get("evidence"))}</div>
  <p class="note-line"><b>备注：</b>{esc_raw(note)}</p>
</div>"""
        out.append(f"""
<details class="spec-item">
  <summary>
    <span class="spec-idx">{pos:02d}</span>
    <span class="spec-main"><span class="spec-title">{esc_raw(title)}</span><span class="spec-note">{esc_raw(note)}</span></span>
    <span class="spec-weight">权重 {esc(it.get("weight"))}</span>
    {pill(st)}
  </summary>{body}
</details>""")
    out.append("</div>")

    matrix = dig(spec_doc, "params", "matrix", default=[]) or []
    if isinstance(matrix, list) and matrix:
        matched = sum(1 for m in matrix if m.get("match") is True)
        rows = []
        for m in matrix:
            match = m.get("match")
            mst = "pass" if match is True else ("fail" if match is False else "skip")
            rows.append([
                esc_raw(m.get("case")), fmt(m.get("expected_status")), fmt(m.get("actual_status")),
                pill(mst, {"pass": "一致", "fail": "不一致", "skip": "未测"}[mst]),
                esc_raw(m.get("detail") or "未测"),
            ])
        out.append(
            f'<details class="matrix"><summary>参数矩阵明细 · {fmt(len(matrix))} 例 · 一致 {fmt(matched)}</summary>'
            + table(["用例", "预期状态", "实测状态", "比对", "说明"], rows,
                    aligns=["", "r", "r", "c", ""])
            + "</details>"
        )
    return "".join(out)


# --------------------------------------------------------------------------- section 05

def render_billing(spec_doc):
    out = [section("05", "计费与缓存", "usage 字段 · 5m / 1h 写入 · 缓存场景")]
    if spec_doc is None:
        out.append(empty_panel("未提供 spec JSON", "usage、缓存写入与场景命中不可用。"))
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

    def bool_row(label, key, expected_text):
        v = usage.get(key)
        if isinstance(v, bool):
            st = "pass" if v else "fail"
            result = "是" if v else "否"
        else:
            st, result = "skip", "未测"
        return [esc_raw(label), result, esc_raw(expected_text), pill(st)]

    rows = [
        bool_row("必需字段", "required_present", ", ".join(str(x) for x in required) or "—"),
        bool_row("total_tokens 一致性", "total_matches", "等于分项之和"),
        bool_row("明细子键", "details_present", detail_text or "—"),
        bool_row("缓存写入字段", "cache_write_present", "cache_write_tokens"),
    ]
    tr = usage.get("token_ratio")
    if is_num(tr):
        if len(ratio_range) == 2 and all(is_num(x) for x in ratio_range):
            ok = float(ratio_range[0]) <= float(tr) <= float(ratio_range[1])
            st = "pass" if ok else "fail"
            exp_text = f"{fmt(ratio_range[0])} – {fmt(ratio_range[1])}"
        else:
            st, exp_text = "skip", "未提供区间"
        rows.append([esc_raw("token / char 比率"), f"{esc(tr)}", esc_raw(exp_text), pill(st)])
    else:
        rows.append([esc_raw("token / char 比率"), "未测", "—", pill("skip")])

    out.append(table(["检查项", "结果", "官方期望", "判定"], rows, aligns=["", "", "", "c"]))

    cache = dig(spec_doc, "cache", default={}) or {}
    out.append('<div class="ttl-grid">')
    for key, title in (("ttl_5m", "5 分钟缓存写入"), ("ttl_1h", "1 小时缓存写入")):
        ttl = cache.get(key) or {}
        if not isinstance(ttl, dict) or not ttl:
            out.append(f'<div class="ttl-card na"><div class="ttl-head"><h3>{esc_raw(title)}</h3>'
                       f'{pill("skip")}</div><div class="dl"><dt>状态</dt><dd>未测</dd></div></div>')
            continue
        st = "pass" if ttl.get("pass") is True else ("fail" if ttl.get("pass") is False else "skip")
        rows_dl = [
            ("写入 tokens", fmt(ttl.get("write_tokens")) if ttl.get("write_tokens") is not None else "未测"),
            ("响应头值", esc(ttl.get("header"))),
            ("回读命中", val_html(ttl.get("read_back_hit_pct"), "%") if is_num(ttl.get("read_back_hit_pct")) else "未测"),
        ]
        if key == "ttl_1h":
            rows_dl.insert(2, ("回读延迟", val_html(ttl.get("read_back_after_s"), "s")
                               if is_num(ttl.get("read_back_after_s")) else "未测"))
            rows_dl.append(("58 分钟回读", esc(ttl.get("read_back_58m"))))
        dl = "".join(f"<dt>{esc_raw(k)}</dt><dd>{v}</dd>" for k, v in rows_dl)
        out.append(
            f'<div class="ttl-card"><div class="ttl-head"><h3>{esc_raw(title)}</h3>{pill(st)}</div>'
            f'<dl class="dl">{dl}</dl></div>'
        )
    out.append("</div>")

    scenarios = cache.get("scenarios") or {}
    if isinstance(scenarios, dict) and scenarios:
        labels = {
            "A_identical_20k": "A · 相同 20k", "B_multiturn_12k": "B · 多轮 12k",
            "C_short_1k": "C · 短文本 1k", "E_cache_key": "E · cache key",
        }
        rows, details_blocks = [], []
        for name, sc in scenarios.items():
            if not isinstance(sc, dict):
                continue
            st = "pass" if sc.get("pass") is True else ("fail" if sc.get("pass") is False else "skip")
            hit = sc.get("hit_rate_pct")
            rounds = sc.get("rounds") if isinstance(sc.get("rounds"), list) else []
            rows.append([
                esc_raw(labels.get(name, name)), pill(st),
                val_html(hit, "%") if is_num(hit) else "未测", fmt(len(rounds)),
            ])
            if rounds:
                rrows = []
                for r in rounds:
                    if not isinstance(r, dict):
                        continue
                    rrows.append([
                        fmt(r.get("i")), fmt(r.get("cached_tokens")), fmt(r.get("prompt_tokens")),
                        val_html(r.get("hit_rate_pct"), "%") if is_num(r.get("hit_rate_pct")) else "未测",
                    ])
                details_blocks.append(
                    f'<details class="rounds"><summary>{esc_raw(labels.get(name, name))} · 逐轮明细</summary>'
                    + table(["轮次", "cached_tokens", "prompt_tokens", "命中率"], rrows,
                            aligns=["r", "r", "r", "r"])
                    + "</details>"
                )
        out.append('<div class="subhead">缓存场景<span>固定前缀重复请求</span></div>')
        out.append(table(["场景", "判定", "命中率", "轮次"], rows, aligns=["", "c", "r", "r"]))
        out.extend(details_blocks)
    else:
        out.append('<div class="subhead">缓存场景</div>')
        out.append(empty_panel("缓存场景未测", "cache.scenarios 为空。"))
    return "".join(out)


# --------------------------------------------------------------------------- section 06

def render_audit(audit):
    out = [section("06", "真实性与渠道", "model_audit 加权检查 · 独立于规范符合度")]
    score = audit.get("score")
    verdict = audit.get("verdict") or "未测"
    hard_fail = audit.get("hard_fail")
    vcls = "red" if hard_fail else verdict_class(audit.get("verdict"))
    zh = {"highly credible": "高度可信", "credible": "可信", "suspicious": "存疑",
          "fake": "不可信", "implausible": "不可信"}.get(str(verdict).lower(), "")
    notes = audit.get("notes") if isinstance(audit.get("notes"), list) else []
    crashed = audit.get("crashed_checks") if isinstance(audit.get("crashed_checks"), list) else []
    usage_inflated = audit.get("usage_inflated")
    meta_bits = [
        f"hard_fail：<b>{'<span class=\"alert\">' + esc_raw(hard_fail) + '</span>' if hard_fail else '未触发'}</b>",
        f"usage 虚高：<b>{'<span class=\"alert\">是</span>' if usage_inflated is True else ('否' if usage_inflated is False else '未测')}</b>",
        f"崩溃检查：<b>{esc_raw(', '.join(str(x) for x in crashed)) if crashed else '无'}</b>",
        f"性能分：<b>{fmt(audit.get('performance'))} / 100</b> · {esc_raw(audit.get('performance_detail') or '未测')}",
    ]
    out.append(
        f'<div class="verdict-banner{" red" if vcls == "red" else (" amber" if vcls == "amber" else "")}">'
        f'<div><div class="verdict-score">{fmt(score) if is_num(score) else "未测"}<small>/ 100</small></div>'
        f'<div class="verdict-word">{esc_raw(verdict)}<span class="zh">{esc_raw(zh)}</span></div></div>'
        f'<div class="verdict-meta">{"<br>".join(meta_bits)}</div></div>'
    )

    checks = audit.get("checks") if isinstance(audit.get("checks"), list) else []
    if checks:
        rows = []
        for c in checks:
            w = c.get("weight")
            s = c.get("score")
            if is_num(s) and is_num(w):
                weighted = f"{float(s) * float(w):.1f} / {fmt(w)}"
                width = max(0.0, min(100.0, float(s) * 100.0))
                bar = f'<span class="mini-bar"><span style="width:{width:.1f}%"></span></span>'
                score_txt = f"{fmt(float(s))}{bar}"
            elif s is None:
                weighted, score_txt = "n/a", "n/a"
            else:
                weighted, score_txt = "未测", esc(s)
            rows.append([
                esc_raw(c.get("name") or "未命名检查"), fmt(w), score_txt, weighted,
                esc_raw(c.get("note") or "未测"),
            ])
        out.append(table(["检查项", "权重", "得分", "加权得分", "说明"], rows,
                         cls="table check-table", aligns=["", "r", "r", "r", "note"]))
    else:
        out.append(empty_panel("未提供 checks", "model_audit 输出中没有检查项。"))

    if notes:
        out.append('<div class="notes-box"><b>审计备注</b><ul>'
                   + "".join(f"<li>{esc_raw(n)}</li>" for n in notes) + "</ul></div>")
    return "".join(out)


# --------------------------------------------------------------------------- totals + footer

def render_totals(spec_doc):
    load = dig(spec_doc, "load", default={}) or {}
    totals = load.get("totals") or {}
    cells = [
        ("输入 tokens 合计", totals.get("prompt_tokens")),
        ("输出 tokens 合计", totals.get("completion_tokens")),
        ("缓存命中 tokens", totals.get("cached_tokens")),
        ("缓存写入 tokens", totals.get("cache_write_tokens")),
    ]
    out = ['<section class="totals">']
    for label, v in cells:
        out.append(
            f'<article class="total"><div class="metric-label">{esc_raw(label)}</div>'
            f'<div class="metric-value">{val_html(v) if is_num(v) else "未测"}</div></article>'
        )
    out.append("</section>")
    return "".join(out)


def render_footer(spec_doc):
    host = dig(spec_doc, "host") or "未测"
    return (
        '<footer class="footer">'
        f'<span>数据来源：{esc_raw(host)} 实测 · 基准：官方存档 2026-11-09 · 单页报告</span>'
        '<span class="right">CARBON DESIGN / SINGLE-PAGE REPORT</span>'
        '</footer>'
    )


# --------------------------------------------------------------------------- assembly

def build_report(audit, spec_doc, title):
    body = [
        render_masthead(audit, spec_doc, title),
        '<main class="page">',
        render_hero(audit, spec_doc, title),
        render_core(spec_doc),
        render_metrics(spec_doc),
        render_baseline(spec_doc),
        render_spec_items(spec_doc),
        render_billing(spec_doc),
        render_audit(audit),
        render_totals(spec_doc),
        render_footer(spec_doc),
        "</main>",
    ]
    model = dig(spec_doc, "model") or dig(audit, "model") or ""
    doc_title = f"{title} · {model}".strip(" ·")
    return (
        '<!DOCTYPE html>\n<html lang="zh-CN"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{esc_raw(doc_title)}</title><style>{CSS}</style></head><body>"
        + "".join(body)
        + "</body></html>\n"
    )


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Render a single-file HTML model conformance report.")
    p.add_argument("--audit", required=True, help="model_audit v2 JSON output")
    p.add_argument("--spec", default="", help="spec_audit JSON output (optional)")
    p.add_argument("--out", required=True, help="output HTML path")
    p.add_argument("--title", default="模型渠道真实性报告", help="report title")
    args = p.parse_args()

    key = os.environ.get("LLM_API_KEY", "")

    try:
        audit = load_json(args.audit, key)
    except (OSError, ValueError) as e:
        sys.exit(f"error: cannot read audit JSON {args.audit}: {e}")
    spec_doc = None
    if args.spec:
        try:
            spec_doc = load_json(args.spec, key)
        except (OSError, ValueError) as e:
            sys.exit(f"error: cannot read spec JSON {args.spec}: {e}")

    doc = build_report(audit, spec_doc, args.title)
    out_path = os.path.abspath(args.out)
    parent = os.path.dirname(out_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(doc)
    print(f"wrote {out_path} ({os.path.getsize(out_path):,} bytes)")


if __name__ == "__main__":
    main()
