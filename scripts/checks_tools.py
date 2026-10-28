"""Tool call + tool-result roundtrip (0.7) and JSON mode (0.3). Unsupported json_mode is n/a: renormalised, not penalised."""
import json

NAME = "tools+json"
TOOLS = [{"type": "function", "function": {"name": "get_weather", "description": "Get weather of a city",
          "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}]


def run(audit):
    ad = audit.adapter
    msgs = [{"role": "user", "content": "What's the weather in Beijing right now? Use the tool."}]
    r = audit.chat(msgs, tools=TOOLS)
    n = r.norm
    score, note = 0.0, "no tool_calls"
    if n.tool_calls and n.tool_calls[0]["args"] is not None:
        score, note = 0.4, "tool call ok"
        r2 = audit.chat(ad.append_tool_result(msgs, n, json.dumps({"temp_c": 23, "sky": "sunny"})), tools=TOOLS)
        if r2.status == 200 and "23" in r2.norm.text:
            score, note = 0.7, "tool roundtrip ok"
        else:
            note = "roundtrip failed: HTTP %s" % r2.status
    if ad.supports("json_mode"):
        _, txt = audit.ask('Return JSON {"cities":["a","b","c"]} with three Chinese cities. JSON only.', json_mode=True)
        try:
            json.loads(txt)
            score += 0.3
            note += "; json_mode ok"
        except Exception:
            note += "; json_mode failed"
    else:
        score = min(1.0, score / 0.7)  # json part is n/a for this protocol: renormalise over the 0.7 that applies
        note += "; json_mode n/a (%s)" % ad.name
    audit.add(NAME, score, note)
