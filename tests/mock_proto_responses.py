"""Mock OpenAI Responses protocol (POST /v1/responses) hook for mock_llm.py. Auto-loaded from tests/.

Reuses the shared brain (core_decide / usage_for / vendor_headers / chat_error / send_json / send_sse); this file only
translates the Responses wire format. Behaviour mirrors the public docs: input str|items, instructions, max_output_tokens >= 16,
flat function tools, function_call / function_call_output pairing, SSE with `event:` lines and NO [DONE].
Extra scenario: `resp_reasoning` (emits a reasoning item before the answer / function_call, reasoning_tokens > 0).
"""
import json, re, time

import mock_llm
from mock_llm import (chat_error, check_model, chunk_text, core_decide, new_id, send_json, send_sse, text_of, usage_for, vendor_headers)

PROTOCOL = "responses"
PATHS = ["/v1/responses"]
SCENARIOS = {"resp_reasoning": {"resp_reasoning": 1}}
KNOBS = {"resp_reasoning": 0}   # 1: add a reasoning item (+ reasoning_tokens) to every answer
REASONING_TOKENS = 7


def _tokens(text):  # same completion-token rule as the chat protocol
    return mock_llm._completion_tokens(text)


def _tool_names(tools):
    out = []
    for t in tools or []:
        if isinstance(t, dict):
            n = t.get("name") or (t.get("function") or {}).get("name")
            if n:
                out.append(n)
    return out


def _normalize(inp, instructions):
    """-> (system_text, user_text, tool_result|None, all_text, error|None). Validates call/output pairing like the real API."""
    if isinstance(inp, str):
        inp = [{"role": "user", "content": inp}]
    sys_parts = [instructions] if isinstance(instructions, str) and instructions else []
    user, all_parts, seen_calls, tool_out = "", list(sys_parts), set(), []
    for it in inp:
        if not isinstance(it, dict):
            return None, None, None, None, "Invalid 'input': each item must be an object."
        typ = it.get("type")
        if typ == "function_call":
            seen_calls.add(it.get("call_id"))
            all_parts.append(json.dumps({"name": it.get("name"), "arguments": it.get("arguments")}))
            tool_out = []
        elif typ == "function_call_output":
            if it.get("call_id") not in seen_calls:
                return None, None, None, None, "No tool call found for function call output with call_id %s." % it.get("call_id")
            out = it.get("output")
            tool_out.append(out if isinstance(out, str) else json.dumps(out))
            all_parts.append(tool_out[-1])
        elif typ == "reasoning":
            continue
        else:  # message item or role/content shorthand
            role, txt = it.get("role"), text_of(it.get("content"))
            all_parts.append(txt)
            tool_out = []
            if role in ("system", "developer"):
                sys_parts.append(txt)
            elif role == "user":
                user = txt
    return "\n".join(sys_parts), user, ("\n".join(tool_out) if tool_out else None), "\n".join(all_parts), None


def _item_message(text):
    return {"id": new_id("msg"), "type": "message", "status": "completed", "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": []}]}


