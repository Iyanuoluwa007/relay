"""
run_real_orchestrator.py -- THE JOIN: the proven orchestrator loop driving the
REAL Claude Code CLI across multiple cycles, with INJECTED interruptions.

The fakes proved control flow; the spike proved single-cycle real resume. This
joins them: orchestrator.run_until_done drives a REAL ClaudeCodeDriver across the
marker task from a 2/5 partial state to 5/5, where each cycle is a real
`claude -p` session that makes real edits, each checkpoint is built from REAL
accumulated git state, and the interruptions are INJECTED (we cannot summon a
real quota reset on demand -- same 'partial work, then kill' mechanism the spike
used). Live session-limit DETECTION against real CLI output is a separate,
additive step and does NOT block this run.

The injection lives in a DRIVER WRAPPER, so orchestrator.py stays exactly as
proven -- it is not modified for this experiment. Wrapper stack the orchestrator
drives:  TranscriptPersistingDriver( InjectedInterruptDriver( <real|fake> ) ).

Run it yourself (the assistant hits a nested-auth 401, so it cannot):
    # validate the wiring with no CLI, no quota (instant, deterministic):
    python harness/run_real_orchestrator.py --driver fake
    # the real join (after HANDOVER.md pre-flight: claude -p "reply OK" etc.):
    python harness/run_real_orchestrator.py --driver claude

Python 3.11+, standard library only.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

HARNESS = Path(__file__).resolve().parent
sys.path.insert(0, str(HARNESS))
sys.path.insert(0, str(HARNESS.parent / "task"))

import task_spec
import run_spike  # reuse _git / _init_project / RESULTS_DIR (the spike's git setup)
from driver import BaseDriver, RunResult, ClaudeCodeDriver
from checkpoint_builder import build_checkpoint, TaskDef
from checkpoint import compose_resume_prompt
from waiter import WaitPolicy
from scorer import score
from orchestrator import run_until_done, COMPLETED, FATAL, GAVE_UP


def _ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# The synthetic interruption appended after real partial work has landed. A known
# limit phrase the detector recognizes (SESSION_LIMIT/HIGH) with a SHORT reset
# hint so the waiter's reset-time path is exercised without a long real sleep.
INJECTED_LIMIT = "[INJECTED-INTERRUPT] Claude usage limit reached. Resets in 2 seconds."

# Marker the fake keys off to detect a single-file (interrupted) session prompt.
_ONE_FILE_MARKER = "EXACTLY ONE file"


def _done_count(wd: Path) -> int:
    return sum(1 for f in task_spec.TARGET_FILES if task_spec.is_done(wd / f))


def _single_file_prompt(wd: Path) -> str:
    """A tight, contradiction-free command to edit exactly ONE file and stop.

    The earlier approach APPENDED a scope note to the orchestrator's resume
    prompt, but that prompt says 'continue with any remaining files' -- real
    claude resolved the contradiction by doing all of them in one session, so no
    second cycle happened. This REPLACES the prompt for an injected cycle: it
    names only the next pending file, forbids the others, and never says
    'continue'. That genuinely constrains the real session to one file, forcing
    the orchestrator to re-checkpoint and run another real session.
    """
    pending = [f for f in task_spec.TARGET_FILES if not task_spec.is_done(wd / f)]
    target = pending[0]
    others = [f for f in pending if f != target]
    forbid = f" Leave {', '.join(others)} untouched." if others else ""
    return (
        f"You are resuming an interrupted task, and this session is itself "
        f"INTERRUPTED after one file: edit {_ONE_FILE_MARKER}, then STOP.\n"
        f"Edit {target} on disk now: replace the literal line "
        f"'{task_spec.TODO_MARKER}' with '{task_spec.DONE_MARKER}' followed by "
        f"'{task_spec.BANNER}'.{forbid}\n"
        f"Make that single edit to {target} on disk now, then STOP and end your "
        f"reply. Do NOT edit any other file in this session; do NOT continue."
    )


class InjectedInterruptDriver(BaseDriver):
    """Wraps an inner driver to inject deterministic interruptions that FORCE
    multiple real cycles.

    On the first `interrupts` cycles: run the inner session with a REPLACED
    single-file prompt (one real file of work), record the resulting done-count,
    then return a synthetic SESSION_LIMIT (rc=1 + INJECTED_LIMIT) -- the 'kill'
    after real partial progress landed on disk. On later cycles: pass the
    orchestrator's real resume prompt through unmodified so the session finishes
    and completes for real. `progress` records the done-count after each session
    so the caller can assert the count actually climbed (no finish-too-early).
    """

    def __init__(self, inner: BaseDriver, interrupts: int):
        self.inner = inner
        self.interrupts = interrupts
        self.cycle = 0
        self.progress: list[int] = []

    def run(self, prompt: str, cwd: str, timeout: int = 900) -> RunResult:
        self.cycle += 1
        wd = Path(cwd)
        total = len(task_spec.TARGET_FILES)
        if self.cycle <= self.interrupts:
            res = self.inner.run(_single_file_prompt(wd), cwd, timeout)
            done = _done_count(wd)
            self.progress.append(done)
            print(f"[{_ts()}] [INFO] injected interruption after cycle {self.cycle}: "
                  f"{done}/{total} done (inner rc={res.returncode})")
            combined = f"{res.stdout or ''}\n{INJECTED_LIMIT}\n"
            return RunResult(False, combined, res.stderr, 1, res.seconds)
        res = self.inner.run(prompt, cwd, timeout)
        self.progress.append(_done_count(wd))
        return res


class TranscriptPersistingDriver(BaseDriver):
    """Outermost wrapper: persist every cycle's session to results/, ALWAYS.

    Persisting the result the orchestrator actually sees (post-injection) means a
    no-op or misbehavior is visible per cycle, regardless of exit code (finding
    #7). The prompt head is included so we can confirm what the session was told.
    """

    def __init__(self, inner: BaseDriver, results_dir: Path, prefix: str):
        self.inner = inner
        self.dir = Path(results_dir)
        self.prefix = prefix
        self.cycle = 0

    def run(self, prompt: str, cwd: str, timeout: int = 900) -> RunResult:
        self.cycle += 1
        res = self.inner.run(prompt, cwd, timeout)
        self.dir.mkdir(exist_ok=True)
        path = self.dir / f"{self.prefix}_cycle{self.cycle}_session.txt"
        path.write_text("\n".join([
            f"cycle: {self.cycle}",
            f"captured_at: {_ts()}",
            f"returncode: {res.returncode}",
            f"ok: {res.ok}",
            f"seconds: {res.seconds:.1f}",
            "===== PROMPT (head) =====",
            prompt[:1500],
            "===== STDOUT =====",
            res.stdout or "",
            "===== STDERR =====",
            res.stderr or "",
        ]), encoding="utf-8")
        print(f"[{_ts()}] [INFO] cycle {self.cycle} transcript -> {path.name} "
              f"(rc={res.returncode}, {len(res.stdout or '')} stdout chars)")
        return res


class _ScopeFake(BaseDriver):
    """No-CLI stand-in for validating the wiring. Behaves as a COMPLIANT real
    session would: a single-file prompt (the _ONE_FILE_MARKER) -> edit ONE
    pending file and stop; the full resume prompt -> edit all remaining and emit
    the done-token. Edits the REAL tree via task_spec so git accumulates for real,
    just like the live path."""

    def run(self, prompt: str, cwd: str, timeout: int = 900) -> RunResult:
        wd = Path(cwd)
        pending = [f for f in task_spec.TARGET_FILES if not task_spec.is_done(wd / f)]
        if not pending:
            return RunResult(True, "[fake] nothing to do\nDONE_TASK_COMPLETE\n", "", 0, 0.05)
        if _ONE_FILE_MARKER in prompt:
            task_spec.apply_transform(wd / pending[0])
            return RunResult(True, f"[fake] edited {pending[0]} only and stopped\n", "", 0, 0.05)
        for f in pending:
            task_spec.apply_transform(wd / f)
        return RunResult(True, "[fake] edited all remaining\nDONE_TASK_COMPLETE\n", "", 0, 0.05)


class _GreedyFake(BaseDriver):
    """NEGATIVE CONTROL: ignores the single-file scope and edits ALL pending files
    in one session -- exactly the real-claude behavior that broke the first join
    attempt. Used to prove the multi-cycle assertion FAILS loudly (no false pass)."""

    def run(self, prompt: str, cwd: str, timeout: int = 900) -> RunResult:
        wd = Path(cwd)
        for f in [x for x in task_spec.TARGET_FILES if not task_spec.is_done(wd / x)]:
            task_spec.apply_transform(wd / f)
        return RunResult(True, "[greedy-fake] edited ALL pending\nDONE_TASK_COMPLETE\n", "", 0, 0.05)


# Imperative, literal, on-disk-now task content (the shape proven to make claude
# act, finding #8); markers render from task_spec so it stays spec-synced.
_EDIT_CLAUSE = (
    f"replace the literal line '{task_spec.TODO_MARKER}' with "
    f"'{task_spec.DONE_MARKER}' followed by '{task_spec.BANNER}'"
)


def _build_task(workdir: Path) -> TaskDef:
    return TaskDef(
        objective=f"Edit every target file on disk so each contains the header: {_EDIT_CLAUSE}.",
        working_dir=str(workdir),
        units=list(task_spec.TARGET_FILES),
        instructions=f"{_EDIT_CLAUSE}.",
        decisions=["Do not modify already-edited files; the transform is not idempotent."],
        notes="Each file gets exactly one header banner. Doubling = a bug.",
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Real-CLI orchestrator JOIN (injected interrupts)")
    ap.add_argument("--driver", choices=["claude", "fake", "greedy"], default="claude",
                    help="claude = the real join; fake = validate wiring; "
                         "greedy = negative control (must FAIL the multi-cycle assertion)")
    ap.add_argument("--interrupts", type=int, default=2,
                    help="number of injected session-limit interruptions")
    ap.add_argument("--stop-after", type=int, default=2,
                    help="files pre-completed before the run (the partial start state)")
    ap.add_argument("--workbench", default=str(run_spike.SPIKE_ROOT / "_workbench"))
    args = ap.parse_args()

    # Each injected cycle consumes one file and the final cycle needs >=1 file, so
    # interrupts must leave room: interrupts <= (total - stop_after) - 1.
    total_files = len(task_spec.TARGET_FILES)
    max_useful = total_files - args.stop_after - 1
    if args.interrupts > max_useful:
        print(f"[{_ts()}] [ERR] --interrupts {args.interrupts} exceeds the {max_useful} "
              f"that {total_files - args.stop_after} pending files can force "
              f"(need 1 file per interrupt cycle + 1 for the final). Lower it.")
        return 2

    base = Path(args.workbench)
    base.mkdir(parents=True, exist_ok=True)
    workdir = base / "real_orch_proj"

    # Seed 0/5 and commit (start_sha anchors the diff), then establish the partial
    # state with real commits -- the orchestrator will accumulate real git state.
    start_sha = run_spike._init_project(workdir)
    pre_done = list(task_spec.TARGET_FILES[:args.stop_after])
    for f in pre_done:
        task_spec.apply_transform(workdir / f)
    run_spike._git(["add", "-A"], workdir)
    run_spike._git(["commit", "-q", "-m", f"partial: {len(pre_done)} of {len(task_spec.TARGET_FILES)}"], workdir)
    print(f"[{_ts()}] [INFO] driver={args.driver} interrupts={args.interrupts}")
    print(f"[{_ts()}] [INFO] partial start: {len(pre_done)}/{len(task_spec.TARGET_FILES)} done {pre_done}")

    task = _build_task(workdir)
    is_unit_done = lambda w, u: task_spec.is_done(w / u)
    verify = lambda w: task_spec.all_done(w)

    # Initial resume prompt, built from the REAL partial git state.
    cp, diff = build_checkpoint(task, start_sha, is_unit_done)
    initial_prompt = compose_resume_prompt(cp, diff)

    if args.driver == "claude":
        inner: BaseDriver = ClaudeCodeDriver()
    elif args.driver == "greedy":
        inner = _GreedyFake()
    else:
        inner = _ScopeFake()
    # Persisting is the INNER wrapper so each transcript captures the ACTUAL prompt
    # the session received (the single-file prompt on injected cycles, not the
    # orchestrator's) and claude's REAL rc/stdout -- the true "did it edit or
    # queue" signal. Injected wraps it to force the multiple cycles.
    persisting = TranscriptPersistingDriver(inner, run_spike.RESULTS_DIR, prefix=f"{args.driver}_real_orch")
    injected = InjectedInterruptDriver(persisting, interrupts=args.interrupts)
    driver = injected

    # Injected interrupts have no real reset to wait for, so waits are short: the
    # injected reset-hint ("2 seconds") drives the wait; floor=1 keeps the clamp
    # honest. (A real session-limit would wait its real reset / longer backoff.)
    policy = WaitPolicy(floor_seconds=1, cap_seconds=5, max_wait_seconds=10,
                        max_attempts=args.interrupts + 4)
    sleep_fn = (lambda s: None) if args.driver == "fake" else time.sleep

    result = run_until_done(
        task, driver, start_sha=start_sha,
        is_unit_done=is_unit_done, verify=verify,
        initial_prompt=initial_prompt, policy=policy, sleep_fn=sleep_fn,
    )

    rep = score(workdir, pre_interrupt_done=pre_done)
    print(f"[{_ts()}] [RESULT] {rep.summary()}")
    print(f"[{_ts()}] [INFO] terminal state={result.state} after {result.attempts} attempt(s)")

    # MULTI-CYCLE assertion -- the whole point of the join. There must be exactly
    # (interrupts + 1) real sessions, and the done-count must CLIMB one file per
    # injected cycle then finish, e.g. 3 -> 4 -> 5 for stop_after=2/interrupts=2.
    # If a session finished too early (scoping failed), this FAILS loudly instead
    # of falsely reporting a proven join.
    sessions = result.attempts
    progress = injected.progress
    expected_sessions = args.interrupts + 1
    expected_progress = [args.stop_after + 1 + i for i in range(args.interrupts)] + [total_files]
    multi_cycle_ok = (sessions == expected_sessions and progress == expected_progress)
    print(f"[{_ts()}] [INFO] real sessions={sessions} (expected {expected_sessions}); "
          f"per-cycle done-count={progress} (expected {expected_progress})")
    if not multi_cycle_ok:
        print(f"[{_ts()}] [ERR] MULTI-CYCLE NOT EXERCISED: a session finished too early "
              f"(scoping did not constrain it to one file), so re-checkpoint + resume "
              f"across real cycles was NOT tested. This is NOT a proven join.")

    healthy = (
        result.state == COMPLETED and rep.end_state_ok
        and rep.duplicated_files == [] and rep.lost_files == []
        and multi_cycle_ok
    )
    tag = "[OK]" if healthy else "[ERR]"
    verdict = "JOIN PROVEN (multi-cycle)" if healthy else "JOIN FAILED"
    print(f"[{_ts()}] {tag} {verdict}: orchestrator drove the {args.driver} CLI to "
          f"{rep.done_count}/{rep.total_files} across {sessions} real sessions, behavior="
          f"{'CONTINUED' if rep.continued else 'DUPLICATED/RESTARTED'}")
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
