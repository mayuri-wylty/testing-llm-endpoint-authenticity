#!/usr/bin/env python3
"""Mock LLM endpoint (stdlib only) for regression-testing model_audit.py / error_probe.py.

Run standalone:   python mock_llm.py --port 8765
Import:           import mock_llm; srv = mock_llm.MockServer().start(); srv.base("good") -> http://127.0.0.1:P/s/good/v1

Scenario is chosen by URL prefix:  /s/<scenario>[:k=v,k=v]/v1/chat/completions   (and /v1/models)
  e.g.  /s/good/v1   /s/inflate/v1   /s/tokprofile:family=qwen/v1   /s/good:persona=gpt,facts=1/v1
Any scenario knob can be overridden with k=v (comma separated; values may be %-encoded).

Built-in scenarios: good emptychoices inflate keyecho truncate_head truncate_tail no_headers weak_reasoner
                    tokprofile wrongvendor        (see SCENARIOS / DEFAULT_KNOBS below)

EXTENSION HOOKS (auto-discovered in this directory on first server start, sorted by file name):
  answerers_*.py   answer(ctx, user_text) -> str | None        non-None overrides the default answer
  usage_*.py       estimate(ctx, text) -> int | None           non-None overrides prompt_tokens estimate
  mock_proto_<name>.py   PATHS = ["/v1/messages"]; handle(ctx, handler, body: dict) -> None
                         (optional) PROTOCOL = "name"; GET_PATHS = [...]; handle_get(ctx, handler) -> bool
  any hook module may also define SCENARIOS = {"name": {knob: value}} and KNOBS = {knob: default}.
  Hooks may `import mock_llm` and reuse: core_answer, core_decide, usage_for, send_json, send_sse,
  vendor_headers, text_of, error_headers, Ctx (see "PUBLIC HELPERS").
A hook that raises is reported once on stderr and treated as "no opinion" (never crashes the server).
"""
import argparse, glob, importlib.util, json, math, os, re, sys, threading, time, traceback, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

if __name__ == "__main__":  # hooks doing `import mock_llm` must get THIS module, not a second copy
    sys.modules.setdefault("mock_llm", sys.modules["__main__"])

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODELS = ["kimi-k3", "kimi-k2", "kimi-k2-thinking"]
CJK_RE = re.compile("[\u3000-\u9fff\uac00-\ud7af\uff00-\uffef]")
# questions matching this are "hard" for the weak_reasoner scenario (override per request with hard=<regex>)
HARD_RE = re.compile(r"\[HARD\]|inlet\s+A|strawberry|bat and a ball|bat and ball|multi-?step", re.I)

