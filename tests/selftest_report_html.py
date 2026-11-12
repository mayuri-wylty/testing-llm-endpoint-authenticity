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

PROFILE_ID = "kimi-k3.1-pro"
LONG_DECIMAL = re.compile(r"(?<![\w.\-])\d+\.\d{3,}(?![\w.])")


def _visible(text):
    """Visible text only: drop <style>, <pre> (raw data) and tags."""
    text = re.sub(r"<style.*?</style>|<pre.*?</pre>", " ", text, flags=re.S)
    return re.sub(r"<[^>]+>", " ", text)


def check_redesign():
    """2-decimal display, bilingual labels, official/measured cells, theme/print, degradation."""
    fails = []
    out = os.path.join(OUT_DIR, "report_profile.html")
    command = [sys.executable, REPORT, "--audit", AUDIT_FIXTURE, "--spec", SPEC_FIXTURE,
               "--profile", PROFILE_ID, "--out", out]
    proc = subprocess.run(command, cwd=S_DIR, env=_env(), capture_output=True, timeout=180)
    if proc.returncode != 0:
        return ["--profile 渲染退出码 %s：%s" % (proc.returncode, _tail(proc.stderr.decode("utf-8", "replace")))]
    text = _read_text(out)
    vis = _visible(text)
    bad = sorted(set(LONG_DECIMAL.findall(vis)))
    if bad:
        fails.append("可见文本含 >=3 位小数：%s" % bad[:8])
    if "官方" not in vis or "实测" not in vis or "ov-o" not in text or "ov-m" not in text:
        fails.append("缺少“官方 / 实测”单元格")
    if not re.search(r'class="ov-o">\d', text):
        fails.append("带 --profile 时应出现数值型官方值")
    for needle in ("prefers-color-scheme: dark", "@media print", "theme-toggle", 'class="table-wrap"'):
        if needle not in text:
            fails.append("缺少 %s" % needle)
    if "<script" in text.lower() or re.search(r"https?://", text):
        fails.append("含 <script 或外链")
    sys.path.insert(0, SCRIPTS)
    try:
        import warnings
        warnings.simplefilter("ignore")
        import i18n_zh
        missing = i18n_zh.lint_html(text)
        if missing:
            fails.append("存在未补译英文：%s" % missing[:12])
    except Exception as exc:  # noqa: BLE001
        fails.append("lint_html 不可用：%s: %s" % (type(exc).__name__, exc))
    # degradation: no --profile -> official shown as 未提供 but still renders; same for no --spec
    out2 = os.path.join(OUT_DIR, "report_noprofile.html")
    proc = subprocess.run([sys.executable, REPORT, "--audit", AUDIT_FIXTURE, "--spec", SPEC_FIXTURE,
                           "--out", out2], cwd=S_DIR, env=_env(), capture_output=True, timeout=180)
    nop = _read_text(out2) if proc.returncode == 0 else ""
    if proc.returncode != 0 or nop.count("未提供") <= text.count("未提供") or re.search(r'class="ov-o">\d', nop):
        fails.append("无 --profile 降级失败（官方值应全部为“未提供”）")
    out3 = os.path.join(OUT_DIR, "report_nospec.html")
    proc = subprocess.run([sys.executable, REPORT, "--audit", AUDIT_FIXTURE, "--out", out3],
                          cwd=S_DIR, env=_env(), capture_output=True, timeout=180)
    if proc.returncode != 0:
        fails.append("无 --spec 渲染失败")
    return fails


# ----------------------------------------------------------------------------- malformed input / title key / lint
MD_REPORT = os.path.join(SCRIPTS, "report_md.py")
TITLE_KEY = "sk-TESTKEY1234567890abcdef"


def _run_cli(script, audit_path, out_path, spec_path="", extra=None, env_extra=None):
    cmd = [sys.executable, script, "--audit", audit_path, "--out", out_path]
    if spec_path:
        cmd += ["--spec", spec_path]
    cmd += list(extra or [])
    env = _env()
    env.pop("LLM_API_KEY", None)
    env.update(env_extra or {})
    return subprocess.run(cmd, cwd=S_DIR, env=env, capture_output=True, timeout=180)


