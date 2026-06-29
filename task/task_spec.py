"""
task_spec.py -- The scripted refactor task used for the fidelity experiment.

We need a task that is (a) multi-file and sequential so it can straddle an
interruption, and (b) mechanically verifiable so 'did resume reach the same
end state' is a boolean, not a judgment call.

The task: across a set of source files, add a standard header banner and
replace a sentinel marker. A file is 'done' when it contains DONE_MARKER and
no longer contains TODO_MARKER. Duplication (the failure mode we hunt) is
detectable because applying the transform twice produces a doubled banner,
which is_doubled() catches.

This same spec is used by FakeDriver (to act) and by the scorer (to verify),
which keeps the experiment self-consistent.
"""

from __future__ import annotations

from pathlib import Path

TARGET_FILES = [
    "alpha.py",
    "bravo.py",
    "charlie.py",
    "delta.py",
    "echo.py",
]

TODO_MARKER = "# TODO: add standard header"
DONE_MARKER = "# STANDARD-HEADER-APPLIED"
BANNER = "# ----- standard header -----"

SEED_BODY = (
    "{todo}\n"
    "def handler():\n"
    "    return 'ok'\n"
)


def seed_files(root: Path) -> None:
    """Create the initial project state: every file carries the TODO marker."""
    for fname in TARGET_FILES:
        (root / fname).write_text(
            SEED_BODY.format(todo=TODO_MARKER), encoding="utf-8"
        )


def apply_transform(fpath: Path) -> None:
    """Apply the header transform to a single file (the unit of work).

    Idempotency is intentionally NOT enforced here: applying twice doubles the
    banner. That is the signal the scorer uses to detect re-done work.
    """
    text = fpath.read_text(encoding="utf-8")
    new = text.replace(TODO_MARKER, f"{DONE_MARKER}\n{BANNER}")
    fpath.write_text(new, encoding="utf-8")


def is_done(fpath: Path) -> bool:
    text = fpath.read_text(encoding="utf-8")
    return DONE_MARKER in text and TODO_MARKER not in text


def is_doubled(fpath: Path) -> bool:
    """True if the transform was applied more than once (duplicated work)."""
    text = fpath.read_text(encoding="utf-8")
    return text.count(BANNER) > 1


def all_done(root: Path) -> bool:
    return all(is_done(root / f) for f in TARGET_FILES)
