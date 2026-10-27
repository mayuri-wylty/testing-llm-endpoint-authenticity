#!/usr/bin/env python3
"""Regression matrix for scripts/model_audit.py against tests/mock_llm.py (stdlib only).

  python regress.py                         run every case in tests/expected_*.json
  python regress.py --area core,tokens      only those files (expected_<area>.json)
  python regress.py --protocol chat --scenario good,inflate [-v] [--jobs 4] [--records 200]
  python regress.py --baseline              run the CORE_MATRIX with the CURRENT script and write tests/baseline_v1.json
  python regress.py --list                  print the selected cases and exit
Exit code: 0 all pass, 1 any failure (or key leak), 2 nothing to run / setup error.

expected_<area>.json (one file per area; a later file overrides an earlier case with the same id: scalar fields are replaced, while the
"checks"/"flags"/"check_notes" dicts are merged per key so earlier per-check assertions stay in force):
{ "area": "core",
  "protocol_args": {"anthropic": ["--protocol", "anthropic", "--path", "/messages"]},   # optional, merged over all files
  "cases": [ {
      "id": "chat-good",                 # optional, default "<protocol>-<scenario>"
      "protocol": "chat",                # label + filter; default "chat"
      "scenario": "good",                # mock scenario incl. params, e.g. "tokprofile:family=qwen"
      "args": ["--needle-pos", "0.1"],   # extra CLI args (placeholders {base} {model}); appended after the defaults
      "no_default_args": false,          # true: drop the default identity/fact/header-prefix args
      "model": "kimi-k3", "records": 200, "env": {"X": "y"}, "timeout": 120,
      "expect_rc": 0,                    # expected exit code of the audit
      "score": [98, 98],                 # inclusive range of top-level "score"
      "flags": {"usage_inflated": false, "hard_fail": false, "crashed_checks": []},
                                         # top-level JSON keys; bool = truthiness, list/number/str = equality,
                                         # {"regex": "..."} = re.search on str(value)
      "checks": {"long context": [0.0, 0.02]},        # check name -> inclusive range of that check's score (0..1)
      "check_notes": {"long context": "needle found"},# check name -> regex that must match the check's note
      "stdout_contains": ["USAGE INFLATED"], "stdout_not_contains": [],
      "xfail": false, "note": "free text" } ] }
Every case also gets a key-leak check (full key and masked forms) over stdout, stderr and the --out JSON.
"""
import argparse, concurrent.futures as cf, datetime, glob, hashlib, json, os, re, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
S_DIR = os.path.dirname(HERE)
DEFAULT_SCRIPT = os.path.join(S_DIR, "scripts", "model_audit.py")
BACKUP_SCRIPT = os.path.join(os.path.expanduser("~"), ".claude", "backups", "testing-llm-endpoint-authenticity.bak-2026-10-26", "scripts", "model_audit.py")
BASELINE_FILE = os.path.join(HERE, "baseline_v1.json")
FAKE_KEY = "sk-TESTKEY1234567890abcdef"
DEFAULT_MODEL = "kimi-k3"
DEFAULT_RECORDS = 200
DEFAULT_ARGS = ["--identity-regex", "Kimi|Moonshot|月之暗面", "--header-prefix", "msh-", "--header-prefix", "x-msh-",
                "--fact", "Kimi K2 总参数和激活参数？::1T|1万亿.*32B"]
PROTOCOL_ARGS = {"chat": []}  # protocol -> extra args; expected files may add entries via "protocol_args"

# cases run by --baseline (current script, chat protocol); expected_core.json was generated from its output
CORE_MATRIX = [{"scenario": s, "protocol": "chat"} for s in (
    "good", "emptychoices", "inflate", "keyecho", "truncate_head", "truncate_tail", "no_headers", "weak_reasoner",
    "wrongvendor", "tokprofile:family=qwen")]
CORE_MATRIX.append({"scenario": "keyecho:reject=401", "protocol": "chat", "id": "chat-keyecho-401"})  # audit aborts; its error text must be scrubbed
for _s in ("truncate_head", "truncate_tail"):
    for _p in ("0.1", "0.9"):
        CORE_MATRIX.append({"scenario": _s, "protocol": "chat", "args": ["--needle-pos", _p], "id": "chat-%s@%s" % (_s, _p)})


