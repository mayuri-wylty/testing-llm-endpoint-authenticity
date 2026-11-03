#!/usr/bin/env python3
"""Append/upsert one channel-test result into a shared markdown ledger, then export csv/docx next to it.

The ledger stores the FULL plaintext API key (the one deliberate exception to "keys never touch disk"); every
other output (stdout/stderr) is masked. Key comes only from env LLM_API_KEY, never argv.
Example:
  LLM_API_KEY=sk-... python ledger.py --base https://api.example.com/v1 --score 76 \
      --model m1=真 --model m2=假 --channel 真 --channel-type "new-api 中转" [--ledger PATH]
Ledger path: --ledger > env LLM_LEDGER > ./llm-channel-ledger.md. Upsert key = (site, key); key "未记录" always appends.
"""
import argparse, os, re, sys, time, uuid
from datetime import datetime
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

TITLE = "LLM 渠道测试台账"
HEADERS = ["时间", "站点", "API Key", "模型真伪", "渠道真伪", "评级", "评分"]
NO_KEY = "未记录"
LOCK_WAIT, LOCK_STALE = 20.0, 15.0  # lock is held for milliseconds; stale < wait so a crash leftover heals within one run
NL = chr(10)
SEP = re.compile(r"^:?-+:?$")
CELL_SPLIT = re.compile(r"(?<!\\)\|")


def rating(score, models_ok, channel_ok):
    """D: score<60 or any model fake or channel fake; C: 60-69; B: 70-84; A: >=85 with no fake."""
    if score < 60 or not all(models_ok) or not channel_ok:
        return "D"
    return "C" if score < 70 else "B" if score < 85 else "A"


def host_of(base):
    b = base.strip()
    if "://" not in b:
        b = "//" + b
    h = urlsplit(b).netloc.rpartition("@")[2].lower()
    h = re.sub(r":(443|80)$", "", h)
    return h.replace(".:", ":").rstrip(".")


def mask(key):
    return "***" if len(key) < 20 else f"{key[:4]}…{key[-4:]}"


def esc(s):
    return " ".join(str(s).split()).replace("|", "\\|")


def fmt_row(cells):
    return "| " + " | ".join(esc(c) for c in cells) + " |"


def split_cells(line):
    s = line.strip()
    if not s.startswith("|"):
        return None
    s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip().replace("\\|", "|") for c in CELL_SPLIT.split(s)]


def is_sep(cells):
    return cells is not None and all(SEP.match(c) for c in cells)


def new_block(row):
    return [fmt_row(HEADERS), "|" + "---|" * len(HEADERS), fmt_row(row)]


def new_text(row):
    return NL.join([f"# {TITLE}", "> ⚠ 含明文 API Key，勿提交/外传。", ""] + new_block(row)) + NL


def upsert(text, row):
    """Return ledger text with row replacing the (site, key) match in place, else appended to the ledger table.
    Only the table whose header starts with the ledger headers is touched; no such table -> a fresh one is appended."""
    if not text.strip():
        return new_text(row)
    lines = text.replace("\r\n", NL).split(NL)
    while lines and not lines[-1].strip():
        lines.pop()
    h = next((i for i in range(len(lines) - 1)
              if (c := split_cells(lines[i])) and c[:2] == HEADERS[:2] and is_sep(split_cells(lines[i + 1]))), None)
    if h is None:
        return NL.join(lines + [""] + new_block(row)) + NL
    end = h + 2
    while end < len(lines) and lines[end].lstrip().startswith("|"):
        end += 1
    hit = None
    if row[2] != NO_KEY:
        hit = next((i for i in range(h + 2, end)
                    if len(c := split_cells(lines[i])) > 2 and c[1] == row[1] and c[2] == row[2]), None)
    if hit is not None:
        lines[hit] = fmt_row(row)
    else:
        lines.insert(end, fmt_row(row))
    return NL.join(lines) + NL


def acquire(lock):
    """Create the lock file exclusively; returns a token that release() checks so we never delete someone else's lock."""
    token = f"{os.getpid()}:{uuid.uuid4().hex}"
    end = time.time() + LOCK_WAIT
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, token.encode())
            os.close(fd)
            return token
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(lock) > LOCK_STALE:
                    grave = f"{lock}.stale.{uuid.uuid4().hex}"
                    os.replace(lock, grave)  # atomic: only one contender wins the rename
                    os.remove(grave)
                    continue
            except OSError:
                pass
            if time.time() > end:
                raise TimeoutError(f"lock busy: {lock}")
            time.sleep(0.05)


def release(lock, token):
    try:
        with open(lock, encoding="utf-8") as f:
            mine = f.read() == token
        if mine:
            os.remove(lock)
    except OSError:
        pass


def atomic_write(path, text):
    tmp = f"{path}.tmp{os.getpid()}"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # file holds a plaintext key
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        for n in range(20):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:  # Windows: target briefly held by a reader
                if n == 19:
                    raise
                time.sleep(0.1)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _norm(p):
    p = os.path.realpath(p)
    if p.startswith("\\\\?\\"):
        p = p[4:]
        if p.upper().startswith("UNC\\"):
            p = "\\\\" + p[4:]
    m = re.match(r"^\\\\[^\\]+\\([A-Za-z])\$(\\.*)?$", p)  # \\host\C$\... admin share -> C:\...
    if m:
        p = m.group(1) + ":" + (m.group(2) or "\\")
    return os.path.normcase(os.path.normpath(p))


