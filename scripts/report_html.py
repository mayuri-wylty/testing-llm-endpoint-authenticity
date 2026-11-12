#!/usr/bin/env python3
"""Render a single-file HTML model-conformance report from spec_audit / model_audit JSON.

Usage:
  python scripts/report_html.py --audit <path> [--spec <path>] --out <path> [--title TEXT]

The report is fully self-contained: inline CSS, no JavaScript, no external resources.
Every dynamic string is HTML-escaped; all numbers come straight from the JSON inputs
(only pass counts and bar widths are derived for presentation).
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import scrub  # noqa: E402
from benchmark import load_profile  # noqa: E402
from sanitize import normalize_audit, normalize_spec  # noqa: E402

from html_components import lab, dig, esc_raw  # noqa: E402
import html_sec_core  # noqa: E402
import html_sec_spec  # noqa: E402
from html_theme import (  # noqa: E402
    CSS, HIERARCHY, render_footer, render_hero, render_masthead, render_nav, render_theme_toggle_input,
)
from html_sec_core import render_baseline, render_core, render_findings, render_metrics  # noqa: E402
from html_sec_spec import render_audit, render_billing, render_spec_items  # noqa: E402

SK_RE = re.compile(r"sk-[A-Za-z0-9_-]{8,}")


# --------------------------------------------------------------------------- data helpers

def load_json(path, key):
    """Read JSON text, scrub the API key, mask sk-* tokens, then parse."""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    text = scrub(text, key)
    text = SK_RE.sub("sk-***", text)
    return json.loads(text)


# --------------------------------------------------------------------------- assembly

def build_css():
    """Theme CSS + the optional per-section ``CSS_EXTRA`` strings (missing -> empty)."""
    return CSS + (getattr(html_sec_core, "CSS_EXTRA", "") or "") + (getattr(html_sec_spec, "CSS_EXTRA", "") or "") + HIERARCHY


def render_profile_row(audit, spec_doc):
    """Single-line test configuration strip under the hero (reference report's 接口 / 并发 / ... row)."""
    load = dig(spec_doc, "load") or {}
    proto = dig(spec_doc, "protocol") or dig(audit, "protocol")
    prof = dig(spec_doc, "profile") or dig(audit, "profile")
    pairs = [("协议", proto), ("画像", prof), ("档位", dig(spec_doc, "tier")),
             ("样本数", load.get("samples") if isinstance(load, dict) else None),
             ("成功", load.get("success") if isinstance(load, dict) else None)]
    glossed = {"协议", "档位"}  # English enum values get 英文（中文） via the glossary; others are plain data
    cells = "".join('<span>%s<b>%s</b></span>' % (
        esc_raw(k), (lab(v) if k in glossed else esc_raw(v)) if v not in (None, "") else "未测") for k, v in pairs)
    return '<div class="profile">%s</div>' % cells


def build_report(audit, spec_doc, title, profile=None, final=None):
    audit, spec_doc = normalize_audit(audit), normalize_spec(spec_doc)  # malformed input degrades to 未测, never raises
    title = SK_RE.sub("sk-***", str(title))  # --title is user text: mask sk- tokens like every other string
    body = [
        render_theme_toggle_input(),
        render_masthead(audit, spec_doc, title, profile),
        '<div class="layout">',
        render_nav(),
        '<main class="page" id="main">',
        render_hero(audit, spec_doc, title, profile, final),
        render_profile_row(audit, spec_doc),
        render_findings(audit, spec_doc, profile),
        render_core(spec_doc, profile),
        render_spec_items(spec_doc, profile),
        render_metrics(spec_doc, profile),
        render_baseline(spec_doc, profile),
        render_billing(spec_doc, profile),
        render_audit(audit),
        render_footer(spec_doc, profile),
        "</main></div>",
    ]
    model = dig(spec_doc, "model") or dig(audit, "model") or ""
    doc_title = f"{title} · {model}".strip(" ·")
    return (
        '<!DOCTYPE html>\n<html lang="zh-CN"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        f"<title>{esc_raw(doc_title)}</title><style>{build_css()}</style></head><body>"
        + "".join(body)
        + "</body></html>\n"
    )


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Render a single-file HTML model conformance report.")
    p.add_argument("--audit", required=True, help="model_audit v2 JSON output")
    p.add_argument("--spec", default="", help="spec_audit JSON output (optional)")
    p.add_argument("--profile", default="", help="profile id or path (official baseline values; optional)")
    p.add_argument("--final", default="", help="final conclusion JSON written by the agent (channel_score, models, "
                   "channel, notes, summary); shown first in the hero and outranks the script verdict")
    p.add_argument("--out", required=True, help="output HTML path")
    p.add_argument("--title", default="模型渠道真实性报告", help="report title")
    args = p.parse_args()

    key = os.environ.get("LLM_API_KEY", "")
    args.title = scrub(args.title, key)  # the title may carry the key (e.g. expanded from $LLM_API_KEY)

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

    final = None
    if args.final:
        try:
            final = load_json(args.final, key)
        except (OSError, ValueError) as e:
            sys.exit(f"error: cannot read final JSON {args.final}: {e}")

    profile = load_profile(args.profile) if args.profile else None
    doc = build_report(audit, spec_doc, args.title, profile, final)
    out_path = os.path.abspath(args.out)
    parent = os.path.dirname(out_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(doc)
    print(f"wrote {out_path} ({os.path.getsize(out_path):,} bytes)")


if __name__ == "__main__":
    main()
