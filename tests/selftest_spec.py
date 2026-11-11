#!/usr/bin/env python3
"""Selftests for spec_checks.py (fixture mode) and spec_audit.py (live mock modes).

  python selftest_spec.py            evaluate tests/fixtures/sample_spec.json (minus its "spec" block)
                                     through scripts/spec_checks.py; expect score 89 and the frozen
                                     per-item statuses. spec_checks.py missing -> exit 2.
  python selftest_spec.py --live-mock  start tests/mock_proto_spec.py in "good" mode and run
                                     scripts/spec_audit.py against it; expect 12 pass, items
                                     7/8/12 partial, score 92 (evidence-honest ceiling).
  python selftest_spec.py --bad        same but against the "bad" mock; expect >= 3 of items
                                     4/5/6/10/12 fail or partial and score < 100.
  --keep                              keep tests/_out/test-dev/spec_*.json (default: delete on pass)
  --timeout N                         seconds allowed for one spec_audit run (default 600)

Exit codes: 0 all selected checks pass, 1 assertion failure, 2 target script not ready (Wave 2).
Only tests/_out/test-dev/ is written. The child audit always gets the fake LLM_API_KEY=test-key.
"""
import argparse
import importlib.util
import inspect
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
S_DIR = os.path.dirname(HERE)
SCRIPTS = os.path.join(S_DIR, "scripts")
FIXTURE = os.path.join(HERE, "fixtures", "sample_spec.json")
PROFILE = os.path.join(S_DIR, "profiles", "kimi-k3.1-pro.json")
OUT_DIR = os.path.join(HERE, "_out", "test-dev")
SPEC_CHECKS = os.path.join(SCRIPTS, "spec_checks.py")
SPEC_AUDIT = os.path.join(SCRIPTS, "spec_audit.py")
GOOD_OUT = os.path.join(OUT_DIR, "spec_good.json")
BAD_OUT = os.path.join(OUT_DIR, "spec_bad.json")
FAKE_KEY = "test-key"
MODEL = "kimi-k3.1-pro"

# Frozen fixture expectation: pass items 1-6, 9-11, 13-14; partial 7, 8, 12, 15 (weights -> 89).
EXPECTED_STATUS = {1: "pass", 2: "pass", 3: "pass", 4: "pass", 5: "pass", 6: "pass",
                   7: "partial", 8: "partial", 9: "pass", 10: "pass", 11: "pass",
                   12: "partial", 13: "pass", 14: "pass", 15: "partial"}
BAD_ITEM_IDS = (4, 5, 6, 10, 12)
# Evidence-honest good-mock ceiling: 100MB video (8), full-length timeout survival (7) and
# the 58-minute cache read-back (12) are never executed by the standard tier, so by the
# frozen rules those items can only be "partial". Weights 4/3/8 -> (100-7.5) -> round 92.
GOOD_PARTIAL_IDS = (7, 8, 12)
GOOD_SCORE = 92
CACHE_WAIT_S = 2  # only the 1h TTL read-back waits; keep the mock run short

if HERE not in sys.path:
    sys.path.insert(0, HERE)

import mock_proto_spec  # noqa: E402  (local test helper)


def _say(text):
    print(text, flush=True)


def _load_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _tail(text, limit=2500):
    text = (text or "").strip()
    return text if len(text) <= limit else "..." + text[-limit:]


# ------------------------------------------------------------------ fixture mode
def _load_spec_checks():
    """(module, None) or (None, reason)."""
    if not os.path.isfile(SPEC_CHECKS):
        return None, "%s 不存在" % SPEC_CHECKS
    if SCRIPTS not in sys.path:
        sys.path.insert(0, SCRIPTS)
    try:
        spec = importlib.util.spec_from_file_location("spec_checks_under_test", SPEC_CHECKS)
        if spec is None or spec.loader is None:
            return None, "无法为 %s 创建 import spec" % SPEC_CHECKS
        module = importlib.util.module_from_spec(spec)
        sys.modules["spec_checks_under_test"] = module
        spec.loader.exec_module(module)
        return module, None
    except Exception as exc:
        return None, "导入 spec_checks.py 失败：%s: %s" % (type(exc).__name__, exc)


