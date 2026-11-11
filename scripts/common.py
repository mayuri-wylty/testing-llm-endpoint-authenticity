"""Shared helpers: HTTP transport, key scrubbing, constants, math (stdlib only)."""
import json, re, time, urllib.error, urllib.request

UA = "curl/8.4.0"  # default Python UA is blocked by Cloudflare (error 1010)
TOK_PER_CHAR_MIN = 0.15  # below this prompt_tokens is implausibly low for the ASCII haystack (truncation / fake usage)
TOK_PER_CHAR_MAX = 0.6   # above this it is implausibly high (inflated billing); typical ~0.33, digit-per-token tokenizers ~0.4

# Default per-request timeout. A profile may override it (K3.1 Pro: 3600s) via set_default_timeout().
# Readers must go through get_default_timeout() or common.DEFAULT_TIMEOUT; `from common import DEFAULT_TIMEOUT`
# would freeze the value at import time and miss a later profile override.
DEFAULT_TIMEOUT = 240


def set_default_timeout(seconds):
    """Set the module-wide default timeout (called once, after the profile is loaded). Returns the new value."""
    global DEFAULT_TIMEOUT
    DEFAULT_TIMEOUT = max(1, int(seconds))
    return DEFAULT_TIMEOUT


def get_default_timeout():
    return DEFAULT_TIMEOUT


_DUR_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
_DUR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|s|m|h|d)", re.I)


def parse_duration(spec):
    """'3600', '3600s', '60m', '1h', '1h30m', '2d' -> seconds (float). Raises ValueError on anything else."""
    if isinstance(spec, (int, float)):
        return float(spec)
    s = str(spec).strip().lower()
    if not s:
        raise ValueError("empty duration")
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        return float(s)
    parts = _DUR_RE.findall(s)
    if not parts or _DUR_RE.sub("", s).strip():
        raise ValueError("cannot parse duration %r (expected e.g. 3600s, 60m, 1h, 1h30m)" % spec)
    return sum(float(n) * _DUR_UNITS[u] for n, u in parts)


def percentile(values, p):
    """Linear-interpolation percentile, the same formula as the official baseline (run_compare.pct).

    None and non-numeric entries are dropped; empty input -> None. p is 0-100.
    """
    xs = sorted(float(v) for v in values if isinstance(v, (int, float)))
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


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
