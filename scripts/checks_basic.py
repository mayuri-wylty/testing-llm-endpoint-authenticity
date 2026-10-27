"""Two small checks: instruction following (module-level NAME/run) and error format (ERRORS)."""
import json, re
from types import SimpleNamespace

NAME = "instruction following"


def run(audit):
    r, txt = audit.ask("今天吃什么好？", system="Reply in English only and start every reply with 'ACK:'.")
    if r.status != 200:
        txt = ""
    ok = txt.lstrip().startswith("ACK:") and not re.search(r"[一-鿿]", txt)
    audit.add(NAME, 1.0 if ok else 0.0, txt[:60].replace("\n", " "))


def run_errors(audit):
    ad, model = audit.adapter, audit.a.model
    p1, b1 = ad.build([], max_tokens=None)
    p2, b2 = ad.build([{"role": "user", "content": "hi"}], max_tokens=None, model=model + "-nonexistent-xyz")
    s1, _, d1, _ = audit.post(p1, b1)
    s2, _, d2, _ = audit.post(p2, b2)

    def is_err(s, d):
        try:
            return s >= 400 and "error" in json.loads(d)
        except Exception:
            return False
    ok = [is_err(s1, d1), is_err(s2, d2)]
    audit.add("error format", sum(ok) / 2, "empty-messages HTTP %s, bad-model HTTP %s" % (s1, s2))
    m = re.search(r"under group ([^\s]+)", str(d2))
    if m:
        audit.notes.append("relay channel group name: %s" % m.group(1))


ERRORS = SimpleNamespace(NAME="error format", run=run_errors)
