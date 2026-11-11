#!/usr/bin/env python3
"""Selftest for scripts/report_html.py (single-file HTML report renderer).

  python selftest_report_html.py

Renders tests/fixtures/sample_audit.json + tests/fixtures/sample_spec.json to
tests/_out/test-dev/report.html and asserts:
  - the file exists, is non-empty, and has no http:// / https:// external refs
  - no <script tag, no "test-key"
  - all 15 spec item titles are present, and both scores (96 and 89) appear
Then writes a copy of the audit fixture with an evil note (a <script> payload and a
fake sk- key) to tests/_out/test-dev/evil.json, renders it, and asserts the output
contains &lt;script&gt; and leaks neither the raw tag nor the raw key.
Finally injects an evil TTL response header / 58-minute read-back into the spec
fixture and asserts the rendered report escapes it (no raw <img> tag).

Exit codes: 0 pass, 1 assertion failure, 2 scripts/report_html.py not ready (Wave 2).
Only tests/_out/test-dev/ is written; no network is used.
"""
import json
import os
import re
import subprocess
import sys
from html import escape

HERE = os.path.dirname(os.path.abspath(__file__))
S_DIR = os.path.dirname(HERE)
SCRIPTS = os.path.join(S_DIR, "scripts")
REPORT = os.path.join(SCRIPTS, "report_html.py")
AUDIT_FIXTURE = os.path.join(HERE, "fixtures", "sample_audit.json")
SPEC_FIXTURE = os.path.join(HERE, "fixtures", "sample_spec.json")
OUT_DIR = os.path.join(HERE, "_out", "test-dev")
REPORT_HTML = os.path.join(OUT_DIR, "report.html")
EVIL_JSON = os.path.join(OUT_DIR, "evil.json")
EVIL_HTML = os.path.join(OUT_DIR, "evil.html")
EVIL_SPEC_JSON = os.path.join(OUT_DIR, "evil_spec.json")
EVIL_SPEC_HTML = os.path.join(OUT_DIR, "evil_spec.html")
RAW_KEY = "sk-abcdefgh12345678"
EVIL_NOTE = "<script>alert(1)</script> " + RAW_KEY
EVIL_HEADER = '<img src=x onerror=alert(1)>'
KEY_LIKE = re.compile(r"sk-[A-Za-z0-9]{8,}")


def _say(text):
    print(text, flush=True)


def _load_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_text(path):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def _tail(text, limit=2500):
    text = (text or "").strip()
    return text if len(text) <= limit else "..." + text[-limit:]


def _env():
    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    return env


def _render(audit_path, out_path, timeout=180, spec_path=SPEC_FIXTURE):
    """Run report_html.py; -> (proc, command) or raises TimeoutExpired."""
    command = [sys.executable, REPORT, "--audit", audit_path,
               "--spec", spec_path, "--out", out_path]
    proc = subprocess.run(command, cwd=S_DIR, env=_env(), capture_output=True, timeout=timeout)
    return proc, command


def _run_step(label, audit_path, out_path, spec_path=SPEC_FIXTURE):
    """Render once and return the HTML text, or (None, fails) after reporting the failure."""
    fails = []
    if os.path.isfile(out_path):
        os.remove(out_path)
    try:
        proc, command = _render(audit_path, out_path, spec_path=spec_path)
    except subprocess.TimeoutExpired as exc:
        _say("FAIL %s：report_html.py 超过 %ss 未完成（%s）" % (label, 180, exc))
        return None, [label + " timeout"]
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace") or proc.stdout.decode("utf-8", "replace")
        fails.append("report_html.py 退出码 %s" % proc.returncode)
        _say("FAIL %s：%s" % (label, fails[-1]))
        _say("  命令：%s" % " ".join(command))
        if detail.strip():
            _say("  stderr：\n" + _tail(detail))
        return None, fails
    if not os.path.isfile(out_path):
        fails.append("未生成输出文件 %s" % out_path)
        _say("FAIL %s：%s" % (label, fails[-1]))
        return None, fails
    text = _read_text(out_path)
    if not text.strip():
        fails.append("输出文件为空")
        _say("FAIL %s：输出文件为空" % label)
        return None, fails
    return text, fails


def _spec_block(doc):
    """sample_spec.json is a full spec_audit output; its items live under the top-level spec block."""
    nested = doc.get("spec") if isinstance(doc, dict) else None
    return nested if isinstance(nested, dict) else doc


def check_render(label, audit_path, out_path, spec_doc, audit_score):
    """Shared structural assertions; returns list of failure strings (empty = pass)."""
    text, fails = _run_step(label, audit_path, out_path)
    if text is None:
        return fails
    low = text.lower()

    if "http://" in low or "https://" in low:
        fails.append("输出包含外链 http(s)://")
    if "<script" in low:
        fails.append("输出包含原始 <script 标签")
    if "test-key" in low:
        fails.append('输出泄漏测试 key 字符串 "test-key"')

    spec = _spec_block(spec_doc)
    items = spec.get("items") if isinstance(spec, dict) else None
    items = items if isinstance(items, list) else []
    if len(items) != 15:
        fails.append("spec fixture 规范项数量 %d（期望 15）" % len(items))
    missing = []
    for pos, item in enumerate(items, 1):
        title = item.get("title") if isinstance(item, dict) else None
        if not isinstance(title, str) or not title:
            fails.append("spec fixture 第 %d 项缺少 title" % pos)
            continue
        if title not in text and escape(title) not in text:
            missing.append("%d:%s" % (pos, title))
    if missing:
        fails.append("输出缺少 %d 个规范项标题：%s" % (len(missing), "；".join(missing)))

    if str(audit_score) not in text:
        fails.append("输出缺少 model audit 分数 %r" % audit_score)
    spec_score = spec.get("score") if isinstance(spec, dict) else None
    if spec_score is not None and str(spec_score) not in text:
        fails.append("输出缺少 spec 分数 %r" % spec_score)

    return fails


