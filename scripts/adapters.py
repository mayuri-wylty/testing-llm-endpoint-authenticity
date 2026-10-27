"""Protocol adapters: turn the audit's internal request/response shapes into one wire protocol and back.

Checks never touch raw wire JSON; they call Audit.chat() and read the normalised Norm. See tests/CONTRACT.md.
Internal message format = OpenAI-style role/content lists ({"role": "user"|"assistant", "content": str}); the system prompt is
passed separately (system=...); tools are given in OpenAI function format and converted by the adapter.
"""
import importlib, json, os, sys
from dataclasses import dataclass, field
from typing import Any, Optional

FEATURES = ("json_mode", "reasoning_effort", "usage_total")


class ProtocolUnavailable(Exception):
    pass


@dataclass
class Norm:
    """Normalised, protocol-independent view of one (non-stream) response. Missing fields are None / empty; never raises."""
    ok: bool = False                 # HTTP 200 and the body is a JSON object
    text: str = ""                   # assistant text ("" when absent)
    tool_calls: list = field(default_factory=list)   # [{"id", "name", "args" (parsed; None if unparseable), "args_raw"}]
    usage: Optional[dict] = None     # {"prompt", "completion", "total", "reasoning"} values as sent (None per missing field) | None
    finish: Optional[str] = None     # OpenAI vocabulary: "stop" | "length" | "tool_calls" | other raw value
    id: Optional[str] = None
    model: Optional[str] = None
    has_reasoning: bool = False      # reasoning channel present in the response
    reasoning_text: str = ""
    has_output: bool = False         # at least one choice / content block / output item (False = empty-choices relay short-circuit)
    echo: Any = None                 # adapter-private assistant turn used by append_tool_result(); opaque to checks


class Adapter:
    name = "base"
    default_path = ""
    SUPPORTS = {}  # feature -> bool; see FEATURES

    def __init__(self, model=""):
        self.model = model

    def headers(self, key):
        """Auth (and protocol-required) headers; Content-Type / User-Agent / Accept are added by common.http()."""
        raise NotImplementedError

    def build(self, messages, *, system=None, tools=None, max_tokens=None, stream=False, json_mode=False,
              reasoning_effort=None, extra=None, model=None):
        """-> (path, body). max_tokens=None means "omit" (adapters that require it supply their own default). model overrides self.model."""
        raise NotImplementedError

    def parse(self, status, data):
        """-> Norm. data = parsed JSON (200) or error text. Must not raise on empty choices / missing fields."""
        raise NotImplementedError

    def parse_stream(self, raw_text, headers=None):
        """-> {"done", "usage_seen", "text", "content_type_ok"} (content_type_ok None when headers not given)."""
        raise NotImplementedError

    def append_tool_result(self, msgs, norm, result_json):
        """Messages for the follow-up request after a tool call: msgs + assistant tool-call echo + tool result. Opaque to checks."""
        raise NotImplementedError

    def supports(self, feature):
        return bool(self.SUPPORTS.get(feature, False))


def _first(d, key):
    """d[key][0] if it is a non-empty list whose first element is a dict, else {}."""
    v = d.get(key)
    return v[0] if isinstance(v, list) and v and isinstance(v[0], dict) else {}


