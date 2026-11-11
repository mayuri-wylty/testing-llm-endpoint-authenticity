#!/usr/bin/env python3
"""Standard-tier load test and cache scenarios for a model channel (stdlib only).

run(prof, base, model, key, ...) -> {"load": ..., "cache": ...}
The output shape is frozen in profiles/README.md and consumed by spec_audit.py. Every statistic goes through
perf_stats; every request re-reads common.get_default_timeout() so a profile override is honoured. The official
baseline host is refused by profile.guard_base() before anything is sent (there is no override switch).

Cache policy:
  A/B/C/E   identical-prefix repeats; hit rate is the average of the rounds after the write round
  ttl_5m    one ordinary write + an immediate read-back (a 5-minute cache cannot be expected to survive a long wait)
  ttl_1h    one write with prompt_cache_options={"mode":"implicit","ttl":"1h"} + read-back after cache_wait_s seconds
  ttl 58m   only with cache_deep=True and cache_wait_s > 0; otherwise reported as "未测" and never fabricated

CLI:
  python load_test.py --profile kimi-k3.1-pro --base https://relay.example.com/v1 --print-plan
  LLM_API_KEY=... python load_test.py --profile kimi-k3.1-pro --base https://relay.example.com/v1 \
      --model kimi-k3.1-pro --cache-wait-s 480 --out result.json
"""
import argparse
import hashlib
import json
import math
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402
import perf_stats  # noqa: E402
import profile as profile_mod  # noqa: E402
from adapters import get_adapter  # noqa: E402

CHAT = "chat"
EST_TOK_PER_CHAR = 0.33  # same rule of thumb as checks_longctx.py
CACHE_TOKENS = {"A": 20000, "B": 12000, "C": 1000, "E": 15000}
B_ROUNDS, C_ROUNDS, E_ROUNDS = 3, 3, 3
CACHE_58M_S = 58 * 60
FIXED_KEYS = ("temperature", "top_p", "n", "presence_penalty", "frequency_penalty")
STOP_MAP = {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length", "tool_use": "tool_calls"}


# --------------------------------------------------------------------------- prompts

def _filler(chars, seed):
    """Deterministic ASCII filler of about `chars` characters (records are easy for any tokenizer)."""
    rnd = random.Random(seed)
    words = ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet",
             "kilo", "lima", "mike", "november", "oscar", "papa", "quebec", "romeo", "sierra", "tango")
    parts, size = [], 0
    while size < chars:
        line = "record %06d: %s %s %s, status normal." % (
            rnd.randrange(1000000), rnd.choice(words), rnd.choice(words), rnd.choice(words))
        parts.append(line)
        size += len(line) + 1
    return " ".join(parts)[:max(0, chars)]


def _prompt(tokens, seed, tail="Reply with the single word OK."):
    """Filler sized from an estimated token count; `tail` is the instruction kept at the end."""
    chars = max(64, int(tokens / EST_TOK_PER_CHAR))
    body = _filler(max(1, chars - len(tail) - 1), seed)
    return body + "\n" + tail


def _load_prompt(i, chars):
    """Unique prompt per sample: a distinct marker at the head keeps results out of the provider cache."""
    tag = "[request %05d %08x]" % (i, random.Random(7919 * (i + 1)).getrandbits(32))
    tail = "\nReply with the single word OK."
    body = _filler(max(1, chars - len(tag) - len(tail) - 2), 1000 + i)
    return tag + "\n" + body + tail


def _cache_key(model):
    """Stable prompt_cache_key for scenario E; identical on every round so the prefix can be reused."""
    digest = hashlib.sha1(("loadtest-E:" + str(model)).encode("utf-8")).hexdigest()[:16]
    return "loadtest-E-" + digest


def _fixed_params(prof):
    fixed = ((prof.get("params") or {}).get("fixed") or {})
    return {k: fixed[k] for k in FIXED_KEYS if k in fixed}


def _extra(prof, protocol, **more):
    """Sampling params (chat only) plus scenario-specific body fields."""
    extra = {}
    if protocol == CHAT:
        extra.update(_fixed_params(prof))
    extra.update({k: v for k, v in more.items() if v is not None})
    return extra or None


# --------------------------------------------------------------------------- usage / finish parsing

def _num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return int(v) if float(v).is_integer() else v


