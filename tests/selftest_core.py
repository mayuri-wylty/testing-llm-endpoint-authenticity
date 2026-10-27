#!/usr/bin/env python3
"""Self-test for the T1 core (no network): adapter factory errors, Norm robustness, scoring arithmetic. Exit 0 = all pass.
  python selftest_core.py
"""
import os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, SCRIPTS)
fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
        print("FAIL", msg)


# 1. unavailable protocol -> clean error, rc != 0, no traceback (run against a copy of scripts/ without the optional adapter modules)
tmp = tempfile.mkdtemp(prefix="selftest_core_")
try:
    for f in os.listdir(SCRIPTS):
        if f.endswith(".py") and f not in ("adapters_anthropic.py", "adapters_responses.py"):
            shutil.copy(os.path.join(SCRIPTS, f), tmp)
    env = dict(os.environ, LLM_API_KEY="sk-TESTKEY1234567890abcdef", PYTHONUTF8="1")
    for proto in ("messages", "responses"):
        p = subprocess.run([sys.executable, os.path.join(tmp, "model_audit.py"), "--base", "http://127.0.0.1:9/v1", "--model", "m",
                            "--protocol", proto], capture_output=True, env=env, timeout=60)
        err = p.stderr.decode("utf-8", "replace")
        check(p.returncode != 0, "%s: rc should be non-zero" % proto)
        check("Traceback" not in err, "%s: traceback in stderr: %s" % (proto, err[-200:]))
        check("not available" in err, "%s: unclear error: %s" % (proto, err[-200:]))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# 2. OpenAIChat.parse never raises on odd bodies
from adapters import OpenAIChat, get_adapter, ProtocolUnavailable
ad = get_adapter("chat", "m")
for data in ({}, {"choices": []}, {"choices": None}, {"choices": [None]}, {"choices": [{"message": None}]}, {"id": "x", "usage": {}},
             {"choices": [{"message": {"content": None, "tool_calls": [{"function": {"arguments": "{bad"}}]}}]}, [], "text", None):
    try:
        n = ad.parse(200, data)
        check(isinstance(n.text, str) and isinstance(n.tool_calls, list), "parse(%r) bad types" % (data,))
    except Exception as e:
        check(False, "parse(%r) raised %r" % (data, e))
n = ad.parse(200, {"id": "i", "model": "m", "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}})
check(n.ok and n.has_output and n.text == "hi" and n.usage == {"prompt": 1, "completion": 2, "total": 3, "reasoning": None}, "parse happy path")
check(not ad.parse(200, {"choices": []}).has_output, "empty choices must give has_output False")
check(not ad.parse(500, "boom").ok, "non-200 must give ok False")
try:
    get_adapter("nope")
    check(False, "unknown protocol should raise")
except ProtocolUnavailable:
    pass

# 3. scoring: n/a renormalisation, cap, hard-fail cap
import scoring
t, _ = scoring.total_score([{"name": "a", "weight": 10, "score": 1.0}, {"name": "b", "weight": 90, "score": None}])
check(t == 98, "cap 98 / n/a renormalise, got %s" % t)
t, v = scoring.total_score([{"name": "a", "weight": 10, "score": 1.0}], hard_fail="x")
check(t == 50 and v == "likely fake/substituted", "hard fail cap, got %s %s" % (t, v))
check(sum(scoring.WEIGHTS.values()) == 100 and scoring.VERSION == 2, "v2 weights should sum to 100 (VERSION 2), got %s / v%s" % (sum(scoring.WEIGHTS.values()), scoring.VERSION))
check(scoring.WEIGHTS == {"vendor headers": 5, "format+stream": 10, "identity+facts": 15, "reasoning": 15, "long context": 25, "tools+json": 10,
                          "instruction following": 5, "error format": 5, "tokenizer": 10}, "v2 weight table changed: %r" % (scoring.WEIGHTS,))

print("selftest_core: %s" % ("FAIL (%d)" % len(fails) if fails else "ok"))
sys.exit(1 if fails else 0)
