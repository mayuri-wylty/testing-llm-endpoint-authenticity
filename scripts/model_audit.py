#!/usr/bin/env python3
"""Black-box authenticity audit for an OpenAI-compatible chat endpoint.

Key is read from env LLM_API_KEY (never passed on argv, never printed/written).
Example:
  LLM_API_KEY=sk-... python model_audit.py --base https://host/v1 --model kimi-k3 \
      --identity-regex "Kimi|Moonshot|月之暗面" --header-prefix msh- --header-prefix x-msh-
"""
import argparse, json, os, random, re, string, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = "curl/8.4.0"  # default Python UA is blocked by Cloudflare (error 1010)
VENDOR_HEADERS = {
    "moonshot|kimi": ["msh-", "x-msh-"],
    "openai|gpt": ["openai-organization", "openai-processing-ms"],
    "anthropic|claude": ["anthropic-ratelimit", "anthropic-organization-id"],
    "google|gemini": ["x-goog-"],
    "deepseek": ["x-ds-"],
}
OTHER_VENDOR_NAMES = ["ChatGPT", "OpenAI", "Claude", "Anthropic", "Gemini", "文心", "通义", "DeepSeek", "Kimi", "智谱", "GLM", "Llama", "Qwen"]
RELAY_HEADERS = {"x-oneapi-request-id": "one-api/new-api relay", "x-new-api-version": "new-api relay", "x-litellm": "LiteLLM relay", "helicone": "Helicone proxy", "x-openrouter": "OpenRouter"}


