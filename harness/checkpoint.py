"""
checkpoint.py -- Checkpoint state model and resume-prompt generation.

This is the core of the resume-fidelity spike. A Checkpoint captures the
minimum state needed to make a fresh Claude Code session continue a halted
task mid-stream rather than restart it. compose_resume_prompt() turns that
state into the opening prompt for the resumed session.

Design notes:
- Git is the source of truth for file state. We record the commit SHA and
  reconstruct "what changed" from the diff, rather than snapshotting files.
- The prompt is deliberately imperative and front-loads the single next
  action, because that is what most strongly steers continuation vs restart.

No external dependencies. Python 3.11+.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


def _ts() -> str:
    """UTC timestamp for logs and records."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class Checkpoint:
    """Durable snapshot of an in-flight task.

    Fields mirror the spec's 'Persistent Task State' section but trimmed to
    what actually drives resume fidelity. Everything reconstructable from git
    is referenced by SHA, not duplicated here.
    """

    objective: str                       # the overall task, one sentence
    working_dir: str                     # absolute path of the project
    git_sha: str                         # HEAD at checkpoint time
    completed_files: list[str] = field(default_factory=list)
    pending_files: list[str] = field(default_factory=list)
    current_file: Optional[str] = None   # file in progress when halted
    next_action: str = ""                # the single next concrete step
    completed_subtasks: list[str] = field(default_factory=list)
    remaining_todos: list[str] = field(default_factory=list)
    recent_decisions: list[str] = field(default_factory=list)
    notes: str = ""                      # free-form continuation context
    created_at: str = field(default_factory=_ts)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Checkpoint":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(**data)


def compose_resume_prompt(cp: Checkpoint, git_diff: str = "") -> str:
    """Build the opening prompt for a resumed Claude Code session.

    The ordering is intentional: state the continuation contract first, then
    the single next action, then supporting context. Diff is included last and
    truncated so it informs without dominating the context budget.
    """
    lines: list[str] = []
    lines.append(
        "You are RESUMING an in-progress task that was interrupted. "
        "Do NOT start over and do NOT re-introduce yourself. "
        "Continue from the exact point described below."
    )
    lines.append("")
    lines.append(f"OBJECTIVE: {cp.objective}")
    lines.append("")

    if cp.next_action:
        lines.append(f"YOUR IMMEDIATE NEXT ACTION: {cp.next_action}")
        lines.append("")

    if cp.current_file:
        lines.append(f"FILE IN PROGRESS WHEN INTERRUPTED: {cp.current_file}")

    if cp.completed_files:
        done = ", ".join(cp.completed_files)
        lines.append(f"ALREADY COMPLETED (do not redo these): {done}")

    if cp.pending_files:
        todo = ", ".join(cp.pending_files)
        lines.append(f"STILL TO DO AFTER CURRENT FILE: {todo}")

    if cp.completed_subtasks:
        lines.append("")
        lines.append("COMPLETED SUBTASKS:")
        for s in cp.completed_subtasks:
            lines.append(f"  - {s}")

    if cp.remaining_todos:
        lines.append("")
        lines.append("REMAINING TODOS:")
        for t in cp.remaining_todos:
            lines.append(f"  - {t}")

    if cp.recent_decisions:
        lines.append("")
        lines.append("DECISIONS ALREADY MADE (honor these, do not relitigate):")
        for d in cp.recent_decisions:
            lines.append(f"  - {d}")

    if cp.notes:
        lines.append("")
        lines.append(f"CONTEXT: {cp.notes}")

    if git_diff.strip():
        truncated = git_diff if len(git_diff) < 4000 else git_diff[:4000] + "\n... [diff truncated]"
        lines.append("")
        lines.append("WORK SO FAR (git diff since task start):")
        lines.append(truncated)

    lines.append("")
    lines.append(
        "Resume now by performing YOUR IMMEDIATE NEXT ACTION. "
        "When the full objective is met, end your turn with the exact token DONE_TASK_COMPLETE."
    )
    return "\n".join(lines)
