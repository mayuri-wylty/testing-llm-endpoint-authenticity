#!/usr/bin/env python3
"""Render a Markdown model-conformance report from model_audit / spec_audit JSON (deterministic, no hand filling).

Usage:
  python scripts/report_md.py --audit A.json [--spec S.json] [--profile ID|PATH] --out X.md [--title T]

Structure follows report-template.md: plain-language body (一 conclusion ... 七 spec conformance, only
"x 项中 y 项通过" style ratios) and a technical appendix whose tables line up with the HTML report sections
(核心标准 / 全部指标 / 官方基准对标 / 规范符合 15 项 / 计费与缓存 / 真实性检查).

Rules
  * --spec / --profile missing -> the matching chapters say 未测 / 未提供, never an error (old usage keeps working).
  * Display numbers only go through common.fmt_num (float -> fixed 2 decimals, int kept); free text (evidence, note,
    performance_detail, ...) goes through common.round_text. JSON inputs are never modified on disk.
  * Every user-controllable string is markdown-escaped (| ` < > & * [ ] and non-intraword _; newlines -> space),
    JSON is key-scrubbed on load and sk-... tokens are masked, so tables stay intact and no raw HTML / key survives.
  * Official-vs-measured rows come from benchmark.build_rows(spec, profile); the footer date from
    benchmark.baseline_source(profile). Nothing here talks to any endpoint.
"""
import argparse
import json
import math
import os
import re
import sys
from decimal import ROUND_HALF_UP, Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import fmt_num, round_obj, round_text, scrub  # noqa: E402
import benchmark  # noqa: E402
from sanitize import normalize_audit, normalize_spec  # noqa: E402

SK_RE = re.compile(r"sk-[A-Za-z0-9_-]{8,}")
MISSING = "未测"
DEFAULT_TITLE = "模型渠道真实性报告"
NOT_GIVEN = "未提供"
STATUS_TEXT = {"pass": "通过", "partial": "部分通过", "fail": "未通过", "skip": "跳过"}
CORE_TEXT = {"pass": "达标", "partial": "部分达标", "fail": "未达标", "skip": "跳过"}
CHECK_ZH = {
    "vendor headers": "服务商标识信息", "format+stream": "返回格式与边生成边返回",
    "identity+facts": "自我介绍与常识", "reasoning": "推理", "long context": "长文档记忆",
    "tools+json": "工具调用与结构化输出", "instruction following": "指令遵循",
    "error format": "错误提示格式", "tokenizer": "文字计量方式",
}
METRIC_ZH = {
    "ttft_s": "首字耗时", "latency_s": "总耗时", "tpot_ms": "每字耗时", "output_tps": "输出速度",
    "cache_identical_20k": "缓存命中 · 相同 20k", "cache_multiturn_12k": "缓存命中 · 多轮 12k",
    "cache_short_1k": "缓存命中 · 短文本 1k",
}
VERDICT_ZH = {"ok": "达标", "bad": "偏离"}


# --------------------------------------------------------------------------- loading

def load_json(path, key):
    """Read JSON text, scrub the API key, mask sk-* tokens, then parse (same logic as report_html.load_json)."""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    text = scrub(text, key)
    text = SK_RE.sub("sk-***", text)
    return json.loads(text)


# --------------------------------------------------------------------------- escaping / formatting

_INTRA_UNDERSCORE = re.compile(r"(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])")


def flat(s):
    """Single-line, fence-safe text (used inside code fences and as the base of esc)."""
    s = re.sub(r"[\r\n\t\v\f]+", " ", str(s))
    return s.replace("`", "'").replace("<", "&lt;").replace(">", "&gt;")


def esc(s):
    """Markdown-escape a plain string for table cells / list items. Newlines become spaces."""
    s = re.sub(r"[\r\n\t\v\f]+", " ", str(s))
    s = SK_RE.sub("sk-***", s)
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = s.replace("|", "\\|").replace("`", "\\`").replace("*", "\\*").replace("[", "\\[").replace("]", "\\]")
    return _INTRA_UNDERSCORE.sub(lambda m: "\\_", s)


def T(v, limit=None):
    """Free text -> rounded (<=2 decimals), optionally truncated, escaped. None/empty -> 未测."""
    if v is None:
        return MISSING
    s = round_text(str(v)).strip()
    if not s:
        return MISSING
    if limit and len(s) > limit:
        s = s[:limit].rstrip() + "…"
    return esc(s)


def N(v, unit="", thousands=True):
    """Number/bool/str -> display text. None -> 未测; float fixed 2 decimals via fmt_num."""
    if v is None:
        return MISSING
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, (int, float)):
        s = fmt_num(v, thousands=thousands)
        return s + unit if s != "—" else s
    return T(v)


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def signed(v, unit=""):
    if not is_num(v):
        return "—"
    s = fmt_num(float(v))
    if s == "—":
        return s
    if not s.startswith("-"):
        s = "+" + s
    return s + unit


def code_key(k):
    k = str(k)
    return "`%s`" % k if re.fullmatch(r"[A-Za-z0-9_.\-]+", k) else T(k)


def table(headers, rows, aligns=None):
    """GFM table; cells must already be escaped. Empty cell -> —."""
    n = len(headers)
    aligns = aligns or [""] * n
    sep = ["---:" if a == "r" else (":---:" if a == "c" else "---") for a in aligns]
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(sep) + "|"]
    for r in rows:
        cells = [(str(c) if str(c) != "" else "—") for c in r]
        cells = (cells + ["—"] * n)[:n]
        out.append("| " + " | ".join(cells) + " |")
    return out


def dig(obj, *path, default=None):
    cur = obj
    for k in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return default if cur is None else cur


def items_of(spec_doc):
    v = dig(spec_doc, "spec", "items", default=[])
    return [it for it in v if isinstance(it, dict)] if isinstance(v, list) else []


def find_item(items, ident=None, key=None):
    for it in items:
        if ident is not None and it.get("id") == ident:
            return it
    if key is not None:
        for it in items:
            if it.get("key") == key:
                return it
    return None


def status_of(v):
    return v if v in STATUS_TEXT else "skip"


