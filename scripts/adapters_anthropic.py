"""Anthropic Messages protocol (POST <base>/messages, base normally ends in /v1) -> AnthropicMessages adapter.

Wire facts implemented here (per the public API docs; verified against tests/mock_proto_anthropic.py only):
  request  : x-api-key + anthropic-version (+ Authorization: Bearer, many relays accept only one of them); max_tokens is REQUIRED
             (the contract's max_tokens=None is replaced by DEFAULT_MAX_TOKENS); system is a top-level field; content may be a
             string or a list of blocks; tools are {name, description, input_schema}; a tool call is an assistant `tool_use` block,
             its result a user `tool_result` block carrying tool_use_id.
  response : {id, type:"message", role, model, content:[blocks], stop_reason, usage:{input_tokens, output_tokens,
             [cache_creation_input_tokens, cache_read_input_tokens]}}; no total_tokens; thinking arrives as `thinking` blocks.
  stream   : SSE with `event:` lines: message_start -> content_block_start/delta/stop -> message_delta -> message_stop; no [DONE].
  error    : {"type":"error","error":{"type":"invalid_request_error","message":"..."}}
No JSON mode (json_mode=False) and no reasoning_effort: the audit records those sub-tests as n/a.
"""
import json

from adapters import Adapter, Norm

