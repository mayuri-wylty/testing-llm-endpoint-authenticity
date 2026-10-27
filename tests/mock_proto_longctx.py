"""Mock chat-protocol interceptor for the long-context failure paths (checks_longctx.py).
Scenario knob lc_http (e.g. `good:lc_http=504`) makes only LONG prompts (> 5000 chars, i.e. the haystack) fail; every other
request (ping, short probes, error probes) is delegated to the stock chat handler unchanged.
  400ctx | 400other | 413 | 504 | 524 | 408 | 500 | 401 | drop (connection closed without a response -> client status -1)"""
import socket

import mock_llm

PROTOCOL = "chat"
PATHS = ["/chat/completions"]
KNOBS = {"lc_http": ""}

_ERR = {
    "400ctx": (400, "This model's maximum context length is 8192 tokens, however you requested 99999 tokens. Please reduce the length of the messages."),
    "400other": (400, "Invalid value for 'temperature': expected a number."),
    "413": (413, "Request Entity Too Large"),
    "504": (504, "Gateway Timeout"),
    "524": (524, "A timeout occurred"),
    "408": (408, "Request Timeout"),
    "500": (500, "Internal server error"),
    "401": (401, "Invalid token"),
}


def handle(ctx, handler, body):
    mode = str(ctx.knob("lc_http", "") or "")
    msgs = body.get("messages") if isinstance(body, dict) else None
    last = next((m for m in reversed(msgs or []) if isinstance(m, dict) and m.get("role") == "user"), None)
    big = bool(mode) and last is not None and len(mock_llm.text_of(last.get("content"))) > 5000
    if big and mode == "drop":
        handler.close_connection = True
        handler.connection.shutdown(socket.SHUT_RDWR)
        return
    if big and mode in _ERR:
        status, msg = _ERR[mode]
        return mock_llm.chat_error(ctx, handler, status, msg, "mock_lc_error", "lc_" + mode)
    return mock_llm.handle_chat(ctx, handler, body)
