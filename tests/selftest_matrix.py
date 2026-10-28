#!/usr/bin/env python3
"""Cross-protocol consistency self-test (needs the mock server, no network): runs the audit for every mx-<protocol>-<scenario>
case of expected_matrix.json and asserts, per scenario, that chat / messages / responses agree: total-score spread <= 2,
same hard_fail / usage_inflated, same set of n/a checks, per-check score spread <= 0.1 (error-format / tools+json notes may differ).
  python selftest_matrix.py
"""
import os, re, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import regress, mock_llm

cases, proto_args = regress.load_expected(["matrix"])
cases = [c for c in cases if re.match(r"mx-(chat|messages|responses)-[a-z_]+$", c["id"])]
fails = []
with tempfile.TemporaryDirectory(prefix="selftest_matrix_") as out:
    srv = mock_llm.MockServer().start()
    try:
        res = {}
        for c in cases:
            r = regress.run_case(c, srv.base(c["scenario"]), regress.DEFAULT_SCRIPT, regress.DEFAULT_RECORDS, proto_args, out, 180)
            res[(c["scenario"], c["protocol"])] = r
            for w, f in (("stdout", r["stdout"]), ("stderr", r["stderr"]), ("json", r["json_text"])):
                if regress.find_leaks(f):
                    fails.append("%s/%s: key leak in %s" % (c["scenario"], c["protocol"], w))
    finally:
        srv.stop()
scen = sorted({c["scenario"] for c in cases})
for s in scen:
    js = {p: (res[(s, p)]["json"] or {}) for p in ("chat", "messages", "responses")}
    if not all(js.values()):
        fails.append("%s: missing JSON for %s" % (s, [p for p, j in js.items() if not j]))
        continue
    sc = [j["score"] for j in js.values()]
    if max(sc) - min(sc) > 2:
        fails.append("%s: score spread %s" % (s, dict(zip(js, sc))))
    for k in ("hard_fail", "usage_inflated"):
        v = {bool(j[k]) for j in js.values()}
        if len(v) > 1:
            fails.append("%s: %s differs %s" % (s, k, {p: j[k] for p, j in js.items()}))
    names = [c["name"] for c in js["chat"]["checks"]]
    for n in names:
        row = {p: next((c["score"] for c in j["checks"] if c["name"] == n), "<missing>") for p, j in js.items()}
        na = {v is None for v in row.values()}
        if "<missing>" in row.values() or len(na) > 1:
            fails.append("%s / %s: n/a or missing differs %s" % (s, n, row))
        elif None not in row.values() and max(row.values()) - min(row.values()) > 0.1:
            fails.append("%s / %s: score spread %s" % (s, n, row))
    print("%-14s scores %s" % (s, dict(zip(js, sc))))
if fails:
    print("%d FAILED" % len(fails))
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("selftest_matrix: all passed (%d scenarios x 3 protocols)" % len(scen))