# ------------------------------------------------------------------ key leak detection
def find_leaks(text, key=FAKE_KEY):
    """Descriptors of key material in `text`: full key, masked echo 'sk-TES***cdef', '***cdef', 'sk-TES' + secret tail."""
    out = []
    if not text:
        return out
    if key in text:
        out.append("full key")
    if re.search(re.escape(key[:6]) + r"[\w\-]*[*.…]{2,}\w*", text):
        out.append("masked key (prefix***suffix)")
    if re.search(r"[*.…]{2,}" + re.escape(key[-4:]), text):
        out.append("masked key (***suffix)")
    if key[6:12] in text:  # the first secret characters after the prefix, however they are wrapped
        out.append("key body fragment")
    return out


def selftest_leak_detector():
    bad = ["Incorrect API key: " + FAKE_KEY, "masked sk-TES***cdef here", "key ...cdef", "x sk-TESTKEY123 y"]
    good = ["Incorrect API key provided: [KEY] (masked: [KEY]).", "sk- is the prefix", "plain text 1234"]
    errs = ["detector missed: %r" % b for b in bad if not find_leaks(b)]
    errs += ["detector false positive: %r" % g for g in good if find_leaks(g)]
    return errs


def redact(text, key=FAKE_KEY):
    if not text:
        return text
    text = text.replace(key, "[KEY]")
    return re.sub(re.escape(key[:6]) + r"[\w\-*.…]*", "[KEY]", text)


# ------------------------------------------------------------------ case loading
def load_expected(areas=None):
    cases, proto_args, seen = [], dict(PROTOCOL_ARGS), {}
    for path in sorted(glob.glob(os.path.join(HERE, "expected_*.json"))):
        area = os.path.basename(path)[len("expected_"):-len(".json")]
        if areas and area not in areas:
            continue
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        proto_args.update(doc.get("protocol_args", {}))
        for c in doc.get("cases", []):
            c = dict(c)
            c.setdefault("protocol", "chat")
            c["id"] = c.get("id") or "%s-%s" % (c["protocol"], c["scenario"])
            c["area"] = doc.get("area", area)
            if c["id"] in seen:
                old = cases[seen[c["id"]]]
                for k in ("checks", "flags", "check_notes"):  # later file overrides per key; unlisted keys keep the earlier assertion
                    if old.get(k):
                        c[k] = dict(old[k], **c.get(k, {}))
                cases[seen[c["id"]]] = None  # later file overrides
            seen[c["id"]] = len(cases)
            cases.append(c)
    return [c for c in cases if c], proto_args


def select(cases, protocols, scenarios):
    def sc_ok(c):
        base = c["scenario"].split(":")[0]
        return not scenarios or c["scenario"] in scenarios or base in scenarios
    return [c for c in cases if (not protocols or c["protocol"] in protocols) and sc_ok(c)]


# ------------------------------------------------------------------ running
def build_env(extra=None):
    env = dict(os.environ)
    for k in list(env):
        if k.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy"):
            del env[k]
    # urllib falls back to the Windows registry proxy (e.g. Clash) when no env proxy exists; a dummy env proxy plus
    # NO_PROXY forces the env path where 127.0.0.1 is honoured as "direct".
    env.update({"LLM_API_KEY": FAKE_KEY, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
                "http_proxy": "http://127.0.0.1:9", "no_proxy": "127.0.0.1,localhost", "NO_PROXY": "127.0.0.1,localhost"})
    env.update(extra or {})
    return env


def run_case(case, base_url, script, records, proto_args, outdir, timeout):
    model = case.get("model", DEFAULT_MODEL)
    sub = lambda a: a.replace("{base}", base_url).replace("{model}", model)
    cmd = [sys.executable, script, "--base", base_url, "--model", model]
    rec = case.get("records", records)  # JSON null = do not pass --records (exercise claimed-window tiers)
    if rec is not None:
        cmd += ["--records", str(rec)]
    if not case.get("no_default_args"):
        cmd += DEFAULT_ARGS
    cmd += [sub(a) for a in proto_args.get(case["protocol"], [])] + [sub(a) for a in case.get("args", [])]
    out_json = os.path.join(outdir, re.sub(r"[^\w.@-]+", "_", case["id"]) + ".json")
    cmd += ["--out", out_json]
    t = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, env=build_env(case.get("env")), timeout=case.get("timeout", timeout), cwd=outdir)
        rc, so, se = p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired as e:
        rc, so, se = -999, (e.stdout or b"").decode("utf-8", "replace"), "TIMEOUT after %ss" % case.get("timeout", timeout)
    res, out_text = None, ""
    if os.path.exists(out_json):
        with open(out_json, encoding="utf-8") as f:
            out_text = f.read()
        try:
            res = json.loads(out_text)
        except ValueError:
            pass
    return {"rc": rc, "stdout": so, "stderr": se, "json": res, "json_text": out_text, "elapsed": time.time() - t}