# --------------------------------------------------------------------------------------------
# knobs / scenarios
# --------------------------------------------------------------------------------------------
DEFAULT_KNOBS = {
    "persona": "kimi",       # key of PERSONAS: what the model says about itself
    "facts": "auto",         # auto: family facts answered only when persona==kimi | 1 always | 0 never
    "vendor": "moonshot",    # key of VENDOR_HEADER_PROFILES ("none" = no vendor headers)
    "relay": "",             # "newapi" adds x-oneapi-request-id relay marker header
    "family": "kimi",        # key of FAMILIES: tokenizer profile for prompt_tokens
    "ratio": "",             # >0: force tokens-per-char (ascii and cjk) instead of the family profile
    "overhead": 0,           # fixed extra prompt tokens (chat-template overhead)
    "usage_mult": 1.0,       # multiply prompt_tokens (inflate)
    "usage_report": "kept",  # truncate_*: "kept" = usage of the truncated prompt, "full" = usage of the full prompt
    "empty_on": "",          # regex on user text: reply choices=[] (emptychoices)
    "truncate": "",          # "head" drops the first `drop` fraction of a long prompt, "tail" drops the last
    "drop": 0.5,
    "trunc_min": 2000,       # prompts shorter than this (chars) are never truncated
    "weak": 0,               # weak_reasoner: wrong answers for "hard" questions
    "hard": "",              # regex overriding HARD_RE
    "keyecho": 0,            # error bodies echo the full key and a masked key
    "reject": 0,             # HTTP status (e.g. 401/429/503) returned for EVERY chat/models request ("endpoint unreachable")
    "group": "",             # unknown-model error mentions "under group <group>" (relay channel leak)
    "models": ",".join(DEFAULT_MODELS),
    "strict_models": 0,      # 1: only models in `models` are accepted
    "latency_ms": 0,         # sleep before answering
    "chunk_ms": 0,           # sleep between SSE events
}
SCENARIOS = {
    "good": {},
    "emptychoices": {"empty_on": r"^\s*say hi\s*$"},
    "inflate": {"usage_mult": 5.0},
    "keyecho": {"keyecho": 1},
    "truncate_head": {"truncate": "head"},
    "truncate_tail": {"truncate": "tail"},
    "no_headers": {"vendor": "none"},
    "weak_reasoner": {"weak": 1},
    "tokprofile": {"family": "qwen"},
    "wrongvendor": {"persona": "gpt"},
}
# (ascii tokens/char, cjk tokens/char); synthetic but plausible, every ascii value inside v1's 0.15-0.6 window
FAMILIES = {
    "kimi": (0.33, 0.33), "qwen": (0.30, 0.60), "llama": (0.27, 0.90), "gpt": (0.27, 0.80),
    "claude": (0.30, 0.90), "deepseek": (0.28, 0.60), "glm": (0.31, 0.65), "gemini": (0.26, 0.50),
}
PERSONAS = {
    "kimi": ("I am Kimi, an AI assistant created by Moonshot AI.", "我是 Kimi，由月之暗面（Moonshot AI）开发的人工智能助手。"),
    "claude": ("I am Claude, an AI assistant made by Anthropic.", "我是 Claude，由 Anthropic 开发的人工智能助手。"),
    "gpt": ("I am ChatGPT, a large language model trained by OpenAI.", "我是 ChatGPT，由 OpenAI 训练的大型语言模型。"),
    "gemini": ("I am Gemini, a large language model built by Google.", "我是 Gemini，由 Google 打造的大型语言模型。"),
    "deepseek": ("I am DeepSeek, an AI assistant created by DeepSeek.", "我是 DeepSeek，由深度求索开发的人工智能助手。"),
    "qwen": ("I am Qwen, a large language model created by Alibaba Cloud.", "我是 Qwen，由阿里云开发的通义千问大模型。"),
    "glm": ("I am GLM, a large language model trained by Zhipu AI.", "我是 GLM，由智谱 AI 训练的大语言模型。"),
}
# (question regex, answer, family-key or None); a family-key fact is only answered for that persona unless facts=1
FACTS = [
    (r"kimi\s*k2.*(参数|parameter)", "Kimi K2 是 MoE 模型：总参数约 1T（1万亿），每次激活参数 32B。", "kimi"),
    (r"capital of france|法国.*首都", "Paris", None),
]
VENDOR_HEADER_PROFILES = {  # callables so each response gets fresh ids
    "moonshot": lambda: {"msh-request-id": uuid.uuid4().hex, "x-msh-trace-id": uuid.uuid4().hex[:16]},
    "openai": lambda: {"openai-organization": "org-mock", "openai-processing-ms": "123", "x-request-id": "req_" + uuid.uuid4().hex},
    "anthropic": lambda: {"anthropic-ratelimit-requests-limit": "1000", "anthropic-ratelimit-requests-remaining": "999",
                          "anthropic-organization-id": str(uuid.uuid4()), "request-id": "req_" + uuid.uuid4().hex[:20]},
    "google": lambda: {"x-goog-request-id": uuid.uuid4().hex, "x-goog-api-client": "mock"},
    "deepseek": lambda: {"x-ds-trace-id": uuid.uuid4().hex},
    "none": lambda: {},
}

# --------------------------------------------------------------------------------------------
# hook loading
# --------------------------------------------------------------------------------------------
class _Hooks:
    def __init__(self):
        self.answerers, self.usages, self.protos, self.errors, self.loaded = [], [], [], [], []
        self.ready = False
        self._reported = set()


HOOKS = _Hooks()
_hook_lock = threading.Lock()


def _hook_error(mod, what, exc):
    key = (getattr(mod, "__name__", "?"), what, repr(exc))
    if key not in HOOKS._reported:
        HOOKS._reported.add(key)
        msg = "[mock_llm] hook %s %s failed: %s: %s" % (key[0], what, type(exc).__name__, exc)
        HOOKS.errors.append(msg)
        print(msg, file=sys.stderr)