def _write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def _malformed_cases():
    """(label, audit_json_text | None, spec_json_text | None); None = use the clean fixture. NaN / Infinity are raw literals."""
    audit_txt = _read_text(AUDIT_FIXTURE)
    spec_doc = _load_json(SPEC_FIXTURE)
    audit_doc = _load_json(AUDIT_FIXTURE)

    def a(mut, raw=None):
        d = json.loads(json.dumps(audit_doc))
        mut(d)
        text = json.dumps(d, ensure_ascii=False)
        return text.replace('"@@RAW@@"', raw) if raw else text

    def sp(mut):
        d = json.loads(json.dumps(spec_doc))
        mut(d)
        return json.dumps(d, ensure_ascii=False)

    def setitem(i, k, v):
        return lambda d: d["spec"]["items"][i].__setitem__(k, v)

    cases = [
        ("audit=[]", "[]", None), ("audit=\"x\"", '"x"', None), ("audit=null", "null", None),
        ("score NaN", a(lambda d: d.__setitem__("score", "@@RAW@@"), "NaN"), None),
        ("score Infinity", a(lambda d: d.__setitem__("score", "@@RAW@@"), "Infinity"), None),
        ("performance 1e999", a(lambda d: d.__setitem__("performance", "@@RAW@@"), "1e999"), None),
        ("verdict=5", a(lambda d: d.__setitem__("verdict", 5)), None),
        ("model=[1]", a(lambda d: d.__setitem__("model", [1])), None),
        ("checks[0].name=[1]", a(lambda d: d["checks"][0].__setitem__("name", [1])), None),
        ("checks[0]=None", a(lambda d: d["checks"].__setitem__(0, None)), None),
        ("spec=[]", None, "[]"), ("spec=\"x\"", None, '"x"'),
        ("items[0]=None", None, sp(lambda d: d["spec"]["items"].__setitem__(0, None))),
        ("items[1].measured=str", None, sp(setitem(1, "measured", "x"))),
        ("items[1].expected=str", None, sp(setitem(1, "expected", "x"))),
        ("items[1].delta=str", None, sp(setitem(1, "delta", "x"))),
        ("items[2].status=list", None, sp(setitem(2, "status", ["pass"]))),
        ("spec.model=list", None, sp(lambda d: d.__setitem__("model", ["a"]))),
        ("load=str", None, sp(lambda d: d.__setitem__("load", "x"))),
        ("cache=str", None, sp(lambda d: d.__setitem__("cache", "x"))),
        ("probes.usage=str", None, sp(lambda d: d["probes"].__setitem__("usage", "x"))),
    ]
    return audit_txt, cases


def check_malformed_cli():
    """Malformed audit/spec JSON: html AND md must exit 0 and degrade to 未测/未提供, never crash."""
    fails = []
    audit_txt, cases = _malformed_cases()
    base = os.path.join(OUT_DIR, "malformed")
    for idx, (label, atxt, stxt) in enumerate(cases):
        ap, sp_ = os.path.join(base, "a%d.json" % idx), os.path.join(base, "s%d.json" % idx)
        _write_text(ap, atxt if atxt is not None else audit_txt)
        if stxt is not None:
            _write_text(sp_, stxt)
        spec_arg = sp_ if stxt is not None else SPEC_FIXTURE
        for kind, script, ext in (("html", REPORT, "html"), ("md", MD_REPORT, "md")):
            out = os.path.join(base, "o%d.%s" % (idx, ext))
            if os.path.isfile(out):
                os.remove(out)
            proc = _run_cli(script, ap, out, spec_arg, extra=["--profile", PROFILE_ID])
            if proc.returncode != 0:
                fails.append("%s %s 退出码 %s：%s" % (kind, label, proc.returncode,
                                                     _tail(proc.stderr.decode("utf-8", "replace"), 300)))
                continue
            text = _read_text(out)
            if "未测" not in text and "未提供" not in text:
                fails.append("%s %s 输出不含“未测/未提供”" % (kind, label))
    return fails


