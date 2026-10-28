"""Mock of the Anthropic Messages protocol (POST /v1/messages) for tests/mock_llm.py (hook file, auto-loaded).

All answering logic is mock_llm.core_decide / core_answer / usage_for, so scenarios (good, inflate, truncate_*, weak_reasoner, ...) behave
exactly as on the chat protocol; this file only translates the Anthropic wire format.
Protocol behaviour mimicked (public docs): x-api-key / Bearer auth, required anthropic-version header, required max_tokens, roles limited to
user|assistant, system as top-level field, content as string or blocks, tool_use / tool_result blocks, SSE with event: lines and no [DONE],
errors as {"type":"error","error":{"type","message"}}, usage = input_tokens/output_tokens (+cache_* fields), no total.
Extra scenarios: msg_thinking (thinking block), msg_cache (90% of the prompt reported as cache_read), msg_bearer_only / msg_xkey_only
(relay that accepts only one auth header), msg_no_usage (usage omitted, stream too).
"""
import json, re

import mock_llm
from mock_llm import (chunk_text, core_decide, check_model, new_id, send_json, send_sse, text_of, usage_for, vendor_headers)

PATHS = ["/v1/messages"]
PROTOCOL = "messages"
KNOBS = {
    "msg_auth": "any",         # any | x-api-key | bearer: which auth header is accepted
    "msg_thinking": 0,         # 1: prepend a thinking block (and a thinking stream block)
    "msg_cache_frac": 0.0,     # share of the prompt tokens reported as cache_read_input_tokens
    "msg_no_usage": 0,         # 1: omit usage entirely
}
SCENARIOS = {
    "msg_thinking": {"msg_thinking": 1},
    "msg_cache": {"msg_cache_frac": 0.9},
    "msg_bearer_only": {"msg_auth": "bearer"},
    "msg_xkey_only": {"msg_auth": "x-api-key"},
    "msg_no_usage": {"msg_no_usage": 1},
}
_ROLES = ("user", "assistant")


def _err(ctx, handler, status, message, etype="invalid_request_error"):
    obj = {"type": "error", "error": {"type": etype, "message": message + ctx.leak_suffix()}, "request_id": new_id("req")}
    send_json(handler, status, mock_llm.error_headers(ctx), obj)


def _block_text(b):
    """Text of one content block (text block, or tool_result whose content is a string / list of blocks)."""
    if isinstance(b, str):
        return b
    if not isinstance(b, dict):
        return ""
    if b.get("type") == "tool_result":
        return text_of(b.get("content"))
    if b.get("type") == "tool_use":
        return json.dumps(b.get("input"), ensure_ascii=False)
    if b.get("type") in (None, "text"):
        return str(b.get("text") or "")
    return ""


def _content_text(content):
    if isinstance(content, list):
        return "".join(_block_text(b) for b in content)
    return text_of(content)


def _is_tool_result(m):
    c = m.get("content")
    return isinstance(c, list) and bool(c) and all(isinstance(b, dict) and b.get("type") == "tool_result" for b in c)


def _tool_choice(tc):
    t = tc.get("type") if isinstance(tc, dict) else tc
    if t == "any":
        return "required"
    if t == "tool":
        return tc.get("name")
    return {"none": "none", "auto": None}.get(t)


def _usage(ctx, prompt, completion):
    frac = ctx.knob("msg_cache_frac", 0.0)
    cached = int(prompt * frac)
    return {"input_tokens": prompt - cached, "cache_creation_input_tokens": 0, "cache_read_input_tokens": cached, "output_tokens": completion}


