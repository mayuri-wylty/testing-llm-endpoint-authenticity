"""The single source of truth for check weights, total-score arithmetic and the performance score."""

VERSION = 2  # scoring version written to the output JSON ("scoring_version"); v1 weights: headers 15/format 15/identity 15/reasoning 10/longctx 15/tools 10/instr 5/error 5/tokenizer 0

# check display name (== checks_*.NAME == output JSON "name") -> weight. Total is renormalised over checks whose score is not None.
WEIGHTS = {
    "vendor headers": 5,
    "format+stream": 10,
    "identity+facts": 15,
    "reasoning": 15,
    "long context": 25,
    "tools+json": 10,
    "instruction following": 5,
    "error format": 5,
    "tokenizer": 10,
}
SCORE_CAP = 98       # a black box can never prove authenticity
HARD_FAIL_CAP = 50   # e.g. identity claims a different vendor


def verdict(total):
    return ("highly credible" if total >= 90 else "mostly credible (check for downgrade)" if total >= 75
            else "doubtful" if total >= 60 else "likely fake/substituted")


def total_score(checks, hard_fail=None):
    """checks: [{"name","weight","score"|None,...}] -> (total 0-100 int, verdict). None scores are n/a and excluded."""
    app = [c for c in checks if c["score"] is not None]
    w = sum(c["weight"] for c in app)
    total = round(100 * sum(c["weight"] * c["score"] for c in app) / w) if w else 0
    total = min(total, SCORE_CAP)
    if hard_fail:
        total = min(total, HARD_FAIL_CAP)
    return total, verdict(total)


def _median(xs):
    return sorted(xs)[len(xs) // 2]


def performance(calls, ttfbs, speeds):
    """0-100 from success rate (40), stream TTFB median (30), output speed median (30); reported separately from the authenticity score."""
    parts = []
    if calls:
        parts.append((40, sum(calls) / len(calls)))
    if ttfbs:
        parts.append((30, max(0.0, min(1.0, (15 - _median(ttfbs)) / 12))))  # <=3s full, >=15s zero
    if speeds:
        parts.append((30, max(0.0, min(1.0, (_median(speeds) - 5) / 25))))  # >=30 tok/s full, <=5 zero
    w = sum(x for x, _ in parts)
    score = round(100 * sum(x * v for x, v in parts) / w) if w else None
    detail = "success %d/%d" % (sum(calls), len(calls))
    if ttfbs:
        detail += "; ttfb %.1fs" % _median(ttfbs)
    if speeds:
        detail += "; speed %.0f tok/s" % _median(speeds)
    return score, detail