def _usage_chat(u):
    if not isinstance(u, dict) or not u:
        return {}
    det = u.get("prompt_tokens_details") if isinstance(u.get("prompt_tokens_details"), dict) else {}
    cached = det.get("cached_tokens")
    if cached is None:
        cached = u.get("cached_tokens")
    write = det.get("cache_write_tokens")
    if write is None:
        write = u.get("cache_write_tokens")
    return {"prompt_tokens": _num(u.get("prompt_tokens")),
            "completion_tokens": _num(u.get("completion_tokens")),
            "cached_tokens": 0 if cached is None else _num(cached),
            "cache_write_tokens": 0 if write is None else _num(write)}


def _usage_anthropic(u):
    if not isinstance(u, dict) or not u:
        return {}
    inp = _num(u.get("input_tokens"))
    read = _num(u.get("cache_read_input_tokens")) or 0
    write = _num(u.get("cache_creation_input_tokens")) or 0
    return {"prompt_tokens": None if inp is None else inp + read + write,
            "completion_tokens": _num(u.get("output_tokens")),
            "cached_tokens": read, "cache_write_tokens": write}


def _usage_responses(u):
    if not isinstance(u, dict) or not u:
        return {}
    det = u.get("input_tokens_details") if isinstance(u.get("input_tokens_details"), dict) else {}
    return {"prompt_tokens": _num(u.get("input_tokens")),
            "completion_tokens": _num(u.get("output_tokens")),
            "cached_tokens": _num(det.get("cached_tokens")) or 0,
            "cache_write_tokens": _num(u.get("cache_creation_input_tokens")) or 0}


def _finish_chat(d):
    choices = d.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        f = choices[0].get("finish_reason")
        return f if isinstance(f, str) and f else None
    return None


def _finish_responses(d):
    status = d.get("status")
    inc = d.get("incomplete_details") if isinstance(d.get("incomplete_details"), dict) else {}
    if inc.get("reason") in ("max_output_tokens", "length"):
        return "length"
    for item in d.get("output") or []:
        if isinstance(item, dict) and item.get("type") == "function_call":
            return "tool_calls"
    if status in (None, "completed"):
        return "stop"
    return status if isinstance(status, str) else None


def _merge_usage(out, usage):
    """Merge a partial usage object; an absent key never clobbers a value already seen."""
    for k in ("prompt_tokens", "completion_tokens", "cached_tokens", "cache_write_tokens"):
        v = usage.get(k)
        if v is None:
            continue
        if k in ("cached_tokens", "cache_write_tokens") and v == 0 and out.get(k):
            continue
        out[k] = v


def _parse_sse(protocol, raw):
    """Extract usage and finish_reason from an SSE body (chat, Anthropic messages, Responses)."""
    out = {"prompt_tokens": None, "completion_tokens": None, "cached_tokens": None,
           "cache_write_tokens": None, "finish": None, "events": 0, "done": False}
    event = None
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith("event:"):
            event = s[6:].strip()
            if event == "message_stop":
                out["done"] = True
            continue
        if not s.startswith("data:"):
            continue
        payload = s[5:].strip()
        if not payload:
            continue
        if payload == "[DONE]":
            out["done"] = True
            continue
        try:
            d = json.loads(payload)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        out["events"] += 1
        typ = d.get("type") or event
        if protocol == CHAT:
            if isinstance(d.get("usage"), dict) and d["usage"]:
                _merge_usage(out, _usage_chat(d["usage"]))
            f = _finish_chat(d)
            if f:
                out["finish"] = f
        elif protocol == "messages":
            if typ == "message_start":
                msg = d.get("message") if isinstance(d.get("message"), dict) else {}
                _merge_usage(out, _usage_anthropic(msg.get("usage")))
            elif typ == "message_delta":
                _merge_usage(out, _usage_anthropic(d.get("usage")))
                delta = d.get("delta") if isinstance(d.get("delta"), dict) else {}
                sr = delta.get("stop_reason")
                if isinstance(sr, str) and sr:
                    out["finish"] = STOP_MAP.get(sr, sr)
            elif typ == "message_stop":
                out["done"] = True
        else:  # responses
            if typ in ("response.completed", "response.incomplete", "response.failed"):
                resp = d.get("response") if isinstance(d.get("response"), dict) else {}
                _merge_usage(out, _usage_responses(resp.get("usage")))
                out["finish"] = _finish_responses(resp)
                out["done"] = True
    return out


