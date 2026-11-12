#!/usr/bin/env python3
"""English-with-Chinese gloss helpers for the report layer ("English（中文）", full-width parentheses).

Public API (all pure text in / text out; this module never emits HTML tags and never escapes HTML,
escaping stays with the caller):

  bi(en)              "finish_reason" -> "finish_reason（结束原因）"; unknown words are returned unchanged and
                      recorded in MISSING (a runtime warning only, never an exception)
  zh(en)              the bare Chinese gloss, or None
  annotate_text(s)    append （中文） after glossary identifiers inside free text (evidence / note);
                      numbers, URLs, sk-... strings, model names, hosts, case ids and units are left alone
  lint_html(html)     visible English words that are not followed by （中文）; <style>/<script>/<pre>, tags and
                      attributes are ignored
  visible_text(html)  the text lint_html looks at
  uncovered(text)     English words in plain text that the glossary can not resolve (used by the self test)

Rules shared by annotate_text and lint_html
  * a word is a run of [A-Za-z][A-Za-z0-9_.+/-]* that does not start inside another word
  * ALLOW_WORDS (units, loanwords, vendor names) and ALLOW_PATTERNS (model names, hosts, case ids ...) are exempt;
    a glossary hit wins over a pattern (so p50 still needs its gloss) but loses to ALLOW_WORDS
  * lint_html treats an English *run* (same line, not interrupted by Chinese text or full-width punctuation)
    followed immediately by （含中文的括号） as glossed, which also covers phrases like
    "time to first token（首个 token 的等待时间）"; text inside a Chinese gloss is not linted
  * glossary values contain no full-width parentheses, so composed glosses stay readable
"""
import re
import warnings
from bisect import bisect_left

# --------------------------------------------------------------------------- glossary

# 1. request / response field identifiers, enum values and protocol names
_FIELDS = {
    "finish_reason": "结束原因",
    "reasoning_content": "推理内容",
    "reasoning_tokens": "推理 token 数",
    "cached_tokens": "缓存命中 token 数",
    "prompt_tokens": "输入 token 数",
    "completion_tokens": "输出 token 数",
    "cache_write_tokens": "缓存写入 token 数",
    "total_tokens": "总 token 数",
    "write_tokens": "写入 token 数",
    "max_tokens": "最大输出 token 数",
    "max_completion_tokens": "最大补全 token 数",
    "prompt_tokens_details": "输入明细",
    "completion_tokens_details": "输出明细",
    "tool_choice": "工具选择策略",
    "tool_calls": "工具调用",
    "tool_call": "单次工具调用",
    "tools": "工具",
    "thinking": "思考模式",
    "reasoning": "推理",
    "reasoning_effort": "推理强度",
    "temperature": "温度",
    "top_p": "核采样",
    "presence_penalty": "存在惩罚",
    "frequency_penalty": "频率惩罚",
    "usage": "用量",
    "content": "正文",
    "message": "消息",
    "messages": "消息列表",
    "assistant": "助手",
    "stream": "流式",
    "non-stream": "非流式",
    "parts": "内容分段",
    "id": "标识",
    "get_weather": "天气查询函数",
    "json_mode": "JSON 模式",
    "fibers": "执行端点",
    "formulas": "官方工具集",
    "formula": "官方工具",
    "multimodal": "多模态",
    "image_b64": "Base64 图片",
    "video_b64_1mb": "1MB Base64 视频",
    "video_url": "公网 URL 视频",
    "video_100mb": "100MB 视频",
    "url.not_found": "路径不存在",
    "finish": "结束状态",
    "load": "负载",
    "raw": "原始样本",
    # enum values
    "stop": "正常结束",
    "length": "长度截断",
    "empty": "空响应",
    "other": "其它",
    "none": "无或不调用",
    "auto": "自动",
    "required": "必须调用",
    "low": "低档",
    "high": "高档",
    "max": "最高档",
    "disabled": "已关闭",
    "enabled": "已开启",
    "standard": "标准档",
    "deep": "深度档",
    "chat": "对话补全接口",
    "responses": "Responses 接口",
    "DONE": "流结束标记",
    "True": "是",
    "False": "否",
}