def inside_skill(path):
    """True if path (lexically or after resolving links) lies in the skill tree. Different drives never match."""
    r = _norm(SKILL_ROOT)
    for cand in (os.path.abspath(path), path):
        p = _norm(cand)
        try:
            if os.path.commonpath([p, r]) == r:
                return True
        except ValueError:  # different drive / mixed UNC form: cannot be inside
            pass
    return False


def model_arg(s):
    mid, eq, v = s.rpartition("=")
    if not eq or not mid.strip() or v not in ("真", "假"):
        raise argparse.ArgumentTypeError(f"--model expects ID=真|假, got {s!r}")
    return mid.strip(), v == "真"


def score_arg(s):
    try:
        n = int(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"--score expects an integer 0-100, got {s!r}")
    if not 0 <= n <= 100:
        raise argparse.ArgumentTypeError(f"--score must be within 0-100, got {n}")
    return n


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, allow_abbrev=False)
    ap.add_argument("--base", required=True, help="endpoint base URL (scheme optional); only host[:port] is recorded")
    ap.add_argument("--score", required=True, type=score_arg, help="total score 0-100")
    ap.add_argument("--model", required=True, action="append", type=model_arg, metavar="ID=真|假", help="repeatable")
    ap.add_argument("--channel", required=True, choices=["真", "假"], help="channel authenticity")
    ap.add_argument("--channel-type", default="", help='e.g. "new-api 中转"')
    ap.add_argument("--ledger", help="ledger .md path (default: env LLM_LEDGER, else ./llm-channel-ledger.md)")
    ap.add_argument("--time", help='override timestamp, "YYYY-MM-DD HH:MM"')
    a, extra = ap.parse_known_args(argv)
    if extra:
        ap.error("unrecognized arguments (not echoed: the API key must be passed via env LLM_API_KEY only)")

    site = host_of(a.base)
    if not site:
        ap.error("cannot parse a host from --base (not echoed)")
    when = a.time
    if when:
        try:
            when = datetime.strptime(when, "%Y-%m-%d %H:%M").strftime("%Y-%m-%d %H:%M")
        except ValueError:
            ap.error('--time must look like "YYYY-MM-DD HH:MM"')
    else:
        when = datetime.now().strftime("%Y-%m-%d %H:%M")

    explicit = a.ledger or os.environ.get("LLM_LEDGER")
    path = os.path.abspath(explicit or "llm-channel-ledger.md")
    if inside_skill(path):
        print(f"refusing to write the ledger inside the skill directory ({SKILL_ROOT}); use --ledger PATH", file=sys.stderr)
        return 2
    if not path.lower().endswith(".md"):
        print("the ledger path must end in .md (the csv/docx exports are written next to it)", file=sys.stderr)
        return 2
    if not explicit and os.path.isdir(os.path.join(os.path.dirname(path), ".git")):
        print("warning: the default ledger sits in a git repository and holds a plaintext key; do not commit it "
              "(or pass --ledger / set LLM_LEDGER)", file=sys.stderr)

    key = " ".join(os.environ.get("LLM_API_KEY", "").split())  # same normalisation as esc(), so the upsert match is stable
    if not key:
        print(f"warning: LLM_API_KEY not set; recording API Key as {NO_KEY}", file=sys.stderr)
        key = NO_KEY

    models_ok = [ok for _, ok in a.model]
    channel_ok = a.channel == "真"
    grade = rating(a.score, models_ok, channel_ok)
    row = [when, site, key,
           "；".join(f"{m}：{'真' if ok else '假'}" for m, ok in a.model),
           a.channel + (f"（{a.channel_type.strip()}）" if a.channel_type.strip() else ""),
           grade, str(a.score)]

    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock = path + ".lock"
    try:
        token = acquire(lock)
    except TimeoutError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    export_err = None
    try:
        text = ""
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                text = f.read()
        text = upsert(text, row)
        atomic_write(path, text)
        try:
            import ledger_export
        except ImportError:
            print("warning: ledger_export not available; csv/docx export skipped", file=sys.stderr)
        else:
            stem = os.path.splitext(path)[0]
            try:
                headers, rows = ledger_export.parse_ledger(text)
                ledger_export.write_csv(headers, rows, stem + ".csv")
                ledger_export.write_docx(headers, rows, stem + ".docx", TITLE)
            except (OSError, UnicodeError) as e:  # e.g. csv/docx held open in Excel/WPS
                export_err = e
    finally:
        release(lock, token)

    print(path)
    print(mask(key) if key != NO_KEY else NO_KEY)
    print(grade)
    if export_err:
        print(f"error: ledger md was updated, but csv/docx export failed ({type(export_err).__name__}); close any program "
              "holding those files and re-run", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