def _read_nonstream(protocol, data):
    """-> (usage dict, finish_reason) from a parsed non-stream 200 body."""
    if protocol == CHAT:
        return _usage_chat(data.get("usage")), _finish_chat(data)
    if protocol == "messages":
        sr = data.get("stop_reason")
        return _usage_anthropic(data.get("usage")), (STOP_MAP.get(sr, sr) if isinstance(sr, str) else None)
    return _usage_responses(data.get("usage")), _finish_responses(data)


# --------------------------------------------------------------------------- one request

def _send(prof, adapter, base, key, messages, *, stream, max_tokens, extra=None):
    """Send one request. Returns a result dict with ok/status/ttft/total/usage/finish/headers/error.

    A timeout, non-200, unparseable body or adapter error gives ok=False and never raises.
    """
    res = {"ok": False, "status": -1, "ttft": None, "total": None, "headers": {}, "error": "",
           "prompt_tokens": None, "completion_tokens": None, "cached_tokens": None,
           "cache_write_tokens": None, "finish": None}
    try:
        path, body = adapter.build(messages, max_tokens=max_tokens, stream=stream, extra=extra)
        url = base.rstrip("/") + path
        status, headers, data, elapsed, ttfb = common.http(
            url, key, body, stream=stream, timeout=common.get_default_timeout(), headers=adapter.headers(key))
        res.update({"status": status, "ttft": ttfb, "total": elapsed, "headers": headers or {}})
        if status != 200:
            res["error"] = str(data)[:300]
            return res
        protocol = adapter.name
        if stream:
            if not isinstance(data, str) or not data.strip():
                res["error"] = "empty stream"
                return res
            parsed = _parse_sse(protocol, data)
            if parsed["events"] == 0:  # no data event at all: [DONE] alone is not a usable sample
                res["error"] = "unparseable stream"
                return res
            for k in ("prompt_tokens", "completion_tokens", "cached_tokens", "cache_write_tokens", "finish"):
                res[k] = parsed[k]
        else:
            if not isinstance(data, dict):
                res["error"] = "non-JSON body"
                return res
            usage, finish = _read_nonstream(protocol, data)
            for k in ("prompt_tokens", "completion_tokens", "cached_tokens", "cache_write_tokens"):
                res[k] = usage.get(k)
            res["finish"] = finish
        res["ok"] = True
    except Exception as e:  # defensive: a broken adapter/response must not abort a batch
        res["error"] = "%s: %s" % (type(e).__name__, e)
    return res


def _failed_sample(total=None):
    return {"ok": False, "ttft": None, "total": total, "completion_tokens": None, "prompt_tokens": None,
            "cached_tokens": None, "cache_write_tokens": None, "finish": None}


def _load_sample(prof, adapter, base, key, i, prompt_chars, max_tokens):
    try:
        r = _send(prof, adapter, base, key, [{"role": "user", "content": _load_prompt(i, prompt_chars)}],
                  stream=True, max_tokens=max_tokens, extra=_extra(prof, adapter.name))
    except Exception:  # one broken request must not abort the batch
        return _failed_sample()
    return {"ok": bool(r["ok"]), "ttft": r["ttft"], "total": r["total"],
            "completion_tokens": r["completion_tokens"], "prompt_tokens": r["prompt_tokens"],
            "cached_tokens": r["cached_tokens"], "cache_write_tokens": r["cache_write_tokens"],
            "finish": r["finish"]}


# --------------------------------------------------------------------------- load block

def _run_load(prof, adapter, base, key, requests, concurrency):
    lt = prof.get("load_test") or {}
    prompt_chars = max(16, int(lt.get("prompt_chars") or 900))
    max_tokens = max(1, int(lt.get("max_completion_tokens") or 512))
    started = time.time()
    results = [None] * requests
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(_load_sample, prof, adapter, base, key, i, prompt_chars, max_tokens): i
                   for i in range(requests)}
        for fut in as_completed(futures):
            i = futures[fut]
            try:
                results[i] = fut.result()
            except Exception:
                results[i] = _failed_sample()
    wall = time.time() - started
    samples = [s for s in results if s is not None]

    agg = perf_stats.summarize(samples)
    totals = agg["totals"]
    return {
        "samples": agg["samples"],
        "success": agg["success"],
        "success_rate": agg["success_rate"],
        "percentiles": {m: agg[m] for m in perf_stats.METRICS},
        "throughput": perf_stats.throughput(wall, agg["samples"], totals["prompt_tokens"], totals["completion_tokens"]),
        "totals": totals,
        "finish": perf_stats.finish_histogram(samples),
        "raw": samples,
    }