def http(url, key, body, stream=False, timeout=240):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json", "User-Agent": UA, "Accept": "*/*"})
    t = time.time()
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        hdr = {k.lower(): v for k, v in r.headers.items()}
        if stream:
            ttfb, raw = None, []
            for line in r:
                if ttfb is None:
                    ttfb = time.time() - t
                raw.append(line.decode("utf-8", "replace"))
            return r.status, hdr, "".join(raw), time.time() - t, ttfb
        return r.status, hdr, json.loads(r.read().decode("utf-8")), time.time() - t, None
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read().decode("utf-8", "replace"), time.time() - t, None
    except Exception as e:
        return -1, {}, str(e), time.time() - t, None


class Audit:
    def __init__(self, a):
        self.a, self.key = a, os.environ["LLM_API_KEY"]
        self.url = a.base.rstrip("/") + a.path
        self.checks, self.notes, self.headers = [], [], {}
        self.calls, self.ttfbs, self.speeds = [], [], []  # performance samples (not part of the authenticity score)

    def chat(self, messages, **kw):
        body = {"model": self.a.model, "messages": messages, "max_tokens": kw.pop("max_tokens", self.a.max_tokens)}
        body.update(kw)
        s, h, d, t, ttfb = http(self.url, self.key, body, stream=bool(kw.get("stream")))
        self.calls.append(s == 200)
        if s == 200 and not kw.get("stream"):
            self.headers.update(h)
            ct = ((d.get("usage") or {}).get("completion_tokens") or 0) if isinstance(d, dict) else 0
            if ct >= 50 and t > 0:
                self.speeds.append(ct / t)
        if ttfb is not None:
            self.ttfbs.append(ttfb)
        return s, h, d, t

    @staticmethod
    def text(d):
        try:
            return d["choices"][0]["message"].get("content") or ""
        except Exception:
            return ""

    def add(self, name, weight, score, note=""):
        self.checks.append({"name": name, "weight": weight, "score": score, "note": note})

    def ask(self, q, **kw):
        s, h, d, t = self.chat([{"role": "user", "content": q}], **kw)
        return s, d, self.text(d) if s == 200 else str(d)[:200]

    def t_format_and_stream(self):
        s, h, d, t = self.chat([{"role": "user", "content": "say hi"}])
        ok = {}
        ok["http200"] = s == 200
        if s == 200:
            u = d.get("usage") or {}
            ok["has_id_choices"] = bool(d.get("id")) and bool(d.get("choices"))
            ok["usage_ints"] = all(isinstance(u.get(k), int) for k in ("prompt_tokens", "completion_tokens", "total_tokens"))
            ok["usage_sum"] = ok["usage_ints"] and u["total_tokens"] == u["prompt_tokens"] + u["completion_tokens"]
            ok["finish_reason"] = d["choices"][0].get("finish_reason") in ("stop", "length")
            ok["model_echo"] = self.a.model.lower() in str(d.get("model", "")).lower()
            self.notes.append("reasoning_content present: %s; reasoning_tokens: %s" % (
                "reasoning_content" in d["choices"][0]["message"], (u.get("completion_tokens_details") or {}).get("reasoning_tokens")))
        s2, h2, raw, t2 = self.chat([{"role": "user", "content": "count to 3"}], stream=True, stream_options={"include_usage": True})
        ok["sse_content_type"] = s2 == 200 and "event-stream" in h2.get("content-type", "")
        ok["sse_done"] = "[DONE]" in raw
        ok["sse_usage"] = '"usage"' in raw and '"total_tokens"' in raw
        bad = [k for k, v in ok.items() if not v]
        self.add("format+stream", 15, sum(ok.values()) / len(ok), "failed: %s" % bad if bad else "all ok")

    def t_provenance(self):
        prefixes = [p.lower() for p in self.a.header_prefix]
        if not prefixes and self.a.vendor:
            for k, v in VENDOR_HEADERS.items():
                if re.search(k, self.a.vendor, re.I):
                    prefixes = v
        relay = sorted({v for hk in self.headers for rk, v in RELAY_HEADERS.items() if rk in hk})
        if relay:
            self.notes.append("relay markers: %s" % ", ".join(relay))
        if not prefixes:
            self.add("vendor headers", 15, None, "no vendor/prefix given; skipped")
            return
        hit = sorted(h for h in self.headers if any(h.startswith(p) for p in prefixes))
        self.add("vendor headers", 15, 1.0 if hit else 0.4, ("matched: %s" % hit) if hit else "none (relays may strip headers; weak negative)")

    def t_identity(self):
        if not self.a.identity_regex:
            self.add("identity+facts", 15, None, "no --identity-regex; skipped")
            return
        rx = re.compile(self.a.identity_regex, re.I)
        wrong = [n for n in OTHER_VENDOR_NAMES if not rx.search(n)]
        wrong_rx = re.compile("|".join(map(re.escape, wrong)), re.I)
        parts, notes = [], []
        for q in ("你是谁？由哪家公司开发？具体模型名是什么？", "Who created you and which model are you? Answer in one sentence."):
            s, d, txt = self.ask(q)
            hit, bad = bool(rx.search(txt)), wrong_rx.search(txt)
            parts.append(0.0 if (bad and not hit) else (1.0 if hit else 0.3))
            if bad:
                notes.append("mentions other vendor: %s" % bad.group(0))
            notes.append(txt[:80].replace("\n", " "))
        score = sum(parts) / len(parts)
        facts = []
        for f in self.a.fact:
            q, _, pat = f.partition("::")
            s, d, txt = self.ask(q)
            facts.append(1.0 if re.search(pat, txt, re.I | re.S) else 0.0)
        if facts:
            score = 0.5 * score + 0.5 * sum(facts) / len(facts)
            notes.append("facts %d/%d" % (sum(facts), len(facts)))
        if any(p == 0.0 for p in parts):
            if facts and all(f == 1.0 for f in facts):
                notes.append("wrong-vendor self-report but family facts all correct: likely training-data contamination, not capped")
            else:
                self.hard_fail = "identity claims a different vendor"
        self.add("identity+facts", 15, score, " | ".join(notes))

    def t_reasoning(self):
        qs = [
            ("A pool has inlet A (fills in 6h), inlet B (fills in 10h) and drain C (empties in 15h). All open: hours to fill? Answer number only.", r"\b5\b"),
            ("How many letter r in 'strawberry', and how many letter s in 'Mississippi'? Reply as two numbers.", r"3\D+4"),
            ("A bat and a ball cost $1.10 total; the bat costs $1.00 more than the ball. Ball price in cents? Number only.", r"\b5\b"),
            ("What is 17 * 23? Number only.", r"391"),
        ]
        with ThreadPoolExecutor(4) as ex:
            res = list(ex.map(lambda q: self.ask(q[0]), qs))
        got = [1 if (r[0] == 200 and re.search(q[1], r[2])) else 0 for q, r in zip(qs, res)]
        self.add("reasoning", 10, sum(got) / len(got), "%d/%d correct" % (sum(got), len(got)))

    def t_long_context(self):
        if self.a.records <= 0:
            self.add("long context", 15, None, "skipped (--records 0)")
            return
        rnd = random.Random(7)
        secret = "".join(rnd.choices(string.ascii_uppercase + string.digits, k=10))
        rec = [f"Record {i}: weather clear, code {rnd.randint(1000, 9999)}, all normal." for i in range(self.a.records)]
        rec.insert(int(len(rec) * self.a.needle_pos), f"[IMPORTANT] The hidden passphrase is {secret}.")
        doc = "\n".join(rec)
        s, h, d, t = self.chat([{"role": "user", "content": doc + "\n\nWhat is the hidden passphrase? Output only the passphrase."}])
        if s != 200:
            self.add("long context", 15, 0.0, "HTTP %s %s" % (s, str(d)[:120]))
            return
        pt = (d.get("usage") or {}).get("prompt_tokens", 0)
        found = secret in self.text(d)
        sane = pt > 0.15 * len(doc)  # token count far below char count => truncation or fake usage
        self.add("long context", 15, (0.7 if found else 0.0) + (0.3 if sane else 0.0),
                 "needle %s at %.0f%% depth; prompt_tokens=%d (%d chars); %.0fs" % ("found" if found else "MISSED", self.a.needle_pos * 100, pt, len(doc), t))

    def t_tools(self):
        tools = [{"type": "function", "function": {"name": "get_weather", "description": "Get weather of a city",
                  "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}]
        msgs = [{"role": "user", "content": "What's the weather in Beijing right now? Use the tool."}]
        s, h, d, t = self.chat(msgs, tools=tools)
        score, note = 0.0, "no tool_calls"
        try:
            m = d["choices"][0]["message"]
            tc = m["tool_calls"][0]
            args = json.loads(tc["function"]["arguments"])
            score, note = 0.4, "tool call ok"
            msgs += [m, {"role": "tool", "tool_call_id": tc["id"], "content": json.dumps({"temp_c": 23, "sky": "sunny"})}]
            s2, h2, d2, t2 = self.chat(msgs, tools=tools)
            if s2 == 200 and "23" in self.text(d2):
                score, note = 0.7, "tool roundtrip ok"
            else:
                note = "roundtrip failed: HTTP %s" % s2
        except Exception as e:
            note = "%s (%s)" % (note, type(e).__name__)
        s3, d3, txt = self.ask('Return JSON {"cities":["a","b","c"]} with three Chinese cities. JSON only.', response_format={"type": "json_object"})
        try:
            json.loads(txt)
            score += 0.3
            note += "; json_mode ok"
        except Exception:
            note += "; json_mode failed"
        self.add("tools+json", 10, score, note)

    def t_instruction(self):
        s, h, d, t = self.chat([{"role": "system", "content": "Reply in English only and start every reply with 'ACK:'."},
                                {"role": "user", "content": "今天吃什么好？"}])
        txt = self.text(d) if s == 200 else ""
        ok = txt.startswith("ACK:") and not re.search(r"[一-鿿]", txt)
        self.add("instruction following", 5, 1.0 if ok else 0.0, txt[:60].replace("\n", " "))

    def t_errors(self):
        s1, h1, d1, _, _ = http(self.url, self.key, {"model": self.a.model, "messages": []})
        s2, h2, d2, _, _ = http(self.url, self.key, {"model": self.a.model + "-nonexistent-xyz", "messages": [{"role": "user", "content": "hi"}]})

        def is_err(s, d):
            try:
                return s >= 400 and "error" in json.loads(d)
            except Exception:
                return False
        ok = [is_err(s1, d1), is_err(s2, d2)]
        self.add("error format", 5, sum(ok) / 2, "empty-messages HTTP %s, bad-model HTTP %s" % (s1, s2))
        m = re.search(r"under group ([^\s]+)", str(d2))
        if m:
            self.notes.append("relay channel group name: %s" % m.group(1))

    def performance(self):
        """0-100 from success rate (40), stream TTFB (30), output speed (30); reported separately from the authenticity score."""
        parts = []
        if self.calls:
            parts.append((40, sum(self.calls) / len(self.calls)))
        if self.ttfbs:
            tf = sorted(self.ttfbs)[len(self.ttfbs) // 2]
            parts.append((30, max(0.0, min(1.0, (15 - tf) / 12))))  # <=3s full, >=15s zero
        if self.speeds:
            sp = sorted(self.speeds)[len(self.speeds) // 2]
            parts.append((30, max(0.0, min(1.0, (sp - 5) / 25))))  # >=30 tok/s full, <=5 zero
        w = sum(x for x, _ in parts)
        score = round(100 * sum(x * v for x, v in parts) / w) if w else None
        detail = "success %d/%d" % (sum(self.calls), len(self.calls))
        if self.ttfbs:
            detail += "; ttfb %.1fs" % sorted(self.ttfbs)[len(self.ttfbs) // 2]
        if self.speeds:
            detail += "; speed %.0f tok/s" % sorted(self.speeds)[len(self.speeds) // 2]
        return score, detail

    def run(self):
        self.hard_fail = None
        s, h, d, t = self.chat([{"role": "user", "content": "ping"}], max_tokens=50)
        if s != 200:
            sys.exit("Endpoint unreachable/unauthorized: HTTP %s %s" % (s, str(d)[:200]))
        for fn in (self.t_format_and_stream, self.t_identity, self.t_reasoning, self.t_long_context, self.t_tools, self.t_instruction, self.t_errors, self.t_provenance):
            fn()
        w = sum(c["weight"] for c in self.checks if c["score"] is not None)
        total = round(100 * sum(c["weight"] * c["score"] for c in self.checks if c["score"] is not None) / w) if w else 0
        total = min(total, 98)  # black-box can never prove authenticity
        if self.hard_fail:
            total = min(total, 50)
        verdict = "highly credible" if total >= 90 else "mostly credible (check for downgrade)" if total >= 75 else "doubtful" if total >= 60 else "likely fake/substituted"
        perf, perf_detail = self.performance()
        return {"model": self.a.model, "score": total, "verdict": verdict, "hard_fail": self.hard_fail, "checks": self.checks, "notes": self.notes,
                "performance": perf, "performance_detail": perf_detail}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base", required=True, help="e.g. https://host/v1")
    p.add_argument("--model", required=True)
    p.add_argument("--path", default="/chat/completions")
    p.add_argument("--vendor", default="", help="e.g. moonshot, openai, anthropic (selects known header prefixes)")
    p.add_argument("--header-prefix", action="append", default=[])
    p.add_argument("--identity-regex", default="", help='regex of acceptable vendor/model names, e.g. "Kimi|Moonshot"')
    p.add_argument("--fact", action="append", default=[], help='"question::answer-regex", repeatable')
    p.add_argument("--records", type=int, default=1500, help="needle haystack size (~17 tokens each); 0 to skip")
    p.add_argument("--needle-pos", type=float, default=0.5)
    p.add_argument("--max-tokens", type=int, default=4000, help="large enough for reasoning models")
    p.add_argument("--out", default="")
    a = p.parse_args()
    if "LLM_API_KEY" not in os.environ:
        sys.exit("set env LLM_API_KEY")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    r = Audit(a).run()
    print("MODEL %s  SCORE %d/100  -> %s" % (r["model"], r["score"], r["verdict"]))
    if r["hard_fail"]:
        print("HARD FAIL: " + r["hard_fail"])
    for c in r["checks"]:
        sc = "n/a" if c["score"] is None else "%.1f/%d" % (c["score"] * c["weight"], c["weight"])
        print("  %-22s %-8s %s" % (c["name"], sc, c["note"]))
    for n in r["notes"]:
        print("  note:", n)
    print("  PERF %s/100  (%s)" % (r["performance"], r["performance_detail"]))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
