#!/usr/bin/env python3
"""Selftest for scripts/report_md.py (deterministic markdown report renderer).

  python selftest_report_md.py

Renders tests/fixtures/sample_audit.json + sample_spec.json (+ --profile kimi-k3.1-pro) to
tests/_out/test-dev/report.md and asserts:
  1. render succeeds, non-empty;
  2. no number with >= 3 decimals (version strings / dates / model names / IPs are protected);
  3. all 15 spec titles + audit score (96) + spec score (89) appear;
  4. the official-vs-measured table header ("官方 / 实测") and at least one numeric row;
  5. degraded runs (no --spec / no --profile / neither) exit 0 and say 未测 or 未提供;
  6. evil fixtures (<script>, sk- key, <img onerror>, '|' and newline in notes/headers): no raw <script / <img,
     no sk- token, and every table row keeps the header's column count.
Only tests/_out/test-dev/ is written; no network is used. Exit 0 pass, 1 failure.
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
S_DIR = os.path.dirname(HERE)
REPORT = os.path.join(S_DIR, "scripts", "report_md.py")
AUDIT_FIXTURE = os.path.join(HERE, "fixtures", "sample_audit.json")
SPEC_FIXTURE = os.path.join(HERE, "fixtures", "sample_spec.json")
OUT_DIR = os.path.join(HERE, "_out", "test-dev")
RAW_KEY = "sk-abcdefgh12345678"
EVIL_NOTE = "<script>alert(1)</script> " + RAW_KEY + " a|b | c\nline2 `tick` *star* [x](http://e.x) _u_"
EVIL_HEADER = "<img src=x onerror=alert(1)> | pipe\nnewline"
KEY_LIKE = re.compile(r"sk-[A-Za-z0-9]{8,}")
LONG_DECIMAL = re.compile(r"\d+\.\d{3,}")
# protected tokens: model names with embedded versions, ISO dates/times, dotted quads, 1.2.3 style versions
PROTECT = re.compile(r"kimi-k\d+\.\d+-pro|\d{4}-\d{2}-\d{2}(?:T[\d:.+\-Z]+)?|\b\d+\.\d+\.\d+(?:\.\d+)*\b")


def say(text):
    print(text, flush=True)


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def env():
    e = dict(os.environ)
    e.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    e.pop("LLM_API_KEY", None)
    return e


def render(audit, out, spec=SPEC_FIXTURE, profile="kimi-k3.1-pro"):
    """-> (text | None, [fail])."""
    cmd = [sys.executable, REPORT, "--audit", audit, "--out", out]
    if spec:
        cmd += ["--spec", spec]
    if profile:
        cmd += ["--profile", profile]
    if os.path.isfile(out):
        os.remove(out)
    try:
        proc = subprocess.run(cmd, cwd=S_DIR, env=env(), capture_output=True, timeout=120)
    except subprocess.TimeoutExpired:
        return None, ["report_md.py 超时：" + " ".join(cmd)]
    if proc.returncode != 0:
        return None, ["退出码 %s：%s" % (proc.returncode, (proc.stderr or proc.stdout).decode("utf-8", "replace")[-1500:])]
    if not os.path.isfile(out):
        return None, ["未生成输出文件 " + out]
    with open(out, "r", encoding="utf-8") as f:
        text = f.read()
    if not text.strip():
        return None, ["输出文件为空"]
    return text, []


def unescape(text):
    return re.sub(r"\\(.)", r"\1", text)


def split_cells(line):
    """Split a GFM table row on unescaped pipes -> list of cells."""
    body = line.strip()
    cells, cur, i = [], [], 0
    if body.startswith("|"):
        body = body[1:]
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            cur.append(body[i:i + 2])
            i += 2
            continue
        if ch == "|":
            cells.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    if "".join(cur).strip():
        cells.append("".join(cur))
    return cells


def table_column_fails(text):
    """Every row of every table must have the header's column count."""
    fails, lines, i = [], text.split("\n"), 0
    in_fence = False
    while i < len(lines):
        if lines[i].startswith("```"):
            in_fence = not in_fence
        if not in_fence and lines[i].startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:\-|]+\|$", lines[i + 1]):
            n = len(split_cells(lines[i]))
            j = i
            while j < len(lines) and lines[j].startswith("|"):
                if len(split_cells(lines[j])) != n:
                    fails.append("表格行列数 %d != 表头 %d：%s" % (len(split_cells(lines[j])), n, lines[j][:120]))
                j += 1
            i = j
            continue
        i += 1
    return fails