def _call_evaluate(module, payload):
    """Call spec_checks.evaluate with the fixture payload, adapting to its signature."""
    fn = getattr(module, "evaluate", None)
    if not callable(fn):
        raise RuntimeError("spec_checks.py 未提供可调用的 evaluate()")
    signature = inspect.signature(fn)
    params = list(signature.parameters.values())
    positional = [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    keyword_required = [p for p in params if p.kind == p.KEYWORD_ONLY and p.default is p.empty]
    needs_profile = len(positional) >= 2 or any(
        p.name.lower() in ("profile", "prof", "prof_data") for p in keyword_required)
    profile = _load_json(PROFILE) if needs_profile else None

    if len(positional) >= 2:
        return fn(payload, profile)
    kwargs = {}
    for param in keyword_required:
        if param.name.lower() in ("profile", "prof", "prof_data"):
            kwargs[param.name] = profile
        else:
            raise RuntimeError("spec_checks.evaluate() 有未知的必填关键字参数 %r" % param.name)
    return fn(payload, **kwargs)


def _extract_spec(result):
    if isinstance(result, dict):
        nested = result.get("spec")
        if isinstance(nested, dict):
            return nested
        if "score" in result or "items" in result:
            return result
    if isinstance(result, (list, tuple)):
        for item in result:
            try:
                return _extract_spec(item)
            except RuntimeError:
                continue
    raise RuntimeError("spec_checks.evaluate() 的返回值中找不到 spec{score, items}")


def _status_map(items):
    out = {}
    for pos, item in enumerate(items, 1):
        if not isinstance(item, dict):
            continue
        ident = item.get("id", pos)
        if isinstance(ident, str) and ident.isdigit():
            ident = int(ident)
        if isinstance(ident, int):
            out[ident] = item.get("status")
    if len(items) == 15:  # fallback when items carry no usable id
        for pos, item in enumerate(items, 1):
            if isinstance(item, dict):
                out.setdefault(pos, item.get("status"))
    return out


def fixture_mode():
    module, reason = _load_spec_checks()
    if module is None:
        _say("spec_checks.py not ready (Wave 2)")
        _say("原因：%s" % reason)
        _say("请等 Wave 2 的 spec_checks.py 就绪后再跑 fixture 自测。")
        return 2

    try:
        fixture = _load_json(FIXTURE)
    except Exception as exc:
        _say("FAIL fixture：读取 %s 失败：%s: %s" % (FIXTURE, type(exc).__name__, exc))
        return 1
    payload = {key: value for key, value in fixture.items() if key != "spec"}

    try:
        spec = _extract_spec(_call_evaluate(module, payload))
    except Exception as exc:
        _say("FAIL fixture：调用 spec_checks.evaluate 失败：%s: %s" % (type(exc).__name__, exc))
        return 1

    fails = []
    items = spec.get("items")
    if not isinstance(items, list):
        fails.append("返回值缺少 items 列表")
        items = []
    if len(items) != 15:
        fails.append("规范项数量 %d（期望 15）" % len(items))
    score = spec.get("score")
    if score != 89:
        fails.append("score=%r（期望 89）" % (score,))
    statuses = _status_map(items)
    for item_id, expected in EXPECTED_STATUS.items():
        got = statuses.get(item_id)
        if got != expected:
            fails.append("第 %d 项状态 %r（期望 %s）" % (item_id, got, expected))
    if score == 89 and not fails:
        _say("PASS fixture spec_checks.evaluate：score=89，15 项状态与 fixture 一致")
        return 0
    _say("FAIL fixture spec_checks.evaluate：score=%r，%d 项断言未通过" % (score, len(fails)))
    for fail in fails:
        _say("  - " + fail)
    return 1


# ------------------------------------------------------------------ live modes
def _audit_ready():
    if os.path.isfile(SPEC_AUDIT):
        return True
    _say("spec_audit.py not ready (pending Wave 2)")
    _say("缺少 %s；请等 Wave 2 的 spec_audit.py 就绪后再跑 --live-mock / --bad。" % SPEC_AUDIT)
    return False


def _child_env():
    """Child env for spec_audit: fake key only, localhost proxy bypass (Windows registry proxy)."""
    env = {}
    for name in os.environ.keys():  # skip LLM_API_KEY without fetching its value
        if name == "LLM_API_KEY":
            continue
        env[name] = os.environ[name]
    for name in list(env):
        if name.lower() in ("http_proxy", "https_proxy", "all_proxy"):
            env.pop(name, None)
    env.update({
        "LLM_API_KEY": FAKE_KEY,
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "http_proxy": "http://127.0.0.1:9",
        "no_proxy": "127.0.0.1,localhost",
        "NO_PROXY": "127.0.0.1,localhost",
    })
    return env


def _run_audit(mode, out_path, timeout):
    """Start the mock, run spec_audit.py against it, always stop the mock. -> result dict."""
    os.makedirs(OUT_DIR, exist_ok=True)
    if os.path.isfile(out_path):
        os.remove(out_path)
    server, base_url, _thread = mock_proto_spec.start(mode)
    _say("mock(%s) READY %s" % (mode, base_url))
    command = [sys.executable, SPEC_AUDIT, "--profile", MODEL, "--base", base_url,
               "--model", MODEL, "--cache-wait-s", str(CACHE_WAIT_S), "--out", out_path]
    started = time.time()
    try:
        proc = subprocess.run(command, cwd=S_DIR, env=_child_env(), capture_output=True, timeout=timeout)
        return {"rc": proc.returncode,
                "stdout": proc.stdout.decode("utf-8", "replace"),
                "stderr": proc.stderr.decode("utf-8", "replace"),
                "elapsed": time.time() - started,
                "out": out_path,
                "command": command}
    finally:
        mock_proto_spec.stop(server)


def _load_audit_spec(path):
    if not os.path.isfile(path):
        return None, "未生成 --out 文件：%s" % path
    try:
        doc = _load_json(path)
    except Exception as exc:
        return None, "--out 文件不是有效 JSON：%s: %s" % (type(exc).__name__, exc)
    spec = doc.get("spec") if isinstance(doc, dict) else None
    if not isinstance(spec, dict):
        return None, "JSON 中缺少 spec 对象"
    return spec, None


def _items(spec):
    items = spec.get("items")
    return items if isinstance(items, list) else []


def _report_failure(mode, result, detail, spec, keep):
    _say("FAIL %s：%s" % (mode, detail))
    if result is not None:
        _say("  命令：%s" % " ".join(result["command"]))
        _say("  退出码=%s，耗时 %.1fs" % (result["rc"], result["elapsed"]))
        if result["stderr"].strip():
            _say("  stderr：\n" + _tail(result["stderr"]))
        elif result["stdout"].strip():
            _say("  stdout：\n" + _tail(result["stdout"]))
    if spec is not None:
        _say("  实际 score=%r，items=%d" % (spec.get("score"), len(_items(spec))))
        for item in _items(spec):
            if isinstance(item, dict) and item.get("status") != "pass":
                _say("    %s: %s (%s)" % (item.get("id"), item.get("status"), item.get("key")))
    if result is not None and os.path.isfile(result["out"]) and not keep:
        _say("  调试输出保留：%s" % result["out"])


def _finish_output(path, keep, ok):
    if ok and not keep:
        try:
            os.remove(path)
            return
        except OSError:
            pass
    if os.path.isfile(path):
        _say("输出文件：%s" % path)


def check_good(args):
    try:
        result = _run_audit("good", GOOD_OUT, args.timeout)
    except subprocess.TimeoutExpired as exc:
        _say("FAIL live-mock：spec_audit 超过 %ss 未完成（%s）" % (args.timeout, exc))
        return False
    except Exception as exc:
        _say("FAIL live-mock：启动 mock 或审计进程失败：%s: %s" % (type(exc).__name__, exc))
        return False

    spec, reason = _load_audit_spec(GOOD_OUT)
    if spec is None:
        _report_failure("live-mock", result, reason, None, args.keep)
        return False

    fails = []
    if result["rc"] != 0:
        fails.append("spec_audit 退出码 %s（期望 0）" % result["rc"])
    if spec.get("score") != GOOD_SCORE:
        fails.append("score=%r（期望 %d）" % (spec.get("score"), GOOD_SCORE))
    items = _items(spec)
    if len(items) != 15:
        fails.append("规范项数量 %d（期望 15）" % len(items))
    statuses = _status_map(items)
    for item_id in range(1, 16):
        expected = "partial" if item_id in GOOD_PARTIAL_IDS else "pass"
        got = statuses.get(item_id)
        if got != expected:
            fails.append("第 %d 项状态 %r（期望 %s）" % (item_id, got, expected))

    if fails:
        for fail in fails:
            _say("  - " + fail)
        _report_failure("live-mock", result,
                        "good mock 未得到 12 pass + 7/8/12 partial、score=%d，失败 %d 项"
                        % (GOOD_SCORE, len(fails)), spec, args.keep)
        return False
    _say("PASS live-mock：12 pass + 第 7/8/12 项 partial，score=%d（%.1fs）"
         % (GOOD_SCORE, result["elapsed"]))
    _finish_output(GOOD_OUT, args.keep, True)
    return True


def check_bad(args):
    try:
        result = _run_audit("bad", BAD_OUT, args.timeout)
    except subprocess.TimeoutExpired as exc:
        _say("FAIL bad-mock：spec_audit 超过 %ss 未完成（%s）" % (args.timeout, exc))
        return False
    except Exception as exc:
        _say("FAIL bad-mock：启动 mock 或审计进程失败：%s: %s" % (type(exc).__name__, exc))
        return False

    spec, reason = _load_audit_spec(BAD_OUT)
    if spec is None:
        _report_failure("bad-mock", result, reason, None, args.keep)
        return False

    fails = []
    score = spec.get("score")
    if not isinstance(score, (int, float)) or isinstance(score, bool) or score >= 100:
        fails.append("score=%r（期望数值且 < 100）" % (score,))
    statuses = _status_map(_items(spec))
    hit = [(item_id, statuses.get(item_id)) for item_id in BAD_ITEM_IDS
           if statuses.get(item_id) in ("fail", "partial")]
    if len(hit) < 3:
        fails.append("第 4/5/6/10/12 项中仅 %d 项 fail/partial（期望 >= 3）：%s"
                     % (len(hit), ", ".join("%s=%s" % pair for pair in hit) or "无"))

    if fails:
        for fail in fails:
            _say("  - " + fail)
        _report_failure("bad-mock", result, "bad mock 未被识别为可疑渠道，失败 %d 项" % len(fails),
                        spec, args.keep)
        return False
    detail = ", ".join("第 %d 项=%s" % pair for pair in hit)
    _say("PASS bad-mock：score=%r < 100，%s 共 %d 项 fail/partial（%.1fs）"
         % (score, detail, len(hit), result["elapsed"]))
    _finish_output(BAD_OUT, args.keep, True)
    return True


# ------------------------------------------------------------------ main
def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="spec_checks/spec_audit selftests (fixture + mock modes)")
    parser.add_argument("--live-mock", action="store_true",
                        help="good mock -> expect 12 pass + items 7/8/12 partial, score 92")
    parser.add_argument("--bad", action="store_true", help="bad mock -> expect >=3 suspect items and score<100")
    parser.add_argument("--keep", action="store_true", help="keep tests/_out/test-dev/spec_*.json")
    parser.add_argument("--timeout", type=int, default=600, help="seconds per spec_audit run (default 600)")
    args = parser.parse_args()

    if not (args.live_mock or args.bad):
        return fixture_mode()
    if not _audit_ready():
        return 2
    ok = True
    if args.live_mock:
        ok = check_good(args) and ok
    if args.bad:
        ok = check_bad(args) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
