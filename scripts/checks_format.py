"""Response format + SSE streaming check (usage consistency, finish_reason, model echo, stream markers)."""
NAME = "format+stream"


def run(audit):
    ad, model = audit.adapter, audit.a.model
    r = audit.chat([{"role": "user", "content": "say hi"}])
    n = r.norm
    ok = {}
    ok["http200"] = r.status == 200
    if r.status == 200:
        u = n.usage or {}  # empty choices = relay short-circuit / broken backend, must not crash
        total_native = ad.supports("usage_total")
        keys = ("prompt", "completion", "total") if total_native else ("prompt", "completion")
        ok["has_id_choices"] = bool(n.id) and n.has_output
        ok["usage_ints"] = all(isinstance(u.get(k), int) for k in keys)
        if total_native:  # protocols without a native total skip this sub-test (n/a: not counted in the denominator)
            ok["usage_sum"] = ok["usage_ints"] and u["total"] == u["prompt"] + u["completion"]
        ok["finish_reason"] = n.has_output and n.finish in ("stop", "length")
        ok["model_echo"] = model.lower() in str(n.model or "").lower()
        audit.notes.append("reasoning_content present: %s; reasoning_tokens: %s" % (n.has_reasoning, u.get("reasoning")))
    r2 = audit.chat([{"role": "user", "content": "count to 3"}], stream=True)
    st = r2.stream
    ok["sse_content_type"] = r2.status == 200 and bool(st["content_type_ok"])
    ok["sse_done"] = bool(st["done"])
    ok["sse_usage"] = bool(st["usage_seen"])
    if r2.status == 200:  # two extra tiny streams so performance() takes the median TTFB of 3 samples; they do not affect the score
        for _ in range(2):
            audit.chat([{"role": "user", "content": "say hi"}], stream=True, max_tokens=16)
    bad = [k for k, v in ok.items() if not v]
    audit.add(NAME, sum(ok.values()) / len(ok), "failed: %s" % bad if bad else "all ok")
