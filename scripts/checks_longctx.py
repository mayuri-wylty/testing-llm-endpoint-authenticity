"""Long-context check: three-depth needle-in-haystack (10/50/90%) sized by the claimed window, plus prompt_tokens plausibility.

Sizing (--claimed-window; an explicit --records N always wins, 0 = skip). WINDOW_FRAC=0.6 because digit-per-token tokenizers
(qwen/glm/gemini, ~0.40-0.44 tok/char) would turn a 0.33-based estimate of 0.8*window into >window and a 400:
  none            -> ~32k tokens            (<128k claimed: min(32k, 0.6*window))
  >=128k          -> min(100k, 0.6*window)  (128k -> ~77k, 200k+ -> ~100k)
  >=1M            -> ~100k, unless --deep: min(0.6*window, 900k)  (1M -> 600k)
Needles: three random 10-char secrets (Random(7), reproducible) at 10%/50%/90% depth, retrieved in ONE request.
An explicit --needle-pos X falls back to the v1 single-needle probe at depth X.
score = 0.7 * (needles found / needles) + 0.3 * (prompt_tokens/char sane); a missing 90% needle => tail truncation, 10% => head.
Cost guard: est tokens = chars * 0.33; > 150k without --deep => not sent (n/a). Timeout scales with size (240..900s).
"""
import random, re, string
from common import TOK_PER_CHAR_MAX, TOK_PER_CHAR_MIN

NAME = "long context"
TOK_PER_RECORD = 17        # one haystack record is ~17 tokens (1500 records ~ 25k tokens)
EST_TOK_PER_CHAR = 0.33    # pre-flight estimate of tokens per haystack char
GUARD_TOKENS = 150_000     # estimates above this need --deep
DEFAULT_TARGET = 32_000
BIG_TARGET = 100_000
DEEP_CAP = 900_000
WINDOW_FRAC = 0.6          # fraction of the claimed window we dare to fill (estimate error up to ~1.33x on digit tokenizers)
TOO_LONG_RE = re.compile(r"context|maximum|too long|too large|exceed|payload|entity|token limit|length", re.I)
GATEWAY_STATUS = (-1, None, 408, 504, 524)  # timeout / gateway: inconclusive for a huge request, not a failure of the model
DEPTHS = (0.1, 0.5, 0.9)

QUESTION_TRIPLE = ("The text above hides exactly three passphrases, each in a line starting with [IMPORTANT] "
                   "(numbered #1, #2, #3 in order of appearance). Output the three passphrases, one per line, as "
                   "'#1: <passphrase>', '#2: <passphrase>', '#3: <passphrase>'. Write MISSING for any you cannot find. "
                   "Output nothing else.")
QUESTION_SINGLE = "What is the hidden passphrase? Output only the passphrase."


def parse_window(s):
    """'128k' / '200K' / '1M' / '1.5m' / '131072' / '128,000 tokens' -> int tokens; '' / garbage -> None."""
    if s is None:
        return None
    m = re.fullmatch(r"\s*([\d][\d,]*(?:\.\d+)?)\s*([kKmM]?)\s*(?:tokens?|tok)?\s*", str(s))
    if not m:
        return None
    n = float(m.group(1).replace(",", "")) * {"": 1, "k": 1_000, "m": 1_000_000}[m.group(2).lower()]
    return int(n) if n > 0 else None


def estimate_tokens(chars):
    return int(chars * EST_TOK_PER_CHAR)


def timeout_for(est_tokens, base=240):
    return int(min(900, max(base, est_tokens / 150)))


def fmt_k(n):
    if n >= 1_000_000 and n % 100_000 == 0:
        return "%gM" % (n / 1_000_000)
    return "%dk" % round(n / 1000) if n >= 1000 else str(n)


def plan(a):
    """Decide haystack size and needle mode from the CLI namespace. -> dict(skip, records, target, mode, pos, window, tier)."""
    records, pos = getattr(a, "records", None), getattr(a, "needle_pos", None)
    raw_win = getattr(a, "claimed_window", "") or ""
    window, deep = parse_window(raw_win), bool(getattr(a, "deep", False))
    p = {"skip": False, "records": records, "target": None, "mode": "triple" if pos is None else "single",
         "pos": None if pos is None else min(max(float(pos), 0.0), 1.0), "window": window, "tier": ""}
    if records is not None and records <= 0:
        p["skip"] = True
        return p
    if raw_win.strip() and window is None:
        p["tier"] = "unparseable --claimed-window %r ignored; " % raw_win
    if records is not None:
        p["target"] = records * TOK_PER_RECORD
        p["tier"] += "explicit --records %d" % records
        return p
    if window is None:
        target, why = DEFAULT_TARGET, "no window claimed"
    elif window < 128_000:
        target, why = max(2_000, min(DEFAULT_TARGET, int(window * WINDOW_FRAC))), "claimed %s" % fmt_k(window)
    elif window < 1_000_000:
        target, why = min(BIG_TARGET, int(window * WINDOW_FRAC)), "claimed %s" % fmt_k(window)
    elif deep:
        target, why = min(int(window * WINDOW_FRAC), DEEP_CAP), "claimed %s + --deep" % fmt_k(window)
    else:
        target, why = BIG_TARGET, "claimed %s, 1M tier needs --deep" % fmt_k(window)
    p["tier"] += "%s -> ~%s tier" % (why, fmt_k(target))
    p["target"] = target
    p["records"] = max(20, round(target / TOK_PER_RECORD))
    return p