# --------------------------------------------------------------------------- cache block

def _cache_rounds(prof, adapter, base, key, message_rounds, max_tokens, extra=None):
    rounds = []
    for i, messages in enumerate(message_rounds, 1):
        r = _send(prof, adapter, base, key, messages, stream=False, max_tokens=max_tokens, extra=extra)
        prompt = r.get("prompt_tokens")
        cached = r.get("cached_tokens")
        rate = perf_stats.cache_hit_rate(cached, prompt)
        rounds.append({"i": i, "cached_tokens": int(cached or 0), "prompt_tokens": int(prompt or 0),
                       "hit_rate_pct": round(rate, 1) if rate is not None else 0.0})
    return rounds


def _scenario_block(rounds, min_pct):
    """Hit rate = average of the rounds after the write round; every round must have a usable prompt size."""
    later = [r["hit_rate_pct"] for r in rounds[1:]]
    complete = bool(rounds) and all(r["prompt_tokens"] > 0 for r in rounds)
    hit_rate = round(sum(later) / len(later), 1) if later else 0.0
    return {"pass": bool(complete and later and hit_rate >= min_pct), "hit_rate_pct": hit_rate, "rounds": rounds}


def _header_int(headers, name):
    """(int value or None, raw string) for a numeric response header."""
    raw = headers.get(name) if isinstance(headers, dict) else None
    if raw is None:
        return None, ""
    text = str(raw).strip()
    try:
        return int(float(text)), text
    except (TypeError, ValueError):
        return None, text


def _ttl_probe(prof, adapter, base, key, prompt, max_tokens, ttl, min_pct, cache_wait_s, cache_deep):
    """One TTL write plus read-back. 5m reads back immediately; 1h waits cache_wait_s seconds."""
    extra = _extra(prof, adapter.name, prompt_cache_options=(
        {"mode": "implicit", "ttl": "1h"} if ttl == "1h" and adapter.name == CHAT else None))
    messages = [{"role": "user", "content": prompt}]
    write = _send(prof, adapter, base, key, messages, stream=False, max_tokens=max_tokens, extra=extra)

    header_name = "msh-usage-cache-write-tokens-%s" % ttl
    header_value, header_raw = _header_int(write.get("headers"), header_name)
    usage_write = write.get("cache_write_tokens")
    write_tokens = header_value if header_value is not None else int(usage_write or 0)

    wait_s = max(0, int(cache_wait_s or 0)) if ttl == "1h" else 0
    if wait_s:
        _log("等待 %d 秒后进行 %s 缓存回读..." % (wait_s, ttl))
        time.sleep(wait_s)
    read_extra = _extra(prof, adapter.name)
    read_back = _send(prof, adapter, base, key, messages, stream=False, max_tokens=max_tokens, extra=read_extra)
    hit = perf_stats.cache_hit_rate(read_back.get("cached_tokens"), read_back.get("prompt_tokens"))
    hit_pct = round(hit, 1) if hit is not None else None

    read_back_58m = "未测"
    if ttl == "1h" and cache_deep and wait_s > 0:
        extra_wait = max(0, CACHE_58M_S - wait_s)
        if extra_wait:
            _log("等待 %d 秒后进行 58 分钟缓存回读..." % extra_wait)
            time.sleep(extra_wait)
        late = _send(prof, adapter, base, key, messages, stream=False, max_tokens=max_tokens, extra=read_extra)
        late_hit = perf_stats.cache_hit_rate(late.get("cached_tokens"), late.get("prompt_tokens"))
        if late_hit is not None:
            read_back_58m = round(late_hit, 1)

    passed = bool(write.get("ok") and header_raw and write_tokens > 0 and hit_pct is not None
                  and hit_pct >= min_pct)
    if isinstance(read_back_58m, (int, float)) and read_back_58m < min_pct:
        passed = False
    out = {"write_tokens": write_tokens, "header": header_raw, "read_back_hit_pct": hit_pct, "pass": passed}
    if ttl == "1h":
        out["read_back_58m"] = read_back_58m
    return out


