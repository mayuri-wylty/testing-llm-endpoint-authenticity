#!/usr/bin/env python3
"""OpenAI-chat mock for spec_audit / spec_checks selftests (stdlib only, localhost only).

Modes
  good  imitates the Kimi K3.1 Pro official behaviour frozen in profiles/kimi-k3.1-pro.json:
        only the five fixed sampling values are accepted (anything else -> 400),
        reasoning_effort low/high/max (and any effort string) -> 200,
        thinking disabled keeps completion_tokens <= 20,
        reasoning_content is a string by default, usage carries the full detail block,
        msh-usage-cache-write-tokens-5m/-1h headers, a prefix cache (an identical prompt
        or a repeated multi-turn prefix hits from the second request), tools/tool_choice
        behave like the official endpoint, Formula endpoints live under /v1 (no /v1 -> 404),
        finish_reason is stop/length.
  bad   a deliberately inauthentic channel used for red/green acceptance: every parameter
        value is accepted, some responses carry an empty finish_reason, usage lacks
        cache_write_tokens/reasoning_tokens, no reasoning_content, Formula endpoints 404,
        cached_tokens stays 0.

CLI:
  python mock_proto_spec.py --mode good --port 0    # prints "READY <base_url>", 0 = random port

Import:
  from mock_proto_spec import start, stop
  server, base_url, thread = start("good")          # base_url = http://127.0.0.1:PORT/v1
  ...                                                   # pass base_url straight to --base
  stop(server)

Only 127.0.0.1 is ever bound. No network access, no real key: requests are accepted without auth.
"""
import argparse
import hashlib
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

NOT_A_HOOK = True  # standalone spec server; mock_llm.load_hooks must not adopt it as a protocol hook

# Official fixed sampling values (profiles/kimi-k3.1-pro.json -> params.fixed).
FIXED = {"temperature": 1.0, "top_p": 0.95, "n": 1, "presence_penalty": 0.0, "frequency_penalty": 0.0}
TOKENS_PER_CHAR = 0.33  # plausible Kimi-family ratio, inside usage_schema.token_ratio_range
REASONING_TEXT = "I need respond with a short answer."
ANSWER_TEXT = "OK."
IDENTITY_TEXT = "我是 Kimi K3.1 Pro，由月之暗面（Moonshot AI）开发。"
IDENTITY_RX = re.compile(r"你是谁|哪家公司|模型名|who are you|which company|what model", re.I)
NAME_RX = re.compile(r"my name|what.{0,12}name|名字|姓名", re.I)
FORMULA_TOOLS = [
    {"type": "function",
     "function": {"name": "get_weather", "description": "Get the current weather for a city.",
                  "parameters": {"type": "object",
                                 "properties": {"city": {"type": "string"}},
                                 "required": ["city"]}}},
    {"type": "function",
     "function": {"name": "get_time", "description": "Return the current time.",
                  "parameters": {"type": "object", "properties": {}}}},
    {"type": "function",
     "function": {"name": "search_web", "description": "Search the web.",
                  "parameters": {"type": "object",
                                 "properties": {"query": {"type": "string"}},
                                 "required": ["query"]}}},
]