def spec_items(spec):
    block = spec.get("spec") if isinstance(spec, dict) else None
    items = (block or {}).get("items") if isinstance(block, dict) else None
    return items if isinstance(items, list) else []


def check_main(audit_doc, spec_doc):
    out = os.path.join(OUT_DIR, "report.md")
    text, fails = render(AUDIT_FIXTURE, out)
    if text is None:
        return fails
    stripped = PROTECT.sub("", text)
    m = LONG_DECIMAL.search(stripped)
    if m:
        ctx = stripped[max(0, m.start() - 40):m.end() + 20].replace("\n", " ")
        fails.append("输出含 >=3 位小数：%s …%s…" % (m.group(0), ctx))
    plain = unescape(text)
    items = spec_items(spec_doc)
    if len(items) != 15:
        fails.append("spec fixture 规范项 %d（期望 15）" % len(items))
    miss = [it.get("title") for it in items if it.get("title") not in plain]
    if miss:
        fails.append("缺少规范项标题：%s" % miss)
    if str(audit_doc.get("score")) not in text:
        fails.append("缺少 audit 分数 %s" % audit_doc.get("score"))
    sp = (spec_doc.get("spec") or {}).get("score")
    if sp is not None and str(sp) not in text:
        fails.append("缺少 spec 分数 %s" % sp)
    if "官方 / 实测" not in text:
        fails.append("缺少“官方 / 实测”表头")
    if not re.search(r"\| *\d+\.\d{2} */ *\d+\.\d{2}", text):
        fails.append("官方 / 实测表缺少数值行（形如 2.46 / 3.10）")
    if "官方存档 2026-11-09" not in text:
        fails.append("页脚缺少 baseline_source 日期 2026-11-09")
    fails += table_column_fails(text)
    if KEY_LIKE.search(text) or "test-key" in text:
        fails.append("输出泄漏 key")
    return fails


def check_channel_score():
    """channel_score(): the shown 2-decimal subtotal and the rounded result must agree (no float 44.4999 -> 44 vs 44.50)."""
    from decimal import Decimal, ROUND_HALF_UP
    sys.path.insert(0, os.path.join(S_DIR, "scripts"))
    import report_md
    fails = []
    pat = re.compile(r"= ([0-9.]+)；扣分：(.*)；结果 (\d+)$")
    bad = 0
    for sc in range(0, 101):
        for pf in range(0, 101):
            final, text = report_md.channel_score({"score": sc, "performance": pf})
            m = pat.search(text)
            if not m:
                fails.append("channel_score(%d,%d) 文本无法解析：%s" % (sc, pf, text))
                return fails
            shown, why, res = Decimal(m.group(1)), m.group(2), int(m.group(3))
            ded = (10 if "假模型" in why else 0)
            want = max(0, min(100, int((shown - ded).quantize(Decimal(1), rounding=ROUND_HALF_UP))))
            exact = (Decimal(sc) * 7 + Decimal(pf) * 3) / 10
            if shown != exact.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) or res != want or final != res:
                bad += 1
                if bad <= 5:
                    fails.append("channel_score(%d,%d) 自相矛盾：%s（期望结果 %d）" % (sc, pf, text, want))
    if bad:
        fails.append("共 %d 组 score x performance 自相矛盾" % bad)
    final, text = report_md.channel_score({"score": 61, "performance": 6})
    if final != 45 or "= 44.50" not in text or "结果 45" not in text:
        fails.append("channel_score(61,6) 应为 44.50 -> 45，得到 %r %s" % (final, text))
    return fails