def _run_cache(prof, adapter, base, key, model, cache_wait_s, cache_deep):
    lt = prof.get("load_test") or {}
    min_pct = float((prof.get("targets") or {}).get("cache_hit_min_pct") or 85)
    repeats = max(2, int(lt.get("cache_repeats_20k") or 6))
    cache_max_tokens = max(1, min(16, int(lt.get("max_completion_tokens") or 512)))

    a_prompt = _prompt(CACHE_TOKENS["A"], 2001)
    a_messages = [[{"role": "user", "content": a_prompt}] for _ in range(repeats)]
    a_rounds = _cache_rounds(prof, adapter, base, key, a_messages, cache_max_tokens,
                             extra=_extra(prof, adapter.name))

    b_base = _prompt(CACHE_TOKENS["B"], 2002, tail="Keep this context. Reply with the single word OK.")
    b_messages = [{"role": "user", "content": b_base + "\nQuestion 1: reply OK."}]
    b_turns = [list(b_messages)]
    for k in range(2, B_ROUNDS + 1):
        b_messages = b_messages + [{"role": "assistant", "content": "OK"},
                                   {"role": "user", "content": "Question %d: reply OK." % k}]
        b_turns.append(list(b_messages))
    b_rounds = _cache_rounds(prof, adapter, base, key, b_turns, cache_max_tokens,
                             extra=_extra(prof, adapter.name))

    c_prompt = _prompt(CACHE_TOKENS["C"], 2003)
    c_messages = [[{"role": "user", "content": c_prompt}] for _ in range(C_ROUNDS)]
    c_rounds = _cache_rounds(prof, adapter, base, key, c_messages, cache_max_tokens,
                             extra=_extra(prof, adapter.name))

    e_prompt = _prompt(CACHE_TOKENS["E"], 2004)
    e_messages = [[{"role": "user", "content": e_prompt}] for _ in range(E_ROUNDS)]
    e_extra = _extra(prof, adapter.name, prompt_cache_key=_cache_key(model))
    e_rounds = _cache_rounds(prof, adapter, base, key, e_messages, cache_max_tokens, extra=e_extra)

    scenarios = {
        "A_identical_20k": _scenario_block(a_rounds, min_pct),
        "B_multiturn_12k": _scenario_block(b_rounds, min_pct),
        "C_short_1k": _scenario_block(c_rounds, min_pct),
        "E_cache_key": _scenario_block(e_rounds, min_pct),
    }

    ttl_5m = _ttl_probe(prof, adapter, base, key, _prompt(CACHE_TOKENS["A"], 2005), cache_max_tokens,
                        "5m", min_pct, 0, False)
    ttl_1h = _ttl_probe(prof, adapter, base, key, _prompt(CACHE_TOKENS["A"], 2006), cache_max_tokens,
                        "1h", min_pct, cache_wait_s, cache_deep)
    ttl_1h["read_back_after_s"] = max(0, int(cache_wait_s or 0))
    ttl_1h = {k: ttl_1h[k] for k in ("write_tokens", "header", "read_back_after_s", "read_back_hit_pct",
                                     "read_back_58m", "pass")}
    return {"scenarios": scenarios, "ttl_5m": ttl_5m, "ttl_1h": ttl_1h}


# --------------------------------------------------------------------------- public entry point

def run(prof, base, model, key, requests=None, concurrency=None, cache_wait_s=480, cache_deep=False):
    """Run the standard load test and cache scenarios; returns {"load": {...}, "cache": {...}}."""
    profile_mod.guard_base(prof, base)  # SystemExit on an official baseline host; no override
    profile_mod.apply_timeout(prof)
    lt = prof.get("load_test") or {}
    n = max(1, int(requests if requests is not None else (lt.get("requests") or 60)))
    workers = max(1, int(concurrency if concurrency is not None else (lt.get("concurrency") or 4)))
    model = model or prof.get("id")
    adapter = get_adapter(prof.get("protocol") or CHAT, model)
    base = str(base).rstrip("/")

    load = _run_load(prof, adapter, base, key, n, workers)
    cache = _run_cache(prof, adapter, base, key, model, cache_wait_s, cache_deep)
    return {"load": load, "cache": cache}


# --------------------------------------------------------------------------- plan / CLI

