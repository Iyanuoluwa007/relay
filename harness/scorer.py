"""
scorer.py -- Objective fidelity metrics for a resume run.

Given a project root after a resume run, plus the set of files that were
completed BEFORE the interruption, compute:

  - end_state_ok    : did the task reach its verifiable done-state?
  - duplicated_files: files where work was re-done (banner doubled)
  - continued       : resume continued (no duplication) vs restarted
  - lost_files      : pre-interruption-complete files that are no longer done

These are deliberately mechanical. No LLM judges this; the whole point of the
spike is a number you can trust.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import json
import sys

# task_spec lives in ../task; the runner puts it on sys.path.
from task_spec import TARGET_FILES, is_done, is_doubled, all_done


@dataclass
class FidelityReport:
    end_state_ok: bool
    continued: bool
    duplicated_files: list[str]
    lost_files: list[str]
    done_count: int
    total_files: int

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    def summary(self) -> str:
        verdict = "CONTINUED" if self.continued else "RESTARTED/DUPLICATED"
        end = "[OK]" if self.end_state_ok else "[ERR]"
        # Separators lead each segment so trailing-whitespace trimming can never
        # collapse them (that produced the 'nonelost' run-together bug).
        return (
            f"end_state={end} ({self.done_count}/{self.total_files} done)"
            f"  resume_behavior={verdict}"
            f"  duplicated={self.duplicated_files or 'none'}"
            f"  lost={self.lost_files or 'none'}"
        )


def score(root: Path, pre_interrupt_done: list[str]) -> FidelityReport:
    duplicated = [f for f in TARGET_FILES if is_doubled(root / f)]
    lost = [f for f in pre_interrupt_done if not is_done(root / f)]
    done_count = sum(1 for f in TARGET_FILES if is_done(root / f))
    report = FidelityReport(
        end_state_ok=all_done(root),
        continued=(len(duplicated) == 0 and len(lost) == 0),
        duplicated_files=duplicated,
        lost_files=lost,
        done_count=done_count,
        total_files=len(TARGET_FILES),
    )
    return report
