"""
test_run_spike_abort.py -- Defect 2 proof: an errored resume session must abort,
not fall through to score() and print a false CONTINUED.

Two cases, no real CLI:
  - injected erroring driver  -> run_interrupt returns None, logs [ABORT] with
    both streams, and never writes a report.json (scoring was skipped).
  - real FakeDriver (continues) -> still scores cleanly (gating didn't break the
    happy path).

Uses a temp results dir and temp workbench so the real results/ stays clean.
Run: python harness/test_run_spike_abort.py   # plain-text [OK]/[ERR].
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_spike
from driver import BaseDriver, RunResult


def _ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class _ErroringDriver(BaseDriver):
    def run(self, prompt: str, cwd: str, timeout: int = 900) -> RunResult:
        return RunResult(False, "partial junk on stdout", "boom on stderr", 1, 0.1)


RESULTS: list[tuple[bool, str]] = []
def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((cond, name))
    tag = "[OK]" if cond else "[ERR]"
    print(f"{tag} {name}" + (f"  -- {detail}" if (detail and not cond) else ""))


def _with_temp_results(fn):
    """Redirect RESULTS_DIR to a temp dir for the duration of fn()."""
    orig_results = run_spike.RESULTS_DIR
    tmp_results = Path(tempfile.mkdtemp(prefix="spike_results_"))
    run_spike.RESULTS_DIR = tmp_results
    try:
        return fn(tmp_results)
    finally:
        run_spike.RESULTS_DIR = orig_results


# --- Case 1: errored session aborts, no score, no report.json ------------------
def _abort_case(tmp_results: Path):
    orig_make = run_spike.make_driver
    run_spike.make_driver = lambda name: _ErroringDriver()
    base = Path(tempfile.mkdtemp(prefix="spike_wb_"))
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            report = run_spike.run_interrupt("aborttest", base, stop_after=2)
    finally:
        run_spike.make_driver = orig_make
    log = buf.getvalue()
    check("errored session -> run_interrupt returns None", report is None)
    check("errored session -> [ABORT] logged", "[ABORT]" in log, log[-300:])
    check("errored session -> stderr surfaced", "boom on stderr" in log, log[-300:])
    check("errored session -> stdout surfaced", "partial junk on stdout" in log, log[-300:])
    check("errored session -> NOT scored (no [RESULT])", "[RESULT]" not in log)
    check("errored session -> no report.json written",
          not (tmp_results / "aborttest_report.json").exists())
    # Transcript persistence: even an errored (or rc=0 no-op) session is captured.
    sess = tmp_results / "resume_session.txt"
    check("errored session -> transcript persisted anyway", sess.exists())
    if sess.exists():
        text = sess.read_text(encoding="utf-8")
        check("transcript captures stdout of the errored run", "partial junk on stdout" in text, text[:200])
        check("transcript captures stderr of the errored run", "boom on stderr" in text, text[:200])

_with_temp_results(_abort_case)


# --- Case 2: happy path (FakeDriver continues) still scores ---------------------
def _happy_case(tmp_results: Path):
    base = Path(tempfile.mkdtemp(prefix="spike_wb_"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        report = run_spike.run_interrupt("fake", base, stop_after=2)
    log = buf.getvalue()
    check("happy path -> report returned", report is not None)
    check("happy path -> scored CONTINUED + done",
          report is not None and report.continued and report.end_state_ok)
    check("happy path -> [RESULT] logged", "[RESULT]" in log)
    check("happy path -> report.json written", (tmp_results / "fake_report.json").exists())
    check("happy path -> resume transcript persisted", (tmp_results / "resume_session.txt").exists())

_with_temp_results(_happy_case)


# --- Case 3: baseline phase also persists its transcript -----------------------
def _baseline_case(tmp_results: Path):
    base = Path(tempfile.mkdtemp(prefix="spike_wb_"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ok = run_spike.run_baseline("fake", base)
    check("baseline -> reached done-state (fake)", ok)
    check("baseline -> transcript persisted", (tmp_results / "baseline_session.txt").exists())

_with_temp_results(_baseline_case)


def main() -> int:
    failures = sum(1 for ok, _ in RESULTS if not ok)
    total = len(RESULTS)
    verdict = "[OK]" if failures == 0 else "[ERR]"
    print(f"[{_ts()}] {verdict} run_spike abort-gate: {total - failures}/{total} passed")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