def check_malformed_mutation(samples=300):
    """Leaf mutation: replace fixture leaves by None/[]/{}/'x'/1e999/[1]/{'a':1}, render html+md in-process, none may raise."""
    import copy
    import random
    import warnings
    warnings.simplefilter("ignore")
    if SCRIPTS not in sys.path:
        sys.path.insert(0, SCRIPTS)
    import benchmark
    import report_html
    import report_md
    audit0, spec0 = _load_json(AUDIT_FIXTURE), _load_json(SPEC_FIXTURE)
    profile = benchmark.load_profile(PROFILE_ID)

    def leaves(o, path=()):
        if isinstance(o, dict):
            for k, v in o.items():
                yield from leaves(v, path + (k,))
        elif isinstance(o, list):
            for i, v in enumerate(o):
                yield from leaves(v, path + (i,))
        if path:
            yield path

    muts = [None, [], {}, "x", float("inf"), float("nan"), [1], {"a": 1}]
    pool = [("a", p) for p in leaves(audit0)] + [("s", p) for p in leaves(spec0)]
    rng = random.Random(20261112)
    picks = [(w, p, rng.choice(muts)) for w, p in rng.sample(pool, min(samples, len(pool)))]
    # always include the named cases of the brief, whatever the sampling drew
    picks += [("a", ("score",), float("nan")), ("a", ("verdict",), 5), ("a", ("model",), [1]),
              ("s", ("model",), [1]), ("s", ("spec", "items", 0), None), ("s", ("spec", "items", 1, "measured"), "x"),
              ("s", ("load",), "x"), ("s", ("cache",), "x"), ("s", ("probes", "usage"), "x"),
              ("s", ("spec", "items", 2, "status"), ["pass"]), ("a", ("checks", 0, "name"), [1])]
    fails = []
    for w, path, mut in picks:
        a, sdoc = copy.deepcopy(audit0), copy.deepcopy(spec0)
        o = a if w == "a" else sdoc
        for k in path[:-1]:
            o = o[k]
        o[path[-1]] = copy.deepcopy(mut)
        for kind, fn in (("html", lambda: report_html.build_report(a, sdoc, "T", profile)),
                         ("md", lambda: report_md.build_report(a, sdoc, profile, "T"))):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                fails.append("%s 变异 %s%r=%r 抛出 %s: %s" % (kind, w, list(path), mut, type(exc).__name__, exc))
                if len(fails) >= 8:
                    return fails
    return fails


def check_title_key():
    """--title carrying the API key must be masked in <title>/h1 (html) as in md; env key and sk- token shapes."""
    fails = []
    for env_key, title in ((TITLE_KEY, "T " + TITLE_KEY), (TITLE_KEY, "T $LLM_API_KEY " + TITLE_KEY),
                           ("plainsecretvalue99", "T plainsecretvalue99 tail")):
        for kind, script, ext in (("html", REPORT, "html"), ("md", MD_REPORT, "md")):
            out = os.path.join(OUT_DIR, "title_key.%s" % ext)
            if os.path.isfile(out):
                os.remove(out)
            proc = _run_cli(script, AUDIT_FIXTURE, out, SPEC_FIXTURE, extra=["--title", title],
                            env_extra={"LLM_API_KEY": env_key})
            if proc.returncode != 0:
                fails.append("%s --title 渲染退出码 %s" % (kind, proc.returncode))
                continue
            text = _read_text(out)
            if env_key in text or KEY_LIKE.search(text) or "TESTKEY1234" in text:
                fails.append("%s --title 泄漏 key（title=%r）" % (kind, title))
    return fails


def check_nospec_lint():
    """Whole-page lint of a render WITHOUT --spec must be empty (placeholder text goes through bi()/lab)."""
    sys.path.insert(0, SCRIPTS)
    import warnings
    warnings.simplefilter("ignore")
    import i18n_zh
    out = os.path.join(OUT_DIR, "report_nospec_lint.html")
    proc = _run_cli(REPORT, AUDIT_FIXTURE, out)
    if proc.returncode != 0:
        return ["无 --spec 渲染退出码 %s" % proc.returncode]
    text = _read_text(out)
    fails = []
    missing = i18n_zh.lint_html(text)
    if missing:
        fails.append("无 --spec 整页 lint 非空：%s" % missing[:12])
    if "未提供" not in text:
        fails.append("无 --spec 页面应含“未提供”")
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

    fails = check_redesign()
    if fails:
        _say("FAIL report_html 重构要求：%d 项断言未通过" % len(fails))
        for fail in fails:
            _say("  - " + fail)
        return 1
    _say("PASS report_html 重构要求：小数<=2 位、英文均补中文、官方/实测单元格、深色/打印/切换、降级不报错")

    for name, fn, ok_msg in (
        ("畸形输入（CLI，html+md）", check_malformed_cli,
         "audit/spec 畸形（[]、\"x\"、NaN/Infinity、None 项、str 容器、list 状态…）均退出 0 且含未测/未提供"),
        ("畸形输入（叶子变异抽样）", check_malformed_mutation, "300+ 个叶子变异渲染 html+md 均不抛异常"),
        ("--title 密钥掩码", check_title_key, "html/md 的 <title>/h1 不含 LLM_API_KEY 与 sk- 令牌"),
        ("无 --spec 整页 lint", check_nospec_lint, "无 --spec 渲染整页 lint 为空，含“未提供”"),
    ):
        fails = fn()
        if fails:
            _say("FAIL report_html %s：%d 项断言未通过" % (name, len(fails)))
            for fail in fails:
                _say("  - " + fail)
            return 1
        _say("PASS report_html %s：%s" % (name, ok_msg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