def build_haystack(n_records, depths, rnd):
    """-> (doc, secrets). Secrets are drawn first, then the filler records (so a given seed is reproducible)."""
    alphabet = string.ascii_uppercase + string.digits
    secrets = ["".join(rnd.choices(alphabet, k=10)) for _ in depths]
    rec = [f"Record {i}: weather clear, code {rnd.randint(1000, 9999)}, all normal." for i in range(n_records)]
    pos = [int(len(rec) * d) for d in depths]
    for k in sorted(range(len(depths)), key=lambda i: pos[i], reverse=True):  # high index first keeps the others valid
        sentence = (f"[IMPORTANT] The hidden passphrase #{k + 1} is {secrets[k]}." if len(depths) > 1
                    else f"[IMPORTANT] The hidden passphrase is {secrets[k]}.")
        rec.insert(pos[k], sentence)
    return "\n".join(rec), secrets


def _alnum(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def diagnose(depths, found):
    miss = [d for d, f in zip(depths, found) if not f]
    if not miss:
        return ""
    if len(depths) == 1:  # v1-style single needle: only a hint, one needle cannot tell truncation from a plain miss
        d = depths[0]
        return ("%d%% depth missing: head truncation possible" % round(d * 100) if d <= 0.3 else
                "%d%% depth missing: tail truncation possible" % round(d * 100) if d >= 0.7 else
                "%d%% depth missing: truncation or lost-in-the-middle possible" % round(d * 100))
    if len(miss) == len(depths):
        return "all needles missing: severe truncation, or real window far below the claim"
    if len(depths) == 3:
        m = tuple(not f for f in found)
        if m == (False, False, True):
            return "90% missing: tail truncation suspected"
        if m == (True, False, False):
            return "10% missing: head truncation suspected"
        if m == (True, True, False):
            return "10%+50% missing: head truncation suspected (more than half dropped)"
        if m == (False, True, True):
            return "50%+90% missing: tail truncation suspected (more than half dropped)"
        if m == (False, True, False):
            return "only 50% missing: middle lost (lost-in-the-middle or context compression)"
        if m == (True, False, True):
            return "10% and 90% missing but 50% found: odd pattern (sliding window / compression?)"
    return "needles at %s missing: partial truncation suspected" % ",".join("%d%%" % round(d * 100) for d in miss)


def run(audit):
    a = audit.a
    p = plan(a)
    if p["skip"]:
        audit.add(NAME, None, "skipped (--records 0)")
        return
    depths = DEPTHS if p["mode"] == "triple" else (p["pos"],)
    doc, secrets = build_haystack(p["records"], depths, random.Random(7))
    est = estimate_tokens(len(doc))
    if est > GUARD_TOKENS and not getattr(a, "deep", False):
        audit.add(NAME, None, "refused: est ~%dk tokens > 150k, pass --deep (%s)" % (round(est / 1000), p["tier"]))
        return
    to = timeout_for(est, getattr(audit, "timeout", 240))
    q = QUESTION_TRIPLE if p["mode"] == "triple" else QUESTION_SINGLE
    r = audit.chat([{"role": "user", "content": doc + "\n\n" + q}], timeout=to)
    pre = "%s; est ~%d tok (%d chars, %d records), timeout %ds" % (p["tier"], est, len(doc), p["records"], to)
    if r.status != 200:
        err = str(r.raw)[:120]
        if r.status in GATEWAY_STATUS:  # timeout / gateway error on a big request: inconclusive, not a 0
            audit.add(NAME, None, "gateway/timeout on large request; inconclusive (HTTP %s %s) [%s]" % (r.status, err, pre))
        elif r.status in (400, 413) and TOO_LONG_RE.search(str(r.raw)):
            audit.add(NAME, None, "request rejected as too long; claimed window may be smaller than stated (HTTP %s %s) [%s]" % (r.status, err, pre))
        else:
            audit.add(NAME, 0.0, "HTTP %s %s [%s]" % (r.status, err, pre))
        return
    pt = (r.norm.usage or {}).get("prompt") or 0
    text = _alnum(r.norm.text)
    found = [_alnum(s) in text for s in secrets]
    ratio = pt / len(doc)
    sane = ratio > TOK_PER_CHAR_MIN  # far below => truncation or fake usage
    audit.usage_inflated = ratio > TOK_PER_CHAR_MAX  # far above => inflated billing
    frac = sum(found) / len(found)
    score = 0.7 * frac + (0.3 if sane and not audit.usage_inflated else 0.0)
    per = " ".join("%d%%:%s" % (round(d * 100), "found" if f else "MISSED") for d, f in zip(depths, found))
    if all(found):
        head = "needle found %d/%d (%s)" % (len(found), len(found), per)
    elif len(depths) == 1:
        head = "needle MISSED at %d%% depth" % round(depths[0] * 100)
    else:
        head = "needle MISSED at %s (found %d/%d; %s)" % (",".join("%d%%" % round(d * 100) for d, f in zip(depths, found) if not f),
                                                          sum(found), len(found), per)
    diag = diagnose(depths, found)
    win = ""
    if pt:
        win = "; prompt_tokens=%d taken as effective-window lower bound (est %d, %+.0f%%)" % (pt, est, 100.0 * (est - pt) / pt)
        if p["window"]:
            win += " = %.0f%% of claimed %s" % (100.0 * pt / p["window"], fmt_k(p["window"]))
    audit.add(NAME, score, "%s%s%s; %d chars, %.2f tok/char%s [%s]; %.0fs" % (
        head, ("; " + diag) if diag else "", win, len(doc), ratio, "; USAGE INFLATED" if audit.usage_inflated else "", pre, r.t))