class OpenAIChat(Adapter):
    name = "chat"
    default_path = "/chat/completions"
    SUPPORTS = {"json_mode": True, "reasoning_effort": True, "usage_total": True}

    def headers(self, key):
        return {"Authorization": "Bearer " + key}

    def build(self, messages, *, system=None, tools=None, max_tokens=None, stream=False, json_mode=False,
              reasoning_effort=None, extra=None, model=None):
        msgs = ([{"role": "system", "content": system}] if system else []) + list(messages)
        body = {"model": model or self.model, "messages": msgs}
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if stream:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}
        if tools:
            body["tools"] = tools
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort
        if extra:
            body.update(extra)
        return self.default_path, body

    def parse(self, status, data):
        n = Norm()
        if status != 200 or not isinstance(data, dict):
            return n
        n.ok = True
        n.id, n.model = data.get("id"), data.get("model")
        ch0 = _first(data, "choices")
        n.has_output = bool(ch0)
        msg = ch0.get("message") if isinstance(ch0.get("message"), dict) else {}
        n.finish = ch0.get("finish_reason")
        content = msg.get("content")
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        n.text = content if isinstance(content, str) else ""
        n.has_reasoning = "reasoning_content" in msg
        rt = msg.get("reasoning_content") or msg.get("reasoning")
        n.reasoning_text = rt if isinstance(rt, str) else ""
        tcs = msg.get("tool_calls")
        for tc in tcs if isinstance(tcs, list) else []:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            raw = fn.get("arguments")
            try:
                args = json.loads(raw) if isinstance(raw, str) else raw
            except ValueError:
                args = None
            n.tool_calls.append({"id": tc.get("id"), "name": fn.get("name"), "args": args, "args_raw": raw})
        u = data.get("usage")
        if isinstance(u, dict) and u:
            det = u.get("completion_tokens_details")
            n.usage = {"prompt": u.get("prompt_tokens"), "completion": u.get("completion_tokens"), "total": u.get("total_tokens"),
                       "reasoning": det.get("reasoning_tokens") if isinstance(det, dict) else None}
        n.echo = msg or None
        return n

    def parse_stream(self, raw_text, headers=None):
        raw = raw_text if isinstance(raw_text, str) else str(raw_text)
        parts = []
        for line in raw.splitlines():
            if not line.startswith("data:"):
                continue
            p = line[5:].strip()
            if not p or p == "[DONE]":
                continue
            try:
                for c in json.loads(p).get("choices") or []:
                    t = (c.get("delta") or {}).get("content")
                    if isinstance(t, str):
                        parts.append(t)
            except Exception:
                pass
        ct = None if headers is None else "event-stream" in headers.get("content-type", "")
        return {"done": "[DONE]" in raw, "usage_seen": '"usage"' in raw and '"total_tokens"' in raw,
                "text": "".join(parts), "content_type_ok": ct}

    def append_tool_result(self, msgs, norm, result_json):
        tc = norm.tool_calls[0]
        echo = norm.echo if isinstance(norm.echo, dict) and norm.echo else {
            "role": "assistant", "content": norm.text or None,
            "tool_calls": [{"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": json.dumps(tc["args"])}}]}
        return list(msgs) + [echo, {"role": "tool", "tool_call_id": tc["id"], "content": result_json}]


# protocol name -> (module, class); modules are imported lazily so a missing one is a clean error, not a crash
_LAZY = {"messages": ("adapters_anthropic", "AnthropicMessages"), "responses": ("adapters_responses", "OpenAIResponses")}
PROTOCOLS = ("chat", "messages", "responses")


def get_adapter(protocol, model=""):
    if protocol == "chat":
        return OpenAIChat(model)
    if protocol not in _LAZY:
        raise ProtocolUnavailable("unknown protocol %r (expected one of %s)" % (protocol, ", ".join(PROTOCOLS)))
    modname, clsname = _LAZY[protocol]
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        mod = importlib.import_module(modname)
    except ModuleNotFoundError as e:
        if e.name == modname:
            raise ProtocolUnavailable("protocol %r not available yet: scripts/%s.py is missing" % (protocol, modname)) from None
        raise ProtocolUnavailable("protocol %r unavailable: %s failed to import (%s)" % (protocol, modname, e)) from None
    except Exception as e:
        raise ProtocolUnavailable("protocol %r unavailable: %s failed to import (%s: %s)" % (protocol, modname, type(e).__name__, e)) from None
    cls = getattr(mod, clsname, None)
    if cls is None:
        raise ProtocolUnavailable("protocol %r unavailable: %s.py does not define class %s" % (protocol, modname, clsname))
    return cls(model)
