"""
test_checkpoint_builder.py -- Proof for build_checkpoint (DESIGN.md sec.5.2, amend.1).

Uses a REAL temporary git repo seeded from task_spec, in the spirit of the spike.
Headline claim: a torn / half-done unit is inferred as PENDING, never silently
completed -- the conservative bias. Also proves the commit-is-the-boundary
behavior, the exception->pending path, and that the composed resume prompt (built
by the UNTOUCHED compose_resume_prompt) carries the literal instructions.

Run: python harness/test_checkpoint_builder.py   # plain-text [OK]/[ERR].
Standard library only; UTC summary line.
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
from checkpoint import compose_resume_prompt
from checkpoint_builder import build_checkpoint, TaskDef, _done_safe


def _ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _git(args, cwd) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout.strip()


def _new_repo() -> tuple[Path, str]:
    wd = Path(tempfile.mkdtemp(prefix="cpb_"))
    task_spec.seed_files(wd)
    _git(["init", "-q"], wd)
    _git(["config", "user.email", "t@local"], wd)
    _git(["config", "user.name", "t"], wd)
    _git(["add", "-A"], wd)
    _git(["commit", "-q", "-m", "seed"], wd)
    return wd, _git(["rev-parse", "HEAD"], wd)


INSTRUCTIONS = (
    f"replace the literal line '{task_spec.TODO_MARKER}' with "
    f"'{task_spec.DONE_MARKER}' followed by '{task_spec.BANNER}'."
)

RESULTS: list[tuple[bool, str]] = []
def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((cond, name))
    tag = "[OK]" if cond else "[ERR]"
    print(f"{tag} {name}" + (f"  -- {detail}" if (detail and not cond) else ""))


# === Main scenario: alpha,bravo done; charlie TORN; delta,echo untouched ======
wd, start_sha = _new_repo()
task_spec.apply_transform(wd / "alpha.py")
task_spec.apply_transform(wd / "bravo.py")
# Torn charlie: has the DONE marker but ALSO still the TODO marker -> not done.
(wd / "charlie.py").write_text(
    f"{task_spec.DONE_MARKER}\n{task_spec.TODO_MARKER}\ndef handler():\n    return 'ok'\n",
    encoding="utf-8",
)
# Partial work is left UNCOMMITTED (a real interruption): tree is dirty now.
dirty_before = _git(["status", "--porcelain"], wd)
check("precondition: tree is dirty before checkpoint", dirty_before != "")

task = TaskDef(
    objective="Apply the standard header transform to every target file.",
    working_dir=str(wd),
    units=list(task_spec.TARGET_FILES),
    instructions=INSTRUCTIONS,
    decisions=["Do not modify already-completed files; the transform is not idempotent."],
    notes="Each file gets exactly one header banner. Doubling = a bug.",
)
cp, diff = build_checkpoint(task, start_sha, is_unit_done=lambda w, u: task_spec.is_done(w / u))

# Conservative bias (the headline).
check("HEADLINE: torn charlie.py is PENDING, not completed", "charlie.py" in cp.pending_files and "charlie.py" not in cp.completed_files)
check("completed == exactly the provably-done files", cp.completed_files == ["alpha.py", "bravo.py"], str(cp.completed_files))
check("pending preserves order of remaining units", cp.pending_files == ["charlie.py", "delta.py", "echo.py"], str(cp.pending_files))
check("current_file is first pending (charlie.py)", cp.current_file == "charlie.py", str(cp.current_file))

# Commit is the consistency boundary.
check("commit-boundary: tree clean after checkpoint", _git(["status", "--porcelain"], wd) == "")
check("commit-boundary: git_sha is HEAD and advanced from start", cp.git_sha == _git(["rev-parse", "HEAD"], wd) and cp.git_sha != start_sha)

# Diff carries the work so far.
check("diff is non-empty and includes alpha.py", "alpha.py" in diff and diff.strip() != "")

# The composed resume prompt (UNTOUCHED formatter) carries what resume needs.
prompt = compose_resume_prompt(cp, git_diff=diff)
check("resume prompt carries the literal markers", task_spec.DONE_MARKER in prompt and task_spec.TODO_MARKER in prompt)
check("resume next_action is IMPERATIVE on-disk-now for charlie.py", "Edit charlie.py on disk now" in prompt and "Make the edit to the file on disk now" in prompt)
check("resume prompt has the IMMEDIATE NEXT ACTION label", "IMMEDIATE NEXT ACTION" in prompt)
check("resume prompt lists completed files", "alpha.py" in prompt and "bravo.py" in prompt)
check("resume prompt carries the decision", "not idempotent" in prompt)


# === Exception path: a predicate that raises -> unit PENDING (unit-level) ======
def _raising_pred(w: Path, u: str) -> bool:
    raise RuntimeError("predicate blew up")
check("exception in done-check -> treated as not done (pending)", _done_safe(_raising_pred, wd, "alpha.py") is False)


# === All-done scenario: current None, verify-style next action ================
wd2, start2 = _new_repo()
for f in task_spec.TARGET_FILES:
    task_spec.apply_transform(wd2 / f)
task2 = TaskDef(objective="o", working_dir=str(wd2), units=list(task_spec.TARGET_FILES), instructions=INSTRUCTIONS)
cp2, _ = build_checkpoint(task2, start2, is_unit_done=lambda w, u: task_spec.is_done(w / u))
check("all-done: no pending, current_file None", cp2.pending_files == [] and cp2.current_file is None)
check("all-done: next_action shifts to verification", "verify" in cp2.next_action.lower())


def main() -> int:
    failures = sum(1 for ok, _ in RESULTS if not ok)
    total = len(RESULTS)
    verdict = "[OK]" if failures == 0 else "[ERR]"
    print(f"[{_ts()}] {verdict} checkpoint_builder: {total - failures}/{total} passed")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
