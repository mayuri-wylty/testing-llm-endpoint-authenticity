"""OpenAI Responses API adapter (POST /v1/responses). See tests/CONTRACT.md for the adapter contract.

Wire shape (per the public OpenAI docs; validated only against tests/mock_proto_responses.py, not a real endpoint):
  request : {"model", "input": str | [item...], "instructions", "max_output_tokens" (>=16), "stream", "tools": [flat function defs],
             "reasoning": {"effort"}, "text": {"format": {"type": "json_object"}}}
  response: {"id": "resp_..", "object": "response", "status": "completed|incomplete|failed", "model", "output": [item...],
             "usage": {"input_tokens", "output_tokens", "total_tokens", "output_tokens_details": {"reasoning_tokens"}}}
  items   : reasoning | message{content:[output_text]} | function_call{call_id, name, arguments}
  stream  : SSE with `event:` lines: response.created -> response.output_text.delta... -> response.completed (no [DONE]).
"""
import json

from adapters import Adapter, Norm

TERMINAL_EVENTS = ("response.completed", "response.incomplete")  # a stream that ends in one of these terminated normally
MIN_OUTPUT_TOKENS = 16  # OpenAI rejects max_output_tokens < 16


def _text_of(content):
    """Flatten a chat-style content value (str | [{"type":"text","text"}...] | None) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p if isinstance(p, str) else str(p.get("text") or "") for p in content if isinstance(p, (str, dict)))
    return "" if content is None else str(content)


def _content_parts(content, role):
    """Chat-style content -> Responses content. Plain strings are passed through unchanged (accepted for every role)."""
    if not isinstance(content, list):
        return content if isinstance(content, str) else _text_of(content)
    kind = "output_text" if role == "assistant" else "input_text"
    out = []
    for p in content:
        if isinstance(p, str):
            out.append({"type": kind, "text": p})
        elif isinstance(p, dict) and p.get("type") in (None, "text", "input_text", "output_text"):
            out.append({"type": kind, "text": str(p.get("text") or "")})
        elif isinstance(p, dict) and p.get("type") == "image_url":
            iu = p.get("image_url")
            out.append({"type": "input_image", "image_url": iu.get("url") if isinstance(iu, dict) else iu})
        elif isinstance(p, dict):
            out.append(p)
    return out


def _to_input(messages):
    """OpenAI-chat-style messages (plus native Responses items produced by append_tool_result) -> `input` items."""
    items = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        if m.get("type"):  # already a native Responses item (reasoning / function_call / function_call_output / message)
            items.append(m)
            continue
        role = m.get("role")
        if role == "tool":  # chat-style tool result, converted for robustness
            items.append({"type": "function_call_output", "call_id": m.get("tool_call_id"), "output": _text_of(m.get("content"))})
            continue
        if role == "assistant" and m.get("tool_calls"):
            if m.get("content"):
                items.append({"role": "assistant", "content": _content_parts(m["content"], "assistant")})
            for tc in m["tool_calls"]:
                fn = tc.get("function") or {}
                items.append({"type": "function_call", "call_id": tc.get("id"), "name": fn.get("name"), "arguments": fn.get("arguments") or "{}"})
            continue
        items.append({"role": role, "content": _content_parts(m.get("content"), role)})
    return items


def _flat_tool(t):
    """OpenAI chat tool {"type":"function","function":{name,description,parameters}} -> flat Responses tool."""
    if isinstance(t, dict) and isinstance(t.get("function"), dict):
        fn = t["function"]
        out = {"type": "function", "name": fn.get("name"), "description": fn.get("description", ""),
               "parameters": fn.get("parameters") or {"type": "object", "properties": {}}}
        if "strict" in fn:
            out["strict"] = fn["strict"]
        return out
    return t


def _loads(raw):
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None


class OpenAIResponses(Adapter):
    name = "responses"
    default_path = "/responses"
    SUPPORTS = {"json_mode": True, "reasoning_effort": True, "usage_total": True}

    def headers(self, key):
        return {"Authorization": "Bearer " + key}

    def build(self, messages, *, system=None, tools=None, max_tokens=None, stream=False, json_mode=False,
              reasoning_effort=None, extra=None, model=None):
        body = {"model": model or self.model}
        items = _to_input(messages)
        if items:  # no messages -> `input` is omitted entirely (a clearly invalid request: the error-format probe expects a 4xx)
            body["input"] = items
        if system:
            body["instructions"] = system
        if max_tokens is not None:
            body["max_output_tokens"] = max(MIN_OUTPUT_TOKENS, int(max_tokens))
        if stream:
            body["stream"] = True
        if tools:
            body["tools"] = [_flat_tool(t) for t in tools]
        if json_mode:
            body["text"] = {"format": {"type": "json_object"}}
        if reasoning_effort:
            body["reasoning"] = {"effort": reasoning_effort}
        if extra:
            body.update(extra)
        return self.default_path, body

    def parse(self, status, data):
        n = Norm()
        if status != 200 or not isinstance(data, dict):
            return n
        n.ok = True
        n.id, n.model = data.get("id"), data.get("model")
        output = data.get("output")
        output = [it for it in output if isinstance(it, dict)] if isinstance(output, list) else []
        n.has_output = bool(output)
        text, rtext, echo = [], [], []
        for it in output:
            typ = it.get("type")
            if typ == "message":
                content = it.get("content")
                if isinstance(content, str):
                    text.append(content)
                for part in content if isinstance(content, list) else []:
                    if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                        text.append(part["text"])
            elif typ == "reasoning":
                n.has_reasoning = True
                echo.append(it)
                for key in ("summary", "content"):
                    for part in it.get(key) if isinstance(it.get(key), list) else []:
                        if isinstance(part, dict) and isinstance(part.get("text"), str):
                            rtext.append(part["text"])
            elif typ == "function_call":
                raw = it.get("arguments")
                n.tool_calls.append({"id": it.get("call_id") or it.get("id"), "name": it.get("name"), "args": _loads(raw), "args_raw": raw})
                echo.append(it)
        n.text = "".join(text)
        if not n.text and isinstance(data.get("output_text"), str):  # convenience field some gateways add
            n.text = data["output_text"]
        n.reasoning_text = "\n".join(rtext)
        st = data.get("status")
        if n.tool_calls:
            n.finish = "tool_calls"
        elif st == "incomplete":
            reason = (data.get("incomplete_details") or {}).get("reason") if isinstance(data.get("incomplete_details"), dict) else None
            n.finish = "length" if reason in (None, "max_output_tokens") else reason
        elif st == "completed" or (st is None and n.has_output):
            n.finish = "stop"
        else:
            n.finish = st  # failed / cancelled / in_progress: kept raw
        u = data.get("usage")
        if isinstance(u, dict) and u:
            det = u.get("output_tokens_details")
            n.usage = {"prompt": u.get("input_tokens"), "completion": u.get("output_tokens"), "total": u.get("total_tokens"),
                       "reasoning": det.get("reasoning_tokens") if isinstance(det, dict) else None}
        n.echo = echo or None
        return n

    def parse_stream(self, raw_text, headers=None):
        raw = raw_text if isinstance(raw_text, str) else str(raw_text)
        deltas, final, done, usage_seen = [], [], False, False
        event = None
        for line in raw.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
                continue
            if not line.startswith("data:"):
                if not line.strip():
                    event = None
                continue
            p = line[5:].strip()
            if not p or p == "[DONE]":
                continue
            try:
                d = json.loads(p)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            typ = d.get("type") or event
            if typ == "response.output_text.delta" and isinstance(d.get("delta"), str):
                deltas.append(d["delta"])
            elif typ in TERMINAL_EVENTS:
                done = True
                resp = d.get("response") if isinstance(d.get("response"), dict) else {}
                usage_seen = usage_seen or bool(isinstance(resp.get("usage"), dict) and resp["usage"])
                final = [pt["text"] for it in resp.get("output") or [] if isinstance(it, dict) and it.get("type") == "message"
                         for pt in it.get("content") or [] if isinstance(pt, dict) and isinstance(pt.get("text"), str)]
        ct = None if headers is None else "event-stream" in headers.get("content-type", "")
        return {"done": done, "usage_seen": usage_seen, "text": "".join(deltas) if deltas else "".join(final), "content_type_ok": ct}

    def append_tool_result(self, msgs, norm, result_json):
        """msgs + the assistant's reasoning / function_call items echoed verbatim + one function_call_output per call."""
        echo = list(norm.echo) if isinstance(norm.echo, list) and norm.echo else []
        if not any(isinstance(i, dict) and i.get("type") == "function_call" for i in echo):  # fall back to the normalised calls
            echo += [{"type": "function_call", "call_id": tc["id"], "name": tc["name"],
                      "arguments": tc.get("args_raw") if isinstance(tc.get("args_raw"), str) else json.dumps(tc.get("args"))}
                     for tc in norm.tool_calls]
        outs = [{"type": "function_call_output", "call_id": i.get("call_id"), "output": result_json}
                for i in echo if isinstance(i, dict) and i.get("type") == "function_call"]
        return list(msgs) + echo + outs
