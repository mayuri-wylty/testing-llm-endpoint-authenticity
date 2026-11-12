"""Shared helpers and constants for the HTML report modules (no section logic)."""
import html
import json

from common import fmt_num, round_obj, round_text
from i18n_zh import annotate_text, bi

STATUS_TEXT = {"pass": "通过", "partial": "部分通过", "fail": "未通过", "skip": "跳过"}
# "not provided" placeholder with the glossary gloss, so no bare English (spec / JSON) reaches the page lint
NO_SPEC_TEXT = f"未提供 {bi('spec')} {bi('JSON')}"
CORE_TEXT = {"pass": "达标", "partial": "部分达标", "fail": "未达标", "skip": "跳过"}


# --------------------------------------------------------------------------- data helpers

def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def fmt(v):
    """Human number formatting with thousands separators; missing data is never zero-filled."""
    if v is None:
        return "未测"
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, (int, float)):
        return fmt_num(v)  # float -> fixed 2 decimals, int stays int
    s = str(v).strip()
    return s if s else "未测"


def esc(v):
    return html.escape(fmt(v), quote=True)


def esc_raw(v):
    """Escape a plain string (used for values that may legitimately be empty text)."""
    return html.escape("" if v is None else str(v), quote=True)


def txt(v):
    """Free text from JSON (evidence/note/detail): clamp >2-decimal numbers, add (中文) to known
    identifiers, then escape. Use for any text that did not originate in this codebase."""
    if v is None:
        return ""
    return html.escape(annotate_text(round_text(str(v))), quote=True)


def lab(en):
    """Escaped label rendered as ``English（中文）`` via the glossary (unknown words pass through)."""
    return html.escape(bi(str(en)), quote=True)


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
    return json.dumps(round_obj(v), ensure_ascii=False, indent=1)  # floats <= 2 decimals


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


def empty_panel(msg=None, detail=""):
    msg = NO_SPEC_TEXT if msg is None else msg
    extra = f'<span class="empty-detail">{esc_raw(detail)}</span>' if detail else ""
    return f'<div class="empty"><b>{esc_raw(msg)}</b>{extra}</div>'


def section(index, title, note=""):
    return (
        f'<div class="section-heading" id="sec-{esc_raw(index)}"><div class="heading-left">'
        f'<span class="section-index">{esc_raw(index)}</span><h2>{esc_raw(title)}</h2>'
        f'</div><span class="section-note">{esc_raw(note)}</span></div>'
    )


def fold_start(index, title, note="", open_=False):
    """Collapsible section: the summary doubles as the section heading (anchor ``#sec-NN`` is the <details>)."""
    return (
        f'<details class="fold" id="sec-{esc_raw(index)}"{" open" if open_ else ""}>'
        f'<summary><span class="section-index">{esc_raw(index)}</span><h2>{esc_raw(title)}</h2>'
        f'<span class="section-note">{esc_raw(note)}</span></summary><div class="fold-body">'
    )


FOLD_END = "</div></details>"


def table(headers, rows, cls="table", aligns=None, caption=None):
    """Responsive table: horizontally scrollable wrapper, ``scope="col"`` headers, optional caption.
    Header strings are escaped here; ``rows`` cells are raw HTML (callers pre-escape)."""
    aligns = aligns or []
    head = []
    for i, h in enumerate(headers):
        al = aligns[i] if i < len(aligns) else ""
        head.append(f'<th scope="col" class="{al}">{esc_raw(h)}</th>')
    body = "".join(
        "<tr>" + "".join(
            f'<td class="{aligns[i] if i < len(aligns) else ""}">{cell}</td>'
            for i, cell in enumerate(row)
        ) + "</tr>"
        for row in rows
    )
    cap = f"<caption>{esc_raw(caption)}</caption>" if caption else ""
    label = f' aria-label="{esc_raw(caption)}"' if caption else ""
    return (
        f'<div class="table-wrap" role="region" tabindex="0"{label}>'
        f'<table class="{esc_raw(cls)}">{cap}<thead><tr>{"".join(head)}</tr></thead>'
        f'<tbody>{body}</tbody></table></div>'
    )


# --------------------------------------------------------------------------- official-vs-measured
# CSS class contract (html_theme.py must style these):
#   .ov .ov-o .ov-sep .ov-m          "官方 / 实测" cell
#   .dlt .dlt.ok|warn|bad|unknown     deviation badge (always has an arrow/text, never colour-only)
#   .barpair .barpair-track .barpair-o .barpair-m   official/measured marker pair (role="img")

VERDICT_TEXT = {"ok": "达标", "warn": "接近", "bad": "偏离", "unknown": "未评"}
VERDICT_MARK = {"ok": "✓", "warn": "!", "bad": "✕", "unknown": "–"}


def delta_badge(delta_pct, verdict="unknown"):
    """Signed deviation badge, e.g. ``▲ +24.80% 偏离``. Colour is secondary to arrow + text."""
    v = verdict if verdict in VERDICT_TEXT else "unknown"
    if not is_num(delta_pct):
        return f'<span class="dlt {v}">{VERDICT_MARK[v]} {VERDICT_TEXT[v]}</span>'
    arrow = "▲" if delta_pct > 0 else ("▼" if delta_pct < 0 else "■")
    sign = "+" if delta_pct > 0 else ""
    return (
        f'<span class="dlt {v}">{arrow} {sign}{fmt_num(float(delta_pct))}% '
        f'{VERDICT_MARK[v]} {VERDICT_TEXT[v]}</span>'
    )


def ov(official, measured, unit="", delta_pct=None, verdict=None):
    """``官方 / 实测`` cell: ``2.46 / 3.07 s`` (+ optional delta badge). None -> 未提供 / 未测."""
    o = val_html(official, unit) if official is not None else '<span class="na">未提供</span>'
    m = val_html(measured, unit) if measured is not None else '<span class="na">未测</span>'
    badge = delta_badge(delta_pct, verdict) if verdict is not None else ""
    return (
        f'<span class="ov"><span class="ov-o">{o}</span><span class="ov-sep"> / </span>'
        f'<span class="ov-m">{m}</span></span>{(" " + badge) if badge else ""}'
    )


def bar_pair(official, measured, label=""):
    """Official/measured marker bars sharing one scale (no JS, no SVG). Text alt in aria-label."""
    if not (is_num(official) and is_num(measured)) or max(official, measured) <= 0:
        return ""
    top = max(official, measured) * 1.08
    wo = min(100.0, official / top * 100)
    wm = min(100.0, measured / top * 100)
    alt = f"{label} 官方 {fmt_num(float(official))}，实测 {fmt_num(float(measured))}".strip()
    return (
        f'<span class="barpair" role="img" aria-label="{esc_raw(alt)}"><span class="barpair-track">'
        f'<i class="barpair-o" style="width:{wo:.1f}%"></i>'
        f'<i class="barpair-m" style="width:{wm:.1f}%"></i></span></span>'
    )