# 2. metric names and the ad-hoc English sub-labels of the old report_html.py
_METRICS = {
    "TTFT": "首字延迟",
    "TPOT": "单 token 输出耗时",
    "TPS": "每秒输出 token 数",
    "RPM": "每分钟请求数",
    "TPM": "每分钟 token 数",
    "input_tpm": "每分钟输入 token 数",
    "output_tpm": "每分钟输出 token 数",
    "percentiles": "分位数",
    "percentile": "分位数",
    "latency": "总延迟",
    "throughput": "吞吐量",
    "output_tps": "输出速度",
    "input_tps": "输入速度",
    "ttft_s": "首字延迟秒数",
    "latency_s": "总延迟秒数",
    "tpot_ms": "单 token 耗时毫秒数",
    "p90_s": "90 分位秒数",
    "p95_s": "95 分位秒数",
    "margin_s": "秒数余量",
    "margin_pct": "百分点余量",
    "p50_margin": "50 分位余量",
    "p90_margin": "90 分位余量",
    "p50_ratio_max": "50 分位比率上限",
    "p90_ratio_max": "90 分位比率上限",
    "output_tps_p50_ratio_min": "输出速度 50 分位比率下限",
    "p90_max_s": "90 分位秒数上限",
    "min_pct": "最低百分比",
    "ttft_p50_ratio": "首字延迟 50 分位比率",
    "ttft_p90_ratio": "首字延迟 90 分位比率",
    "latency_p50_ratio": "总延迟 50 分位比率",
    "latency_p90_ratio": "总延迟 90 分位比率",
    "tpot_p50_ratio": "单 token 耗时 50 分位比率",
    "ratio": "比率",
    "success_rate": "成功率",
    "samples": "样本数",
    "success": "成功",
    "ttfb": "首字节时延",
    "speed": "速度",
    "total wall time": "总耗时",
    "time to first token": "首个 token 的等待时间",
    "time per output token": "输出每个 token 的耗时",
    "tokens per second": "每秒输出 token 数",
    "Output TPS": "输出速度",
    "Input TPS": "输入速度",
    "KV cache": "键值缓存",
    "Request success rate": "请求成功率",
    "cache key": "缓存键",
    "prompt_cache_key": "提示缓存键",
    "cache": "缓存",
    "cache_hit": "缓存命中",
    "hit_rate_pct": "命中率",
    "ttl_5m": "5 分钟缓存",
    "ttl_1h": "1 小时缓存",
    "read_back_hit_pct": "回读命中率",
    "read_back_58m": "58 分钟回读",
    "read_back_after_s": "回读等待秒数",
    "token_ratio": "token 与字符比率",
    "token_ratio_range": "合理比率区间",
    "scenarios": "场景",
    "rounds": "轮次",
    "A_identical_20k": "相同内容 20k",
    "B_multiturn_12k": "多轮对话 12k",
    "C_short_1k": "短文本 1k",
    "E_cache_key": "缓存键",
    "identical_20k_pct": "相同内容 20k 命中率",
    "multiturn_12k_pct": "多轮对话 12k 命中率",
    "short_1k_pct": "短文本 1k 命中率",
    "required_present": "必需字段齐备",
    "total_matches": "总数一致",
    "details_present": "明细字段齐备",
    "cache_write_present": "缓存写入字段齐备",
    "expected_status": "预期状态码",
    "actual_status": "实测状态码",
}

# 3. status words, verdicts, report chrome and generic technical terms
_STATUS = {
    "pass": "通过",
    "partial": "部分通过",
    "fail": "未通过",
    "skip": "跳过",
    "measured": "实测",
    "expected": "预期",
    "delta": "差值",
    "evidence": "证据",
    "official": "官方",
    "baseline": "官方基准",
    "n/a": "不适用",
    "ok": "正常",
    "hard_fail": "硬失败",
    "usage_inflated": "用量虚高",
    "crashed_checks": "崩溃检查项",
    "performance": "性能",
    "performance_detail": "性能明细",
    "scoring_version": "评分版本",
    "verdict": "结论",
    "score": "分数",
    "weight": "权重",
    "checks": "检查项",
    "notes": "备注",
    "note": "备注",
    "detail": "说明",
    "status": "状态",
    "name": "名称",
    "key": "键",
    "case": "用例",
    "match": "匹配",
    "target": "目标",
    "tier": "档位",
    "protocol": "协议",
    "profile": "画像",
    "model": "模型",
    "host": "主机",
    "tested_at": "测试时间",
    "fixed": "固定值",
    "spec": "规范",
    "audit": "审计",
    "model_audit": "真实性审计",
    "spec_audit": "规范审计",
    "fixture": "测试夹具",
    "highly credible": "高度可信",
    "mostly credible (check for downgrade)": "基本可信，需警惕降级",
    "doubtful": "存疑",
    "likely fake/substituted": "疑似造假或被替换",
    "credible": "可信",
    "plausible": "合理",
    "suspect": "可疑",
    "suspicious": "存疑",
    "mixed": "混合",
    "doubt": "怀疑",
    "fake": "造假",
    "implausible": "不可信",
    "identity claims a different vendor": "身份自称属于另一家厂商",
    "ENDPOINT": "端点",
    "Conformance Report": "符合性报告",
    "ENDPOINT / Conformance Report": "端点符合性报告",
    "MODEL CONFORMANCE ASSESSMENT": "模型符合性评估",
    "SINGLE-PAGE REPORT": "单页报告",
    "CARBON DESIGN / SINGLE-PAGE REPORT": "Carbon 设计体系，单页报告",
    "API": "接口",
    "HTTP": "超文本传输协议",
    "URL": "网址",
    "JSON": "结构化数据格式",
    "HTML": "网页格式",
    "SSE": "服务器推送事件流",
    "CDN": "内容分发网络",
    "base64": "二进制转文本编码",
    "GET": "获取",
    "POST": "提交",
    "TTL": "缓存有效期",
    "relay": "中转站",
    "channel": "渠道",
    "vendor": "厂商",
}