def kv_text(v, limit=420):
    """dict/list/scalar -> compact 'k=v; k=v' (floats <=2 decimals), escaped."""
    if v is None or v == "" or v == {} or v == []:
        return MISSING
    if isinstance(v, dict):
        parts = []
        for k, x in v.items():
            if isinstance(x, (dict, list)):
                xs = json.dumps(round_obj(x), ensure_ascii=False, separators=(",", ":"))
            elif is_num(x) or isinstance(x, bool):
                xs = "是" if x is True else ("否" if x is False else fmt_num(x, thousands=False))
            else:
                xs = "未测" if x is None else str(x)
            parts.append("%s=%s" % (k, xs))
        return T("; ".join(parts), limit)
    if isinstance(v, list):
        return T(json.dumps(round_obj(v), ensure_ascii=False, separators=(",", ":")), limit)
    if is_num(v) or isinstance(v, bool):
        return N(v, thousands=False)
    return T(v, limit)


def evidence_text(ev, limit=520):
    if isinstance(ev, list) and ev:
        return T("；".join(str(x) for x in ev), limit)
    if isinstance(ev, str) and ev.strip():
        return T(ev, limit)
    return MISSING


# --------------------------------------------------------------------------- derived facts (shared logic with HTML)

def perf_flags(spec_doc):
    """Five explicit booleans from the report contract (same as report_html.perf_flags); None without spec."""
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


def ratio_groups(item):
    groups = {"p50": [], "p90": [], "p95": []}
    measured = dig(item, "measured", default={}) or {}
    if isinstance(measured, dict):
        for k, v in measured.items():
            if is_num(v) and k.endswith("_ratio"):
                for q in groups:
                    if "_%s_" % q in k:
                        groups[q].append((k, float(v)))
    return groups


def _dec(x):
    """Exact Decimal of a finite number via its shortest repr (0.7 x 61 in binary floats is 42.699999...)."""
    return Decimal(repr(float(x))) if isinstance(x, float) else Decimal(x)


