"""
detector.py -- Classify a finished session run into a control-flow Event.

This is a PURE function (no I/O, no clock, no logging) so it is fully testable
in isolation. The orchestrator owns logging and sequencing; the detector only
maps a RunResult to one member of a closed, plain-text event set.

Layered design (see DESIGN.md sec.3) -- it must NOT rely solely on exact text,
because Anthropic can re-word the limit message at any time:

  Layer 0  success first : done-token present AND rc == 0        -> COMPLETED
  FATAL    never retried : rc 127 (CLI missing) / auth failure   -> FATAL
  Layer 1  known strings : curated literal limit phrases + rc!=0 -> SESSION_LIMIT (HIGH)
  Layer 2  degradation   : loose limit regex + rc!=0             -> SESSION_LIMIT (MEDIUM)
  Layer 3  fallback      : anything else not done                -> UNKNOWN_INTERRUPTION (LOW)

UNKNOWN_INTERRUPTION is deliberately routed by the orchestrator through the SAME
recovery path as SESSION_LIMIT, so a type-miss degrades to generic recovery
rather than to a false "success". The only error the detector is engineered to
almost never make is success-vs-not-success, because only that one is
unrecoverable: COMPLETED requires BOTH the agent-emitted done-token AND rc == 0.

No limit-specific exit code is hardcoded: claude's exit codes for usage limits
are not contractually documented, so the degradation path leans on text in the
output TAIL plus an abnormal exit, not on a magic number.

Python 3.11+. Standard library only (plus the local RunResult shape).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from driver import RunResult  # reuse the proven result shape (see DESIGN.md sec.2)


DONE_TOKEN_DEFAULT = "DONE_TASK_COMPLETE"
TAIL_LINES = 15  # a real CLI limit/auth notice lands in the last lines, not mid-body


class EventType(str, Enum):
    """Closed set. Values are plain-text labels (hard rule)."""

    COMPLETED = "COMPLETED"
    SESSION_LIMIT = "SESSION_LIMIT"
    UNKNOWN_INTERRUPTION = "UNKNOWN_INTERRUPTION"
    FATAL = "FATAL"


# Plain-text confidence labels.
HIGH = "HIGH"
MEDIUM = "MEDIUM"
LOW = "LOW"


@dataclass
class Event:
    type: EventType
    confidence: str
    evidence: str               # human-readable, for the orchestrator's log line
    reset_hint: Optional[str] = None


# Layer 1: curated literal phrases. One block so a wording change is a one-line
# edit (one-thing-per-change). Matched case-insensitively against the tail.
KNOWN_LIMIT_STRINGS = (
    "usage limit reached",
    "reached your usage limit",
    "claude usage limit",
    "session limit reached",
    "session limit",
)

# Layer 2: loose families -- "they reworded it but it's obviously still a limit".
LIMIT_REGEXES = (
    re.compile(r"(usage|rate|session)\s*limit", re.IGNORECASE),
    re.compile(r"limit.{0,20}(reached|exceeded)", re.IGNORECASE),
    re.compile(r"resets?\s+(at|in)\b", re.IGNORECASE),
)

# FATAL auth signals -- retrying these is harmful, so they break the loop.
AUTH_FATAL_STRINGS = (
    "invalid authentication credentials",
    "failed to authenticate",
    "authentication failed",
)

# Best-effort reset-time extraction for the waiter; raw hint string, not parsed.
RESET_HINT_RE = re.compile(r"resets?\s+(?:at|in)\s+([^\n.]+)", re.IGNORECASE)


def _tail(text: str, n: int) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-n:]) if lines else ""


def _first_match(hay_lower: str, needles: tuple[str, ...]) -> Optional[str]:
    for needle in needles:
        if needle in hay_lower:
            return needle
    return None


def _parse_reset_hint(tail: str) -> Optional[str]:
    m = RESET_HINT_RE.search(tail)
    return m.group(1).strip() if m else None


def classify(
    result: RunResult,
    done_token: str = DONE_TOKEN_DEFAULT,
    tail_lines: int = TAIL_LINES,
) -> Event:
    """Map a finished RunResult to exactly one Event. Pure; deterministic."""
    rc = result.returncode
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    tail = _tail(f"{stdout}\n{stderr}", tail_lines)
    tail_lower = tail.lower()

    # Layer 0 -- success first, so no interruption pattern can shadow a real done.
    if done_token in stdout and rc == 0:
        return Event(EventType.COMPLETED, HIGH, "done-token present and rc=0")

    # FATAL -- never retried.
    if rc == 127:
        detail = (stderr.strip() or stdout.strip())[:120] or "no output"
        return Event(EventType.FATAL, HIGH, f"rc=127 (CLI not found): {detail}")
    auth = _first_match(tail_lower, AUTH_FATAL_STRINGS)
    if auth:
        return Event(EventType.FATAL, HIGH, f"auth failure: matched '{auth}'")

    # Limit detection only on an abnormal exit (a clean rc=0 is never a limit).
    if rc != 0:
        known = _first_match(tail_lower, KNOWN_LIMIT_STRINGS)
        if known:
            return Event(
                EventType.SESSION_LIMIT, HIGH,
                f"matched known limit string '{known}' (rc={rc})",
                _parse_reset_hint(tail),
            )
        for rx in LIMIT_REGEXES:
            m = rx.search(tail)
            if m:
                return Event(
                    EventType.SESSION_LIMIT, MEDIUM,
                    f"matched limit pattern '{m.group(0)}' (rc={rc})",
                    _parse_reset_hint(tail),
                )

    # Layer 3 -- fallback. Both an abnormal exit with no recognized signal and a
    # clean exit that never declared completion are "not done, recover/retry".
    if rc != 0:
        return Event(
            EventType.UNKNOWN_INTERRUPTION, LOW,
            f"abnormal exit rc={rc}, no limit/fatal signal in tail",
        )
    return Event(
        EventType.UNKNOWN_INTERRUPTION, LOW,
        "clean exit (rc=0) without completion token",
    )