# 4. audit check names (scoring.WEIGHTS == checks_*.NAME)
_CHECKS = {
    "vendor headers": "厂商响应头",
    "format+stream": "格式与流式",
    "identity+facts": "身份与常识",
    "long context": "长上下文",
    "tools+json": "工具与 JSON",
    "instruction following": "指令遵循",
    "error format": "错误格式",
    "tokenizer": "分词器",
}

# 5. spec_checks.ITEM_SPECS keys not already listed above (ttft/finish_reason/timeout/reasoning_content/hidden_prompt)
_SPEC_KEYS = {
    "perf_baseline": "性能基准",
    "usage_schema": "用量字段结构",
    "thinking_params": "思考参数",
    "timeout": "超时",
    "video": "视频",
    "param_matrix": "参数矩阵",
    "expected_probes": "预期探测用例",
    "formula_tools": "官方工具接入",
    "cache_ttl": "缓存有效期",
    "tools_choice": "工具选择",
    "structured_output": "结构化输出",
    "hidden_prompt": "隐藏提示词",
    "params": "参数",
    "matrix": "矩阵",
    "probes": "探测",
}

# 6. response headers
_HEADERS = {
    "msh-usage-cache-write-tokens-5m": "5 分钟缓存写入 token 数响应头",
    "msh-usage-cache-write-tokens-1h": "1 小时缓存写入 token 数响应头",
    "x-oneapi-request-id": "中转站请求标识响应头",
    "x-request-id": "请求标识响应头",
    "headers": "响应头",
    "header": "响应头",
}


def _merge(*parts):
    merged, seen = {}, {}
    for part in parts:
        for key, value in part.items():
            low = key.lower()
            if low in seen:
                raise ValueError("i18n_zh: duplicate glossary key %r (also %r)" % (key, seen[low]))
            seen[low] = key
            merged[key] = value
    return merged


GLOSSARY: dict = _merge(_FIELDS, _METRICS, _STATUS, _CHECKS, _SPEC_KEYS, _HEADERS)
_INDEX = {_k.lower(): _v for _k, _v in GLOSSARY.items()}  # case-insensitive lookup table

# --------------------------------------------------------------------------- exemptions

# lower-case words that never need a gloss: units, loanwords and vendor names.
# These win over the glossary (so "tokens" is never annotated by annotate_text / flagged by lint_html).
ALLOW_WORDS = frozenset({
    "ms", "s", "sec", "secs", "min", "mins", "h", "hr", "hrs", "d", "tok", "token", "tokens",
    "req", "reqs", "char", "chars", "pp", "x", "kb", "mb", "gb", "mib", "kib", "k", "m", "b",
    "openai", "anthropic", "pro", "localhost", "utf-8", "utf8",
})

_UNIT = r"(?:tok|tokens?|req|requests?|chars?|s|ms|min|h)"
_MODEL = (r"(?:kimi|moonshot|gpt|claude|gemini|glm|qwen\d*|qwq|deepseek|llama\d*|mistral|doubao|ernie)"
          r"(?:[-_.][A-Za-z0-9.]+)*")
_TLD = (r"(?:com|net|org|io|ai|cn|dev|app|xyz|xin|cc|co|me|top|site|online|cloud|example|test|local|"
        r"edu|gov|info|tech|pro|vip|ltd|club)")