def check_degraded():
    fails = []
    for label, spec, profile in (("无 --spec", "", "kimi-k3.1-pro"), ("无 --profile", SPEC_FIXTURE, ""), ("二者皆无", "", "")):
        out = os.path.join(OUT_DIR, "degraded_%s.md" % re.sub(r"\W", "", label.encode("ascii", "ignore").decode() or str(len(label))))
        text, f = render(AUDIT_FIXTURE, out, spec=spec, profile=profile)
        if text is None:
            fails += ["%s：%s" % (label, x) for x in f]
            continue
        if "未测" not in text and "未提供" not in text:
            fails.append("%s：输出不含“未测/未提供”" % label)
        if LONG_DECIMAL.search(PROTECT.sub("", text)):
            fails.append("%s：输出含 >=3 位小数" % label)
        fails += ["%s：%s" % (label, x) for x in table_column_fails(text)]
        if label == "无 --profile" and "未提供" not in text:
            fails.append("无 --profile：官方列应为“未提供”")
    return fails


def check_evil():
    fails = []
    audit = load_json(AUDIT_FIXTURE)
    spec = load_json(SPEC_FIXTURE)
    audit["checks"][0]["note"] = EVIL_NOTE
    audit["performance_detail"] = EVIL_NOTE
    audit["notes"] = [EVIL_NOTE]
    spec["cache"]["ttl_5m"]["header"] = EVIL_HEADER
    spec["cache"]["ttl_1h"]["header"] = EVIL_HEADER
    spec["cache"]["ttl_1h"]["read_back_58m"] = EVIL_HEADER
    spec["spec"]["items"][1]["note"] = EVIL_NOTE
    spec["spec"]["items"][1]["evidence"] = [EVIL_NOTE, EVIL_HEADER]
    spec["spec"]["items"][2]["title"] = "evil | title <b>x</b>"
    spec["hidden_prompt"]["verdict"] = EVIL_NOTE
    ea, es = os.path.join(OUT_DIR, "evil_audit.json"), os.path.join(OUT_DIR, "evil_spec.json")
    write_json(ea, audit)
    write_json(es, spec)
    text, f = render(ea, os.path.join(OUT_DIR, "evil.md"), spec=es)
    if text is None:
        return f
    low = text.lower()
    for bad in ("<script", "<img", "<b>"):
        if bad in low:
            fails.append("输出含原始标签 " + bad)
    if RAW_KEY in text or KEY_LIKE.search(text):
        fails.append("输出含 sk- key")
    if "&lt;script" not in low:
        fails.append("未见转义后的 &lt;script")
    if "\\|" not in text:
        fails.append("`|` 未被转义")
    fails += table_column_fails(text)
    return fails


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not os.path.isfile(REPORT):
        say("report_md.py not ready: " + REPORT)
        return 2
    os.makedirs(OUT_DIR, exist_ok=True)
    try:
        audit_doc, spec_doc = load_json(AUDIT_FIXTURE), load_json(SPEC_FIXTURE)
    except Exception as exc:
        say("FAIL 读取 fixture：%s: %s" % (type(exc).__name__, exc))
        return 1
    rc = 0
    for name, fn, ok_msg in (
        ("渲染/小数位/标题/分数/官方实测表", lambda: check_main(audit_doc, spec_doc),
         "fixtures + --profile kimi-k3.1-pro 渲染成功，无 >=3 位小数，15 标题与分数 96/89 齐全，含“官方 / 实测”表"),
        ("降级（无 spec / 无 profile / 皆无）", check_degraded, "三种降级均退出 0 且含“未测/未提供”"),
        ("evil 转义", check_evil, "无 <script/<img、无 sk- key，表格列数未被 | 破坏"),
        ("渠道评分算式一致", check_channel_score, "score 0..100 x performance 0..100 共 10201 组，展示的 2 位小数与取整结果一致"),
    ):
        fails = fn()
        if fails:
            rc = 1
            say("FAIL %s：%d 项断言未通过" % (name, len(fails)))
            for x in fails:
                say("  - " + x)
        else:
            say("PASS %s：%s" % (name, ok_msg))
    say("  输出目录：%s" % OUT_DIR)
    return rc


if __name__ == "__main__":
    sys.exit(main())