def check_escaping():
    """Evil-note render: markup escaped, fake key scrubbed."""
    fails = []
    try:
        evil = _load_json(AUDIT_FIXTURE)
    except Exception as exc:
        return ["读取 sample_audit.json 失败：%s: %s" % (type(exc).__name__, exc)]
    checks = evil.get("checks")
    if not isinstance(checks, list) or not checks or not isinstance(checks[0], dict):
        return ["sample_audit.json 缺少 checks[0]，无法注入恶意 note"]
    checks[0]["note"] = EVIL_NOTE
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(EVIL_JSON, "w", encoding="utf-8") as handle:
        json.dump(evil, handle, ensure_ascii=False, indent=2)

    text, fails = _run_step("evil-note 转义", EVIL_JSON, EVIL_HTML)
    if text is None:
        return fails
    low = text.lower()
    if "&lt;script&gt;" not in low:
        fails.append("输出未包含转义后的 &lt;script&gt;")
    if "<script>alert" in low:
        fails.append("输出包含原始 <script>alert 载荷")
    if RAW_KEY in text:
        fails.append("输出包含原始 sk- key")
    match = KEY_LIKE.search(text)
    if match:
        fails.append("输出包含未清洗的 key 形式：%s" % match.group(0))
    return fails


def check_spec_escaping():
    """TTL response headers and 58-minute read-back are remote strings: must be escaped."""
    fails = []
    try:
        spec = _load_json(SPEC_FIXTURE)
    except Exception as exc:
        return ["读取 sample_spec.json 失败：%s: %s" % (type(exc).__name__, exc)]
    cache = spec.get("cache") if isinstance(spec, dict) else None
    if not isinstance(cache, dict):
        return ["sample_spec.json 缺少 cache，无法注入恶意响应头"]
    ttl_5m = cache.get("ttl_5m")
    ttl_1h = cache.get("ttl_1h")
    if not isinstance(ttl_5m, dict) or not isinstance(ttl_1h, dict):
        return ["sample_spec.json 缺少 ttl_5m/ttl_1h，无法注入恶意响应头"]
    ttl_5m["header"] = EVIL_HEADER
    ttl_1h["header"] = EVIL_HEADER
    ttl_1h["read_back_58m"] = EVIL_HEADER
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(EVIL_SPEC_JSON, "w", encoding="utf-8") as handle:
        json.dump(spec, handle, ensure_ascii=False, indent=2)

    text, fails = _run_step("恶意响应头转义", AUDIT_FIXTURE, EVIL_SPEC_HTML, spec_path=EVIL_SPEC_JSON)
    if text is None:
        return fails
    low = text.lower()
    if "&lt;img" not in low:
        fails.append("输出未包含转义后的 &lt;img")
    if "<img src=x" in low:
        fails.append("输出包含原始 <img> 载荷（响应头未转义）")
    return fails


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not os.path.isfile(REPORT):
        _say("report_html.py not ready (pending Wave 2)")
        _say("缺少 %s；请等 Wave 2 的 report_html.py 就绪后再跑本自测。" % REPORT)
        return 2
    os.makedirs(OUT_DIR, exist_ok=True)

    try:
        audit = _load_json(AUDIT_FIXTURE)
        spec = _load_json(SPEC_FIXTURE)
    except Exception as exc:
        _say("FAIL 读取 fixture：%s: %s" % (type(exc).__name__, exc))
        return 1
    audit_score = audit.get("score") if isinstance(audit, dict) else None

    fails = check_render("report_html 渲染", AUDIT_FIXTURE, REPORT_HTML, spec, audit_score)
    if fails:
        _say("FAIL report_html 渲染：%d 项断言未通过" % len(fails))
        for fail in fails:
            _say("  - " + fail)
        return 1
    _say("PASS report_html 渲染：15 项标题齐全，含分数 96/89，无外链、无 <script、无 test-key")
    _say("  输出：%s" % REPORT_HTML)

    fails = check_escaping()
    if fails:
        _say("FAIL report_html 转义：%d 项断言未通过" % len(fails))
        for fail in fails:
            _say("  - " + fail)
        return 1
    _say("PASS report_html 转义：<script> 输出为 &lt;script&gt;，原始 sk- key 未泄漏")
    _say("  输出：%s" % EVIL_HTML)

    fails = check_spec_escaping()
    if fails:
        _say("FAIL report_html 响应头转义：%d 项断言未通过" % len(fails))
        for fail in fails:
            _say("  - " + fail)
        return 1
    _say("PASS report_html 响应头转义：<img> 载荷被转义")
    _say("  输出：%s" % EVIL_SPEC_HTML)
    return 0


if __name__ == "__main__":
    sys.exit(main())
