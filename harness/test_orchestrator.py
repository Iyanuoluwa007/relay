"""
test_orchestrator.py -- Proof for run_until_done (DESIGN.md amendment 2 + sec.5).

HEADLINE (amendment 2): from the known 2/5 partial state, the loop drives the
task to 5/5 across multiple simulated session-limit interruptions, scored by the
EXISTING scorer.py, with is_doubled clean throughout. Plus the closed exit set
(COMPLETED/FATAL/GAVE_UP), UNKNOWN routed through recovery, completion confirmed
from the tree (not stdout), and NO real sleeping (injected sleep recorder).

Uses real temp git repos and task_spec, in the spirit of the spike. No CLI.
Run: python harness/test_orchestrator.py   # plain-text [OK]/[ERR].
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

HARNESS = Path(__file__).resolve().parent
sys.path.insert(0, str(HARNESS))
sys.path.insert(0, str(HARNESS.parent / "task"))

import task_spec
from driver import BaseDriver, RunResult
from checkpoint_builder import build_checkpoint, TaskDef
from checkpoint import compose_resume_prompt
from waiter import WaitPolicy
from scorer import score
import orchestrator
from orchestrator import run_until_done, COMPLETED, FATAL, GAVE_UP


def _ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _git(args, cwd) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout.strip()


def _seed_2of5() -> tuple[Path, str]:
    """Seed a repo and complete alpha+bravo (the known 2/5 partial state)."""
    wd = Path(tempfile.mkdtemp(prefix="orch_"))
    task_spec.seed_files(wd)
    _git(["init", "-q"], wd)
    _git(["config", "user.email", "t@local"], wd)
    _git(["config", "user.name", "t"], wd)
    _git(["add", "-A"], wd)
    _git(["commit", "-q", "-m", "seed"], wd)
    sha0 = _git(["rev-parse", "HEAD"], wd)
    task_spec.apply_transform(wd / "alpha.py")
    task_spec.apply_transform(wd / "bravo.py")
    _git(["add", "-A"], wd)
    _git(["commit", "-q", "-m", "2 of 5"], wd)
    return wd, sha0


_EDIT_CLAUSE = (
    f"replace the literal line '{task_spec.TODO_MARKER}' with "
    f"'{task_spec.DONE_MARKER}' followed by '{task_spec.BANNER}'"
)


def _make_task(wd: Path) -> TaskDef:
    return TaskDef(
        objective=f"Edit every target file on disk so each contains the header: {_EDIT_CLAUSE}.",
        working_dir=str(wd),
        units=list(task_spec.TARGET_FILES),
        instructions=f"{_EDIT_CLAUSE}.",
        decisions=["Do not modify already-edited files; the transform is not idempotent."],
        notes="Each file gets exactly one header banner. Doubling = a bug.",
    )


_is_unit_done = lambda w, u: task_spec.is_done(w / u)
_verify = lambda w: task_spec.all_done(w)


# --- Scripted fake drivers (no CLI). Each edits via task_spec on the real tree. -
def _pending(wd: Path) -> list[str]:
    return [f for f in task_spec.TARGET_FILES if not task_spec.is_done(wd / f)]


class _LimitThenFinish(BaseDriver):
    """Edits ONE pending file per call; emits a session-limit interruption the
    first `interrupts` times, then finishes the rest and completes."""
    def __init__(self, interrupts: int):
        self.interrupts = interrupts
        self.calls = 0

    def run(self, prompt: str, cwd: str, timeout: int = 900) -> RunResult:
        self.calls += 1
        wd = Path(cwd)
        pend = _pending(wd)
        if not pend:
            return RunResult(True, "[fake] nothing left\nDONE_TASK_COMPLETE\n", "", 0, 0.1)
        task_spec.apply_transform(wd / pend[0])  # one unit of progress
        if self.interrupts > 0:
            self.interrupts -= 1
            return RunResult(False, f"[fake] edited {pend[0]}\nClaude usage limit reached. Resets at 5pm.\n", "", 1, 0.1)
        for f in _pending(wd):  # no more interruptions: finish the rest
            task_spec.apply_transform(wd / f)
        return RunResult(True, "[fake] finished\nDONE_TASK_COMPLETE\n", "", 0, 0.1)


class _Fatal(BaseDriver):
    def run(self, prompt, cwd, timeout=900):
        return RunResult(False, "claude CLI not found on PATH", "", 127, 0.1)


class _StuckLimit(BaseDriver):
    """Always interrupts, makes NO progress -> must hit the attempt cap."""
    def run(self, prompt, cwd, timeout=900):
        return RunResult(False, "Claude usage limit reached.", "", 1, 0.1)


class _UnknownThenFinish(BaseDriver):
    """First call: generic nonzero (UNKNOWN) after one edit; then completes.
    Proves UNKNOWN routes through recovery, not a stop."""
    def __init__(self):
        self.calls = 0

    def run(self, prompt, cwd, timeout=900):
        self.calls += 1
        wd = Path(cwd)
        pend = _pending(wd)
        if pend:
            task_spec.apply_transform(wd / pend[0])
        if self.calls == 1:
            return RunResult(False, "Traceback: transient glitch", "", 1, 0.1)
        for f in _pending(wd):
            task_spec.apply_transform(wd / f)
        return RunResult(True, "[fake] done\nDONE_TASK_COMPLETE\n", "", 0, 0.1)


class _FalseCompleteThenFinish(BaseDriver):
    """First call: emits DONE-token + rc0 but edits NOTHING (tree not done).
    Orchestrator must NOT call it complete; recover and finish on call 2."""
    def __init__(self):
        self.calls = 0

    def run(self, prompt, cwd, timeout=900):
        self.calls += 1
        if self.calls == 1:
            return RunResult(True, "I think I'm done.\nDONE_TASK_COMPLETE\n", "", 0, 0.1)
        wd = Path(cwd)
        for f in _pending(wd):
            task_spec.apply_transform(wd / f)
        return RunResult(True, "[fake] really done\nDONE_TASK_COMPLETE\n", "", 0, 0.1)


RESULTS: list[tuple[bool, str]] = []
def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((cond, name))
    tag = "[OK]" if cond else "[ERR]"
    print(f"{tag} {name}" + (f"  -- {detail}" if (detail and not cond) else ""))


def _run(driver, policy=None, interrupts_label=""):
    """Set up a fresh 2/5 repo, build the initial resume prompt, run the loop with
    an injected (no-op) sleep recorder and fixed clock. Returns (result, wd, sleeps)."""
    wd, sha0 = _seed_2of5()
    task = _make_task(wd)
    cp, diff = build_checkpoint(task, sha0, _is_unit_done)
    initial_prompt = compose_resume_prompt(cp, diff)
    sleeps: list[float] = []
    res = run_until_done(
        task, driver, start_sha=sha0,
        is_unit_done=_is_unit_done, verify=_verify,
        initial_prompt=initial_prompt,
        policy=policy or WaitPolicy(max_attempts=10),
        sleep_fn=lambda s: sleeps.append(s),  # NEVER really sleeps
    )
    return res, wd, sleeps


# === HEADLINE: 2/5 -> 5/5 across 2 session-limit interruptions ================
result, wd, sleeps = _run(_LimitThenFinish(interrupts=2))
rep = score(wd, pre_interrupt_done=["alpha.py", "bravo.py"])
check("HEADLINE: loop result is COMPLETED", result.state == COMPLETED, result.state)
check("HEADLINE: reached 5/5 (end_state_ok)", rep.end_state_ok and rep.done_count == 5, rep.summary())
check("HEADLINE: is_doubled clean (no duplicated files)", rep.duplicated_files == [], str(rep.duplicated_files))
check("HEADLINE: no lost files (completed-list respected)", rep.lost_files == [], str(rep.lost_files))
check("HEADLINE: scorer verdict CONTINUED", rep.continued)
check("HEADLINE: took 3 attempts (2 interruptions + finish)", result.attempts == 3, str(result.attempts))
check("HEADLINE: NO real sleeping; 2 waits recorded", sleeps == [60, 120], str(sleeps))

# Survives MORE interruptions and still terminates clean (idempotency under churn).
result3, wd3, _ = _run(_LimitThenFinish(interrupts=3), policy=WaitPolicy(max_attempts=10))
rep3 = score(wd3, pre_interrupt_done=["alpha.py", "bravo.py"])
check("3 interruptions -> still COMPLETED 5/5, no doubling",
      result3.state == COMPLETED and rep3.end_state_ok and rep3.duplicated_files == [])


# === FATAL short-circuits: no retry, no wait ==================================
resF, wdF, sleepsF = _run(_Fatal())
check("FATAL: state FATAL", resF.state == FATAL, resF.state)
check("FATAL: stopped on first attempt", resF.attempts == 1, str(resF.attempts))
check("FATAL: never waited (no sleeps)", sleepsF == [], str(sleepsF))


# === GAVE_UP: attempt cap with no progress ====================================
resG, wdG, sleepsG = _run(_StuckLimit(), policy=WaitPolicy(max_attempts=3))
check("GAVE_UP: state GAVE_UP", resG.state == GAVE_UP, resG.state)
check("GAVE_UP: stopped at the attempt cap", resG.attempts == 3, str(resG.attempts))
check("GAVE_UP: waited only between attempts (2 sleeps)", len(sleepsG) == 2, str(sleepsG))


# === UNKNOWN routes through recovery (not a stop) =============================
resU, wdU, _ = _run(_UnknownThenFinish())
repU = score(wdU, pre_interrupt_done=["alpha.py", "bravo.py"])
check("UNKNOWN: recovered to COMPLETED 5/5", resU.state == COMPLETED and repU.end_state_ok)


# === Completion confirmed from the TREE, not stdout ===========================
resC, wdC, _ = _run(_FalseCompleteThenFinish())
check("false done-token NOT accepted; recovered then COMPLETED",
      resC.state == COMPLETED and resC.attempts == 2, f"{resC.state}/{resC.attempts}")


def main() -> int:
    failures = sum(1 for ok, _ in RESULTS if not ok)
    total = len(RESULTS)
    verdict = "[OK]" if failures == 0 else "[ERR]"
    print(f"[{_ts()}] {verdict} orchestrator: {total - failures}/{total} passed")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