def _in_range(v, rng):
    return v is not None and rng[0] <= v <= rng[1]


def evaluate(case, r):
    """-> list of failure strings (empty = pass)."""
    fails = []
    leaks = [("stdout", find_leaks(r["stdout"])), ("stderr", find_leaks(r["stderr"])), ("--out json", find_leaks(r["json_text"]))]
    for where, found in leaks:
        if found:
            fails.append("KEY LEAK in %s: %s" % (where, ", ".join(found)))
    if r["rc"] != case.get("expect_rc", 0):
        fails.append("exit code %s (expected %s)%s" % (r["rc"], case.get("expect_rc", 0), "; stderr: " + redact(r["stderr"]).strip()[-200:] if r["stderr"].strip() else ""))
    js = r["json"]
    needs_json = any(k in case for k in ("score", "flags", "checks", "check_notes"))
    if js is None:
        if needs_json and case.get("expect_rc", 0) == 0:
            fails.append("no/invalid --out JSON")
        js = {}
    if "score" in case and js:
        if not _in_range(js.get("score"), case["score"]):
            fails.append("score %s not in %s" % (js.get("score"), case["score"]))
    for k, exp in (case.get("flags") or {}).items():
        if k not in js:
            fails.append("flag %s missing from JSON" % k)
            continue
        got = js[k]
        if isinstance(exp, bool):
            ok = bool(got) == exp
        elif isinstance(exp, dict) and "regex" in exp:
            ok = re.search(exp["regex"], str(got)) is not None
        else:
            ok = got == exp
        if not ok:
            fails.append("flag %s = %r (expected %r)" % (k, got, exp))
    checks = {c["name"]: c for c in js.get("checks", [])}
    for name, rng in (case.get("checks") or {}).items():
        c = checks.get(name)
        if c is None:
            fails.append("check %r missing" % name)
        elif rng is None:
            if c["score"] is not None:
                fails.append("check %r score %s (expected skipped/None)" % (name, c["score"]))
        elif not _in_range(c["score"], rng):
            fails.append("check %r score %s not in %s" % (name, c["score"], rng))
    for name, rx in (case.get("check_notes") or {}).items():
        c = checks.get(name)
        if c is None or not re.search(rx, c.get("note") or "", re.I | re.S):
            fails.append("check %r note %r !~ %r" % (name, (c or {}).get("note", "<missing>")[:100], rx))
    for s in case.get("stdout_contains", []):
        if s not in r["stdout"]:
            fails.append("stdout lacks %r" % s)
    for s in case.get("stdout_not_contains", []):
        if s in r["stdout"]:
            fails.append("stdout contains %r" % s)
    return fails


def run_all(cases, script, records, proto_args, jobs, timeout, outdir, verbose):
    import mock_llm
    srv = mock_llm.MockServer().start()
    results = [None] * len(cases)
    try:
        def work(i):
            c = cases[i]
            return i, run_case(c, srv.base(c["scenario"]), script, records, proto_args, outdir, timeout)
        with cf.ThreadPoolExecutor(max(1, jobs)) as ex:
            for i, r in ex.map(work, range(len(cases))):
                results[i] = r
    finally:
        srv.stop()
    return results, mock_llm.HOOKS


def summarize(r):
    js = r["json"] or {}
    return "score=%s" % js.get("score") if js else "rc=%s" % r["rc"]