def plan(prof, model, requests=None, concurrency=None, cache_wait_s=480, cache_deep=False):
    """Human-readable estimate; sends nothing."""
    lt = prof.get("load_test") or {}
    n = max(1, int(requests if requests is not None else (lt.get("requests") or 60)))
    workers = max(1, int(concurrency if concurrency is not None else (lt.get("concurrency") or 4)))
    repeats = max(2, int(lt.get("cache_repeats_20k") or 6))
    wait_s = max(0, int(cache_wait_s or 0))
    cache_requests = repeats + B_ROUNDS + C_ROUNDS + E_ROUNDS + 4 + (1 if cache_deep and wait_s > 0 else 0)
    baseline = profile_mod.model_baseline(prof, model) or {}
    p50 = ((baseline.get("latency_s") or {}).get("p50")) or 10.0

    load_s = math.ceil(n / workers) * float(p50)
    cache_s = cache_requests * float(p50) + wait_s
    if cache_deep and wait_s > 0:
        cache_s += max(0, CACHE_58M_S - wait_s)
    total_requests = n + cache_requests

    print("计划（--print-plan，不发送任何请求）")
    print("  画像: %s (%s), protocol=%s" % (prof.get("id"), prof.get("display_name", ""), prof.get("protocol")))
    print("  模型: %s" % (model or prof.get("id")))
    print("  负载: %d 请求, 并发 %d, prompt_chars=%s, max_completion_tokens=%s" % (
        n, workers, lt.get("prompt_chars"), lt.get("max_completion_tokens")))
    print("  缓存: A=%d 次相同 20k, B=%d 轮多轮 12k, C=%d 轮短 1k, E=%d 次 prompt_cache_key, TTL 写入+回读 4 次"
          % (repeats, B_ROUNDS, C_ROUNDS, E_ROUNDS))
    print("  TTL 回读等待: %d 秒%s" % (wait_s, "（含 58 分钟深度回读）" if cache_deep and wait_s > 0 else ""))
    print("  预计请求总数: %d + %d = %d" % (n, cache_requests, total_requests))
    print("  预计耗时: %s（按画像 p50 与串行批次粗估）" % _fmt_seconds(load_s + cache_s))


def _fmt_seconds(seconds):
    s = int(round(seconds))
    if s < 60:
        return "%d 秒" % s
    return "%d 分 %d 秒" % (s // 60, s % 60)


def _log(message):
    sys.stderr.write("[load_test] %s\n" % message)
    sys.stderr.flush()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="标准档负载测试 + 缓存场景（请求数与并发默认取画像）")
    ap.add_argument("--profile", required=True, help="画像 id 或 JSON 路径")
    ap.add_argument("--base", required=True, help="渠道 URL，如 https://relay.example.com/v1")
    ap.add_argument("--model", default="", help="模型名（默认画像 id）")
    ap.add_argument("--requests", type=int, default=None, help="负载请求数（默认 load_test.requests）")
    ap.add_argument("--concurrency", type=int, default=None, help="并发数（默认 load_test.concurrency）")
    ap.add_argument("--cache-wait-s", type=int, default=480, help="1h 缓存回读等待秒数（默认 480）")
    ap.add_argument("--cache-deep", action="store_true", help="额外做 58 分钟缓存回读")
    ap.add_argument("--out", default="", help="把完整 JSON 结果写入文件")
    ap.add_argument("--print-plan", action="store_true", help="只打印预计请求数/并发/耗时，不发请求")
    a = ap.parse_args()

    try:
        prof = profile_mod.load(a.profile)
    except (OSError, ValueError) as e:
        sys.exit("error: %s" % e)
    model = a.model or prof.get("id")

    if a.print_plan:
        try:
            profile_mod.guard_base(prof, a.base)  # same refusal path as a real run, before anything is sent
        except ValueError as e:
            sys.exit("error: %s" % e)
        plan(prof, model, a.requests, a.concurrency, a.cache_wait_s, a.cache_deep)
        return

    key = os.environ.get("LLM_API_KEY")
    if not key:
        sys.exit("set env LLM_API_KEY")
    try:
        result = run(prof, a.base, model, key, requests=a.requests, concurrency=a.concurrency,
                     cache_wait_s=a.cache_wait_s, cache_deep=a.cache_deep)
    except SystemExit:
        raise
    except Exception as e:
        sys.exit(common.scrub("error: %s: %s" % (type(e).__name__, e), key))

    text = common.scrub(json.dumps(result, ensure_ascii=False, indent=1), key)
    print(text)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main()