# full-match patterns (case-insensitive) applied to a single word after the glossary has been consulted
ALLOW_PATTERNS = [re.compile(p, re.I) for p in (
    r"[A-Za-z]",                                             # single letters: scenario A/B/C/E, n, x
    r"v\d+(?:\.\d+)*",                                       # versions: v2
    r"k\d+(?:\.\d+)*",                                       # display names: Kimi K3.1 Pro
    _MODEL,                                                  # kimi-k3.1-pro, moonshot-style, gpt-4o ...
    r"(?:[a-z0-9-]+\.)+" + _TLD,                             # host names: relay.example.com
    r"%s/%s" % (_UNIT, _UNIT),                               # tok/s, tokens/min, req/min, token/char
    r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*_\d+_[0-9a-f]{6,}",       # tool call ids: get_weather_0_3f9a1c22
    r"[a-f][0-9a-f]{7,}",                                    # hex ids
    r"[\w.-]+\.(?:json|py|md|html|css|txt|toml|ya?ml|csv|cmd|sh)",  # file names
)]

_PCTL_RE = re.compile(r"p(\d{1,3})", re.I)

# --------------------------------------------------------------------------- text scanning

_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9_.+/-]*")
_TRAIL = "._+/-"
_URL_RE = re.compile(r"https?://[^\s<>\"'（）]+")
_SK_RE = re.compile(r"(?<![A-Za-z0-9_])sk-[A-Za-z0-9_*-]*")
_CASE_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Z]:[A-Za-z0-9_.]+")        # P:temp_0.6, T:choice_none, E:formula_tools_v1
_PATH_RE = re.compile(r"(?<![^\s(\[\"'（])/[A-Za-z0-9_.%{}~-][^\s（）]*")   # " /v1/formulas/x/date:latest/tools"
_PROTECTED_RES = (_URL_RE, _SK_RE, _CASE_RE, _PATH_RE)
_ZH_PAREN_RE = re.compile(r"（[^（）]*[一-鿿][^（）]*）")
_GLOSS_AFTER = re.compile(r"[  ]?（[^（）]*[一-鿿][^（）]*）")
_BREAK_RE = re.compile(r"[　-〿㐀-䶿一-鿿＀-￯\n]")  # CJK text, full-width punctuation, EOL
_SEPS = (("/", "/"), ("+", "+"), (".", "·"))


def _atom(tok):
    z = _INDEX.get(tok.lower())
    if z is not None:
        return z
    m = _PCTL_RE.fullmatch(tok)
    if m:
        return "%s 分位" % m.group(1)
    return None


def _resolve(tok):
    """Chinese gloss for a word: glossary hit, P<n> percentile, or a / + . composite of such parts."""
    z = _atom(tok)
    if z is not None:
        return z
    for sep, joiner in _SEPS:
        if sep in tok:
            parts = tok.split(sep)
            if len(parts) > 1 and all(parts):
                zs = [_resolve(p) for p in parts]
                if all(x is not None for x in zs):
                    return joiner.join(zs)
    return None


def _exempt(tok, extra=()):
    """True when the word needs no gloss (units, names, ids ...). ALLOW_WORDS beat the glossary, patterns do not."""
    low = tok.lower()
    if low in ALLOW_WORDS or low in extra:
        return True
    if _resolve(tok) is not None:
        return False
    return any(p.fullmatch(tok) for p in ALLOW_PATTERNS)


def _protected_spans(s):
    return [m.span() for rx in _PROTECTED_RES for m in rx.finditer(s)]


def _overlaps(span, spans):
    a, b = span
    return any(a < y and x < b for x, y in spans)


def _words(s):
    """Yield (start, end, word) for every English word, trailing punctuation stripped."""
    for m in _TOKEN_RE.finditer(s):
        tok = m.group().rstrip(_TRAIL)
        if tok:
            yield m.start(), m.start() + len(tok), tok


# phrases (glossary keys containing whitespace) are matched as a unit, longest first
_PHRASES = sorted((k for k in GLOSSARY if len(k.split()) > 1), key=len, reverse=True)
_PHRASE_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:" + "|".join(r"\s+".join(re.escape(w) for w in k.split()) for k in _PHRASES)
    + r")(?![A-Za-z0-9_])", re.I)

# --------------------------------------------------------------------------- bi / zh

MISSING: set = set()   # English strings bi() could not gloss (runtime warning only)
WARN = True            # set False to silence the one-time warning per missing word


def _has_zh_paren(s):
    return _ZH_PAREN_RE.search(s) is not None


def zh(en):
    """Bare Chinese gloss of ``en`` (phrase, word or composite), or None."""
    if not isinstance(en, str):
        return None
    key = " ".join(en.split())
    if not key:
        return None
    z = _INDEX.get(key.lower())
    if z is not None:
        return z
    return _resolve(key) if " " not in key else None