def handle(ctx, handler, body):
    h = handler.headers
    xkey, auth = (h.get("x-api-key") or "").strip(), (h.get("Authorization") or "").strip()
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    mode = str(ctx.knob("msg_auth", "any"))
    have = {"any": xkey or bearer, "x-api-key": xkey, "bearer": bearer}.get(mode, xkey or bearer)
    if not have:
        return _err(ctx, handler, 401, "invalid x-api-key" if mode != "bearer" else "missing Authorization: Bearer", "authentication_error")
    if ctx.knob("reject", 0):
        return _err(ctx, handler, ctx.knob("reject", 0), "Request rejected by mock (reject=%s)." % ctx.knob("reject", 0), "mock_reject")
    if not (h.get("anthropic-version") or "").strip():
        return _err(ctx, handler, 400, "anthropic-version: header is required")
    msgs = body.get("messages")
    if not isinstance(msgs, list) or not msgs:
        return _err(ctx, handler, 400, "messages: at least one message is required")
    if not all(isinstance(m, dict) for m in msgs):
        return _err(ctx, handler, 400, "messages.0: Input should be a valid dictionary")
    mt = body.get("max_tokens")
    if mt is None:
        return _err(ctx, handler, 400, "max_tokens: Field required")
    if not isinstance(mt, int) or isinstance(mt, bool) or mt < 1:
        return _err(ctx, handler, 400, "max_tokens: Input should be a valid integer, greater than or equal to 1")
    if mt > 128000:
        return _err(ctx, handler, 400, "max_tokens: %d > 128000, which is the maximum allowed number of output tokens for this model" % mt)
    for i, m in enumerate(msgs):
        if m.get("role") not in _ROLES:
            return _err(ctx, handler, 400, "messages.%d.role: Input should be 'user' or 'assistant'" % i)
        if m.get("content") is None:
            return _err(ctx, handler, 400, "messages.%d.content: Input should be a valid string or a list of content blocks" % i)
    temp = body.get("temperature")
    if temp is not None and (not isinstance(temp, (int, float)) or not 0 <= temp <= 1):
        return _err(ctx, handler, 400, "temperature: range: 0..1")
    tools = body.get("tools")
    if tools is not None:
        if not isinstance(tools, list) or not all(isinstance(t, dict) and t.get("name") and isinstance(t.get("input_schema", {"type": "object"}), dict) for t in tools):
            return _err(ctx, handler, 400, "tools.0.custom.name: Field required")
    for k in ("response_format", "reasoning_effort"):  # Anthropic rejects unknown top-level fields
        if k in body:
            return _err(ctx, handler, 400, "%s: Extra inputs are not permitted" % k)
    err = check_model(ctx)
    if err:
        return _err(ctx, handler, 404, err, "not_found_error")
    ctx.sleep()

    sys_field = body.get("system")
    system_text = _content_text(sys_field) if isinstance(sys_field, (str, list)) else ""
    user_text, tool_result = "", None
    for m in msgs:
        if m["role"] == "user" and not _is_tool_result(m):
            user_text = _content_text(m.get("content"))
    last = msgs[-1]
    if last["role"] == "user" and _is_tool_result(last):
        tool_result = "\n".join(_block_text(b) for b in last["content"])
    all_text = "\n".join(([system_text] if system_text else []) + [_content_text(m.get("content")) for m in msgs])
    dec = core_decide(ctx, user_text, [t.get("name") for t in tools or [] if isinstance(t, dict) and t.get("name")], tool_result, system_text, False,
                      _tool_choice(body.get("tool_choice")))

    empty_rx = ctx.knob("empty_on", "")
    empty = bool(empty_rx and dec["kind"] == "text" and re.search(empty_rx, user_text, re.I))
    comp_tokens = getattr(mock_llm, "_completion_tokens", lambda t: max(1, int(round(len(t) * 0.3))) if t else 0)
    text, stop, tool_use = "", "end_turn", None
    if dec["kind"] == "tool_call":
        tool_use = {"type": "tool_use", "id": new_id("toolu"), "name": dec["name"], "input": dec["arguments"]}
        stop, usage = "tool_use", usage_for(ctx, all_text, "", completion_tokens=12)
    else:
        text = dec["text"]
        if comp_tokens(text) > mt:
            text, stop = text[:max(1, int(mt / 0.3))], "max_tokens"
        usage = usage_for(ctx, all_text, "" if empty else text)
    if empty:
        usage["completion_tokens"] = 0
    wire_usage = _usage(ctx, usage["prompt_tokens"], usage["completion_tokens"])
    hide_usage = bool(ctx.knob("msg_no_usage", 0))
    thinking = "Let me think about this step by step." if ctx.knob("msg_thinking", 0) and not empty else ""
    mid, headers, stream = new_id("msg"), vendor_headers(ctx), bool(body.get("stream"))
    blocks = []
    if thinking:
        blocks.append({"type": "thinking", "thinking": thinking, "signature": "mocksig"})
    if tool_use:
        blocks += [{"type": "text", "text": "I'll look that up."}, tool_use]
    elif text and not empty:
        blocks.append({"type": "text", "text": text})
    msg = {"id": mid, "type": "message", "role": "assistant", "model": ctx.model, "content": blocks,
           "stop_reason": stop, "stop_sequence": None}
    if not stream:
        return send_json(handler, 200, headers, msg if hide_usage else dict(msg, usage=wire_usage))

    def events():
        start = dict(msg, content=[], stop_reason=None)
        if not hide_usage:
            start["usage"] = dict(wire_usage, output_tokens=1)
        yield ("message_start", {"type": "message_start", "message": start})
        yield ("ping", {"type": "ping"})
        for i, b in enumerate(blocks):
            if b["type"] == "thinking":
                yield ("content_block_start", {"type": "content_block_start", "index": i, "content_block": {"type": "thinking", "thinking": ""}})
                yield ("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "thinking_delta", "thinking": b["thinking"]}})
            elif b["type"] == "text":
                yield ("content_block_start", {"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}})
                for piece in chunk_text(b["text"]):
                    yield ("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": piece}})
            else:
                yield ("content_block_start", {"type": "content_block_start", "index": i,
                                               "content_block": {"type": "tool_use", "id": b["id"], "name": b["name"], "input": {}}})
                yield ("content_block_delta", {"type": "content_block_delta", "index": i,
                                               "delta": {"type": "input_json_delta", "partial_json": json.dumps(b["input"])}})
            yield ("content_block_stop", {"type": "content_block_stop", "index": i})
        end = {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None}}
        if not hide_usage:
            end["usage"] = {"output_tokens": wire_usage["output_tokens"]}
        yield ("message_delta", end)
        yield ("message_stop", {"type": "message_stop"})

    send_sse(handler, 200, headers, events(), delay=ctx.knob("chunk_ms", 0) / 1000.0)
