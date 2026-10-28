"""Shared helpers: HTTP transport, key scrubbing, constants (stdlib only)."""
import json, re, time, urllib.error, urllib.request

UA = "curl/8.4.0"  # default Python UA is blocked by Cloudflare (error 1010)
TOK_PER_CHAR_MIN = 0.15  # below this prompt_tokens is implausibly low for the ASCII haystack (truncation / fake usage)
TOK_PER_CHAR_MAX = 0.6   # above this it is implausibly high (inflated billing); typical ~0.33, digit-per-token tokenizers ~0.4
DEFAULT_TIMEOUT = 240


def scrub(s, key):
    """Redact the API key and masked echoes of it (e.g. 'sk-abc***wxyz' in upstream errors) before text is printed or written."""
    if not key or not s:
        return s
    s = s.replace(key, "[KEY]")
    if len(key) >= 12:
        s = re.sub(re.escape(key[:6]) + r"[\w\-*.…]*", "[KEY]", s)
        s = re.sub(r"[*.…]{2,}" + re.escape(key[-4:]), "[KEY]", s)
    return s


def http(url, key, body, stream=False, timeout=DEFAULT_TIMEOUT, headers=None):
    """POST JSON. Returns (status, resp_headers_lowercased, data, elapsed, ttfb).

    data: parsed JSON (non-stream 200) | raw text (stream, or any HTTP error) | error string (status -1, network failure).
    headers: auth/extra request headers from Adapter.headers(key); None -> Bearer auth. Content-Type/UA/Accept are defaulted.
    """
    hdrs = {"Content-Type": "application/json", "User-Agent": UA, "Accept": "*/*"}
    hdrs.update(headers if headers is not None else {"Authorization": "Bearer " + key})
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=hdrs)
    t = time.time()
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        hdr = {k.lower(): v for k, v in r.headers.items()}
        if stream:
            ttfb, raw = None, []
            for line in r:
                if ttfb is None:
                    ttfb = time.time() - t
                raw.append(line.decode("utf-8", "replace"))
            return r.status, hdr, "".join(raw), time.time() - t, ttfb
        return r.status, hdr, json.loads(r.read().decode("utf-8")), time.time() - t, None
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read().decode("utf-8", "replace"), time.time() - t, None
    except Exception as e:
        return -1, {}, str(e), time.time() - t, None
