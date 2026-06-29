"""
run_spike.py -- Resume-fidelity experiment runner.

Answers one question with data: when a halted Claude Code session is handed a
structured resume prompt, does it CONTINUE mid-task or RESTART/duplicate work?

Method (simulated interruption):
  1. Seed a fresh git project of marker files.
  2. BASELINE: run the task to completion in one shot; record end state; reset.
  3. INTERRUPT RUN:
       a. Run the task but stop it after `stop_after` files are done
          (simulated kill -- we simply run a partial driver pass).
       b. Build a Checkpoint from the partial state + git diff.
       c. Compose a resume prompt and run a SECOND session with it.
       d. Score the final state vs baseline.
  4. Write results/<driver>_report.json and print a one-line verdict.

Usage:
  python run_spike.py --driver fake          # validate harness logic
  python run_spike.py --driver fake-restart  # confirm scorer catches restarts
  python run_spike.py --driver claude        # the real measurement (local)

No credentials handled here. Python 3.11+, stdlib only.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path


def _on_rm_error(func, path, exc_info):
    """rmtree error handler for Windows.

    Git marks files under .git/objects read-only; Windows refuses to unlink a
    read-only file, so rmtree raises PermissionError. Clear the read-only bit
    and retry. POSIX is unaffected.
    """
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _rmtree(path: Path) -> None:
    """Cross-platform recursive delete that tolerates read-only git objects."""
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_on_rm_error)
    else:
        shutil.rmtree(path, onerror=_on_rm_error)

HARNESS_DIR = Path(__file__).resolve().parent
SPIKE_ROOT = HARNESS_DIR.parent
TASK_DIR = SPIKE_ROOT / "task"
RESULTS_DIR = SPIKE_ROOT / "results"

# Make sibling modules importable regardless of CWD.
sys.path.insert(0, str(HARNESS_DIR))
sys.path.insert(0, str(TASK_DIR))

from checkpoint import Checkpoint, compose_resume_prompt   # noqa: E402
from driver import make_driver                              # noqa: E402
from scorer import score                                    # noqa: E402
import task_spec                                             # noqa: E402


def _ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _git(args: list[str], cwd: Path) -> str:
    # Force UTF-8: a git diff can carry non-cp1252 bytes from file contents, which
    # would crash the default Windows decoder. errors="replace" never crashes.
    out = subprocess.run(
        ["git", *args], cwd=cwd,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return out.stdout.strip()


def _init_project(workdir: Path) -> str:
    if workdir.exists():
        _rmtree(workdir)
    workdir.mkdir(parents=True)
    task_spec.seed_files(workdir)
    _git(["init", "-q"], workdir)
    _git(["config", "user.email", "spike@local"], workdir)
    _git(["config", "user.name", "spike"], workdir)
    _git(["add", "-A"], workdir)
    _git(["commit", "-q", "-m", "seed"], workdir)
    return _git(["rev-parse", "HEAD"], workdir)


def _partial_apply(workdir: Path, stop_after: int) -> list[str]:
    """Simulate an interruption: complete only the first `stop_after` files.

    This stands in for 'the session was killed mid-task'. It is deterministic
    so the experiment is repeatable.
    """
    done: list[str] = []
    for fname in task_spec.TARGET_FILES[:stop_after]:
        task_spec.apply_transform(workdir / fname)
        done.append(fname)
    return done


def _baseline_prompt() -> str:
    """Self-contained, IMPERATIVE baseline instruction for a real -p session.

    A direct probe proved `claude -p` edits files when the prompt names each file,
    gives the literal before/after strings, and commands the edit on disk -- and
    does NOT act when handed a 'describe the transform' abstraction. So this
    enumerates every target file by name with a literal per-file replace command,
    ending with an explicit on-disk-now imperative. Marker/filename literals are
    rendered from task_spec so the text stays in sync with the spec while exposing
    only what an agent can act on; no task_spec symbols are named in the prompt.
    """
    lines = ["Edit the following files in the current directory, on disk, now."]
    for f in task_spec.TARGET_FILES:
        lines.append(
            f"- In {f}, replace the literal line '{task_spec.TODO_MARKER}' "
            f"with '{task_spec.DONE_MARKER}' followed by '{task_spec.BANNER}'."
        )
    lines.append(
        "Make these edits to the files on disk now. Change nothing else, and "
        "each file must contain the header exactly once (never twice)."
    )
    lines.append(
        "When all files are edited, end your reply with the exact token "
        "DONE_TASK_COMPLETE on its own line."
    )
    return "\n".join(lines)


def _persist_transcript(phase: str, res) -> None:
    """Write a session's full stdout/stderr to results/<phase>_session.txt ALWAYS.

    A rc=0-but-did-nothing run is otherwise invisible -- exactly the failure where
    claude -p exits clean yet edits no files. Persisting the transcript regardless
    of exit code keeps the evidence (finding #6: rc=0 != task-done; capture is
    independent of score). The char counts in the log line make a silent no-op
    ('0 stdout chars' or 'wrote nothing') obvious at a glance.
    """
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"{phase}_session.txt"
    body = "\n".join([
        f"phase: {phase}",
        f"captured_at: {_ts()}",
        f"returncode: {res.returncode}",
        f"ok: {res.ok}",
        f"seconds: {res.seconds:.1f}",
        "===== STDOUT =====",
        res.stdout or "",
        "===== STDERR =====",
        res.stderr or "",
    ])
    path.write_text(body, encoding="utf-8")
    print(
        f"[{_ts()}] [INFO] {phase} transcript -> {path.name} "
        f"(rc={res.returncode}, {len(res.stdout or '')} stdout chars, "
        f"{len(res.stderr or '')} stderr chars)"
    )


def run_baseline(driver_name: str, base: Path) -> bool:
    workdir = base / "baseline_proj"
    _init_project(workdir)
    driver = make_driver(driver_name)
    prompt = _baseline_prompt()
    res = driver.run(prompt, cwd=str(workdir))
    _persist_transcript("baseline", res)
    ok = task_spec.all_done(workdir)
    tag = "[OK]" if ok else "[ERR]"
    print(f"[{_ts()}] {tag} baseline reached done-state = {ok}")
    return ok


def run_interrupt(driver_name: str, base: Path, stop_after: int = 2):
    workdir = base / "interrupt_proj"
    start_sha = _init_project(workdir)

    # (a) partial work, then simulated kill
    pre_done = _partial_apply(workdir, stop_after)
    _git(["add", "-A"], workdir)
    _git(["commit", "-q", "-m", f"checkpoint after {stop_after} files"], workdir)
    print(f"[{_ts()}] [INFO] interrupted after {stop_after} files: {pre_done}")

    pending = [f for f in task_spec.TARGET_FILES if f not in pre_done]

    # (b) build checkpoint
    diff = _git(["diff", start_sha, "HEAD"], workdir)
    # Checkpoint CONTENT uses the IMPERATIVE, literal, on-disk-now shape proven to
    # make claude act rather than infer (finding #8). The prior weak content
    # ("Apply the header transform to charlie.py", no literal markers) is why a
    # resume reported it "inferred the task from commit history" -- the diff was
    # present but the instructions were too soft. Markers render from task_spec.
    _edit_clause = (
        f"replace the literal line '{task_spec.TODO_MARKER}' with "
        f"'{task_spec.DONE_MARKER}' followed by '{task_spec.BANNER}'"
    )
    cp = Checkpoint(
        objective=(
            "Edit every target file on disk so each contains the standard header: "
            f"in each file, {_edit_clause}."
        ),
        working_dir=str(workdir),
        git_sha=_git(["rev-parse", "HEAD"], workdir),
        completed_files=pre_done,
        pending_files=pending,
        current_file=pending[0] if pending else None,
        next_action=(
            (f"Edit {pending[0]} on disk now: {_edit_clause}. Make the edit to the "
             f"file on disk now, then continue with any remaining files the same way.")
            if pending
            else "All files appear complete; verify each contains the header exactly once."
        ),
        completed_subtasks=[f"edited {f} on disk" for f in pre_done],
        remaining_todos=[f"edit {f} on disk" for f in pending],
        recent_decisions=[
            "Header format is fixed; do not alter already-edited files.",
        ],
        notes="Each file gets exactly one header banner. Doubling = a bug.",
    )
    RESULTS_DIR.mkdir(exist_ok=True)
    cp.save(RESULTS_DIR / f"{driver_name}_checkpoint.json")

    # (c) resume
    resume_prompt = compose_resume_prompt(cp, git_diff=diff)
    (RESULTS_DIR / f"{driver_name}_resume_prompt.txt").write_text(
        resume_prompt, encoding="utf-8"
    )
    driver = make_driver(driver_name)
    res = driver.run(resume_prompt, cwd=str(workdir))
    _persist_transcript("resume", res)
    if not res.ok:
        # Do NOT score an errored session. A no-op resume leaves the pre-seeded
        # files in place, so score() would falsely report CONTINUED/2-of-5 -- a
        # false green, the exact trap that produced the retracted result. Abort
        # loudly with both captured streams instead of falling through.
        err = (res.stderr or "").strip()
        out = (res.stdout or "").strip()
        print(f"[{_ts()}] [ABORT] resume session errored rc={res.returncode}; refusing to score (would be a false green)")
        print(f"[{_ts()}] [ABORT] stderr: {err[:500] if err else '<empty>'}")
        print(f"[{_ts()}] [ABORT] stdout: {out[:500] if out else '<empty>'}")
        return None

    # (d) score
    report = score(workdir, pre_interrupt_done=pre_done)
    (RESULTS_DIR / f"{driver_name}_report.json").write_text(
        report.to_json(), encoding="utf-8"
    )
    print(f"[{_ts()}] [RESULT] {report.summary()}")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="Resume-fidelity spike")
    ap.add_argument(
        "--driver",
        default="fake",
        choices=["fake", "fake-restart", "claude"],
        help="which session driver to use",
    )
    ap.add_argument(
        "--stop-after",
        type=int,
        default=2,
        help="files to complete before the simulated interruption",
    )
    ap.add_argument(
        "--workbench",
        default=str(SPIKE_ROOT / "_workbench"),
        help="scratch dir for the generated projects",
    )
    args = ap.parse_args()

    base = Path(args.workbench)
    base.mkdir(parents=True, exist_ok=True)

    print(f"[{_ts()}] [INFO] driver={args.driver} stop_after={args.stop_after}")
    run_baseline(args.driver, base)
    report = run_interrupt(args.driver, base, stop_after=args.stop_after)

    # A session that errored returns no report: exit distinctly (not a clean
    # pass, not a scored fail) so an aborted run is never mistaken for a result.
    if report is None:
        print(f"[{_ts()}] [ABORT] run aborted before scoring; baseline remains UNKNOWN")
        return 2

    # Exit non-zero if resume did not continue cleanly, so CI/scripts can gate.
    return 0 if (report.continued and report.end_state_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
