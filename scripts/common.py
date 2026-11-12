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
        # (?<![*.…]) anchors the match at the START of a mask run; without it every position of a long run
        # of '*' rescans the whole run (quadratic ReDoS). Leftmost semantics are unchanged.
        s = re.sub(r"(?<![*.…])[*.…]{2,}" + re.escape(key[-4:]), "[KEY]", s)
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


# ---------------------------------------------------------------------------------------------
# Display-layer number formatting (HTML / md rendering boundary ONLY).
# audit/spec/load JSON keeps full precision: regress / expected_* / perf_stats depend on raw values,
# so none of these functions may be called on data that is written back to JSON for machines.
# ---------------------------------------------------------------------------------------------
import decimal as _decimal
import math as _math

_DASH = "\u2014"  # "—": shown for nan/inf


def _quantize_half_up(d, places=2):
    """Decimal -> Decimal rounded HALF_UP (half away from zero) to `places`, with enough precision for any float."""
    with _decimal.localcontext() as ctx:
        ctx.prec = 400  # float max is ~1.8e308 (309 digits) + decimals
        return d.quantize(_decimal.Decimal(1).scaleb(-places), rounding=_decimal.ROUND_HALF_UP)


def round_half_up(v, nd=2):
    """float -> float rounded HALF_UP to `nd` decimals using the shortest repr (2.675 -> 2.68, unlike round()).

    nan/inf pass through; a result of -0.0 is normalised to 0.0.
    """
    if not isinstance(v, float) or _math.isnan(v) or _math.isinf(v):
        return v
    r = float(_quantize_half_up(_decimal.Decimal(repr(v)), nd))
    return 0.0 if r == 0 else r


def fmt_num(v, thousands=True):
    """Number -> display string. bool -> str(v); None -> ""; int -> integer (thousands separators);
    float -> fixed 2 decimals (HALF_UP, 100.0 -> "100.00", no scientific notation); nan/inf -> "—"."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    spec = ",f" if thousands else "f"
    if isinstance(v, int):
        return format(v, "," if thousands else "d")
    if isinstance(v, float):
        if _math.isnan(v) or _math.isinf(v):
            return _DASH
        d = _quantize_half_up(_decimal.Decimal(repr(v)), 2)
        if d == 0:
            d = _decimal.Decimal("0.00")  # no "-0.00"
        with _decimal.localcontext() as ctx:
            ctx.prec = 400
            return format(d, spec)
    return str(v)


# Letter units that may directly follow a number ("3.141ms", "1.261x"); anything else glued on (1.234a, 1.234abc,
# 1.234x86, 1.2345e-5) means identifier / hex / scientific notation and the number is left alone.
_TXT_UNITS = r"(?:ms|us|\u00b5s|ns|s|x|m|h|d|tok|toks|tokens|tps|rps|qps|kb|mb|gb|kib|mib|gib|b|k)"
_ROUND_TEXT_RE = re.compile(
    # protected spans first (leftmost-longest wins at a given start): URLs and sk- style secrets are returned verbatim
    r"(?P<keep>https?://[^\s<>\"'`)\]]+|\bsk-[\w\-*.\u2026]+)"
    r"|"
    r"(?<![\w.])"                      # not glued to an identifier / digit / another number segment ("1.2.3", "v0.149", "x_1.2345")
    r"(?<![A-Za-z_]-)"                 # not "name-1.2345" (kimi-k3.1-pro, temp-0.6789); "1.234-5.678" ranges are fine
    r"(?<!\d:)"                        # not a clock time "12:30:45.123" / "1:23.456"
    r"(?P<sign>-?)"
    r"(?P<int>\d+)\.(?P<frac>\d{3,})"
    r"(?!\.\w)"                        # not "1.2.3", "1.234.567", "1.234.example.com" (sentence-final "1.234." is fine)
    r"(?!-[A-Za-z_])"                  # not "1.2345-pro"
    r"(?:(?![A-Za-z0-9_])|(?=(?i:" + _TXT_UNITS + r")(?![A-Za-z0-9_])))"  # no letter/digit glued on, unless a known unit
)


def _round_text_sub(m):
    if m.group("keep") is not None:
        return m.group("keep")
    d = _quantize_half_up(_decimal.Decimal(m.group("int") + "." + m.group("frac")), 2)
    sign = m.group("sign")
    if d == 0:
        sign = ""  # "-0.001" -> "0.00", not "-0.00"
    return sign + format(d, "f")


def round_text(s):
    """Free text -> same text with every decimal that has >= 3 fractional digits cut to 2 (HALF_UP on the literal
    digits, so no float error: "2.675" -> "2.68", "2.500" -> "2.50", "0.999" -> "1.00"). Decimals with <= 2 digits
    are untouched, so the function is idempotent. Non-str input is returned unchanged.

    Protected (left verbatim), by design:
      * multi-segment numbers: 1.2.3, v0.149.0, 127.0.0.1, 1.234.567 (a number followed by ".<word>");
      * numbers glued to an identifier: letter/digit/underscore/dot right before (kimi-k3.1-pro, P:temp_0.6, x_1.2345,
        deadbeef1.2345, 0x1.8p3) or "name-" before (temp-0.6789); "-pro"/"_turbo" right after (1.2345-pro);
      * clock times 12:30:45.123 / 1:23.456 and ISO timestamps (2026-11-09T12:30:45.123Z: the "Z" glues on);
      * URLs (http/https up to whitespace/quote/bracket) and sk-... key-like tokens;
      * scientific notation (1.2345e-5) and any number followed by non-unit letters (1.234a, 1.234abc, 1.234x86).
    Rounded: a plain number, optionally negative, optionally followed by a unit (x, %, s, ms, us, ns, m, h, d, tok,
    tps, KB/MB/GB, ...) or non-letter text ("3.141ms", "1.261x", "1.261×", "12.345%", "p50=3.101", "(0.1234)").
    Known limitations: a number like "1.2345" that is meant as an identifier but stands alone is rounded; a unit
    letter that is really an identifier suffix ("1.234m" as a build tag) is rounded; ".5678" (no leading digit) is
    not touched; "1.234.example" style tokens are protected even at a sentence boundary without a space.
    """
    if not isinstance(s, str):
        return s
    return _ROUND_TEXT_RE.sub(_round_text_sub, s)


def round_obj(o):
    """Recursively copy dict/list/tuple with every float rounded HALF_UP to <= 2 decimals (still a float, so
    json.dumps prints at most 2). int/bool/None/str are unchanged (strings go through round_text separately).
    The input is never mutated."""
    if isinstance(o, dict):
        return {k: round_obj(v) for k, v in o.items()}
    if isinstance(o, list):
        return [round_obj(v) for v in o]
    if isinstance(o, tuple):
        return tuple(round_obj(v) for v in o)
    if isinstance(o, float):
        return round_half_up(o, 2)
    return o
