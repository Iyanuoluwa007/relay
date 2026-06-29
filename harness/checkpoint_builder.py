"""
checkpoint_builder.py -- Snapshot the live git tree and build a Checkpoint.

The bridge from "a session was interrupted" to "a resume prompt that makes a
fresh session continue". It REUSES the proven Checkpoint model and feeds the
(untouched) compose_resume_prompt; it reinvents neither.

Two DESIGN.md commitments are concrete here:

  - The COMMIT is the consistency boundary (sec.5.2). We `git add -A && commit`
    the partial tree first, so the recorded SHA names a coherent snapshot even if
    the kill landed mid-write. We never trust in-memory progress claims.
  - completed_files is inferred from committed evidence, BIASED TO PENDING
    (amendment 1). A unit counts as done only if a caller-supplied predicate
    affirms it on the committed tree; any uncertainty (predicate False, missing
    file, or a raised exception) lands the unit in pending. Worst case is a
    benign re-do of one borderline unit -- which the resume prompt's "do not
    redo" guard further suppresses -- never a silent loss.

Because the agent cannot see the project's internal task spec, the Checkpoint
CONTENT must carry the literal, agent-visible instructions. TaskDef.instructions
is front-loaded into next_action so the resumed session knows exactly what to do
-- this is how we reach completion WITHOUT touching compose_resume_prompt.

Python 3.11+. Standard library only (plus the local Checkpoint model).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from checkpoint import Checkpoint


def _git(args: list[str], cwd: str | Path) -> str:
    """Run a git command, returning stdout. Tolerant by design: a non-zero exit
    (e.g. 'nothing to commit, working tree clean') is not raised -- the caller
    reads HEAD afterward, which is the real source of truth. A shared gitutil is
    a deliberate later consolidation; kept local now to stay one-change-focused.
    """
    # Force UTF-8: a git diff can carry non-cp1252 bytes from file contents, which
    # would crash the default Windows decoder. errors="replace" never crashes.
    out = subprocess.run(
        ["git", *args], cwd=str(cwd),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return out.stdout.strip()


@dataclass
class TaskDef:
    """The orchestrator's view of the task -- enough to checkpoint and resume.

    `units` are the ordered units of work (filenames in the spike). `instructions`
    is the literal, agent-visible description of what to do to each unit; a fresh
    session needs it because it cannot see any internal spec module.
    """

    objective: str
    working_dir: str
    units: list[str]
    instructions: str
    decisions: list[str] = field(default_factory=list)
    notes: str = ""
    done_token: str = "DONE_TASK_COMPLETE"


def _done_safe(pred: Callable[[Path, str], bool], wd: Path, unit: str) -> bool:
    """Conservative done-check: any uncertainty -> not done (pending)."""
    try:
        return bool(pred(wd, unit))
    except Exception:
        return False


def build_checkpoint(
    task: TaskDef,
    start_sha: str,
    is_unit_done: Callable[[Path, str], bool],
    commit_message: str = "checkpoint: interrupted task snapshot",
) -> tuple[Checkpoint, str]:
    """Commit the live tree, infer state conservatively, and build a Checkpoint.

    Returns (checkpoint, git_diff). Hand both to compose_resume_prompt (unchanged)
    to produce the resume prompt.
    """
    wd = Path(task.working_dir)

    # (1) The commit is the consistency boundary -- snapshot whatever the tree is.
    _git(["add", "-A"], wd)
    _git(["commit", "-q", "-m", commit_message], wd)  # tolerant if nothing to commit
    sha = _git(["rev-parse", "HEAD"], wd)

    # (2) Conservative state inference from the committed tree.
    completed = [u for u in task.units if _done_safe(is_unit_done, wd, u)]
    completed_set = set(completed)
    pending = [u for u in task.units if u not in completed_set]  # order preserved
    current: Optional[str] = pending[0] if pending else None

    # (3) Front-load an IMPERATIVE, on-disk-now edit command so a spec-blind -p
    #     session acts rather than narrates. A direct probe proved claude -p edits
    #     files only when the prompt names the file, gives the literal before/after
    #     strings (carried in task.instructions), and commands the edit on disk.
    if current is not None:
        next_action = (
            f"Edit {current} on disk now: {task.instructions.strip()} "
            f"Make the edit to the file on disk now, then continue with any "
            f"remaining files the same way."
        )
    else:
        next_action = "All units appear complete; verify the objective is fully met."

    diff = _git(["diff", start_sha, "HEAD"], wd)

    cp = Checkpoint(
        objective=task.objective,
        working_dir=task.working_dir,
        git_sha=sha,
        completed_files=completed,
        pending_files=pending,
        current_file=current,
        next_action=next_action,
        completed_subtasks=[f"completed {u}" for u in completed],
        remaining_todos=[f"edit {u} on disk" for u in pending],
        recent_decisions=list(task.decisions),
        notes=task.notes,
    )
    return cp, diff