def _import_hook(path):
    name = "mockhook_" + os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_hooks(directory=HERE, force=False):
    """Import answerers_*.py / usage_*.py / mock_proto_*.py from `directory` (idempotent unless force)."""
    with _hook_lock:
        if HOOKS.ready and not force:
            return HOOKS
        HOOKS.__init__()
        if directory not in sys.path:
            sys.path.insert(0, directory)
        for pattern, bucket, attr in (("answerers_*.py", HOOKS.answerers, "answer"), ("usage_*.py", HOOKS.usages, "estimate"),
                                      ("mock_proto_*.py", HOOKS.protos, "handle")):
            for path in sorted(glob.glob(os.path.join(directory, pattern))):
                try:
                    mod = _import_hook(path)
                    if not hasattr(mod, attr) and not (attr == "handle" and hasattr(mod, "handle_get")):
                        raise AttributeError("module has no %s()" % attr)
                except Exception as e:  # a broken teammate hook must not take the mock down
                    _hook_error(type(sys)(os.path.basename(path)), "import", e)
                    continue
                bucket.append(mod)
                HOOKS.loaded.append(os.path.basename(path))
                SCENARIOS.update(getattr(mod, "SCENARIOS", {}) or {})
                DEFAULT_KNOBS.update(getattr(mod, "KNOBS", {}) or {})
        HOOKS.ready = True
        return HOOKS


def _call_hook(mod, fn, *args):
    try:
        return getattr(mod, fn)(*args)
    except Exception as e:
        _hook_error(mod, fn, e)
        return None


# --------------------------------------------------------------------------------------------
# request context
# --------------------------------------------------------------------------------------------
def _coerce(v, like):
    if like is None or not isinstance(v, str):
        return v if like is None or isinstance(like, str) or not isinstance(v, (int, float, bool)) else type(like)(v)
    try:
        if isinstance(like, bool):
            return v.strip().lower() in ("1", "true", "yes", "on")
        if isinstance(like, int):
            return int(float(v))
        if isinstance(like, float):
            return float(v)
    except ValueError:
        return like
    return v


class Ctx:
    """Per-request context. Attributes: scenario, params(dict[str,str]), protocol, model, api_key, body, path,
    handler, knobs; filled by core_answer: system_text, user_text_raw, user_text (after truncation), json_mode."""

    def __init__(self, scenario, params, protocol="chat", model=None, api_key="", body=None, path="", handler=None):
        self.scenario, self.params, self.protocol = scenario, dict(params), protocol
        self.model, self.api_key, self.body, self.path, self.handler = model, api_key, body, path, handler
        self.knobs = dict(DEFAULT_KNOBS)
        self.knobs.update(SCENARIOS.get(scenario, {}))
        self.system_text = self.user_text_raw = self.user_text = ""
        self.json_mode = False

    def knob(self, name, default=None):
        """Request param (name:k=v) > scenario default > DEFAULT_KNOBS; coerced to the type of `default`/DEFAULT_KNOBS."""
        raw = self.params[name] if name in self.params else self.knobs.get(name, default)
        like = default if default is not None else DEFAULT_KNOBS.get(name)
        return _coerce(raw, like) if raw is not None else default

    def param(self, name, default=None):
        return self.params.get(name, default)

    def sleep(self, knob="latency_ms"):
        ms = self.knob(knob, 0)
        if ms and ms > 0:
            time.sleep(ms / 1000.0)

    def mask_key(self):
        k = self.api_key
        return (k[:6] + "***" + k[-4:]) if len(k) >= 12 else ("***" if k else "")

    def leak_suffix(self):
        """'' normally; under keyecho appends the full key AND its masked form (to be put into error messages)."""
        if self.knob("keyecho", 0) and self.api_key:
            return " Incorrect API key provided: %s (masked: %s)." % (self.api_key, self.mask_key())
        return ""

    def is_hard(self, text):
        rx = self.knob("hard", "")
        return bool(re.search(rx, text, re.I) if rx else HARD_RE.search(text))

    def truncate(self, text):
        """Apply the scenario's truncation (truncate=head|tail) to a long prompt; a short trailing question paragraph is kept."""
        mode = self.knob("truncate", "")
        if not mode or len(text) <= self.knob("trunc_min", 2000):
            return text
        body, sep, last = text.rpartition("\n\n")
        if not sep or len(last) > 500:
            body, sep, last = text, "", ""
        drop = min(max(self.knob("drop", 0.5), 0.0), 1.0)
        if mode == "head":
            cut = int(len(body) * drop)
            nl = body.find("\n", cut)
            body = body[nl + 1:] if nl >= 0 else ""
        else:
            cut = int(len(body) * (1 - drop))
            nl = body.rfind("\n", 0, cut)
            body = body[:nl] if nl >= 0 else ""
        return body + sep + last


