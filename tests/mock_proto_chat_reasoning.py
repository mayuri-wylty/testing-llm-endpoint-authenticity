"""Mock hook: failure modes for the reasoning check, selected with the `rzn` knob (e.g. /s/good:rzn=hard_429/v1/...).
It takes over POST /chat/completions and delegates (to the next chat interceptor in file-name order, e.g. mock_proto_longctx.py, else
mock_llm.handle_chat) unless the request is one of the 7 reasoning questions
AND a mode applies. Modes (all other requests, and all other scenarios, behave exactly like the built-in chat handler):
  rl_once   every reasoning question gets HTTP 429 on its 1st request, then works (the check's single retry must absorb it)
  all_429   every reasoning question always gets 429            -> reasoning is n/a (None)
  hard_429  hard questions always get 429                       -> hard tier not counted, score = easy tier only
  one_503   hard-mod-sequence always gets 503                   -> that one question not counted
  len_empty hard-binary-runs answers 200 with empty content and finish_reason=length -> not counted, 'raise --max-tokens'
(The file name sorts BEFORE mock_proto_longctx.py on purpose: hooks are tried in name order and the first match wins.)
  paren     (answerers_reasoning.py) hard answers carry a trailing "(...)" remark
"""
import re
import sys
import threading

import mock_llm

PROTOCOL = "chat"
PATHS = ["/v1/chat/completions"]
_HARD = [
    (r"binary strings of length 18", "hard-binary-runs"),
    (r"a_1 = 5 and, for n >= 2", "hard-mod-sequence"),
    (r"58 people numbered 1 to 58", "hard-circle-elimination"),
]
_EASY = [r"inlet\s+A", r"strawberry", r"bat and a ball", r"what is 17 \* 23"]
_seen, _lock = {}, threading.Lock()


def _qid(text):
    for rx, qid in _HARD:
        if re.search(rx, text):
            return qid
    for rx in _EASY:
        if re.search(rx, text, re.I):
            return "easy"
    return None


def handle(ctx, handler, body):
    mode = ctx.knob("rzn", "")
    msgs = body.get("messages")
    qid = None
    if mode and isinstance(msgs, list) and msgs and all(isinstance(m, dict) for m in msgs) and ctx.api_key:
        qid = _qid(mock_llm.normalize_chat(msgs)[1])
    if qid:
        hard = qid.startswith("hard")
        if mode == "rl_once":
            with _lock:
                key = (handler.path, qid)
                _seen[key] = _seen.get(key, 0) + 1
                first = _seen[key] == 1
            if first:
                return mock_llm.chat_error(ctx, handler, 429, "Rate limit reached (mock).", "rate_limit_error", "rate_limited")
        elif mode == "all_429" or (mode == "hard_429" and hard):
            return mock_llm.chat_error(ctx, handler, 429, "Rate limit reached (mock).", "rate_limit_error", "rate_limited")
        elif mode == "one_503" and qid == "hard-mod-sequence":
            return mock_llm.chat_error(ctx, handler, 503, "Service unavailable (mock).", "server_error", "unavailable")
        elif mode == "len_empty" and qid == "hard-binary-runs":
            usage = {"prompt_tokens": 60, "completion_tokens": 4000, "total_tokens": 4060}
            return mock_llm.send_json(handler, 200, mock_llm.vendor_headers(ctx), {
                "id": mock_llm.new_id("chatcmpl", "-"), "object": "chat.completion", "model": ctx.model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": ""}, "finish_reason": "length"}], "usage": usage})
    me = sys.modules.get(__name__)
    protos = list(mock_llm.HOOKS.protos)
    later = protos[protos.index(me) + 1:] if me in protos else []
    for mod in later:  # chain to the next interceptor of the chat path
        if "/chat/completions" in [mock_llm._norm_path(p) for p in getattr(mod, "PATHS", [])]:
            return mod.handle(ctx, handler, body)
    return mock_llm.handle_chat(ctx, handler, body)