DEFAULT_MAX_TOKENS = 4000
API_VERSION = "2023-06-01"
_STOP = {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length", "tool_use": "tool_calls"}


def _text(content):
    """str | [blocks] | None -> plain text (text blocks only)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join((b.get("text") or "") if isinstance(b, dict) and b.get("type") in (None, "text") else (b if isinstance(b, str) else "")
                       for b in content)
    return "" if content is None else str(content)


def _loads(raw):
    if isinstance(raw, dict):
        return raw
    try:
        v = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def _args(inp):
    """tool_use.input -> dict, or None when it is not (and cannot be parsed into) a JSON object."""
    if isinstance(inp, dict):
        return inp
    if isinstance(inp, str):
        try:
            v = json.loads(inp)
        except ValueError:
            return None
        return v if isinstance(v, dict) else None
    return None


def _convert_tool(t):
    """OpenAI function tool -> Anthropic tool. Anything that is not a well-formed function tool is passed through untouched
    (so error probes can still send deliberately malformed definitions)."""
    if not isinstance(t, dict):
        return t
    fn = t.get("function")
    if not isinstance(fn, dict):
        return t
    out = {"name": fn.get("name")}
    if fn.get("description") is not None:
        out["description"] = fn["description"]
    out["input_schema"] = fn.get("parameters") if isinstance(fn.get("parameters"), dict) else {"type": "object", "properties": {}}
    return out


class AnthropicMessages(Adapter):
    name = "messages"
    default_path = "/messages"
    SUPPORTS = {"json_mode": False, "reasoning_effort": False, "usage_total": False}

    def headers(self, key):
        return {"x-api-key": key, "anthropic-version": API_VERSION, "Authorization": "Bearer " + key}

    # ------------------------------------------------------------------ request
    def _messages(self, messages):
        """OpenAI-style (and our own native) messages -> (system_text, Anthropic messages). Unknown shapes pass through unchanged.
        Only role 'system' is folded into the system field; other unknown roles (developer, banana, ...) are sent as-is so probes work."""
        sys_parts, out = [], []
        for m in messages or []:
            if not isinstance(m, dict):
                out.append(m)
                continue
            role = m.get("role")
            if role == "system":
                sys_parts.append(_text(m.get("content")))
            elif role == "tool":
                out.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": m.get("tool_call_id"),
                                                         "content": _text(m.get("content"))}]})
            elif role == "assistant" and isinstance(m.get("tool_calls"), list) and m["tool_calls"]:
                blocks = [{"type": "text", "text": _text(m.get("content"))}] if _text(m.get("content")) else []
                for tc in m["tool_calls"]:
                    fn = tc.get("function") if isinstance(tc, dict) and isinstance(tc.get("function"), dict) else {}
                    blocks.append({"type": "tool_use", "id": (tc or {}).get("id"), "name": fn.get("name"), "input": _loads(fn.get("arguments"))})
                out.append({"role": "assistant", "content": blocks})
            else:
                out.append(dict(m))
        return "\n\n".join(p for p in sys_parts if p), out

    def build(self, messages, *, system=None, tools=None, max_tokens=None, stream=False, json_mode=False,
              reasoning_effort=None, extra=None, model=None):
        folded, msgs = self._messages(messages)
        sys_text = "\n\n".join(p for p in (system, folded) if p)
        body = {"model": model or self.model, "max_tokens": DEFAULT_MAX_TOKENS if max_tokens is None else max_tokens, "messages": msgs}
        if sys_text:
            body["system"] = sys_text
        if stream:
            body["stream"] = True
        if tools:
            body["tools"] = [_convert_tool(t) for t in tools]
        # json_mode / reasoning_effort: not part of this protocol (SUPPORTS False); checks do not request them, ignore if they do.
        if extra:
            body.update(extra)
        return self.default_path, body

    # ------------------------------------------------------------------ response
    def parse(self, status, data):
        n = Norm()
        if status != 200 or not isinstance(data, dict):
            return n
        n.ok = True
        n.id, n.model = data.get("id"), data.get("model")
        content = data.get("content")
        if isinstance(content, str):  # some relays flatten content to a plain string
            content = [{"type": "text", "text": content}] if content else []
        blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
        n.has_output = bool(blocks)
        texts, thoughts = [], []
        for b in blocks:
            t = b.get("type")
            if t == "text":
                if isinstance(b.get("text"), str):
                    texts.append(b["text"])
            elif t in ("thinking", "redacted_thinking"):
                n.has_reasoning = True
                if isinstance(b.get("thinking"), str):
                    thoughts.append(b["thinking"])
            elif t == "tool_use":
                inp = b.get("input")
                n.tool_calls.append({"id": b.get("id"), "name": b.get("name"), "args": _args(inp),
                                     "args_raw": json.dumps(inp, ensure_ascii=False) if isinstance(inp, dict) else inp})
        n.text, n.reasoning_text = "".join(texts), "".join(thoughts)
        sr = data.get("stop_reason")
        n.finish = _STOP.get(sr, sr) if isinstance(sr, str) else None
        u = data.get("usage")
        if isinstance(u, dict) and u:
            num = lambda k: u.get(k) if isinstance(u.get(k), int) and not isinstance(u.get(k), bool) else None
            inp = num("input_tokens")
            prompt = None if inp is None else inp + (num("cache_creation_input_tokens") or 0) + (num("cache_read_input_tokens") or 0)
            n.usage = {"prompt": prompt, "completion": num("output_tokens"), "total": None, "reasoning": None}
        n.echo = blocks or None
        return n

    def parse_stream(self, raw_text, headers=None):
        raw = raw_text if isinstance(raw_text, str) else str(raw_text)
        parts, done, usage_seen, event = [], False, False, None
        for line in raw.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
                done = done or event == "message_stop"
                continue
            if not line.startswith("data:"):
                continue
            p = line[5:].strip()
            if not p:
                continue
            try:
                d = json.loads(p)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            typ = d.get("type") or event
            done = done or typ == "message_stop"
            if typ == "content_block_delta" and isinstance(d.get("delta"), dict):
                t = d["delta"].get("text")
                if d["delta"].get("type") in (None, "text_delta") and isinstance(t, str):
                    parts.append(t)
            for u in (d.get("usage"), (d.get("message") or {}).get("usage") if isinstance(d.get("message"), dict) else None):
                if isinstance(u, dict) and any(isinstance(u.get(k), int) for k in ("input_tokens", "output_tokens")):
                    usage_seen = True
        ct = None if headers is None else "event-stream" in headers.get("content-type", "")
        return {"done": done, "usage_seen": usage_seen, "text": "".join(parts), "content_type_ok": ct}

    def append_tool_result(self, msgs, norm, result_json):
        echo = [dict(b) for b in norm.echo] if isinstance(norm.echo, list) and norm.echo else []
        ids = [b.get("id") for b in echo if b.get("type") == "tool_use"]
        if not ids:  # echo missing / had no tool_use block: rebuild from the normalised calls
            for tc in norm.tool_calls:
                echo.append({"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["args"] or {}})
                ids.append(tc["id"])
        results = [{"type": "tool_result", "tool_use_id": i, "content": result_json} for i in ids]
        return list(msgs) + [{"role": "assistant", "content": echo}, {"role": "user", "content": results}]
