#!/usr/bin/env python3
"""Send malformed requests to an LLM endpoint and dump raw responses (leaked upstream error strings reveal the real backend).
Key via env LLM_API_KEY. Stdlib only. Request bodies are built by the protocol adapter, so every protocol gets the same probes.
Usage:
  LLM_API_KEY=sk-... python error_probe.py --base https://host/v1 --model m1 [--model m2] [--protocol chat|messages|responses]
                                          [--path /custom] [--only a,b] [--out probe.json]
Probes that make no sense for a protocol are skipped and listed as "SKIPPED (reason)".
"""
import argparse, json, os, re, sys, urllib.error, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # also under `python -I`
from adapters import PROTOCOLS, ProtocolUnavailable, get_adapter

UA = "curl/8.5.0"
USER = {"role": "user", "content": "hi"}


# A probe is f(protocol_name) -> dict of Adapter.build kwargs (messages, system, tools, max_tokens, extra, ...; "model_suffix" appends to
# the model name; "drop" lists body keys to delete after build; "patch" is a callable(body) applied last, for malformed shapes an adapter
# would otherwise normalise away), or a str = reason to skip. Chat bodies must stay what they always were.
def _p(messages, **kw):
    return dict(messages=messages, max_tokens=32, **kw)


def _bad_response_format(proto):
    if proto == "chat":
        return _p([USER], extra={"response_format": {"type": "nope"}})
    if proto == "responses":
        return _p([USER], extra={"text": {"format": {"type": "nope"}}})
    return "this protocol has no JSON-mode / response_format field"


def _null_content(body):
    """Set content=None on the first conversation item, whatever the protocol calls the list (chat/messages: messages, responses: input)."""
    for k in ("messages", "input"):
        if isinstance(body.get(k), list) and body[k] and isinstance(body[k][0], dict):
            body[k][0]["content"] = None


def _no_max_tokens(proto):
    return _p([USER], drop=["max_tokens"]) if proto == "messages" else "max_tokens is optional in this protocol"


PROBES = {
    "empty_messages": lambda proto: _p([]),
    "no_system_first": lambda proto: _p([USER]),
    "developer_role": lambda proto: _p([{"role": "developer", "content": "x"}, USER]),
    "bad_role": lambda proto: _p([{"role": "banana", "content": "x"}]),
    "non_stream": lambda proto: _p([USER], system="x", extra={"stream": False}),
    "stream": lambda proto: _p([USER], system="x", extra={"stream": True}),  # via extra: no adapter-added stream_options, same as ever
    "huge_max_tokens": lambda proto: dict(messages=[USER], max_tokens=10_000_000),
    "bad_temperature": lambda proto: _p([USER], extra={"temperature": 99}),
    "bad_tools": lambda proto: _p([USER], tools=[{"type": "function"}]),
    "bad_response_format": _bad_response_format,
    "null_content": lambda proto: _p([{"role": "user", "content": None}], patch=_null_content),
    "unknown_model": lambda proto: _p([USER], model_suffix="-zzz-not-exist"),
    "no_max_tokens": _no_max_tokens,  # messages only: max_tokens is required there
}
DEFAULT_PROBES = [n for n in PROBES if n != "no_max_tokens"]  # historical set; no_max_tokens joins whenever it applies (see main)


def scrub(s, key):
    """Redact the API key and masked echoes of it (e.g. 'sk-abc***wxyz' in upstream errors) before text is printed or written."""
    if not key or not s:
        return s
    s = s.replace(key, "[KEY]")
    if len(key) >= 12:
        s = re.sub(re.escape(key[:6]) + r"[\w\-*.…]*", "[KEY]", s)
        s = re.sub(r"[*.…]{2,}" + re.escape(key[-4:]), "[KEY]", s)
    return s


def build_probe(adapter, proto, name, model):
    """-> (path, body) for the probe, or a str (the reason) when the probe does not apply to the protocol."""
    spec = PROBES[name](proto)
    if isinstance(spec, str):
        return spec
    spec = dict(spec)
    suffix, drop, patch = spec.pop("model_suffix", ""), spec.pop("drop", []), spec.pop("patch", None)
    path, body = adapter.build(spec.pop("messages"), model=model + suffix, **spec)
    for k in drop:
        body.pop(k, None)
    if patch:
        patch(body)
    return path, body


def post(url, key, body, headers):
    hdrs = {"Content-Type": "application/json", "User-Agent": UA, "Accept": "*/*"}
    hdrs.update(headers)
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=hdrs)
    try:
        r = urllib.request.urlopen(req, timeout=60)
        status, raw, hdr = r.status, r.read(4000), dict(r.headers)
    except urllib.error.HTTPError as e:
        status, raw, hdr = e.code, e.read(4000), dict(e.headers)
    except Exception as e:
        return {"status": None, "error": repr(e)}
    return {"status": status, "body": raw.decode("utf-8", "replace")[:1500], "headers": hdr}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--model", action="append", required=True)
    ap.add_argument("--protocol", choices=PROTOCOLS, default="chat", help="wire protocol (default chat = /chat/completions)")
    ap.add_argument("--path", default="", help="override the protocol's default path")
    ap.add_argument("--only", help="comma-separated probe names")
    ap.add_argument("--out")
    a = ap.parse_args()
    key = os.environ.get("LLM_API_KEY") or sys.exit("set LLM_API_KEY")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        adapter = get_adapter(a.protocol)
    except ProtocolUnavailable as e:
        sys.exit("error: %s" % e)
    names = a.only.split(",") if a.only else DEFAULT_PROBES + (["no_max_tokens"] if a.protocol == "messages" else [])
    unknown = [n for n in names if n not in PROBES]
    if unknown:
        sys.exit("unknown probe(s): %s (known: %s)" % (", ".join(unknown), ", ".join(PROBES)))
    res = {}
    for m in a.model:
        res[m] = {}
        for n in names:
            built = build_probe(adapter, a.protocol, n, m)
            if isinstance(built, str):
                res[m][n] = {"status": None, "skipped": built}
                print(scrub("[%s] %s: SKIPPED (%s)" % (m, n, built), key))
                continue
            path, body = built
            r = post(a.base.rstrip("/") + (a.path or path), key, body, adapter.headers(key))
            res[m][n] = r
            print(scrub(f"[{m}] {n}: {r.get('status')} {(r.get('body') or r.get('error') or '')[:200]!r}", key))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(scrub(json.dumps(res, ensure_ascii=False, indent=1), key))


if __name__ == "__main__":
    main()