# --------------------------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------------------------
def _text_of(content):
    """Flatten str / [{"type":"text","text":..}, ...] / None into plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, str):
                out.append(part)
            elif isinstance(part, dict) and part.get("type") in (None, "text", "input_text", "output_text"):
                out.append(str(part.get("text") or ""))
        return "".join(out)
    return str(content)


def _all_text(messages):
    parts = []
    for msg in messages:
        parts.append(_text_of(msg.get("content")))
        if msg.get("tool_calls"):
            parts.append(json.dumps(msg["tool_calls"], ensure_ascii=False, sort_keys=True))
    return "\n".join(parts)


def _last_user_text(messages):
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return _text_of(msg.get("content"))
    return ""


def _prompt_tokens(text):
    """Deterministic prompt-token estimate (~0.33 tokens/char, always >= 1)."""
    return max(1, int(round(len(text) * TOKENS_PER_CHAR)))


def _pieces(text):
    """Split text into SSE-sized pieces (words + trailing whitespace)."""
    return re.findall(r"\S+\s*|\s+", text) or []


def _fixed_error(body):
    """None when every fixed param present in the body has its official value, else an error dict."""
    for name, expected in FIXED.items():
        if name not in body:
            continue
        value = body[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return {"message": "Invalid value for '%s': expected %s." % (name, expected),
                    "param": name, "code": "invalid_value"}
        if abs(float(value) - float(expected)) > 1e-9:
            return {"message": "Invalid value for '%s': %r is not supported; only %s is accepted."
                               % (name, value, expected),
                    "param": name, "code": "invalid_value"}
    return None


def _thinking_disabled(body):
    """True for the usual 'thinking off' encodings (thinking.type / thinking.enabled / enable_thinking)."""
    thinking = body.get("thinking")
    if isinstance(thinking, dict):
        if str(thinking.get("type", "")).lower() in ("disabled", "off", "none", "false"):
            return True
        if thinking.get("enabled") is False:
            return True
        if thinking.get("budget_tokens") == 0:
            return True
    elif isinstance(thinking, str) and thinking.lower() in ("disabled", "off", "none", "false"):
        return True
    return body.get("enable_thinking") is False


def _tool_names(tools):
    names = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function")
        name = (fn or {}).get("name") if isinstance(fn, dict) else None
        name = name or tool.get("name")
        if name:
            names.append(str(name))
    return names


def _tool_choice(raw):
    """-> (kind, function_name); kind in auto|none|required|specific."""
    if raw is None:
        return "auto", None
    if isinstance(raw, str):
        return (raw if raw in ("auto", "none", "required") else "auto"), None
    if isinstance(raw, dict):
        fn = raw.get("function")
        name = (fn or {}).get("name") if isinstance(fn, dict) else None
        return "specific", name
    return "auto", None


def _wants_tool(text):
    return bool(re.search(r"weather|temperature|天气|气温", text or "", re.I))


def _tool_args(name):
    if "weather" in name.lower():
        return {"city": "Beijing"}
    return {}


def _usage(prompt_tokens, completion_tokens, cached, write, reasoning_tokens, mode):
    usage = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
             "total_tokens": prompt_tokens + completion_tokens}
    if mode == "good":
        usage["prompt_tokens_details"] = {"cached_tokens": cached, "cache_write_tokens": write}
        usage["completion_tokens_details"] = {"reasoning_tokens": reasoning_tokens}
    else:
        # bad mode deliberately drops cache_write_tokens and reasoning_tokens
        usage["prompt_tokens_details"] = {"cached_tokens": 0}
    return usage


def _cache_headers(write):
    return {"msh-usage-cache-write-tokens-5m": str(write),
            "msh-usage-cache-write-tokens-1h": str(write)}


def _bad_empty_finish(digest):
    """Deterministic ~50% subset of prompts gets an empty finish_reason in bad mode."""
    return int(digest[:2], 16) % 2 == 0


class _Cache:
    """Prefix cache: a request hits on the longest message prefix seen before.

    Mirrors real prompt caching (the wire prompt of a multi-turn request starts with the
    previous turn's messages), so identical repeats and growing conversations both hit.
    """

    def __init__(self):
        self._seen = {}  # canonical(prefix messages) -> estimated prompt tokens of that prefix
        self._lock = threading.Lock()

    @staticmethod
    def _key(messages):
        return json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def record(self, messages, prompt_tokens):
        with self._lock:
            hit_tokens = 0
            for size in range(len(messages), 0, -1):
                tokens = self._seen.get(self._key(messages[:size]))
                if tokens is not None:
                    hit_tokens = tokens
                    break
            for size in range(1, len(messages) + 1):
                self._seen.setdefault(self._key(messages[:size]),
                                      _prompt_tokens(_all_text(messages[:size])))
        if not hit_tokens:
            return 0, prompt_tokens
        cached = min(prompt_tokens, hit_tokens)
        if cached >= prompt_tokens:  # identical prompt: the final tokens are new
            cached = prompt_tokens if prompt_tokens < 100 else prompt_tokens - 1
        return cached, prompt_tokens - cached


# --------------------------------------------------------------------------------------------
# HTTP handler
# --------------------------------------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"  # one request per connection; SSE ends at close
    server_version = "MockProtoSpec/1"
    mode = "good"
    cache = None

    def log_message(self, *args, **kwargs):  # silent
        pass

    def do_GET(self):
        self._guard(self._get)

    def do_POST(self):
        self._guard(self._post)

    def _guard(self, fn):
        try:
            fn()
        except (ConnectionError, TimeoutError, BrokenPipeError):
            pass
        except Exception as exc:  # never let a mock bug kill the server silently
            try:
                self._error(500, "mock internal error: %s: %s" % (type(exc).__name__, exc))
            except Exception:
                pass

    # ---------------------------------------------------------------- responses
    def _send_json(self, status, obj, headers=None):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, str(value))
        self.end_headers()
        self.wfile.write(data)
        self.wfile.flush()

    def _send_sse(self, events, headers=None):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        for key, value in (headers or {}).items():
            self.send_header(key, str(value))
        self.end_headers()
        for event in events:
            payload = event if isinstance(event, str) else json.dumps(event, ensure_ascii=False)
            self.wfile.write(("data: %s\n\n" % payload).encode("utf-8"))
            self.wfile.flush()

    def _error(self, status, message, param=None, code=None):
        self._send_json(status, {"error": {"message": message, "type": "invalid_request_error",
                                           "param": param, "code": code}})

    def _not_found(self, path):
        return self._error(404, "Unknown path: %s" % path, None, "not_found")

    # ---------------------------------------------------------------- routing
    @staticmethod
    def _path(raw_path):
        path = unquote(urlparse(raw_path).path)
        if len(path) > 1:
            path = path.rstrip("/")
        return path or "/"

    def _get(self):
        path = self._path(self.path)
        if path == "/health":
            return self._send_json(200, {"status": "ok", "mode": self.mode})
        if path == "/v1/models":
            return self._send_json(200, {"object": "list", "data": [
                {"id": "kimi-k3.1-pro", "object": "model", "created": 1700000000, "owned_by": "moonshot"}]})
        if path == "/v1/formulas/moonshot/date:latest/tools":
            if self.mode == "good":
                return self._send_json(200, {"tools": FORMULA_TOOLS})
            return self._not_found(path)
        return self._not_found(path)

    def _post(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        path = self._path(self.path)
        if path == "/v1/formulas/moonshot/date:latest/fibers":
            if self.mode == "good":
                fiber_id = hashlib.sha256(raw).hexdigest()[:8]
                return self._send_json(200, {"id": "fiber-spec-" + fiber_id, "object": "fiber",
                                             "status": "succeeded", "created": 1700000000,
                                             "result": {"ok": True}})
            return self._not_found(path)
        if path != "/v1/chat/completions":
            return self._not_found(path)
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
        except Exception as exc:
            return self._error(400, "Invalid JSON body: %s" % exc, None, "invalid_json")
        return self._chat(body)

    # ---------------------------------------------------------------- chat
    def _chat(self, body):
        messages = body.get("messages")
        if (not isinstance(messages, list) or not messages
                or not all(isinstance(m, dict) for m in messages)):
            return self._error(400, "Invalid 'messages': expected a non-empty array of message objects.",
                               "messages", "invalid_messages")

        if self.mode == "good":
            bad = _fixed_error(body)
            if bad:
                return self._error(400, bad["message"], bad["param"], bad["code"])

        choice_kind, _choice_name = _tool_choice(body.get("tool_choice"))
        if choice_kind == "specific":
            # official behaviour: naming one function is incompatible with thinking mode
            return self._error(400, "tool_choice naming a function is not compatible with thinking mode.",
                               "tool_choice", "invalid_tool_choice")

        canonical = json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        user_text = _last_user_text(messages)
        messages_text = _all_text(messages)
        disabled = _thinking_disabled(body)

        # good mode answers like a real model would: identity questions and trivial
        # context questions get the expected content instead of the default "OK."
        content = ANSWER_TEXT
        if self.mode == "good":
            if IDENTITY_RX.search(user_text):
                content = IDENTITY_TEXT
            elif NAME_RX.search(user_text) and "Ada" in messages_text:
                content = "Ada"

        tool = None
        names = _tool_names(body.get("tools"))
        if names and choice_kind != "none":
            if choice_kind == "required":
                tool = names[0]
            elif choice_kind == "auto" and _wants_tool(user_text):
                tool = next((n for n in names if "weather" in n.lower()), names[0])

        prompt_tokens = _prompt_tokens(messages_text)
        if self.mode == "good":
            cached, write = self.cache.record(messages, prompt_tokens)
        else:
            cached, write = 0, 0

        if disabled:
            completion_tokens, reasoning_tokens = 4, 0
        else:
            completion_tokens, reasoning_tokens = 12, 8
        reasoning = "" if (disabled or self.mode == "bad") else REASONING_TEXT

        finish = "tool_calls" if tool else "stop"
        limit = body.get("max_completion_tokens", body.get("max_tokens"))
        if (not tool and isinstance(limit, int) and not isinstance(limit, bool)
                and 0 < limit < completion_tokens):
            finish = "length"
            content = ANSWER_TEXT[:max(1, int(limit / TOKENS_PER_CHAR))]
        if self.mode == "bad" and not tool and finish == "stop" and _bad_empty_finish(digest):
            finish = ""

        message = {"role": "assistant", "content": "" if tool else content}
        if reasoning:
            message["reasoning_content"] = reasoning
        if tool:
            message["tool_calls"] = [{
                "id": "%s_0_%s" % (tool, digest[:8]),
                "type": "function",
                "function": {"name": tool,
                             "arguments": json.dumps(_tool_args(tool), ensure_ascii=False)},
            }]

        usage = _usage(prompt_tokens, completion_tokens, cached, write, reasoning_tokens, self.mode)
        base = {"id": "chatcmpl-spec-" + digest[:12], "created": 1700000000,
                "model": body.get("model") or "kimi-k3.1-pro"}
        headers = _cache_headers(write)

        if not body.get("stream"):
            obj = dict(base, object="chat.completion",
                       choices=[{"index": 0, "message": message, "finish_reason": finish}],
                       usage=usage)
            return self._send_json(200, obj, headers)

        chunk_base = dict(base, object="chat.completion.chunk")

        def events():
            yield dict(chunk_base, choices=[{"index": 0, "delta": {"role": "assistant"},
                                             "finish_reason": None}])
            if reasoning:
                for piece in _pieces(reasoning):
                    yield dict(chunk_base, choices=[{"index": 0,
                                                     "delta": {"reasoning_content": piece},
                                                     "finish_reason": None}])
            if tool:
                call = dict(message["tool_calls"][0])
                call["index"] = 0
                yield dict(chunk_base, choices=[{"index": 0, "delta": {"tool_calls": [call]},
                                                 "finish_reason": None}])
            else:
                for piece in _pieces(content):
                    yield dict(chunk_base, choices=[{"index": 0, "delta": {"content": piece},
                                                     "finish_reason": None}])
            yield dict(chunk_base, choices=[{"index": 0, "delta": {}, "finish_reason": finish}])
            # always emit usage so probes see the same accounting in both modes
            yield dict(chunk_base, choices=[], usage=usage)
            yield "[DONE]"

        return self._send_sse(events(), headers)


# --------------------------------------------------------------------------------------------
# server lifecycle
# --------------------------------------------------------------------------------------------
class _Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def _make_handler(mode):
    cache = _Cache() if mode == "good" else None
    return type("SpecMockHandler_" + mode, (_Handler,), {"mode": mode, "cache": cache})


def start(mode="good", host="127.0.0.1", port=0):
    """Start a mock on 127.0.0.1 (random free port when port=0).

    Returns (server, base_url, thread); base_url already includes /v1, e.g.
    'http://127.0.0.1:54321/v1', so it can be passed straight to spec_audit.py --base.
    """
    if mode not in ("good", "bad"):
        raise ValueError("mode must be 'good' or 'bad', got %r" % (mode,))
    server = _Server((host, port), _make_handler(mode))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    server.mode = mode
    server._spec_thread = thread
    server._spec_stopped = False
    server.origin = "http://%s:%d" % (host, server.server_address[1])
    server.base_url = server.origin + "/v1"
    return server, server.base_url, thread


def stop(server):
    """Shut the mock down and join its serving thread."""
    if server is None or getattr(server, "_spec_stopped", False):
        return
    server._spec_stopped = True
    server.shutdown()
    server.server_close()
    thread = getattr(server, "_spec_thread", None)
    if thread is not None:
        thread.join(5)


def main():
    ap = argparse.ArgumentParser(description="OpenAI-chat mock for spec_audit selftests (good/bad modes).")
    ap.add_argument("--mode", choices=("good", "bad"), default="good")
    ap.add_argument("--port", type=int, default=0, help="0 = random free port")
    args = ap.parse_args()
    server, base_url, thread = start(args.mode, port=args.port)
    print("READY %s" % base_url, flush=True)
    try:
        thread.join()
    except KeyboardInterrupt:
        pass
    finally:
        stop(server)


if __name__ == "__main__":
    main()
