#!/usr/bin/env python3
"""Send malformed chat requests to an OpenAI-compatible endpoint and dump raw responses.
Key via env LLM_API_KEY. Stdlib only. Usage:
  LLM_API_KEY=sk-... python error_probe.py --base https://host/v1 --model m1 [--model m2] [--out probe.json]
"""
import argparse, json, os, sys, urllib.error, urllib.request

UA = "curl/8.5.0"
USER = {"role": "user", "content": "hi"}
PROBES = {
    "empty_messages": lambda m: {"model": m, "messages": [], "max_tokens": 32},
    "no_system_first": lambda m: {"model": m, "messages": [USER], "max_tokens": 32},
    "developer_role": lambda m: {"model": m, "messages": [{"role": "developer", "content": "x"}, USER], "max_tokens": 32},
    "bad_role": lambda m: {"model": m, "messages": [{"role": "banana", "content": "x"}], "max_tokens": 32},
    "non_stream": lambda m: {"model": m, "messages": [{"role": "system", "content": "x"}, USER], "stream": False, "max_tokens": 32},
    "stream": lambda m: {"model": m, "messages": [{"role": "system", "content": "x"}, USER], "stream": True, "max_tokens": 32},
    "huge_max_tokens": lambda m: {"model": m, "messages": [USER], "max_tokens": 10_000_000},
    "bad_temperature": lambda m: {"model": m, "messages": [USER], "temperature": 99, "max_tokens": 32},
    "bad_tools": lambda m: {"model": m, "messages": [USER], "tools": [{"type": "function"}], "max_tokens": 32},
    "bad_response_format": lambda m: {"model": m, "messages": [USER], "response_format": {"type": "nope"}, "max_tokens": 32},
    "null_content": lambda m: {"model": m, "messages": [{"role": "user", "content": None}], "max_tokens": 32},
    "unknown_model": lambda m: {"model": m + "-zzz-not-exist", "messages": [USER], "max_tokens": 32},
}


def post(url, key, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json", "User-Agent": UA, "Accept": "*/*"})
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
    ap.add_argument("--only", help="comma-separated probe names")
    ap.add_argument("--out")
    a = ap.parse_args()
    key = os.environ.get("LLM_API_KEY") or sys.exit("set LLM_API_KEY")
    names = a.only.split(",") if a.only else list(PROBES)
    res = {}
    for m in a.model:
        res[m] = {}
        for n in names:
            r = post(a.base.rstrip("/") + "/chat/completions", key, PROBES[n](m))
            res[m][n] = r
            print(f"[{m}] {n}: {r.get('status')} {(r.get('body') or r.get('error') or '')[:200]!r}")
    if a.out:
        json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