# ------------------------------------------------------------------ baseline
def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def write_baseline(cases, results, script, records):
    rows = {}
    for c, r in zip(cases, results):
        js = r["json"] or {}
        rows[c["id"]] = {
            "scenario": c["scenario"], "args": c.get("args", []), "rc": r["rc"], "score": js.get("score"), "verdict": js.get("verdict"),
            "hard_fail": js.get("hard_fail"), "usage_inflated": js.get("usage_inflated"), "crashed_checks": js.get("crashed_checks"),
            "checks": {x["name"]: {"score": x["score"], "weight": x["weight"], "note": x["note"]} for x in js.get("checks", [])},
        }
    doc = {"generated": datetime.date.today().isoformat(), "script": os.path.relpath(script, S_DIR), "script_sha256": sha256(script),
           "matches_backup_v1": os.path.exists(BACKUP_SCRIPT) and sha256(BACKUP_SCRIPT) == sha256(script),
           "records": records, "key": "fake test key (never a real one)", "results": rows}
    text = json.dumps(doc, ensure_ascii=False, indent=1)
    if find_leaks(text):
        sys.exit("refusing to write baseline: key leak detected")
    with open(BASELINE_FILE, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    return doc


def main():
    ap = argparse.ArgumentParser(description="model_audit.py regression matrix against mock_llm.py")
    ap.add_argument("--area", help="comma list; expected_<area>.json files to load")
    ap.add_argument("--protocol", help="comma list")
    ap.add_argument("--scenario", help="comma list (base name or full 'name:k=v' string)")
    ap.add_argument("--baseline", action="store_true", help="run CORE_MATRIX with the current script and write tests/baseline_v1.json")
    ap.add_argument("--script", default=DEFAULT_SCRIPT, help="audit script to test (default scripts/model_audit.py)")
    ap.add_argument("--records", type=int, default=DEFAULT_RECORDS, help="--records passed to the audit (default %(default)s)")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--out-dir", help="keep the audit --out JSON files here (default: temp dir)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true", help="print (redacted) stdout of failing cases")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    split = lambda s: [x.strip() for x in s.split(",") if x.strip()] if s else []

    if a.baseline:
        cases, proto_args = [dict(c, id=c.get("id") or "%s-%s" % (c["protocol"], c["scenario"])) for c in CORE_MATRIX], dict(PROTOCOL_ARGS)
    else:
        cases, proto_args = load_expected(split(a.area))
    cases = select(cases, split(a.protocol), split(a.scenario))
    if a.list:
        for c in cases:
            print("%-32s %-8s %-28s %s" % (c["id"], c["protocol"], c["scenario"], " ".join(c.get("args", []))))
        return
    if not cases:
        print("no cases selected")
        sys.exit(2)

    failures = [("selftest-leak-detector", e) for e in selftest_leak_detector()]
    outdir_ctx = tempfile.TemporaryDirectory(prefix="regress_")
    outdir = a.out_dir or outdir_ctx.name
    os.makedirs(outdir, exist_ok=True)
    t0 = time.time()
    results, hooks = run_all(cases, os.path.abspath(a.script), a.records, proto_args, a.jobs, a.timeout, outdir, a.verbose)
    for e in hooks.errors:
        print("WARN", e)
    print("mock hooks loaded: %s" % (", ".join(hooks.loaded) or "none"))

    if a.baseline:
        script = os.path.abspath(a.script)
        doc = write_baseline(cases, results, script, a.records)
        for c, r in zip(cases, results):
            leak = [f for f in (find_leaks(r["stdout"]), find_leaks(r["json_text"])) if f]
            print("%-30s %-10s %s%s" % (c["id"], summarize(r), (r["json"] or {}).get("verdict", ""), "  KEY LEAK" if leak else ""))
            if leak:
                failures.append((c["id"], "key leak"))
        print("baseline written: %s (matches backup v1: %s)" % (BASELINE_FILE, doc["matches_backup_v1"]))
    else:
        npass = nxfail = 0
        for c, r in zip(cases, results):
            fails = evaluate(c, r)
            tag = "PASS"
            if c.get("xfail"):
                tag = "XFAIL" if fails else "XPASS"
                if fails:
                    nxfail += 1
                    fails = []
                else:
                    fails = ["unexpected pass (remove xfail)"]
            if fails:
                tag = "FAIL"
                failures.extend((c["id"], f) for f in fails)
            else:
                npass += tag == "PASS"
            print("%-5s %-34s %-10s %5.1fs" % (tag, c["id"], summarize(r), r["elapsed"]))
            for f in fails:
                print("        - " + f)
            if fails and a.verbose:
                print("        --- stdout (redacted) ---\n" + redact(r["stdout"]) + "        --- stderr ---\n" + redact(r["stderr"]))
        print("\n%d passed, %d xfail, %d failed of %d cases in %.1fs" % (npass, nxfail, len({f[0] for f in failures if f[0] != "selftest-leak-detector"}), len(cases), time.time() - t0))
    for cid, msg in failures:
        if cid == "selftest-leak-detector":
            print("SELFTEST FAIL", msg)
    outdir_ctx.cleanup()
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
