"""
test_driver.py -- Defect 1 proof: a non-zero claude exit must never be silent.

The original retracted result came from an error that left no trace, and the
real failing run showed a blank "failed:" because claude reported the error on
stdout while the log only printed stderr. These tests monkeypatch
subprocess.run (no real CLI, no quota) to assert the driver surfaces BOTH
streams on failure and stays quiet on success.

Run: python harness/test_driver.py   # plain-text [OK]/[ERR], non-zero on fail.
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import driver as drv
from driver import ClaudeCodeDriver, build_child_env, ENV_BASE_URL, ENV_AUTH_TOKEN, DEFAULT_PERMISSION_ARGS

# The genuine subprocess.run, captured before any monkeypatch (modules are
# singletons, so patching drv.subprocess.run would otherwise clobber this too).
_REAL_RUN = subprocess.run


def _ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class _FakeProc:
    def __init__(self, rc: int, out: str, err: str):
        self.returncode, self.stdout, self.stderr = rc, out, err


def _run_with(fake_run):
    """Run ClaudeCodeDriver.run with subprocess.run swapped, capturing stdout."""
    orig = drv.subprocess.run
    drv.subprocess.run = fake_run
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            res = ClaudeCodeDriver().run("prompt", cwd=".")
    finally:
        drv.subprocess.run = orig
    return res, buf.getvalue()


RESULTS: list[tuple[bool, str]] = []
def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((cond, name))
    tag = "[OK]" if cond else "[ERR]"
    print(f"{tag} {name}" + (f"  -- {detail}" if (detail and not cond) else ""))


# 1) Error reported on STDOUT (the real 401 shape) must appear in the log.
res, log = _run_with(lambda *a, **k: _FakeProc(1, "Failed to authenticate. API Error: 401 Invalid authentication credentials", ""))
check("stdout-error: rc surfaced (not ok)", not res.ok and res.returncode == 1)
check("stdout-error: RunResult carries stdout", "401" in res.stdout)
check("stdout-error: log shows [ERR] stdout with the message", "[ERR] stdout:" in log and "401" in log, log)
check("stdout-error: empty stderr shown as <empty>", "stderr: <empty>" in log, log)

# 2) Error reported on STDERR must also appear.
res, log = _run_with(lambda *a, **k: _FakeProc(2, "", "boom on stderr"))
check("stderr-error: log shows [ERR] stderr with the message", "[ERR] stderr: boom on stderr" in log, log)

# 3) Success path stays quiet (no stream dump) and reports [OK].
res, log = _run_with(lambda *a, **k: _FakeProc(0, "all good\nDONE_TASK_COMPLETE", ""))
check("success: ok and rc0", res.ok and res.returncode == 0)
check("success: log says [OK], no [ERR] dump", "[OK] claude exited rc=0" in log and "[ERR]" not in log, log)

# 4) FileNotFound branch still yields rc=127 (genuine missing CLI).
def _raise_fnf(*a, **k):
    raise FileNotFoundError()
res, _ = _run_with(_raise_fnf)
check("missing-CLI: rc=127", res.returncode == 127 and not res.ok)


# 5) Endpoint assertion: an inherited localhost/Ollama redirect must be stripped
#    from the child env even when the parent env sets it (the real 401 cause).
_saved = {k: os.environ.get(k) for k in (ENV_BASE_URL, ENV_AUTH_TOKEN)}
try:
    os.environ[ENV_BASE_URL] = "http://localhost:11434"
    os.environ[ENV_AUTH_TOKEN] = "ollama"

    env, warns = build_child_env()  # default: strip
    joined = " ".join(warns)
    check("default: base URL removed from child env", ENV_BASE_URL not in env, str(env.get(ENV_BASE_URL)))
    check("default: auth token removed from child env", ENV_AUTH_TOKEN not in env)
    check("default: [WARN] names the stripped base URL", "[WARN] stripped non-Anthropic base URL http://localhost:11434" in joined, joined)
    check("default: token value never logged in full (masked)", "ollama" not in joined, joined)

    # The driver instance must carry the stripped env.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        d = ClaudeCodeDriver()
    check("driver.env has redirect stripped", ENV_BASE_URL not in d.env and ENV_AUTH_TOKEN not in d.env)

    # Opt-in: allow_custom_base_url keeps the vars but still warns (never silent).
    env2, warns2 = build_child_env(allow_custom_base_url=True)
    check("allow_custom: base URL retained", env2.get(ENV_BASE_URL) == "http://localhost:11434")
    check("allow_custom: still [WARN]s about custom base URL", any("custom base URL" in w for w in warns2), str(warns2))
finally:
    for k, v in _saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# 6) UTF-8 capture: a stray non-cp1252 byte from the session must not crash the
#    reader thread or lose output (the cp1252 'charmap' decode bug).
# 6a) The driver must pass the decode kwargs -- and the permission flags into cmd.
_captured: dict = {}
_captured_cmd: list = []
def _capture_run(cmd, **kwargs):
    _captured.update(kwargs)
    _captured_cmd[:] = cmd
    return _FakeProc(0, "ok", "")
_run_with(_capture_run)
check("driver passes encoding=utf-8", _captured.get("encoding") == "utf-8", str(_captured.get("encoding")))
check("driver passes errors=replace", _captured.get("errors") == "replace", str(_captured.get("errors")))
# Permission model: acceptEdits must reach the actual command (queued-edits bug).
check("driver passes --permission-mode acceptEdits", "--permission-mode" in _captured_cmd and "acceptEdits" in _captured_cmd, str(_captured_cmd))
check("driver no longer uses --dangerously-skip-permissions", "--dangerously-skip-permissions" not in _captured_cmd, str(_captured_cmd))
check("default permission args are acceptEdits", DEFAULT_PERMISSION_ARGS == ["--permission-mode", "acceptEdits"], str(_captured_cmd))
# Arg placement: control flags must precede -p so the prompt can't swallow them.
check("permission flags precede -p in argv",
      "--permission-mode" in _captured_cmd and "-p" in _captured_cmd
      and _captured_cmd.index("--permission-mode") < _captured_cmd.index("-p"), str(_captured_cmd))

# 6b) End-to-end through the driver: a real child emits a lone invalid 0x8f plus
#     valid UTF-8 (em-dash, smart quotes). Using the driver's own decode kwargs,
#     capture must succeed -- replacement char for the bad byte, real chars kept.
_BAD_BYTE_SCRIPT = (
    r"import sys; sys.stdout.buffer.write("
    r"b'start \x8f mid \xe2\x80\x94 \xe2\x80\x9cq\xe2\x80\x9d done')"
)
def _bad_byte_run(cmd, **kwargs):
    passthru = {k: kwargs[k] for k in ("capture_output", "text", "encoding", "errors") if k in kwargs}
    return _REAL_RUN([sys.executable, "-c", _BAD_BYTE_SCRIPT], **passthru)

_raised = None
_res = None
try:
    _res, _ = _run_with(_bad_byte_run)
except Exception as e:  # a decode crash would land here
    _raised = e
check("bad bytes captured WITHOUT raising", _raised is None, repr(_raised))
check("bad-byte run reports ok rc0", _res is not None and _res.ok)
check("valid UTF-8 decoded (em-dash + smart quotes)", _res is not None and "—" in _res.stdout and "“" in _res.stdout)
check("invalid 0x8f -> replacement char U+FFFD", _res is not None and "�" in _res.stdout)
check("surrounding ascii intact", _res is not None and "start" in _res.stdout and "done" in _res.stdout)


def main() -> int:
    failures = sum(1 for ok, _ in RESULTS if not ok)
    total = len(RESULTS)
    verdict = "[OK]" if failures == 0 else "[ERR]"
    print(f"[{_ts()}] {verdict} driver: {total - failures}/{total} passed")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
