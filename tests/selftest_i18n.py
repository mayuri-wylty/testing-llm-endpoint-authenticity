#!/usr/bin/env python3
"""Self-test for scripts/i18n_zh.py (pure text, no network). Exit 0 = all checks pass.

  python tests/selftest_i18n.py
"""
import ast
import json
import os
import re
import sys
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import i18n_zh as z  # noqa: E402

z.WARN = False

fails = []
checks = 0


def check(cond, msg):
    global checks
    checks += 1
    if not cond:
        fails.append(msg)
        print("FAIL", msg)


def eq(got, want, msg):
    check(got == want, "%s: got %r, want %r" % (msg, got, want))


def load(path):
    with open(os.path.join(ROOT, path), "r", encoding="utf-8") as f:
        return json.load(f)


def literal_from(path, name):
    """Value of a top-level ``name = <literal>`` assignment, read with ast so no module is imported."""
    with open(os.path.join(ROOT, path), "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise KeyError("%s:%s" % (path, name))


# --------------------------------------------------------------------------- glossary shape
low_keys = [k.lower() for k in z.GLOSSARY]
check(len(low_keys) == len(set(low_keys)), "glossary keys are unique ignoring case")
check(len(z.GLOSSARY) >= 200, "glossary has at least 200 entries (got %d)" % len(z.GLOSSARY))
for k, v in z.GLOSSARY.items():
    check(isinstance(v, str) and any("一" <= c <= "鿿" for c in v), "gloss of %r contains Chinese" % k)
    check("（" not in v and "）" not in v, "gloss of %r has no full-width parentheses" % k)
    check("<" not in v and ">" not in v, "gloss of %r has no markup" % k)

# --------------------------------------------------------------------------- bi
z.MISSING.clear()
eq(z.bi("finish_reason"), "finish_reason（结束原因）", "bi known identifier")
eq(z.bi("pass"), "pass（通过）", "bi status word")
eq(z.bi("PASS"), "PASS（通过）", "bi is case-insensitive and keeps the caller's casing")
eq(z.bi("TTFT"), "TTFT（首字延迟）", "bi metric")
eq(z.bi("P90"), "P90（90 分位）", "bi percentile pattern")
eq(z.bi("stop/length"), "stop/length（正常结束/长度截断）", "bi composite")
eq(z.bi("load.percentiles.ttft_s"), "load.percentiles.ttft_s（负载·分位数·首字延迟秒数）", "bi dotted path")
eq(z.bi("time to first token"), "time to first token（首个 token 的等待时间）", "bi phrase")
eq(z.bi("msh-usage-cache-write-tokens-5m"), "msh-usage-cache-write-tokens-5m（5 分钟缓存写入 token 数响应头）", "bi header")
eq(z.bi("pass（通过）"), "pass（通过）", "bi does not double-gloss")
eq(z.bi(z.bi("pass")), z.bi("pass"), "bi is idempotent")
eq(z.bi("pass（自定义说明）"), "pass（自定义说明）", "bi keeps an existing Chinese gloss")
check(not z.MISSING, "bi of known words records nothing in MISSING: %r" % sorted(z.MISSING))
eq(z.bi("ms"), "ms", "bi leaves units alone")
eq(z.bi("kimi-k3.1-pro"), "kimi-k3.1-pro", "bi leaves model names alone")
eq(z.bi("relay.example.com"), "relay.example.com", "bi leaves hosts alone")
check(not z.MISSING, "units / model names / hosts are not MISSING: %r" % sorted(z.MISSING))
eq(z.bi("totally_unknown_word"), "totally_unknown_word", "bi returns unknown words unchanged")
check("totally_unknown_word" in z.MISSING, "bi records unknown words in MISSING")
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    z.WARN = True
    z.bi("another_unknown_word")
    z.WARN = False
check(len(caught) == 1 and "another_unknown_word" in str(caught[0].message), "bi warns (not raises) on unknown words")
eq(z.bi(""), "", "bi of empty string")
eq(z.zh("finish_reason"), "结束原因", "zh returns the bare gloss")
check(z.zh("nonsense_word") is None, "zh of unknown is None")

# --------------------------------------------------------------------------- annotate_text
eq(z.annotate_text("p50=3.101 p90=6.402"), "p50（50 分位）=3.101 p90（90 分位）=6.402", "numbers are untouched")
out = z.annotate_text("cache_write_tokens=4200")
eq(out, "cache_write_tokens（缓存写入 token 数）=4200", "longest identifier wins over its parts")
check(out.count("（") == 1, "cache_write_tokens gets exactly one gloss")
out = z.annotate_text("msh-usage-cache-write-tokens-5m=20144 / -1h=20080")
check(out.startswith("msh-usage-cache-write-tokens-5m（5 分钟缓存写入 token 数响应头）=20144"), "header annotated as a whole: %r" % out)
check(out.count("（") == 1, "header is not annotated word by word")
eq(z.annotate_text("completion_tokens_details.reasoning_tokens=842"),
   "completion_tokens_details.reasoning_tokens（输出明细·推理 token 数）=842", "dotted path composed")
eq(z.annotate_text("format+stream tools+json"), "format+stream（格式与流式） tools+json（工具与 JSON）", "check names with '+'")
eq(z.annotate_text("low/high/max 均返回 200"), "low/high/max（低档/高档/最高档） 均返回 200", "slash composite, Chinese text kept")
eq(z.annotate_text("未发现强注入迹象；故判 partial。"), "未发现强注入迹象；故判 partial（部分通过）。", "plain Chinese untouched, trailing dot kept")
eq(z.annotate_text("很普通的中文，没有标识符 12.345%"), "很普通的中文，没有标识符 12.345%", "pure Chinese / numbers unchanged")
key = "sk-abc123XYZ789secret"
eq(z.annotate_text("Bearer %s finish_reason" % key), "Bearer %s finish_reason（结束原因）" % key, "sk- string untouched")
eq(z.annotate_text("sk-***"), "sk-***", "masked sk- string untouched")
url = "https://relay.example.com/v1/chat?finish_reason=stop&usage=1"
eq(z.annotate_text("请求 %s 返回" % url), "请求 %s 返回" % url, "URL untouched")
eq(z.annotate_text("model kimi-k3.1-pro on relay.example.com"), "model（模型） kimi-k3.1-pro on relay.example.com", "model name and host untouched")
eq(z.annotate_text("case P:temp_0.6 T:choice_none"), "case（用例） P:temp_0.6 T:choice_none", "case ids untouched")
eq(z.annotate_text("GET /v1/formulas/moonshot/date:latest/tools -> 200"), "GET（获取） /v1/formulas/moonshot/date:latest/tools -> 200", "URL path untouched")
eq(z.annotate_text("tool_call id=get_weather_0_3f9a1c22"), "tool_call（单次工具调用） id（标识）=get_weather_0_3f9a1c22", "tool call id untouched")
eq(z.annotate_text("12 tokens 34 tok/s 56ms 7.8s"), "12 tokens 34 tok/s 56ms 7.8s", "units untouched")
eq(z.annotate_text("time to first token and TTFT"), "time to first token（首个 token 的等待时间） and TTFT（首字延迟）", "phrase matched as a unit")
eq(z.annotate_text("finish_reason（已有说明）"), "finish_reason（已有说明）", "already glossed text is not glossed twice")
for sample in ("finish_reason stop/length p90=6.4", "ttl_5m write_tokens=1 header=2", "A_identical_20k 99.6%"):
    once = z.annotate_text(sample)
    eq(z.annotate_text(once), once, "annotate_text is idempotent for %r" % sample)
for sample in ("a<b && c>d", "<b>finish_reason</b>"):
    out = z.annotate_text(sample)
    check(out.count("<") == sample.count("<") and out.count(">") == sample.count(">"), "annotate_text adds no markup: %r" % out)
eq(z.annotate_text(""), "", "annotate_text of empty string")
eq(z.annotate_text(None), None, "annotate_text passes non-strings through")

# --------------------------------------------------------------------------- lint_html
page = (
    "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"UTF-8\"><title>报告 · kimi-k3.1-pro</title>"
    "<style>.pill{color:red} body{font-family:sans-serif}</style><script>var finish_reason = 1;</script></head>"
    "<body><pre>{\"finish_reason\": \"stop\", \"foo_bar\": 1}</pre>"
    "<h2>真实性 pass（通过）</h2></body></html>"
)
eq(z.lint_html(page), [], "style/script/pre/attributes are exempt, glossed word passes")
eq(z.lint_html("<p>finish_reason</p>"), ["finish_reason"], "unglossed word is reported")
eq(z.lint_html("<p>finish_reason（结束原因）</p>"), [], "glossed word is not reported")
eq(z.lint_html("<p><b>finish_reason</b>（结束原因）</p>"), [], "inline tags join the gloss")
eq(z.lint_html("<td>pass</td><td>（通过）</td>"), ["pass"], "block boundaries separate a word from a gloss")
eq(z.lint_html("<p>pass 通过</p>"), ["pass"], "old-style 'pass 通过' pairing is reported")
eq(z.lint_html("<p>通过 pass</p>"), ["pass"], "reversed pairing is reported")
eq(z.lint_html("<p>foo_bar baz_qux foo_bar</p>"), ["foo_bar", "baz_qux"], "unknown words reported once, in order")
eq(z.lint_html("<p>time to first token（首个 token 的等待时间）</p>"), [], "a phrase run followed by a gloss counts as glossed")
eq(z.lint_html("<p>stop（正常结束）/length（长度截断）</p>"), [], "slash separated glossed words")
eq(z.lint_html("<p>Formula（官方 Formula 工具）</p>"), [], "English inside a gloss is not linted")
eq(z.lint_html("<p>6.4<small>s</small> 12 tok/s 3 ms 5 MB 2.5× 98 % 40 pp</p>"), [], "numbers and units are exempt")
eq(z.lint_html("<p>200 400 404 HTTP</p>"), ["HTTP"], "status codes are exempt, HTTP needs a gloss")
eq(z.lint_html("<p>kimi-k3.1-pro Kimi K3.1 Pro moonshot gpt-4o relay.example.com api.moonshot.cn</p>"), [],
   "model names and hosts are exempt")
eq(z.lint_html("<p>https://relay.example.com/v1/chat/completions?x=1 sk-*** sk-abc123XYZ789</p>"), [], "URLs and sk- strings are exempt")
eq(z.lint_html("<p>P:temp_0.6 T:choice_none E:formula_tools_noV1 A B C E</p>"), [], "case ids and single letters are exempt")
eq(z.lint_html("<p>get_weather_0_3f9a1c22 v2 1.2.3 2026-11-12T12:00:00+08:00</p>"), [], "ids, versions and dates are exempt")
eq(z.lint_html("<p>&lt;b&gt;stop&lt;/b&gt; &amp; length</p>"), ["stop", "length"], "entities are decoded, escaped markup is text")
eq(z.lint_html("<!-- finish_reason --><p>ok（正常）</p>"), [], "comments are ignored")
eq(z.lint_html("<p title=\"finish_reason\" aria-label='stop reason'>x</p>"), [], "attribute values are not visible text")
eq(z.lint_html("<p>my_extra_word</p>", allow={"my_extra_word"}), [], "allow= extends the exemptions")
eq(z.lint_html("<p>MY_EXTRA_WORD</p>", allow={"my_extra_word"}), [], "allow= is case-insensitive")
eq(z.lint_html("<p>stop</p><PRE>stop reason</PRE><p>length</p>"), ["stop", "length"], "uppercase PRE is exempt too")
# a page built purely with bi() / annotate_text() lints clean
built = (
    "<h2>%s</h2><table><tr><th>%s</th><th>%s</th></tr><tr><td>%s</td><td>%s</td></tr></table><p>%s</p>"
    % (z.bi("Request success rate"), z.bi("measured"), z.bi("expected"), z.bi("TTFT"), z.bi("P90"),
       z.annotate_text("GET /v1/x -> HTTP 200; load.percentiles.ttft_s p50=3.1 kimi-k3.1-pro"))
)
eq(z.lint_html(built), [], "markup assembled from bi()/annotate_text() has no unglossed words")

# --------------------------------------------------------------------------- vocabulary coverage (fixtures, specs, profile)
audit = load("tests/fixtures/sample_audit.json")
spec = load("tests/fixtures/sample_spec.json")
profile = load("profiles/kimi-k3.1-pro.json")

texts = []  # (origin, text) pairs of plain text that appears (or can appear) in the rendered report
for c in audit["checks"]:
    texts.append(("audit check name", c["name"]))
for field in ("verdict", "performance_detail", "profile", "model", "protocol"):
    texts.append(("audit." + field, str(audit.get(field) or "")))
for n in audit.get("notes") or []:
    texts.append(("audit note", n))
texts.append(("hard_fail text", "identity claims a different vendor"))
for k in ("profile", "model", "protocol", "tier", "host", "tested_at"):
    texts.append(("spec." + k, str(spec.get(k) or "")))
for it in spec["spec"]["items"]:
    for f in ("key", "title", "note"):
        texts.append(("spec item %s" % it["key"], str(it.get(f) or "")))
    for ev in it.get("evidence") or []:
        texts.append(("spec evidence %s" % it["key"], ev))
for m in spec["params"]["matrix"]:
    texts.append(("matrix case", m["case"]))
    texts.append(("matrix detail", str(m.get("detail") or "")))
for k in spec["load"]["percentiles"]:
    texts.append(("load percentile key", k))
for k in spec["load"]["finish"]:
    texts.append(("load finish key", k))
for k in spec["cache"]["scenarios"]:
    texts.append(("cache scenario", k))
texts.append(("profile display_name", profile["display_name"]))
texts.append(("profile vendor", profile["vendor"]))
for h in profile["usage_schema"]["headers"]:
    texts.append(("profile header", h))
for k in profile["usage_schema"]["required"]:
    texts.append(("profile usage required", k))
for k, vs in profile["usage_schema"]["details"].items():
    texts.append(("profile usage details", k))
    for v in vs:
        texts.append(("profile usage details", v))
for t in profile["params"]["thinking"]["tiers"]:
    texts.append(("profile tier", t))
for case in profile["expected_probes"]:
    texts.append(("profile case id", case))

# audit check notes are free English prose written by checks_*.py; they are not a glossary matter (see report).
prose_origins = {"audit note"}
for origin, text in texts:
    if origin in prose_origins:
        continue
    miss = z.uncovered(text)
    check(not miss, "glossary covers %s %r: missing %s" % (origin, text[:60], miss))

# explicit key coverage
spec_items = literal_from("scripts/spec_checks.py", "ITEM_SPECS")
check(len(spec_items) == 15, "ITEM_SPECS has 15 items (got %d)" % len(spec_items))
for ident, key, title, weight in spec_items:
    check(z.zh(key) is not None, "glossary has spec key %r" % key)
    check(not z.uncovered(title), "glossary covers spec title %r: %s" % (title, z.uncovered(title)))
for it in spec["spec"]["items"]:
    check(z.zh(it["key"]) is not None, "glossary has fixture item key %r" % it["key"])
weights = literal_from("scripts/scoring.py", "WEIGHTS")
for name in weights:
    check(z.zh(name) is not None, "glossary has audit check name %r" % name)
for fname in ("basic", "format", "headers", "identity", "longctx", "reasoning", "tokenizer", "tools"):
    name = literal_from("scripts/checks_%s.py" % fname, "NAME")
    check(name in weights, "checks_%s.NAME %r is in scoring.WEIGHTS" % (fname, name))
for c in audit["checks"]:
    check(z.zh(c["name"]) is not None, "glossary has fixture check %r" % c["name"])
for word in ("finish_reason", "reasoning_content", "cached_tokens", "prompt_tokens", "completion_tokens",
             "cache_write_tokens", "total_tokens", "tool_choice", "tool_calls", "thinking", "hard_fail",
             "stop", "length", "empty", "other", "TTFT", "TPOT", "TPS", "RPM", "TPM", "P50", "P90", "P95",
             "latency", "output_tps", "pass", "partial", "fail", "skip", "measured", "expected", "delta",
             "evidence", "official", "msh-usage-cache-write-tokens-5m", "msh-usage-cache-write-tokens-1h"):
    check(z.zh(word) is not None, "glossary has required word %r" % word)

# visible English of the original report_html.py (snapshot, kept here so the test survives refactors of that file)
REPORT_HTML_VOCAB = [
    "ENDPOINT", "Conformance Report", "ENDPOINT / Conformance Report", "MODEL CONFORMANCE ASSESSMENT",
    "CARBON DESIGN / SINGLE-PAGE REPORT", "SINGLE-PAGE REPORT", "pass", "partial", "fail", "skip",
    "measured", "expected", "delta", "evidence", "Latency", "total wall time", "TTFT", "time to first token",
    "TPOT", "time per output token", "Output TPS", "tokens per second", "Input TPS", "RPM", "TPM", "P50", "P90",
    "P95", "finish_reason", "stop", "length", "tool_calls", "empty", "other", "hard_fail", "usage_inflated",
    "crashed_checks", "perf_baseline", "KV cache", "Request success rate", "cache key", "total_tokens",
    "cache_write_tokens", "cached_tokens", "prompt_tokens", "completion_tokens", "token_ratio", "n/a",
    "model_audit", "spec_audit", "spec", "JSON", "HTML", "API", "highly credible", "credible", "suspicious",
    "fake", "implausible", "mostly credible (check for downgrade)", "doubtful", "likely fake/substituted",
    "usage_schema", "cache_hit", "ttl_5m", "ttl_1h", "A_identical_20k", "B_multiturn_12k", "C_short_1k",
    "E_cache_key", "scoring_version", "performance", "performance_detail", "standard", "deep", "chat",
    "messages", "responses", "temperature", "top_p", "presence_penalty", "frequency_penalty",
    "http", "SSE", "Formula", "base64", "GET", "POST", "URL", "TTL", "reasoning", "thinking",
]
for w in REPORT_HTML_VOCAB:
    check(z.zh(w) is not None, "glossary has report_html vocabulary %r" % w)

# every glossary entry round-trips through bi() and lint_html()
for k in z.GLOSSARY:
    glossed = z.bi(k)
    check(glossed.startswith(k) and glossed.endswith("）"), "bi(%r) -> %r" % (k, glossed))
    check(z.lint_html("<p>%s</p>" % glossed.replace("&", "&amp;").replace("<", "&lt;")) == [], "lint accepts bi(%r)" % k)

# --------------------------------------------------------------------------- end-to-end on the fixture text
rendered = "".join(
    "<li>%s</li>" % z.annotate_text(ev) for it in spec["spec"]["items"] for ev in (it.get("evidence") or [])
)
left = z.lint_html(rendered)
check(left == [], "annotated fixture evidence lints clean, left: %s" % left)
for it in spec["spec"]["items"]:
    ann = z.annotate_text(it["note"])
    check(re.sub(r"（[^（）]*）", "", ann) == it["note"], "annotation only inserts glosses: %r" % ann[:80])

# --------------------------------------------------------------------------- "not provided" placeholders carry glosses
import html_components  # noqa: E402

for label, frag in (("empty_panel() default", html_components.empty_panel()),
                    ("NO_SPEC_TEXT", "<b>%s</b>" % html_components.esc_raw(html_components.NO_SPEC_TEXT))):
    check(z.lint_html(frag) == [], "%s has no bare English: %s" % (label, z.lint_html(frag)))
    check("未提供" in frag, "%s says 未提供" % label)

if fails:
    print("\n%d/%d checks failed" % (len(fails), checks))
    sys.exit(1)
print("PASS: all %d checks" % checks)
