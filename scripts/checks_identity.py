"""Self-reported identity (zh + en) and family facts. Claiming another vendor sets audit.hard_fail unless all facts are right."""
import re

NAME = "identity+facts"
OTHER_VENDOR_NAMES = ["ChatGPT", "OpenAI", "Claude", "Anthropic", "Gemini", "文心", "通义", "DeepSeek", "Kimi", "智谱", "GLM", "Llama", "Qwen"]


def run(audit):
    a = audit.a
    if not a.identity_regex:
        audit.add(NAME, None, "no --identity-regex; skipped")
        return
    rx = re.compile(a.identity_regex, re.I)
    wrong = [n for n in OTHER_VENDOR_NAMES if not rx.search(n)]
    wrong_rx = re.compile("|".join(map(re.escape, wrong)), re.I)
    parts, notes = [], []
    for q in ("你是谁？由哪家公司开发？具体模型名是什么？", "Who created you and which model are you? Answer in one sentence."):
        _, txt = audit.ask(q)
        hit, bad = bool(rx.search(txt)), wrong_rx.search(txt)
        parts.append(0.0 if (bad and not hit) else (1.0 if hit else 0.3))
        if bad:
            notes.append("mentions other vendor: %s" % bad.group(0))
        notes.append(txt[:80].replace("\n", " "))
    score = sum(parts) / len(parts)
    facts = []
    for f in a.fact:
        q, _, pat = f.partition("::")
        _, txt = audit.ask(q)
        facts.append(1.0 if re.search(pat, txt, re.I | re.S) else 0.0)
    if facts:
        score = 0.5 * score + 0.5 * sum(facts) / len(facts)
        notes.append("facts %d/%d" % (sum(facts), len(facts)))
    if any(p == 0.0 for p in parts):
        if facts and all(f == 1.0 for f in facts):
            notes.append("wrong-vendor self-report but family facts all correct: likely training-data contamination, not capped")
        else:
            audit.hard_fail = "identity claims a different vendor"
    audit.add(NAME, score, " | ".join(notes))