def handle(ctx, handler, body):
    if not ctx.api_key:
        return chat_error(ctx, handler, 401, "You didn't provide an API key.", "authentication_error", "invalid_api_key")
    if ctx.knob("reject", 0):
        return chat_error(ctx, handler, ctx.knob("reject", 0), "Request rejected by mock (reject=%s)." % ctx.knob("reject", 0), "mock_reject", "rejected")
    inp = body.get("input")
    if inp is None:
        return chat_error(ctx, handler, 400, "Missing required parameter: 'input'.", code="missing_required_parameter")
    if isinstance(inp, list) and not inp:
        return chat_error(ctx, handler, 400, "Invalid 'input': empty array. Expected an array with minimum length 1.", code="empty_array")
    if not isinstance(inp, (str, list)):
        return chat_error(ctx, handler, 400, "Invalid type for 'input': expected a string or an array.", code="invalid_type")
    err = check_model(ctx)
    if err:
        return chat_error(ctx, handler, 400, err, code="model_not_found")
    limit = body.get("max_output_tokens")
    if limit is not None and (not isinstance(limit, int) or limit < 16):
        return chat_error(ctx, handler, 400, "Invalid 'max_output_tokens': integer below minimum value. Expected a value >= 16, but got %r instead." % (limit,),
                          code="integer_below_min_value")
    system_text, user_text, tool_result, all_text, perr = _normalize(inp, body.get("instructions"))
    if perr:
        return chat_error(ctx, handler, 400, perr, code="invalid_value")
    ctx.sleep()
    fmt = (body.get("text") or {}).get("format") if isinstance(body.get("text"), dict) else None
    json_mode = isinstance(fmt, dict) and fmt.get("type") in ("json_object", "json_schema")
    tc = body.get("tool_choice")
    if isinstance(tc, dict):
        tc = tc.get("name") or (tc.get("function") or {}).get("name")
    dec = core_decide(ctx, user_text, _tool_names(body.get("tools")), tool_result, system_text, json_mode, tc)
    empty_rx = ctx.knob("empty_on", "")
    empty = bool(empty_rx and dec["kind"] == "text" and re.search(empty_rx, user_text, re.I))
    reasoning = bool(ctx.knob("resp_reasoning", 0))  # only the scenario knob adds reasoning items (a `reasoning` request field is just accepted)

    status, incomplete, text, call = "completed", None, "", None
    if dec["kind"] == "tool_call":
        call = {"id": new_id("fc"), "type": "function_call", "status": "completed", "call_id": new_id("call"),
                "name": dec["name"], "arguments": json.dumps(dec["arguments"])}
        usage = usage_for(ctx, all_text, "", completion_tokens=12)
    else:
        text = dec["text"]
        if isinstance(limit, int) and limit > 0 and _tokens(text) > limit:
            text, status, incomplete = text[:max(1, int(limit / 0.3))], "incomplete", {"reason": "max_output_tokens"}
        usage = usage_for(ctx, all_text, "" if empty else text)
    rtok = REASONING_TOKENS if reasoning and not empty else 0
    prompt, completion = usage["prompt_tokens"], usage["completion_tokens"] + rtok
    if empty:
        completion = 0
    u = {"input_tokens": prompt, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": completion,
         "output_tokens_details": {"reasoning_tokens": rtok}, "total_tokens": prompt + completion}

    output = []
    if not empty:
        if reasoning:
            output.append({"id": new_id("rs"), "type": "reasoning", "summary": []})
        output.append(call if call else _item_message(text))
    resp = {"id": new_id("resp"), "object": "response", "created_at": int(time.time()), "status": status, "error": None,
            "incomplete_details": incomplete, "model": ctx.model, "output": output, "parallel_tool_calls": True,
            "tool_choice": "auto", "usage": u}
    headers = vendor_headers(ctx)
    if not body.get("stream"):
        return send_json(handler, 200, headers, resp)

    seq = [0]

    def ev(name, **kw):
        kw.update(type=name, sequence_number=seq[0])
        seq[0] += 1
        return (name, kw)

    def events():
        shell = dict(resp, status="in_progress", output=[], usage=None, incomplete_details=None)
        yield ev("response.created", response=shell)
        yield ev("response.in_progress", response=shell)
        for i, it in enumerate(output):
            if it["type"] == "message":
                head = dict(it, status="in_progress", content=[])
                yield ev("response.output_item.added", output_index=i, item=head)
                part = {"type": "output_text", "text": "", "annotations": []}
                yield ev("response.content_part.added", output_index=i, item_id=it["id"], content_index=0, part=part)
                for piece in chunk_text(text):
                    yield ev("response.output_text.delta", output_index=i, item_id=it["id"], content_index=0, delta=piece)
                yield ev("response.output_text.done", output_index=i, item_id=it["id"], content_index=0, text=text)
                yield ev("response.content_part.done", output_index=i, item_id=it["id"], content_index=0, part=dict(part, text=text))
            elif it["type"] == "function_call":
                yield ev("response.output_item.added", output_index=i, item=dict(it, status="in_progress", arguments=""))
                yield ev("response.function_call_arguments.delta", output_index=i, item_id=it["id"], delta=it["arguments"])
                yield ev("response.function_call_arguments.done", output_index=i, item_id=it["id"], arguments=it["arguments"])
            else:
                yield ev("response.output_item.added", output_index=i, item=it)
            yield ev("response.output_item.done", output_index=i, item=it)
        yield ev("response.incomplete" if status == "incomplete" else "response.completed", response=resp)  # no [DONE] in this protocol

    send_sse(handler, 200, headers, events(), delay=ctx.knob("chunk_ms", 0) / 1000.0)
