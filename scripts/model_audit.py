#!/usr/bin/env python3
"""Black-box authenticity audit for an LLM chat endpoint (OpenAI chat today; messages/responses via adapters).

Key is read from env LLM_API_KEY (never passed on argv, never printed/written).
Example:
  LLM_API_KEY=sk-... python model_audit.py --base https://host/v1 --model kimi-k3 \
      --identity-regex "Kimi|Moonshot|月之暗面" --header-prefix msh- --header-prefix x-msh-

This file is only CLI + orchestration + the CHECKS registry. Weights live in scoring.py, wire formats in adapters*.py,
check logic in checks_*.py (see tests/CONTRACT.md).
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # also under `python -I`

import scoring
import checks_headers, checks_longctx, checks_reasoning, checks_tokenizer, checks_format, checks_identity, checks_tools, checks_basic
from adapters import Norm, ProtocolUnavailable, PROTOCOLS, get_adapter
from common import DEFAULT_TIMEOUT, http, scrub

# Execution order. Each entry exposes NAME (a key of scoring.WEIGHTS) and run(audit). Register a new check by appending here
# (and adding its weight to scoring.WEIGHTS). Headers/provenance must stay after the checks that collect response headers.
CHECKS = [checks_format, checks_identity, checks_reasoning, checks_longctx, checks_tools,
          checks_basic, checks_basic.ERRORS, checks_headers, checks_tokenizer]


class Resp:
    """One HTTP exchange. raw: parsed JSON (non-stream 200) | raw text (stream or error). stream: adapter.parse_stream() dict or None."""
    __slots__ = ("status", "headers", "t", "ttfb", "norm", "raw", "stream")

    def __init__(self, status, headers, t, ttfb, norm, raw, stream=None):
        self.status, self.headers, self.t, self.ttfb, self.norm, self.raw, self.stream = status, headers, t, ttfb, norm, raw, stream


class Audit:
    def __init__(self, a, adapter=None):
        self.a, self.key = a, os.environ["LLM_API_KEY"]
        self.adapter = adapter or get_adapter(getattr(a, "protocol", "chat"), a.model)
        self.base = a.base.rstrip("/")
        self.path = getattr(a, "path", "") or self.adapter.default_path  # explicit --path overrides the adapter's path
        self.url = self.base + self.path
        self.timeout = DEFAULT_TIMEOUT
        self.checks, self.notes, self.headers = [], [], {}
        self.calls, self.ttfbs, self.speeds = [], [], []  # performance samples (not part of the authenticity score)
        self.crashed, self.usage_inflated, self.hard_fail = [], False, None

    def _url(self, path):
        return self.base + (getattr(self.a, "path", "") or path)

    def chat(self, messages, *, system=None, tools=None, max_tokens=None, stream=False, json_mode=False,
             reasoning_effort=None, timeout=None, extra=None):
        path, body = self.adapter.build(messages, system=system, tools=tools, stream=stream, json_mode=json_mode,
                                        reasoning_effort=reasoning_effort, extra=extra,
                                        max_tokens=self.a.max_tokens if max_tokens is None else max_tokens)
        s, h, d, t, ttfb = http(self._url(path), self.key, body, stream=stream, timeout=timeout or self.timeout,
                                headers=self.adapter.headers(self.key))
        self.calls.append(s == 200)
        if stream:
            st = self.adapter.parse_stream(d if isinstance(d, str) else str(d), h)
            norm = Norm(ok=s == 200, text=st["text"])
            resp = Resp(s, h, t, ttfb, norm, d, st)
        else:
            resp = Resp(s, h, t, ttfb, self.adapter.parse(s, d), d)
            if s == 200:
                self.headers.update(h)
                u = resp.norm.usage or {}
                ct = u.get("completion") if isinstance(u.get("completion"), (int, float)) else 0
                if ct >= 50 and t > 0:
                    self.speeds.append(ct / t)
        if ttfb is not None:
            self.ttfbs.append(ttfb)
        return resp

    def ask(self, q, **kw):
        r = self.chat([{"role": "user", "content": q}], **kw)
        return r, (r.norm.text if r.status == 200 else str(r.raw)[:200])

    def post(self, path, body, timeout=None):
        """Raw request outside the performance stats (error probes). -> (status, headers, data, elapsed)."""
        s, h, d, t, _ = http(self._url(path), self.key, body, timeout=timeout or self.timeout, headers=self.adapter.headers(self.key))
        return s, h, d, t

    def add(self, name, score, note="", weight=None):
        if weight is None:
            if name not in scoring.WEIGHTS:
                raise KeyError("no weight for check %r: add it to scoring.WEIGHTS" % name)
            weight = scoring.WEIGHTS[name]
        self.checks.append({"name": name, "weight": weight, "score": score, "note": note})

    def performance(self):
        return scoring.performance(self.calls, self.ttfbs, self.speeds)

    def run(self):
        r = self.chat([{"role": "user", "content": "ping"}], max_tokens=50)
        if r.status != 200:
            sys.exit(scrub("Endpoint unreachable/unauthorized: HTTP %s %s" % (r.status, str(r.raw)[:200]), self.key))
        for mod in CHECKS:
            try:
                mod.run(self)
            except Exception as e:  # one malformed response must not lose the whole audit; count the check as failed
                self.crashed.append(mod.NAME)
                self.add(mod.NAME, 0.0, "CRASHED (%s: %s); malformed upstream response?" % (type(e).__name__, str(e)[:80]),
                         weight=scoring.WEIGHTS.get(mod.NAME, 0))
        total, verdict = scoring.total_score(self.checks, self.hard_fail)
        perf, perf_detail = self.performance()
        return {"model": self.a.model, "protocol": getattr(self.a, "protocol", "chat"), "scoring_version": scoring.VERSION,
                "score": total, "verdict": verdict, "hard_fail": self.hard_fail, "checks": self.checks, "notes": self.notes,
                "performance": perf, "performance_detail": perf_detail,
                "usage_inflated": self.usage_inflated, "crashed_checks": self.crashed}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base", required=True, help="e.g. https://host/v1")
    p.add_argument("--model", required=True)
    p.add_argument("--protocol", choices=PROTOCOLS, default="chat", help="wire protocol: chat=/chat/completions, messages=Anthropic /v1/messages, responses=/responses")
    p.add_argument("--path", default="", help="override the protocol's default path (chat: /chat/completions)")
    p.add_argument("--vendor", default="", help="e.g. moonshot, openai, anthropic (selects known header prefixes)")
    p.add_argument("--header-prefix", action="append", default=[])
    p.add_argument("--claim", choices=["official", "bedrock", "vertex", "azure", "relay", "unknown"], default="unknown",
                   help="what the channel claims to be (drives the headers check)")
    p.add_argument("--claimed-window", default="", help="claimed context window, e.g. 32k/128k/200k/1M (drives the long-context check)")
    p.add_argument("--deep", action="store_true", help="allow expensive runs (e.g. 1M-window long-context test)")
    p.add_argument("--tokenizer-family", default="", help="tokenizer family to compare against (default: derived from --vendor/--model)")
    p.add_argument("--identity-regex", default="", help='regex of acceptable vendor/model names, e.g. "Kimi|Moonshot"')
    p.add_argument("--fact", action="append", default=[], help='"question::answer-regex", repeatable')
    p.add_argument("--records", type=int, default=None, help="needle haystack size (~17 tokens each); 0 to skip")
    p.add_argument("--needle-pos", type=float, default=None)
    p.add_argument("--max-tokens", type=int, default=4000, help="large enough for reasoning models")
    p.add_argument("--out", default="")
    a = p.parse_args()
    try:
        adapter = get_adapter(a.protocol, a.model)
    except ProtocolUnavailable as e:
        sys.exit("error: %s" % e)
    if "LLM_API_KEY" not in os.environ:
        sys.exit("set env LLM_API_KEY")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    key = os.environ["LLM_API_KEY"]
    r = Audit(a, adapter).run()

    def out(line):
        print(scrub(line, key))
    out("MODEL %s  SCORE %d/100  -> %s" % (r["model"], r["score"], r["verdict"]))
    if r["hard_fail"]:
        out("HARD FAIL: " + r["hard_fail"])
    if r["usage_inflated"]:
        out("USAGE INFLATED: prompt_tokens far above plausible for the input size (apply -5 channel deduction)")
    for c in r["checks"]:
        sc = "n/a" if c["score"] is None else "%.1f/%d" % (c["score"] * c["weight"], c["weight"])
        out("  %-22s %-8s %s" % (c["name"], sc, c["note"]))
    for n in r["notes"]:
        out("  note: " + n)
    out("  PERF %s/100  (%s)" % (r["performance"], r["performance_detail"]))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(scrub(json.dumps(r, ensure_ascii=False, indent=1), key))


if __name__ == "__main__":
    main()