# --------------------------------------------------------------------------------------------
# PUBLIC HELPERS (reuse these in protocol hooks)
# --------------------------------------------------------------------------------------------
def text_of(content):
    """Flatten str / [{"type":"text","text":..}, ...] / None into plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for p in content:
            if isinstance(p, str):
                out.append(p)
            elif isinstance(p, dict):
                out.append(str(p.get("text") or p.get("content") or "") if p.get("type") in (None, "text", "input_text", "output_text", "tool_result") else "")
        return "".join(out)
    return str(content)


def new_id(prefix, sep="_"):
    return "%s%s%s" % (prefix, sep, uuid.uuid4().hex[:24])


def vendor_headers(ctx):
    h = VENDOR_HEADER_PROFILES.get(str(ctx.knob("vendor", "moonshot")), VENDOR_HEADER_PROFILES["none"])()
    if ctx.knob("relay", ""):
        h["x-oneapi-request-id"] = uuid.uuid4().hex
    return h


error_headers = vendor_headers  # alias: errors carry the same provenance headers


def send_json(handler, status, headers, obj):
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    for k, v in (headers or {}).items():
        handler.send_header(k, str(v))
    handler.end_headers()
    handler.wfile.write(data)
    handler.wfile.flush()


def sse_encode(ev):
    """str -> 'data: str'; dict/list -> 'data: json'; (event_name, obj_or_str) -> 'event: n' + 'data: ...'."""
    event = None
    if isinstance(ev, tuple):
        event, ev = ev
    payload = ev if isinstance(ev, str) else json.dumps(ev, ensure_ascii=False)
    return (("event: %s\n" % event) if event else "") + "data: %s\n\n" % payload


def send_sse(handler, status, headers, events, delay=0.0):
    """Write a Server-Sent-Events response. `events` is any iterable (generator ok) of items accepted by sse_encode."""
    handler.send_response(status)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    for k, v in (headers or {}).items():
        handler.send_header(k, str(v))
    handler.end_headers()
    for i, ev in enumerate(events):
        if delay and i:
            time.sleep(delay)
        handler.wfile.write(sse_encode(ev).encode("utf-8"))
        handler.wfile.flush()


def chunk_text(text):
    """Split an answer into stream pieces (words + trailing whitespace); empty text -> []."""
    return re.findall(r"\S+\s*|\s+", text) or []


def check_model(ctx):
    """None if the model is acceptable, else an error message (unknown / nonexistent model)."""
    m = ctx.model
    if not m or not isinstance(m, str):
        return "'model' is required"
    bad = re.search(r"nonexistent|not-exist|not_exist|unknown", m, re.I)
    if ctx.knob("strict_models", 0) and m not in [x.strip() for x in str(ctx.knob("models")).split(",")]:
        bad = True
    if bad:
        msg = "The model `%s` does not exist or you do not have access to it." % m
        grp = ctx.knob("group", "")
        if grp:
            msg += " (no available channel for model %s under group %s)" % (m, grp)
        return msg
    return None


def _completion_tokens(text):
    return max(1, int(round(len(text) * 0.3))) if text else 0


def default_estimate(ctx, text):
    """Built-in prompt-token estimate: per-family (ascii, cjk) tokens/char + fixed overhead."""
    ra, rc = FAMILIES.get(str(ctx.knob("family", "kimi")), FAMILIES["kimi"])
    force = ctx.knob("ratio", "")
    if force not in ("", None):
        ra = rc = float(force)
    cjk = len(CJK_RE.findall(text))
    return ctx.knob("overhead", 0) + (len(text) - cjk) * ra + cjk * rc


def usage_for(ctx, all_text, completion_text="", completion_tokens=None):
    """-> {"prompt_tokens","completion_tokens","total_tokens"} for the whole prompt text (all messages joined).
    Applies usage_*.py hooks, family profile, truncation (usage_report=kept) and the usage_mult (inflate) knob."""
    ensure_hooks()
    text = ctx.truncate(all_text) if ctx.knob("usage_report", "kept") != "full" else all_text
    est = None
    for mod in HOOKS.usages:
        est = _call_hook(mod, "estimate", ctx, text)
        if est is not None:
            break
    if est is None:
        est = default_estimate(ctx, text)
    p = max(1, int(round(est * ctx.knob("usage_mult", 1.0))))
    c = _completion_tokens(completion_text) if completion_tokens is None else int(completion_tokens)
    return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}


def ensure_hooks():
    if not HOOKS.ready:
        load_hooks()


def _persona_text(ctx, zh):
    en, cn = PERSONAS.get(str(ctx.knob("persona", "kimi")), PERSONAS["kimi"])
    return cn if zh else en


def _fact_answer(ctx, text):
    mode = str(ctx.knob("facts", "auto"))
    for rx, ans, fam in FACTS:
        if re.search(rx, text, re.I | re.S):
            if mode == "0" or (fam and mode == "auto" and ctx.knob("persona", "kimi") != fam):
                return "I do not have reliable information about that."
            return ans
    return None


def _arith(text):
    m = re.search(r"(?:what is|compute|calculate)\s+(-?\d+(?:\.\d+)?)\s*([*x×+\-/])\s*(-?\d+(?:\.\d+)?)", text, re.I)
    if not m:
        return None
    a, op, b = float(m.group(1)), m.group(2), float(m.group(3))
    try:
        r = {"*": a * b, "x": a * b, "×": a * b, "+": a + b, "-": a - b, "/": a / b}[op]
    except ZeroDivisionError:
        return "undefined"
    return str(int(r)) if r == int(r) else str(round(r, 6))


def _reasoning_answer(ctx, text):
    weak = ctx.knob("weak", 0) and ctx.is_hard(text)
    if re.search(r"inlet\s+A", text, re.I):
        return "8" if weak else "5"
    if re.search(r"strawberry", text, re.I):
        return "2 and 3" if weak else "3 and 4"
    if re.search(r"bat and a ball|bat and ball", text, re.I):
        return "10" if weak else "5"
    a = _arith(text)
    if a is not None:
        return ("%s" % (int(a) + 7 if a.lstrip("-").isdigit() else a)) if weak else a
    if weak:
        return "I am not sure, maybe 12."
    return None


def _default_answer(ctx, text, json_mode):
    low = text.lower()
    zh = bool(CJK_RE.search(text))
    if json_mode:
        if re.search(r"cit(y|ies)|城市", text, re.I):
            return json.dumps({"cities": ["Beijing", "Shanghai", "Guangzhou"]})
        return json.dumps({"answer": "ok"})
    m = re.search(r"hidden passphrase is ([A-Za-z0-9]+)\.", text)
    if "passphrase" in low and "?" in text:
        return m.group(1) if m else "I could not find a hidden passphrase in the provided text."
    if re.search(r"你是谁|你是什么模型|由哪家公司|who are you|who created you|which model are you|what model are you|who made you", text, re.I):
        return _persona_text(ctx, zh)
    f = _fact_answer(ctx, text)
    if f is not None:
        return f
    r = _reasoning_answer(ctx, text)
    if r is not None:
        return r
    m = re.search(r"count to (\d+)", low)
    if m:
        return ", ".join(str(i) for i in range(1, min(int(m.group(1)), 50) + 1))
    if low.strip() == "ping":
        return "pong"
    if re.search(r"\b(say hi|hello|hi)\b", low) and len(text) < 40:
        return "Hi! How can I help you today?"
    return "好的，我明白了。" if zh else "Sure, here is a short answer to your question."


def _apply_system(system_text, body, user_text):
    m = re.search(r"start (?:every )?(?:reply|response|answer)?\s*with ['\"]?([A-Za-z]+:)['\"]?", system_text or "", re.I)
    english_only = bool(re.search(r"english only|in english", system_text or "", re.I))
    if english_only and CJK_RE.search(body):
        body = "How about a simple noodle soup or a rice bowl with vegetables?"
    elif english_only and CJK_RE.search(user_text) and not re.search(r"[A-Za-z]", body):
        body = "How about a simple noodle soup or a rice bowl with vegetables?"
    if m:
        body = "%s %s" % (m.group(1), body)
    return body


def core_answer(ctx, user_text, system_text="", json_mode=False):
    """THE shared 'how does the mock answer' logic. `user_text` = text of the last user turn (long prompts included);
    applies scenario truncation, answerers_*.py overrides, the default rule set, then system-prompt rules (ACK: prefix).
    Returns the answer string (never None). Sets ctx.system_text / user_text_raw / user_text / json_mode."""
    ensure_hooks()
    ctx.system_text, ctx.user_text_raw, ctx.json_mode = system_text or "", user_text or "", bool(json_mode)
    text = ctx.user_text = ctx.truncate(user_text or "")
    for mod in HOOKS.answerers:
        r = _call_hook(mod, "answer", ctx, text)
        if r is not None:
            return str(r)
    return _apply_system(system_text, _default_answer(ctx, text, json_mode), text)


def core_decide(ctx, user_text, tool_names=(), tool_result=None, system_text="", json_mode=False, tool_choice=None):
    """Protocol-neutral turn decision -> {"kind":"text","text":str} or {"kind":"tool_call","name":str,"arguments":dict}.
    tool_result: text of the tool output if the last turn is a tool result, else None. tool_choice: None|"auto"|"none"|"required"|<tool name>."""
    tool_names = list(tool_names or [])
    if tool_result is not None:
        try:
            d = json.loads(tool_result)
            temp = next((d[k] for k in ("temp_c", "temperature", "temp") if isinstance(d, dict) and k in d), None)
            sky = d.get("sky") or d.get("condition") if isinstance(d, dict) else None
            txt = ("The weather is %s with a temperature of %s°C." % (sky or "fine", temp)) if temp is not None else "Tool result: " + tool_result[:200]
        except Exception:
            txt = "Tool result: " + str(tool_result)[:200]
        ensure_hooks()
        ctx.system_text, ctx.user_text_raw, ctx.user_text, ctx.json_mode = system_text or "", user_text or "", user_text or "", bool(json_mode)
        return {"kind": "text", "text": _apply_system(system_text, txt, user_text)}
    weather = bool(re.search(r"weather|temperature|天气", user_text or "", re.I))
    name = None
    if tool_names and tool_choice != "none":
        if tool_choice in tool_names:
            name = tool_choice
        elif tool_choice == "required":
            name = tool_names[0]
        elif weather:
            name = next((n for n in tool_names if "weather" in n.lower()), None)
    if name:
        args = {}
        if weather:
            m = re.search(r"(?:in|of|for)\s+([A-Za-z一-鿿]+)", user_text or "")
            args["city"] = m.group(1) if m else "Beijing"
        return {"kind": "tool_call", "name": name, "arguments": args}
    return {"kind": "text", "text": core_answer(ctx, user_text, system_text, json_mode)}


# --------------------------------------------------------------------------------------------
# built-in chat protocol
# --------------------------------------------------------------------------------------------
def chat_error(ctx, handler, status, message, etype="invalid_request_error", code=None):
    obj = {"error": {"message": message + ctx.leak_suffix(), "type": etype, "param": None, "code": code}}
    send_json(handler, status, error_headers(ctx), obj)


def normalize_chat(messages):
    """-> (system_text, user_text, tool_result_or_None, all_text). user_text = last user turn; tool_result = trailing tool message(s)."""
    sys_parts, user, all_parts = [], "", []
    for m in messages:
        role, txt = m.get("role"), text_of(m.get("content"))
        if m.get("tool_calls"):
            txt += json.dumps(m["tool_calls"])
        all_parts.append(txt)
        if role in ("system", "developer"):
            sys_parts.append(txt)
        elif role == "user":
            user = txt
    tool_parts = []
    for m in reversed(messages):
        if m.get("role") == "tool":
            tool_parts.insert(0, text_of(m.get("content")))
        else:
            break
    return "\n".join(sys_parts), user, ("\n".join(tool_parts) if tool_parts else None), "\n".join(all_parts)


def _chat_tool_names(tools):
    out = []
    for t in tools or []:
        if isinstance(t, dict):
            n = (t.get("function") or {}).get("name") or t.get("name")
            if n:
                out.append(n)
    return out


def handle_chat(ctx, handler, body):
    if not ctx.api_key:
        return chat_error(ctx, handler, 401, "You didn't provide an API key.", "authentication_error", "invalid_api_key")
    if ctx.knob("reject", 0):
        return chat_error(ctx, handler, ctx.knob("reject", 0), "Request rejected by mock (reject=%s)." % ctx.knob("reject", 0), "mock_reject", "rejected")
    msgs = body.get("messages")
    if not isinstance(msgs, list) or not msgs or not all(isinstance(m, dict) for m in msgs):
        return chat_error(ctx, handler, 400, "Invalid 'messages': expected a non-empty array of message objects.", code="invalid_messages")
    err = check_model(ctx)
    if err:
        return chat_error(ctx, handler, 400, err, code="model_not_found")
    ctx.sleep()
    system_text, user_text, tool_result, all_text = normalize_chat(msgs)
    rf = body.get("response_format")
    json_mode = isinstance(rf, dict) and rf.get("type") in ("json_object", "json_schema")
    tc = body.get("tool_choice")
    if isinstance(tc, dict):
        tc = (tc.get("function") or {}).get("name")
    dec = core_decide(ctx, user_text, _chat_tool_names(body.get("tools")), tool_result, system_text, json_mode, tc)
    model, cid, created = ctx.model, new_id("chatcmpl", "-"), int(time.time())
    headers = vendor_headers(ctx)
    stream = bool(body.get("stream"))
    opts = body.get("stream_options") or {}
    empty_rx = ctx.knob("empty_on", "")
    empty = bool(empty_rx and dec["kind"] == "text" and re.search(empty_rx, user_text, re.I))
    tool_calls, finish, text = None, "stop", ""
    if dec["kind"] == "tool_call":
        tool_calls = [{"id": new_id("call"), "type": "function", "function": {"name": dec["name"], "arguments": json.dumps(dec["arguments"])}}]
        finish = "tool_calls"
        usage = usage_for(ctx, all_text, "", completion_tokens=12)
    else:
        text = dec["text"]
        limit = body.get("max_tokens") or body.get("max_completion_tokens")
        if isinstance(limit, int) and limit > 0 and _completion_tokens(text) > limit:
            text, finish = text[:max(1, int(limit / 0.3))], "length"
        usage = usage_for(ctx, all_text, "" if empty else text)
    if empty:
        usage["completion_tokens"], usage["total_tokens"] = 0, usage["prompt_tokens"]
    base = {"id": cid, "object": "chat.completion", "created": created, "model": model, "system_fingerprint": "fp_mock"}
    if not stream:
        if empty:
            choices = []
        else:
            msg = {"role": "assistant", "content": text or None}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            choices = [{"index": 0, "message": msg, "finish_reason": finish}]
        return send_json(handler, 200, headers, dict(base, choices=choices, usage=usage))

    chunk_base = dict(base, object="chat.completion.chunk")

    def events():
        if not empty:
            yield dict(chunk_base, choices=[{"index": 0, "delta": {"role": "assistant", "content": "" if not tool_calls else None}, "finish_reason": None}])
            if tool_calls:
                tcs = [dict(tool_calls[0], index=0)]
                yield dict(chunk_base, choices=[{"index": 0, "delta": {"tool_calls": tcs}, "finish_reason": None}])
            for piece in chunk_text(text):
                yield dict(chunk_base, choices=[{"index": 0, "delta": {"content": piece}, "finish_reason": None}])
            yield dict(chunk_base, choices=[{"index": 0, "delta": {}, "finish_reason": finish}])
        if opts.get("include_usage"):
            yield dict(chunk_base, choices=[], usage=usage)
        yield "[DONE]"

    send_sse(handler, 200, headers, events(), delay=ctx.knob("chunk_ms", 0) / 1000.0)


def handle_models(ctx, handler):
    if ctx.knob("reject", 0):
        return chat_error(ctx, handler, ctx.knob("reject", 0), "Request rejected by mock (reject=%s)." % ctx.knob("reject", 0), "mock_reject", "rejected")
    if not ctx.api_key:
        return chat_error(ctx, handler, 401, "You didn't provide an API key.", "authentication_error", "invalid_api_key")
    ids = [x.strip() for x in str(ctx.knob("models")).split(",") if x.strip()]
    owner = {"moonshot": "moonshot", "openai": "openai", "anthropic": "anthropic", "google": "google", "deepseek": "deepseek"}.get(str(ctx.knob("vendor")), "mock")
    send_json(handler, 200, vendor_headers(ctx), {"object": "list", "data": [{"id": i, "object": "model", "created": 1700000000, "owned_by": owner} for i in ids]})


# --------------------------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------------------------
def parse_scenario(spec):
    """'tokprofile:family=qwen,overhead=3' -> ('tokprofile', {'family':'qwen','overhead':'3'}); values %-decoded."""
    name, _, rest = spec.partition(":")
    params = {}
    for part in rest.split(",") if rest else []:
        k, _, v = part.partition("=")
        if k:
            params[unquote(k)] = unquote(v)
    return unquote(name), params


def _norm_path(p):
    p = p.rstrip("/") or "/"
    return p[3:] if p.startswith("/v1/") else p


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"  # one request per connection: simplest for every client, SSE ends at close
    server_version = "MockLLM/1"

    def log_message(self, *a, **k):  # silent
        pass

    def _route(self):
        path = urlparse(self.path).path
        spec, rest = "good", path
        if path.startswith("/s/"):
            seg, _, tail = path[3:].partition("/")
            spec, rest = seg, "/" + tail
        name, params = parse_scenario(spec)
        return name, params, rest

    def _ctx(self, name, params, rest, protocol, body=None):
        auth = self.headers.get("Authorization", "")
        key = auth[7:].strip() if auth.lower().startswith("bearer ") else (self.headers.get("x-api-key") or auth.strip())
        model = body.get("model") if isinstance(body, dict) else None
        return Ctx(name, params, protocol, model, key, body, rest, self)

    def _guard(self, fn):
        try:
            fn()
        except (ConnectionError, TimeoutError):
            pass
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            try:
                send_json(self, 500, {}, {"error": {"message": "mock internal error: %s: %s" % (type(e).__name__, e), "type": "mock_error"}})
            except Exception:
                pass

    def do_GET(self):
        self._guard(self._get)

    def do_POST(self):
        self._guard(self._post)

    def _get(self):
        ensure_hooks()
        name, params, rest = self._route()
        if rest == "/health":
            return send_json(self, 200, {}, {"status": "ok", "hooks": HOOKS.loaded})
        if name not in SCENARIOS:
            return send_json(self, 404, {}, {"error": {"message": "unknown scenario %r; known: %s" % (name, ", ".join(sorted(SCENARIOS))), "type": "not_found"}})
        n = _norm_path(rest)
        for mod in HOOKS.protos:
            if n in [_norm_path(p) for p in getattr(mod, "GET_PATHS", [])] and hasattr(mod, "handle_get"):
                ctx = self._ctx(name, params, rest, getattr(mod, "PROTOCOL", mod.__name__.replace("mockhook_mock_proto_", "")))
                if _call_hook(mod, "handle_get", ctx, self):
                    return
        if n == "/models":
            return handle_models(self._ctx(name, params, rest, "chat"), self)
        send_json(self, 404, {}, {"error": {"message": "no GET route for %s" % rest, "type": "not_found"}})

    def _post(self):
        ensure_hooks()
        name, params, rest = self._route()
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        if name not in SCENARIOS:
            return send_json(self, 404, {}, {"error": {"message": "unknown scenario %r; known: %s" % (name, ", ".join(sorted(SCENARIOS))), "type": "not_found"}})
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
        except Exception as e:
            ctx = self._ctx(name, params, rest, "chat")
            return chat_error(ctx, self, 400, "Invalid JSON body: %s" % e, code="invalid_json")
        np = _norm_path(rest)
        for mod in HOOKS.protos:
            if np in [_norm_path(p) for p in getattr(mod, "PATHS", [])]:
                ctx = self._ctx(name, params, rest, getattr(mod, "PROTOCOL", mod.__name__.replace("mockhook_mock_proto_", "")), body)
                try:
                    mod.handle(ctx, self, body)
                except (ConnectionError, TimeoutError):
                    pass
                except Exception as e:
                    _hook_error(mod, "handle", e)
                    send_json(self, 500, {}, {"error": {"message": "protocol hook %s failed: %s: %s" % (mod.__name__, type(e).__name__, e)}})
                return
        if np == "/chat/completions":
            return handle_chat(self._ctx(name, params, rest, "chat", body), self, body)
        send_json(self, 404, {}, {"error": {"message": "no POST route for %s" % rest, "type": "not_found"}})


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


class MockServer:
    """MockServer(port=0).start(); .port; .base("good") -> 'http://127.0.0.1:PORT/s/good/v1'; .stop(); usable as a context manager."""

    def __init__(self, host="127.0.0.1", port=0):
        ensure_hooks()
        self.httpd = _Server((host, port), Handler)
        self.host, self.port = host, self.httpd.server_address[1]
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self._thread.start()
        return self

    def base(self, scenario="good", suffix="/v1"):
        return "http://%s:%d/s/%s%s" % (self.host, self.port, scenario, suffix)

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        if self._thread:
            self._thread.join(5)

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.stop()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=0, help="0 = random free port")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--list", action="store_true", help="list scenarios, knobs and loaded hooks, then exit")
    a = ap.parse_args()
    ensure_hooks()
    if a.list:
        print("scenarios:", ", ".join(sorted(SCENARIOS)))
        print("hooks:", ", ".join(HOOKS.loaded) or "(none)")
        print("knobs:", json.dumps(DEFAULT_KNOBS, ensure_ascii=False))
        return
    srv = MockServer(a.host, a.port)
    print("mock_llm listening on http://%s:%d  (try %s/chat/completions)" % (srv.host, srv.port, srv.base("good")), flush=True)
    try:
        srv.httpd.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