def half_up_int(x):
    """Round half up to an int on the exact decimal value (floats go through repr, so 44.5 never becomes 44)."""
    return int(_dec(x).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def half_up_2(x):
    """Round half up to 2 decimals on the exact decimal value; returned as float for fmt_num."""
    return float(_dec(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def grade_auth(score, hard_fail=None):
    """Template grade table: >=90 / 75-89 / 60-74 / <60."""
    if not is_num(score):
        return MISSING
    if hard_fail or score < 60:
        return "很可能为假"
    return "高度可信" if score >= 90 else ("大体可信" if score >= 75 else "存疑")


def grade_spec(score):
    if not is_num(score):
        return "覆盖不足，不出分"
    return "高度符合" if score >= 90 else ("大体符合" if score >= 75 else ("有出入" if score >= 60 else "明显不符"))


def check_status(score):
    """Per-check score (0..1 or None) -> pass / partial / fail / na."""
    if not is_num(score):
        return "na"
    return "pass" if score >= 0.9 else ("partial" if score >= 0.5 else "fail")


CHECK_TEXT = {"pass": "通过", "partial": "部分通过", "fail": "未通过", "na": "不适用"}


def channel_score(audit):
    """0.7 x authenticity + 0.3 x performance, minus the deductions derivable from the JSON. -> (score|None, formula text)."""
    score = audit.get("score")
    perf = audit.get("performance")
    if not is_num(score):
        return None, "真伪分未测，无法计算"
    if is_num(perf):
        # exact decimal arithmetic, then round to the 2 shown decimals FIRST so the integer result is derived
        # from the number the reader sees (44.50 -> 45), never from a hidden 44.4999... float.
        sub = (_dec(score) * 7 + _dec(perf) * 3) / 10
        sub = _dec(half_up_2(sub))
        base = "0.7 × %s + 0.3 × %s = %s" % (fmt_num(score), fmt_num(perf), fmt_num(float(sub)))
    else:
        sub = _dec(score)
        base = "性能分未测，仅取真伪分 %s" % fmt_num(score)
    ded, why = 0, []
    if audit.get("hard_fail") or str(audit.get("verdict") or "").lower().startswith("likely fake") or score < 60:
        ded += 10
        why.append("假模型 −10")
    if audit.get("usage_inflated") is True:
        ded += 5
        why.append("用量虚高 −5")
    final = max(0, min(100, half_up_int(sub - ded)))
    text = "%s；扣分：%s；结果 %s" % (base, "、".join(why) if why else "无", fmt_num(final))
    return final, text


def model_is_fake(audit):
    score = audit.get("score")
    return bool(audit.get("hard_fail")) or (is_num(score) and score < 60) or \
        str(audit.get("verdict") or "").lower().startswith(("likely fake", "fake"))


def plain_experience(spec_doc, audit):
    """Plain-language 使用体验 sentence without raw numbers."""
    if spec_doc is None:
        return "未提供 spec JSON，使用体验（成功率、响应快慢）未测。"
    load = dig(spec_doc, "load", default={}) or {}
    sr = load.get("success_rate")
    items = items_of(spec_doc)
    bits = []
    if is_num(sr):
        bits.append("请求全部成功" if float(sr) == 100.0 else ("请求基本都能成功" if float(sr) >= 95.0 else "请求时有失败"))
    else:
        bits.append("成功率未测")
    it2 = find_item(items, ident=2, key="ttft")
    it1 = find_item(items, ident=1, key="perf_baseline")
    if it2 and it2.get("status") in STATUS_TEXT and it2.get("status") != "skip":
        bits.append("首次响应在可接受范围内" if it2["status"] == "pass" else "首次响应偏慢")
    else:
        bits.append("首次响应耗时未测")
    if it1 and it1.get("status") in ("pass", "partial", "fail"):
        bits.append({"pass": "整体速度与官方相近", "partial": "整体速度与官方有一定差距",
                     "fail": "整体速度明显慢于官方"}[it1["status"]])
    return "，".join(bits) + "。"


# --------------------------------------------------------------------------- sections

def render_header(audit, spec_doc, title):
    host = dig(spec_doc, "host") or MISSING
    model = dig(spec_doc, "model") or dig(audit, "model") or MISSING
    tested_at = dig(spec_doc, "tested_at") or MISSING
    date = str(tested_at)[:10] if tested_at != MISSING else MISSING
    protocol = dig(spec_doc, "protocol") or dig(audit, "protocol") or MISSING
    profile = dig(spec_doc, "profile") or dig(audit, "profile") or MISSING
    tier = dig(spec_doc, "tier") or MISSING
    if title == DEFAULT_TITLE:
        out = ["# %s 大模型接口测试报告（%s）" % (T(host), T(date)), ""]
    else:
        out = ["# %s" % T(title), ""]
    out += table(["模型", "主机", "测试时间", "协议", "画像", "测试档位"],
                 [[T(model), T(host), T(tested_at), T(protocol), T(profile), T(tier)]])
    out += ["", "> 只写主机名与模型 id，不含 key；数字展示统一不超过 2 位小数；没做的检查写“未测”，未提供的输入写“未提供”。", ""]
    return out


def render_conclusion(audit, spec_doc, rows):
    score = audit.get("score")
    model = dig(spec_doc, "model") or dig(audit, "model") or MISSING
    fake = model_is_fake(audit)
    ch, _ = channel_score(audit)
    items = items_of(spec_doc)
    n_pass = sum(1 for it in items if it.get("status") == "pass")
    n_bad = sum(1 for it in items if it.get("status") in ("fail", "partial"))
    n_skip = sum(1 for it in items if it.get("status") == "skip")

    notes = []
    if audit.get("hard_fail"):
        notes.append("真实性出现关键项不通过：%s" % flat(round_text(str(audit.get("hard_fail")))))
    if audit.get("usage_inflated") is True:
        notes.append("返回的用量数字明显偏高，可能多计费用")
    if items:
        if n_bad:
            notes.append("官方规范 %d 项中有 %d 项存在出入（见第七章）" % (len(items), n_bad))
    else:
        notes.append("未提供规范符合度数据（未传 spec JSON）")
    notes.append("渠道来源需结合错误提示与服务商标识人工核对，本报告未提供")
    if n_skip or any(r["official"] is None for r in rows):
        notes.append("部分项目未测或缺少官方对照")
    notes = notes[:4]
    circled = "①②③④"
    note_line = "；".join("%s%s" % (circled[i], t) for i, t in enumerate(notes)) if notes else "无"

    verdict_true = "假" if fake else "真"
    lines = [
        "渠道评分：%s/100" % (fmt_num(ch) if ch is not None else MISSING),
        "模型真伪：%s（%s：%s）" % (verdict_true, flat(model), verdict_true),
        "渠道真伪：未判定（本报告未提供错误指纹与渠道类型证据）",
        "注释：%s" % note_line,
    ]
    perf = audit.get("performance")
    low = (ch is not None and ch < 70) or fake
    if low:
        if fake:
            why = "该渠道所提供的模型并非其所声称的模型，可用性较低，不建议使用。"
        elif is_num(perf) and perf < 60:
            why = "该渠道所提供的模型为真，但响应缓慢且时有失败，可用性较低，不建议使用。"
        else:
            why = "该渠道所提供模型的真实性存疑，可用性较低，不建议使用。"
        lines.append("总评：" + why)

    if fake:
        advice = "不建议使用：该渠道很可能并非所声称的模型，不适合任何需要该模型真实能力的场景。"
    elif ch is not None and ch < 70:
        advice = "综合评分偏低，仅适合低风险用途，投入使用前建议先小规模验证。"
    elif n_bad or n_skip:
        advice = "可以使用；个别项目与官方规范有出入或未测（见第七章与第六章），重要业务建议保留抽查。"
    elif items:
        advice = "可以使用；各项表现与官方规范一致。"
    else:
        advice = "可以使用；但缺少规范符合度数据，建议补测后再用于重要业务。"

    out = ["## 一、结论", "```"] + lines + ["```"]
    out += ["", "**使用建议：** " + advice, ""]
    return out


def render_method():
    return [
        "## 二、测试内容与判定方式",
        "本次对每个模型逐类检查，用以判断它是否确为所声称的模型，并单独评估使用体验：", "",
        "1. 服务商标识信息：查看服务返回中附带的厂商与渠道标记，是否与对方所述来源一致。",
        "2. 返回格式：返回内容的结构与“边生成边返回”的方式，是否符合该模型官方的规范。",
        "3. 自我介绍与常识：询问模型身份与若干常识问题，核对其回答是否符合所声称的模型。",
        "4. 推理：用难度不同的题目检验推理能力，较弱模型常在此暴露。",
        "5. 长文档记忆：把关键信息藏在很长的文档中，检验模型能否在开头、中段、结尾都找回，用以识别被缩减的上下文长度。",
        "6. 工具调用与结构化输出：检验模型能否按要求调用工具、输出规定格式的数据。",
        "7. 指令遵循：检验模型是否严格按指令的格式与限制作答。",
        "8. 错误提示格式：故意发送不合规请求，核对返回的错误提示是否与官方一致。",
        "9. 文字计量方式：不同模型切分文字的方式不同，核对其计量结果是否与所声称的模型相符（此项仅作辅助，精度有限）。",
        "10. 性能：测试请求成功率、首次响应耗时与生成速度，评估使用体验，不影响真伪判断。", "",
        "**评分如何得出：** 前 9 类检查合成“真实性得分”，满分 100；性能单独得出“性能得分”。渠道评分以真实性为主（约占七成）、"
        "性能为辅（约占三成），再按下列情形扣分：渠道暗中添加指令、混用多个供应商、声称的渠道未能证实、模型为假、用量数字虚高。"
        "关键项不通过时，该模型的真实性得分设有上限。本脚本只能自动计入“模型为假”“用量虚高”两项扣分，其余需人工判定。", "",
        "**等级含义：**", "",
    ] + table(["得分", "等级", "含义"], [
        ["90 分及以上", "高度可信", "各项表现与所称模型一致"],
        ["75–89 分", "大体可信", "基本一致，个别项有出入"],
        ["60–74 分", "存疑", "多项出入，不能确认"],
        ["60 分以下", "很可能为假", "与所称模型明显不符"],
    ]) + [""]


def render_per_model(audit, spec_doc):
    model = dig(spec_doc, "model") or dig(audit, "model") or MISSING
    checks = [c for c in (audit.get("checks") if isinstance(audit.get("checks"), list) else []) if isinstance(c, dict)]
    applicable = [c for c in checks if check_status(c.get("score")) != "na"]
    n_ok = sum(1 for c in applicable if check_status(c.get("score")) == "pass")
    weak = ["%s（%s）" % (esc(CHECK_ZH.get(c.get("name"), c.get("name") or "未命名检查")),
                          CHECK_TEXT[check_status(c.get("score"))])
            for c in applicable if check_status(c.get("score")) != "pass"]
    if not checks:
        cover, remark = MISSING, "未提供检查项（audit JSON 中没有 checks）。"
    else:
        cover = "%d 项中 %d 项通过" % (len(applicable), n_ok)
        remark = ("未完全通过：" + "、".join(weak) + "；其余各项通过。") if weak else "各项检查均通过。"
    out = ["## 三、逐模型结果", "每个模型一张简表；只写“通过 / 部分通过 / 未通过”与等级，不堆数字。", ""]
    out += table(["模型", "等级", "通过情况", "主要说明"],
                 [[T(model), grade_auth(audit.get("score"), audit.get("hard_fail")), cover, remark]])
    out += [""]
    if checks:
        bullets = "；".join("%s：%s" % (esc(CHECK_ZH.get(c.get("name"), c.get("name") or "未命名检查")),
                                       CHECK_TEXT[check_status(c.get("score"))]) for c in checks)
        out.append("- %s：%s。" % (T(model), bullets))
    out.append("- 使用体验：" + plain_experience(spec_doc, audit))
    out.append("")
    return out


def _check_note(audit, name):
    for c in audit.get("checks") or []:
        if isinstance(c, dict) and c.get("name") == name:
            return c.get("note")
    return None


def render_provenance(audit):
    hdr = _check_note(audit, "vendor headers")
    out = ["## 四、渠道来源判断",
           "- 对方声称的渠道：" + NOT_GIVEN + "（audit JSON 不含 `--claim` 原文）",
           "- 实测更像：未判定（需结合 `error_probe.py` 输出与人工核对）",
           "- 判断依据：服务商标识信息检查 —— " + T(hdr),
           "- 错误提示摘录：" + NOT_GIVEN + "（未获得有效错误提示）",
           "- 已排除的假设：" + NOT_GIVEN,
           "- 结论：未判定；无官方对照样本时不下渠道真伪结论", ""]
    return out


def render_risk(audit, spec_doc):
    items = items_of(spec_doc)
    it15 = find_item(items, ident=15, key="hidden_prompt")
    it5 = find_item(items, ident=5, key="thinking_params")
    it6 = find_item(items, ident=6, key="finish_reason")

    def risk_text(it, bad_word, ok_word):
        if not it or it.get("status") in (None, "skip"):
            return MISSING
        return ok_word if it.get("status") == "pass" else bad_word

    inj = risk_text(it15, "疑似存在", "未发现")
    if it15 and it15.get("status") == "partial":
        inj = "证据不全，无法排除"
    lock = risk_text(it5, "有出入（可能被固定于某档或参数不符）", "未发现固定")
    if it5 and it5.get("status") == "partial":
        lock = "部分项与官方不符，需复核"
    abnormal = []
    if audit.get("usage_inflated") is True:
        abnormal.append("用量数字虚高")
    lc = [c for c in (audit.get("checks") or []) if isinstance(c, dict) and c.get("name") == "long context"]
    if lc and check_status(lc[0].get("score")) in ("partial", "fail"):
        abnormal.append("长文档回找不完整")
    if it6 and it6.get("status") in ("partial", "fail"):
        abnormal.append("结束原因字段异常")
    out = ["## 五、风险提示",
           "- 渠道是否暗中添加指令：%s；后果：%s" % (
               inj, "有则回答中的自我介绍不可靠，可能被渠道改写为其他身份" if inj != "未发现" else "自我介绍可信度较高"),
           "- 推理强度是否被固定：%s；后果：%s" % (
               lock, "固定于低档则复杂问题表现可能偏弱，且无法按需调高" if lock not in ("未发现固定", MISSING) else "无明显影响"),
           "- 响应是否异常：%s；后果：%s" % (
               "、".join(abnormal) if abnormal else "未发现",
               "长文档被缩减会漏掉内容，用量虚高会多计费用" if abnormal else "无"),
           "- 其他：无", ""]
    return out


def render_limits(audit, spec_doc):
    items = items_of(spec_doc)
    unmeasured = []
    for it in items:
        if it.get("status") in ("skip", "partial"):
            unmeasured.append("%s（%s）" % (T(it.get("title") or it.get("key")), STATUS_TEXT[status_of(it.get("status"))]))
    if spec_doc is None:
        unmeasured.insert(0, "规范符合度、负载与缓存（未提供 spec JSON）")
    notes = []
    for src in (audit.get("notes"), dig(spec_doc, "notes")):
        if isinstance(src, list):
            notes += [T(x, 300) for x in src]
    out = ["## 六、说明与局限",
           "- 未测项：" + ("；".join(unmeasured) if unmeasured else "无"),
           "- 本套检查的部分环节目前只在模拟环境中验证过，尚无真实渠道的对照样本，结果应视为参考而非定论。",
           "- 文字计量方式的比对依据公开资料离线推算，只是近似，部分模型之间区分不开，模型版本更新后也可能误判，因此仅作辅助。",
           "- 若渠道自行重算用量，则文字计量一项反映的是渠道自身的算法，而非模型。",
           "- 渠道评分仅自动计入“模型为假”“用量虚高”两项扣分；渠道暗中添加指令、混用多个供应商、声称渠道未证实需人工判定。",
           "- 若密钥曾粘贴在聊天中，请尽快更换该密钥。"]
    for n in notes:
        out.append("- 备注：" + n)
    out.append("")
    return out


def bench_summary(rows):
    gated = [r for r in rows if r["verdict"] in ("ok", "bad")]
    ok = sum(1 for r in gated if r["verdict"] == "ok")
    return len(gated), ok


def render_spec_summary(audit, spec_doc, rows, profile):
    out = ["## 七、官方规范符合度与使用性能",
           "本章依据 `spec_audit.py` 的输出，回答“这条渠道对官方接口规范的遵守程度”。与第三章的“真伪”分开看：规范符合度低通常意味着"
           "渠道做了改写、限流或降级，而不是模型一定为假。", ""]
    items = items_of(spec_doc)
    if not items:
        out += ["- 规范符合度：" + NOT_GIVEN + "（未传 spec JSON，15 项均为未测）",
                "- 官方基准对标：" + MISSING, "- 缓存复用：" + MISSING, ""]
        return out
    n_pass = sum(1 for it in items if it.get("status") == "pass")
    score = dig(spec_doc, "spec", "score")
    if is_num(score):
        sc = "%d 项中 %d 项通过（满分 100，得 %s 分；等级：%s）" % (len(items), n_pass, fmt_num(score), grade_spec(score))
    else:
        sc = "%d 项中 %d 项通过（覆盖不足，不出分）" % (len(items), n_pass)
    out.append("- 规范符合度：" + sc)
    off = [it for it in items if it.get("status") in ("partial", "fail")]
    if off:
        out.append("- 主要出入：")
        for it in off:
            out.append("  - %s（%s）：%s" % (T(it.get("title") or it.get("key")), STATUS_TEXT[status_of(it.get("status"))],
                                          T(it.get("note"), 200)))
    else:
        out.append("- 主要出入：未发现明显出入")
    if profile is None:
        out.append("- 官方基准对标：" + NOT_GIVEN + "（未传 `--profile`，无官方绝对值可对照）")
    else:
        total, ok = bench_summary(rows)
        if total:
            out.append("- 官方基准对标：%d 项带门槛的指标中 %d 项在阈值内%s" % (
                total, ok, "；其余偏离官方，详见附录 A.5.3" if ok < total else "；响应速度与官方相近"))
        else:
            out.append("- 官方基准对标：缺少可对照的实测数据（未测）")
    cache_rows = [r for r in rows if r["group"] == "cache" and r["verdict"] in ("ok", "bad")]
    if cache_rows:
        okc = sum(1 for r in cache_rows if r["verdict"] == "ok")
        out.append("- 缓存复用：%d 个场景中 %d 个达到目标；5 分钟与 1 小时缓存写入见附录 A.5.5" % (len(cache_rows), okc))
    else:
        out.append("- 缓存复用：" + (NOT_GIVEN + "（无官方对照或门槛）" if profile is None else MISSING))
    specials = []
    if dig(spec_doc, "multimodal", "video_100mb", "status") in (None, "未测", MISSING):
        specials.append("100MB 视频未实测")
    ttl58 = dig(spec_doc, "cache", "ttl_1h", "read_back_58m")
    if ttl58 in (None, "未测", MISSING):
        specials.append("58 分钟缓存回读未测")
    out.append("- 特殊说明：" + ("；".join(specials) if specials else "无"))
    out.append("")
    return out


# ---- appendix

def render_a0(audit, spec_doc):
    score = audit.get("score")
    ch, _ = channel_score(audit)
    spec_score = dig(spec_doc, "spec", "score")
    items = items_of(spec_doc)
    out = ["---", "", "## 附录：技术明细",
           "以下为技术口径明细，供复核与存档，面向技术人员。", "",
           "### A.0 判定依据（技术口径）",
           "- 渠道评分：%s（算式见 A.1.3）" % (fmt_num(ch) if ch is not None else MISSING),
           "- 模型真伪：真实性分 %s / 100，评级 %s；hard_fail：%s" % (
               N(score), grade_auth(score, audit.get("hard_fail")),
               T(audit.get("hard_fail")) if audit.get("hard_fail") else "未触发"),
           "- 规范符合度：%s" % ("规范分 %s / 100，%d 项中 %d 项通过" % (
               N(spec_score), len(items), sum(1 for it in items if it.get("status") == "pass"))
               if items else NOT_GIVEN),
           "- 渠道真伪：未判定（无错误指纹与官方对照样本）", ""]
    return out


def render_a1(audit, spec_doc):
    score = audit.get("score")
    verdict = audit.get("verdict") or MISSING
    out = ["### A.1 逐模型评分与性能（真实性检查）",
           "数据来源：`model_audit.py --out`（评分版本 `scoring_version`=%s；v2 与 v1 分数不可比）。" % N(audit.get("scoring_version") or 2),
           "", "#### A.1.0 审计参数", ""]
    out += table(["模型", "协议 `protocol`", "评分版本", "声称渠道 `--claim`", "声称窗口", "声称分词器家族"], [[
        T(dig(spec_doc, "model") or audit.get("model")), T(dig(spec_doc, "protocol") or audit.get("protocol")),
        N(audit.get("scoring_version") or 2), NOT_GIVEN, NOT_GIVEN, NOT_GIVEN]])
    out += ["", "#### A.1.1 真伪分与逐项检查", "",
            "真实性分 **%s / 100**；评级 %s（原始 verdict：%s）；用量虚高：%s；崩溃检查：%s。" % (
                N(score), grade_auth(score, audit.get("hard_fail")), T(verdict),
                N(audit.get("usage_inflated")),
                T("、".join(str(x) for x in audit["crashed_checks"])) if audit.get("crashed_checks") else "无"), ""]
    checks = [c for c in (audit.get("checks") if isinstance(audit.get("checks"), list) else []) if isinstance(c, dict)]
    if checks:
        rows = []
        for c in checks:
            w, s = c.get("weight"), c.get("score")
            if is_num(s) and is_num(w):
                weighted = "%s / %s" % (fmt_num(float(s) * float(w)), fmt_num(w))
            elif s is None:
                weighted = "n/a"
            else:
                weighted = MISSING
            rows.append([T(c.get("name") or "未命名检查"), N(w), N(float(s)) if is_num(s) else ("n/a" if s is None else T(s)),
                         weighted, check_status_cn(s), T(c.get("note"), 400)])
        out += table(["检查项", "权重", "得分", "加权得分", "判定", "说明"], rows, ["", "r", "r", "r", "c", ""])
    else:
        out.append("未提供 checks。")
    notes = audit.get("notes") if isinstance(audit.get("notes"), list) else []
    if notes:
        out += ["", "审计备注："] + ["- " + T(n, 300) for n in notes]
    out += ["", "#### A.1.2 性能分", ""]
    out += table(["性能分", "明细"], [[N(audit.get("performance")), T(audit.get("performance_detail"))]])
    out += ["", "#### A.1.3 渠道评分算式", "", "`" + flat(channel_score(audit)[1]) + "`", ""]
    return out


def check_status_cn(score):
    return CHECK_TEXT[check_status(score)]


def render_a2():
    return ["### A.2 渠道溯源与错误指纹",
            "数据来源：响应头与 `error_probe.py --out`。本报告的输入不含这两类数据：声称渠道、错误指纹、上游推断、被排除的假设均为" + NOT_GIVEN +
            "；不适用的探针不得当作“无泄漏”。", ""]


def render_a3(spec_doc):
    out = ["### A.3 注入检测与思考锁定", ""]
    if spec_doc is None:
        return out + ["未提供 spec JSON：" + MISSING, ""]
    hp = dig(spec_doc, "hidden_prompt", default={}) or {}
    rows = [["隐藏 system prompt 固定开销（tokens）", N(hp.get("fixed_overhead"))],
            ["泄漏率", T(hp.get("leak_rate"))],
            ["身份自述", T(dig(hp, "identity", "detail"))],
            ["探针成功 / 总数", "%s / %s" % (N(hp.get("probe_successes")), N(hp.get("probe_total")))],
            ["结论", T(hp.get("verdict"))]]
    out += table(["隐藏 prompt 检测", "结果"], rows) + [""]
    th = dig(spec_doc, "probes", "thinking", default={}) or {}
    out += table(["思考参数检查", "结果"], [
        ["档位 tiers_ok", kv_text(th.get("tiers_ok"))], ["默认 reasoning", N(th.get("default_reasoning"))],
        ["disabled 时 completion tokens", N(th.get("disabled_completion_tokens"))],
        ["固定参数 fixed_ok", N(th.get("fixed_ok"))], ["max_completion_tokens 限制", N(th.get("mct_ok"))],
        ["assistant 回传", N(th.get("assistant_echo_ok"))]]) + [""]
    return out


def render_a4(spec_doc):
    items = items_of(spec_doc)
    out = ["### A.4 规范符合度（15 项）",
           "数据来源：`spec_audit.py --out`（`spec.version`=%s）。`spec_score` 独立于真伪分，两者不可互相换算。" %
           N(dig(spec_doc, "spec", "version") or 1), ""]
    if not items:
        return out + ["未提供 spec JSON：15 项规范清单为" + MISSING + "。", ""]
    out += table(["#", "规范项", "权重", "状态", "实测", "预期", "偏差 / 证据"], [[
        str(i), "%s（%s）" % (T(it.get("title") or it.get("key")), code_key(it.get("key") or "?")),
        N(it.get("weight")), STATUS_TEXT[status_of(it.get("status"))],
        kv_text(it.get("measured")), kv_text(it.get("expected")),
        "；".join(x for x in (kv_text(it.get("delta")) if it.get("delta") else "", evidence_text(it.get("evidence")))
                 if x and x != MISSING) or MISSING,
    ] for i, it in enumerate(items, 1)], ["r", "", "r", "c", "", "", ""])
    out += ["", "逐项一句话结论："]
    for i, it in enumerate(items, 1):
        out.append("- %02d %s：%s" % (i, T(it.get("title") or it.get("key")), T(it.get("note"), 300)))
    score = dig(spec_doc, "spec", "score")
    out += ["", "规范分：%s / 100（%s）。`spec_score = round(100 × Σ(权重×状态) / Σ(非 skip 权重))`，pass=1、partial=0.5、fail=0、skip 不入分母；"
            "非 skip 权重不足 60%% 时不出分。partial 只给证据不全的项，不得当作通过。" % (N(score), grade_spec(score))]
    matrix = dig(spec_doc, "params", "matrix", default=[]) or []
    if isinstance(matrix, list) and matrix:
        ms = [m for m in matrix if isinstance(m, dict)]
        matched = sum(1 for m in ms if m.get("match") is True)
        out += ["", "参数矩阵：%d 例，一致 %d 例。" % (len(ms), matched)]
        bad = [m for m in ms if m.get("match") is not True]
        if bad:
            out += table(["用例", "预期状态", "实测状态", "比对", "说明"], [[
                T(m.get("case")), N(m.get("expected_status"), thousands=False), N(m.get("actual_status"), thousands=False),
                "不一致" if m.get("match") is False else MISSING, T(m.get("detail"), 200)] for m in bad])
    out.append("")
    return out


def bench_unit(r):
    return {"%": "%", "s": " s", "ms": " ms", "tok/s": " tok/s"}.get(r["unit"], "")


def bench_threshold(r):
    thr, kind = r.get("threshold"), r.get("threshold_kind")
    if thr is None or kind is None:
        return "未设"
    t = fmt_num(float(thr))
    return {"ratio_max": "实测 ÷ 官方 ≤ " + t, "ratio_min": "实测 ÷ 官方 ≥ " + t,
            "abs_max": "实测 ≤ " + t + " s", "pct_min": "实测 ≥ " + t + "%"}.get(kind, "未设")


def bench_verdict(r):
    if r["verdict"] in VERDICT_ZH:
        return VERDICT_ZH[r["verdict"]]
    if r.get("threshold") is None and r["official"] is not None and r["measured"] is not None:
        return "仅展示"
    return "未评"


def bench_label(r):
    name = "%s `%s`" % (METRIC_ZH.get(r["metric"], esc(r["metric"])), r["metric"])
    if r["group"] == "ttft":
        name += "（绝对上限）"
    return name


def render_a5(audit, spec_doc, profile, rows):
    out = ["### A.5 核心标准、全部指标、官方基准、计费与缓存",
           "数据来源：`spec_audit.py` 的 `load` 与 `cache` 块；官方值来自画像基准存档。", ""]
    if spec_doc is None:
        return out + ["未提供 spec JSON：以下各节均为" + MISSING + "；官方基准对标见 A.5.3。", ""] + \
            render_a53(None, profile, rows)
    items = items_of(spec_doc)
    load = dig(spec_doc, "load", default={}) or {}
    pf = perf_flags(spec_doc)

    # ---- A.5.1 core standards (HTML section 01)
    out += ["#### A.5.1 核心标准（%s / 5 项达标）" % N(pf), ""]
    crows = []
    it1 = find_item(items, ident=1, key="perf_baseline")
    if it1:
        groups = ratio_groups(it1)
        worst = {q: max((v for _, v in pairs), default=None) for q, pairs in groups.items()}
        main = worst["p90"] if worst["p90"] is not None else worst["p50"]
        exp = dig(it1, "expected", default={}) or {}
        tb = []
        if is_num(exp.get("p50_ratio_max")):
            tb.append("P50 ≤ %s×" % fmt_num(exp["p50_ratio_max"]))
        if is_num(exp.get("p90_ratio_max")):
            tb.append("P90 ≤ %s×" % fmt_num(exp["p90_ratio_max"]))
        if is_num(exp.get("output_tps_p50_ratio_min")):
            tb.append("TPS ≥ %s×" % fmt_num(exp["output_tps_p50_ratio_min"]))
        d = dig(it1, "delta", default={}) or {}
        db = []
        if is_num(d.get("p50_margin")):
            db.append("P50 余量 %s×" % fmt_num(d["p50_margin"]))
        if is_num(d.get("p90_margin")):
            db.append("P90 余量 %s×" % fmt_num(d["p90_margin"]))
        crows.append(["官方基准对标（最差 P90 比率，实测 ÷ 官方）", N(main, "×") if main is not None else MISSING,
                      " · ".join(tb) or MISSING, " · ".join(db) or MISSING, CORE_TEXT[status_of(it1.get("status"))]])
    else:
        crows.append(["官方基准对标", MISSING, MISSING, MISSING, MISSING])
    it2 = find_item(items, ident=2, key="ttft")
    if it2:
        m, e = dig(it2, "measured", default={}) or {}, dig(it2, "expected", default={}) or {}
        mg = dig(it2, "delta", "margin_s")
        crows.append(["TTFT P90（首 token 延迟）", N(m.get("p90_s"), " s") if is_num(m.get("p90_s")) else MISSING,
                      "≤ %s s" % fmt_num(e["p90_max_s"]) if is_num(e.get("p90_max_s")) else MISSING,
                      "余量 %s s" % fmt_num(mg) if is_num(mg) else MISSING, CORE_TEXT[status_of(it2.get("status"))]])
    else:
        crows.append(["TTFT P90", MISSING, MISSING, MISSING, MISSING])
    it3 = find_item(items, ident=3, key="cache_hit")
    if it3:
        m, e = dig(it3, "measured", default={}) or {}, dig(it3, "expected", default={}) or {}
        pairs = [(k, v) for k, v in m.items() if is_num(v) and "pct" in k]
        low = min(pairs, key=lambda kv: kv[1]) if pairs else None
        mg = dig(it3, "delta", "margin_pct")
        crows.append(["缓存命中（三类场景最低值）", N(low[1], "%") if low else MISSING,
                      "≥ %s %%" % fmt_num(e["min_pct"]) if is_num(e.get("min_pct")) else MISSING,
                      "余量 %s pp" % fmt_num(mg) if is_num(mg) else MISSING, CORE_TEXT[status_of(it3.get("status"))]])
    else:
        crows.append(["缓存命中", MISSING, MISSING, MISSING, MISSING])
    sr = load.get("success_rate")
    if is_num(sr):
        srv = "达标" if float(sr) == 100.0 else ("部分达标" if float(sr) >= 95.0 else "未达标")
    else:
        srv = MISSING
    crows.append(["请求成功率（成功 %s / 样本 %s）" % (N(load.get("success")), N(load.get("samples"))),
                  N(sr, "%") if is_num(sr) else MISSING, "= 100 %", "—", srv])
    tp = dig(load, "percentiles", "tpot_ms", default={}) or {}
    crows.append(["TPOT P90（每输出 token 耗时）", N(tp.get("p90"), " ms") if is_num(tp.get("p90")) else MISSING,
                  "未提供阈值", "—", "仅展示"])
    out += table(["核心标准", "实测", "目标", "余量", "判定"], crows, ["", "r", "", "", "c"])

    # ---- A.5.2 all metrics (HTML section 02)
    out += ["", "#### A.5.2 全部指标（分位数、吞吐、token、finish）", ""]
    pct = load.get("percentiles") or {}
    if not isinstance(pct, dict) or not pct:
        out.append("负载分位数：" + MISSING)
    else:
        mrows = []
        for key, label, unit in (("latency_s", "总耗时", "s"), ("ttft_s", "首字耗时", "s"),
                                 ("tpot_ms", "每字耗时", "ms"), ("output_tps", "输出速度", "tok/s")):
            node = pct.get(key) or {}
            mrows.append(["%s `%s`（%s）" % (label, key, unit)] +
                         [N(node.get(q)) if is_num(node.get(q)) else MISSING for q in ("p50", "p90", "p95")] +
                         [N(node.get("n")) if is_num(node.get("n")) else MISSING])
        out += table(["指标", "P50", "P90", "P95", "样本 n"], mrows, ["", "r", "r", "r", "r"])
    th = load.get("throughput") or {}
    fin = load.get("finish") or {}
    tot = load.get("totals") or {}
    out += ["", "- 吞吐：RPM %s、TPM %s（输入 %s / 输出 %s）" % (N(th.get("rpm")), N(th.get("tpm")), N(th.get("input_tpm")),
                                                            N(th.get("output_tpm"))),
            "- token 合计：prompt %s / completion %s / cached %s / cache_write %s" % (
                N(tot.get("prompt_tokens")), N(tot.get("completion_tokens")), N(tot.get("cached_tokens")),
                N(tot.get("cache_write_tokens"))),
            "- finish 分布：stop %s / length %s / tool_calls %s / empty %s / other %s；出现 empty 即第 6 项失败" % (
                N(fin.get("stop")), N(fin.get("length")), N(fin.get("tool_calls")), N(fin.get("empty")), N(fin.get("other"))),
            "- 样本：%s 次，成功 %s 次（成功率 %s）；p95 在 60 样本下置信度低。" % (
                N(load.get("samples")), N(load.get("success")), N(sr, "%") if is_num(sr) else MISSING), ""]

    out += render_a53(spec_doc, profile, rows)
    out += render_a54(spec_doc, items)
    out += render_a55(spec_doc)
    return out


def render_a53(spec_doc, profile, rows):
    out = ["#### A.5.3 官方基准对标（官方 / 实测）", ""]
    if spec_doc is None:
        out.append("未提供 spec JSON：实测值为" + MISSING + "。")
    if profile is None:
        out.append("未提供 `--profile`：官方值为" + NOT_GIVEN + "，不做判定。")
    pid = profile.get("id") if isinstance(profile, dict) else None
    if pid:
        out.append("画像：%s。" % T(pid))
    if spec_doc is None and profile is None:
        return out + [""]
    trs = []
    for r in rows:
        off = N(r["official"]) if r["official"] is not None else NOT_GIVEN
        mea = N(r["measured"]) if r["measured"] is not None else MISSING
        trs.append([bench_label(r), (r["pctl"] or "—").upper() if r["pctl"] else "—",
                    "%s / %s%s" % (off, mea, bench_unit(r)),
                    signed(r["delta_pct"], "%") if r["delta_pct"] is not None else "—",
                    bench_threshold(r), bench_verdict(r)])
    out += [""] + table(["指标", "分位", "官方 / 实测", "偏差", "门槛", "判定"], trs, ["", "c", "r", "r", "", "c"])
    out += ["", "偏差 =（实测 − 官方）÷ 官方；延迟类越低越好，输出速度与缓存命中类越高越好。官方值为画像存档，不是本次调用官方端点的结果。", ""]
    return out


def render_a54(spec_doc, items):
    out = ["#### A.5.4 计费（usage）", ""]
    usage = dig(spec_doc, "probes", "usage", default={}) or {}
    item4 = find_item(items, ident=4, key="usage_schema")
    exp = dig(item4, "expected", default={}) or {}
    required = exp.get("required") if isinstance(exp.get("required"), list) else []
    details = exp.get("details") if isinstance(exp.get("details"), dict) else {}
    dtext = "；".join("%s: %s" % (k, ", ".join(str(x) for x in v)) for k, v in details.items() if isinstance(v, list))

    def brow(label, key, expected):
        v = usage.get(key)
        return [T(label), "是" if v is True else ("否" if v is False else MISSING), T(expected),
                "通过" if v is True else ("未通过" if v is False else "跳过")]
    rows = [brow("必需字段", "required_present", ", ".join(str(x) for x in required) or "—"),
            brow("total_tokens 一致性", "total_matches", "等于分项之和"),
            brow("明细子键", "details_present", dtext or "—"),
            brow("缓存写入字段", "cache_write_present", "cache_write_tokens")]
    tr = usage.get("token_ratio")
    rr = exp.get("token_ratio_range") if isinstance(exp.get("token_ratio_range"), list) else []
    if is_num(tr):
        if len(rr) == 2 and all(is_num(x) for x in rr):
            rows.append(["token / char 比率", fmt_num(tr), "%s – %s" % (fmt_num(rr[0]), fmt_num(rr[1])),
                         "通过" if float(rr[0]) <= float(tr) <= float(rr[1]) else "未通过"])
        else:
            rows.append(["token / char 比率", fmt_num(tr), NOT_GIVEN, "跳过"])
    else:
        rows.append(["token / char 比率", MISSING, "—", "跳过"])
    return out + table(["检查项", "结果", "官方期望", "判定"], rows, ["", "", "", "c"]) + [""]


def render_a55(spec_doc):
    out = ["#### A.5.5 缓存场景与写入有效期", ""]
    cache = dig(spec_doc, "cache", default={}) or {}
    trows = []
    for key, title in (("ttl_5m", "5 分钟缓存写入"), ("ttl_1h", "1 小时缓存写入")):
        ttl = cache.get(key)
        if not isinstance(ttl, dict) or not ttl:
            trows.append([title, "跳过", MISSING, MISSING, MISSING, MISSING, MISSING])
            continue
        st = "通过" if ttl.get("pass") is True else ("未通过" if ttl.get("pass") is False else "跳过")
        trows.append([title, st, N(ttl.get("write_tokens")), T(ttl.get("header")),
                      N(ttl.get("read_back_hit_pct"), "%") if is_num(ttl.get("read_back_hit_pct")) else MISSING,
                      N(ttl.get("read_back_after_s"), " s") if is_num(ttl.get("read_back_after_s")) else "—",
                      T(ttl.get("read_back_58m")) if key == "ttl_1h" else "—"])
    out += table(["写入项", "判定", "写入 tokens", "响应头值", "回读命中", "回读延迟", "58 分钟回读"], trows,
                 ["", "c", "r", "", "r", "r", ""])
    sc = cache.get("scenarios")
    labels = {"A_identical_20k": "A · 相同 20k", "B_multiturn_12k": "B · 多轮 12k", "C_short_1k": "C · 短文本 1k",
              "E_cache_key": "E · cache_key"}
    out.append("")
    if isinstance(sc, dict) and sc:
        rows = []
        for name, s in sc.items():
            if not isinstance(s, dict):
                continue
            st = "通过" if s.get("pass") is True else ("未通过" if s.get("pass") is False else "跳过")
            rnd = s.get("rounds") if isinstance(s.get("rounds"), list) else []
            rows.append([T(labels.get(name, name)), st,
                         N(s.get("hit_rate_pct"), "%") if is_num(s.get("hit_rate_pct")) else MISSING, N(len(rnd))])
        out += table(["缓存场景", "判定", "命中率", "轮次"], rows, ["", "c", "r", "r"])
    else:
        out.append("缓存场景：" + MISSING + "（cache.scenarios 为空）。")
    out.append("")
    return out


def render_footer(profile):
    date = benchmark.baseline_source(profile)
    return ["---", "",
            "数据来源：实测 JSON · 基准：官方存档 %s · 由 `report_md.py` 确定性生成，结论与 HTML 报告一致。" %
            (T(date) if date else NOT_GIVEN), ""]


# --------------------------------------------------------------------------- assembly

def build_report(audit, spec_doc, profile, title):
    audit, spec_doc = normalize_audit(audit), normalize_spec(spec_doc)  # malformed input degrades to 未测, never raises
    rows = benchmark.build_rows(spec_doc, profile)
    parts = []
    parts += render_header(audit, spec_doc, title)
    parts += render_conclusion(audit, spec_doc, rows)
    parts += render_method()
    parts += render_per_model(audit, spec_doc)
    parts += render_provenance(audit)
    parts += render_risk(audit, spec_doc)
    parts += render_limits(audit, spec_doc)
    parts += render_spec_summary(audit, spec_doc, rows, profile)
    parts += render_a0(audit, spec_doc)
    parts += render_a1(audit, spec_doc)
    parts += render_a2()
    parts += render_a3(spec_doc)
    parts += render_a4(spec_doc)
    parts += render_a5(audit, spec_doc, profile, rows)
    parts += render_footer(profile)
    text = "\n".join(parts)
    return re.sub(r"\n{3,}", "\n\n", text).rstrip("\n") + "\n"


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Render a Markdown model conformance report (deterministic).")
    p.add_argument("--audit", required=True, help="model_audit v2 JSON output")
    p.add_argument("--spec", default="", help="spec_audit JSON output (optional)")
    p.add_argument("--profile", default="", help="profile id or .json path for the official baseline (optional)")
    p.add_argument("--out", required=True, help="output .md path")
    p.add_argument("--title", default=DEFAULT_TITLE, help="report title (default: use the template heading <host> 大模型接口测试报告)")
    args = p.parse_args()

    key = os.environ.get("LLM_API_KEY", "")
    args.title = scrub(args.title, key)  # the title may carry the key (e.g. expanded from $LLM_API_KEY)
    try:
        audit = load_json(args.audit, key)
    except (OSError, ValueError) as e:
        sys.exit("error: cannot read audit JSON %s: %s" % (args.audit, e))
    if not isinstance(audit, dict):
        sys.stderr.write("warning: audit JSON %s is not an object; every audit field is reported as 未测\n" % args.audit)
    spec_doc = None
    if args.spec:
        try:
            spec_doc = load_json(args.spec, key)
        except (OSError, ValueError) as e:
            sys.exit("error: cannot read spec JSON %s: %s" % (args.spec, e))
        if not isinstance(spec_doc, dict):
            sys.stderr.write("warning: spec JSON %s is not an object; every spec field is reported as 未测\n" % args.spec)
    profile = benchmark.load_profile(args.profile) if args.profile else None

    doc = build_report(audit, spec_doc, profile, args.title)
    out_path = os.path.abspath(args.out)
    parent = os.path.dirname(out_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(doc)
    print("wrote %s (%s bytes)" % (out_path, format(os.path.getsize(out_path), ",")))


if __name__ == "__main__":
    main()