def bi(en):
    """``English（中文）``. Unknown text is returned unchanged and recorded in MISSING."""
    if not isinstance(en, str) or not en.strip():
        return en
    if _has_zh_paren(en):
        return en
    key = " ".join(en.split())
    z = zh(key)
    if z is None:
        if _exempt(key) or re.search(r"[一-鿿]", en):
            return en
        if key not in MISSING:
            MISSING.add(key)
            if WARN:
                warnings.warn("i18n_zh: no Chinese gloss for %r" % key, stacklevel=2)
        return en
    return "%s（%s）" % (en, z)


# --------------------------------------------------------------------------- annotate_text

def annotate_text(s):
    """Append （中文） after glossary identifiers in free text; leave everything else byte-identical."""
    if not isinstance(s, str) or not s:
        return s
    blocked = _protected_spans(s) + [m.span() for m in _ZH_PAREN_RE.finditer(s)]
    inserts = []
    taken = []
    for m in _PHRASE_RE.finditer(s):
        if _overlaps(m.span(), blocked) or _GLOSS_AFTER.match(s, m.end()):
            continue
        z = _INDEX.get(" ".join(m.group().split()).lower())
        if z is not None:
            inserts.append((m.end(), "（%s）" % z))
            taken.append(m.span())
    for a, b, tok in _words(s):
        if _overlaps((a, b), blocked) or _overlaps((a, b), taken):
            continue
        if tok.lower() in ALLOW_WORDS:
            continue
        z = _resolve(tok)
        if z is None or _GLOSS_AFTER.match(s, b):
            continue
        inserts.append((b, "（%s）" % z))
    if not inserts:
        return s
    out, last = [], 0
    for pos, text in sorted(inserts):
        out.append(s[last:pos])
        out.append(text)
        last = pos
    out.append(s[last:])
    return "".join(out)


# --------------------------------------------------------------------------- lint_html

_DROP_RE = re.compile(r"<(script|style|pre)\b[^>]*>.*?</\1\s*>", re.I | re.S)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_TAG_RE = re.compile(r"<(/?)([A-Za-z][\w:-]*)((?:\"[^\"]*\"|'[^']*'|[^'\">])*)>|<![^>]*>")
_INLINE_TAGS = frozenset({"span", "b", "i", "em", "strong", "small", "a", "code", "abbr", "sup", "sub", "u",
                          "mark", "bdi", "bdo", "s", "kbd", "samp", "var", "wbr", "tspan"})


def _tag_sub(m):
    name = (m.group(2) or "").lower()
    return "" if name in _INLINE_TAGS else "\n"


def visible_text(html_doc):
    """Visible text of an HTML document: <script>/<style>/<pre>, comments, tags and attributes removed,
    block-level tags become line breaks, inline tags join their neighbours, entities decoded."""
    import html as _html
    text = _DROP_RE.sub("\n", html_doc)
    text = _COMMENT_RE.sub("", text)
    text = _TAG_RE.sub(_tag_sub, text)
    return _html.unescape(text)


def _mask(line):
    for rx in _PROTECTED_RES:
        line = rx.sub(lambda m: " " * len(m.group()), line)
    return line


def _lint_line(line, extra):
    line = _mask(line)
    zh_spans = [m.span() for m in _ZH_PAREN_RE.finditer(line)]
    breaks = [m.start() for m in _BREAK_RE.finditer(line)]
    bad = []
    for a, b, tok in _words(line):
        if _overlaps((a, b), zh_spans) or _exempt(tok, extra):
            continue
        k = bisect_left(breaks, b)
        run_end = breaks[k] if k < len(breaks) else len(line)
        if _GLOSS_AFTER.match(line, run_end):
            continue
        bad.append(tok)
    return bad


def lint_html(html, allow=None):
    """Unglossed English words in the visible text, de-duplicated in first-seen order.

    ``allow``: extra exact words (case-insensitive) to exempt, e.g. model or host names of the report."""
    extra = {str(a).lower() for a in (allow or ())}
    seen, out = set(), []
    for line in visible_text(html).split("\n"):
        for tok in _lint_line(line, extra):
            if tok not in seen:
                seen.add(tok)
                out.append(tok)
    return out


def uncovered(text, allow=None):
    """English words in plain text that need a gloss but that the glossary can not resolve."""
    extra = {str(a).lower() for a in (allow or ())}
    text = _PHRASE_RE.sub(lambda m: " " * len(m.group()), _mask(text))
    spans = [m.span() for m in _ZH_PAREN_RE.finditer(text)]
    seen, out = set(), []
    for a, b, tok in _words(text):
        if _overlaps((a, b), spans) or _exempt(tok, extra) or _resolve(tok) is not None:
            continue
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out
