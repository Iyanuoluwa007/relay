"""
test_detector.py -- Truth-table proof for detector.classify (DESIGN.md sec.6).

Pure-function tests: no CLI, no quota. Each case asserts (type, confidence) and,
where relevant, a reset_hint. Run directly:

    python harness/test_detector.py     # prints [OK]/[ERR], exits non-zero on fail

Standard library only; plain-text status labels; UTC-stamped summary line.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Make sibling modules importable regardless of CWD.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from driver import RunResult
from detector import classify, EventType, HIGH, MEDIUM, LOW


def _ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _rr(stdout: str = "", stderr: str = "", rc: int = 0) -> RunResult:
    return RunResult(ok=(rc == 0), stdout=stdout, stderr=stderr, returncode=rc, seconds=0.1)


# Each case: (name, RunResult, expected_type, expected_confidence, reset_substr|None)
CASES = [
    (
        "done-token + rc0 -> COMPLETED",
        _rr(stdout="did the work\nDONE_TASK_COMPLETE\n", rc=0),
        EventType.COMPLETED, HIGH, None,
    ),
    (
        "known literal limit + rc1 -> SESSION_LIMIT/HIGH",
        _rr(stdout="working...", stderr="Claude usage limit reached.", rc=1),
        EventType.SESSION_LIMIT, HIGH, None,
    ),
    (
        "reworded limit (loose regex only) + rc1 -> SESSION_LIMIT/MEDIUM",
        _rr(stderr="You have exhausted your usage limit for now.", rc=1),
        EventType.SESSION_LIMIT, MEDIUM, None,
    ),
    (
        "generic stderr rc1, no pattern -> UNKNOWN/LOW",
        _rr(stderr="Traceback: ValueError: boom", rc=1),
        EventType.UNKNOWN_INTERRUPTION, LOW, None,
    ),
    (
        "rc127 -> FATAL",
        _rr(stderr="claude CLI not found on PATH", rc=127),
        EventType.FATAL, HIGH, None,
    ),
    (
        "real 401 auth failure -> FATAL",
        _rr(stdout="Failed to authenticate. API Error: 401 Invalid authentication credentials", rc=1),
        EventType.FATAL, HIGH, None,
    ),
    (
        "FALSE-POSITIVE GUARD: body mentions 'rate limit' but rc0 + token -> COMPLETED",
        _rr(stdout="I respected the rate limit while editing.\nDONE_TASK_COMPLETE\n", rc=0),
        EventType.COMPLETED, HIGH, None,
    ),
    (
        "TAIL-ONLY GUARD: limit text only in early body, clean tail, rc1 -> UNKNOWN",
        _rr(stdout="line about a usage limit reached in the docs\n" + "\n".join(f"clean line {i}" for i in range(25)), rc=1),
        EventType.UNKNOWN_INTERRUPTION, LOW, None,
    ),
    (
        "token present but rc1 (no clean exit) -> NOT completed -> UNKNOWN",
        _rr(stdout="DONE_TASK_COMPLETE", rc=1),
        EventType.UNKNOWN_INTERRUPTION, LOW, None,
    ),
    (
        "clean rc0 without token -> UNKNOWN (continued-but-incomplete)",
        _rr(stdout="I made some edits.", rc=0),
        EventType.UNKNOWN_INTERRUPTION, LOW, None,
    ),
    (
        "timeout rc124 -> UNKNOWN (still recovers, counts toward cap)",
        _rr(stderr="timeout after 900s", rc=124),
        EventType.UNKNOWN_INTERRUPTION, LOW, None,
    ),
    (
        "reset hint parsed from known-limit tail",
        _rr(stderr="Claude usage limit reached. Resets at 5pm.", rc=1),
        EventType.SESSION_LIMIT, HIGH, "5pm",
    ),
]


def main() -> int:
    failures = 0
    for name, rr, exp_type, exp_conf, reset_substr in CASES:
        ev = classify(rr)
        ok = ev.type == exp_type and ev.confidence == exp_conf
        if reset_substr is not None:
            ok = ok and (ev.reset_hint is not None and reset_substr in ev.reset_hint)
        tag = "[OK]" if ok else "[ERR]"
        print(f"{tag} {name}")
        if not ok:
            failures += 1
            print(f"     expected type={exp_type.value} conf={exp_conf} reset~{reset_substr}")
            print(f"     got      type={ev.type.value} conf={ev.confidence} reset={ev.reset_hint!r} evidence={ev.evidence!r}")

    total = len(CASES)
    passed = total - failures
    verdict = "[OK]" if failures == 0 else "[ERR]"
    print(f"[{_ts()}] {verdict} detector truth table: {passed}/{total} passed")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
