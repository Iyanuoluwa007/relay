"""
waiter.py -- Decide what to do after an Event, and how long to wait first.

The waiter COMPUTES a decision; it never sleeps. The orchestrator performs the
actual sleep (and in tests injects a no-op sleep). Keeping the side effect out of
this module is what makes the wait math testable with a fake clock and no real
wall-clock waiting (see DESIGN.md sec.6, condition 2).

Control rules (DESIGN.md sec.5):
  - FATAL bypasses everything: no wait, no relaunch, immediate STOP/FATAL. A
    waiter that slept-and-retried on an unrecoverable auth error (the 401 case)
    would be the "stuck forever" failure the closed exit set exists to prevent.
  - SESSION_LIMIT / UNKNOWN_INTERRUPTION are recoverable: relaunch after a wait,
    UNLESS the attempt cap is hit -> STOP/GAVE_UP (no further sleeping).
  - A SESSION_LIMIT may carry a reset_hint; we honor it when we can parse it
    safely, else fall back to bounded exponential backoff. UNKNOWN ignores any
    hint and uses the conservative backoff.

Timezone discipline: all absolute-time math is done in UTC. The clock is injected
(`now_fn` returns an aware UTC datetime) so tests are deterministic.

Python 3.11+. Standard library only (plus local detector types).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from detector import Event, EventType


# Plain-text action / stop-state labels (hard rule).
RELAUNCH = "RELAUNCH"
STOP = "STOP"
FATAL_STATE = "FATAL"
GAVE_UP_STATE = "GAVE_UP"


@dataclass
class WaitPolicy:
    floor_seconds: float = 60.0          # never relaunch faster than this
    cap_seconds: float = 3600.0          # ceiling for a single backoff wait
    max_wait_seconds: float = 6 * 3600.0  # ceiling for an honored reset-hint wait
    max_attempts: int = 5                # total runs before GAVE_UP


@dataclass
class WaitDecision:
    action: str                       # RELAUNCH | STOP
    sleep_seconds: float              # 0.0 when action == STOP
    reason: str                       # plain-text, for the orchestrator's log
    stop_state: Optional[str] = None  # FATAL | GAVE_UP when action == STOP


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def _backoff(attempt: int, policy: WaitPolicy) -> float:
    """Bounded exponential backoff: floor, 2x, 4x ... clamped to cap."""
    raw = policy.floor_seconds * (2 ** max(0, attempt - 1))
    return _clamp(raw, policy.floor_seconds, policy.cap_seconds)


def _wait_from_hint(
    hint: str, policy: WaitPolicy, now_fn: Callable[[], datetime]
) -> Optional[float]:
    """Parse a reset_hint into a wait, or None if not safely parseable.

    Two supported, testable forms; anything else degrades to None (-> backoff),
    so an unparseable hint can never produce a wrong wait:
      - relative duration: "2 hours", "30 minutes", "45 seconds"
      - absolute UTC ISO time: "2026-06-29T13:00:00Z" (interpreted as UTC)
    """
    rel = re.search(r"(\d+)\s*(second|minute|hour)s?", hint, re.IGNORECASE)
    if rel:
        n = int(rel.group(1))
        secs = n * {"second": 1, "minute": 60, "hour": 3600}[rel.group(2).lower()]
        return _clamp(float(secs), policy.floor_seconds, policy.max_wait_seconds)

    iso = re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?", hint)
    if iso:
        # Force UTC regardless of any local zone -- we store/compare in UTC.
        target = datetime.fromisoformat(iso.group(0)).replace(tzinfo=timezone.utc)
        delta = (target - now_fn()).total_seconds()
        if delta <= 0:
            return policy.floor_seconds  # reset already passed; brief wait only
        return _clamp(delta, policy.floor_seconds, policy.max_wait_seconds)

    return None


def decide(
    event: Event,
    attempt: int,
    policy: WaitPolicy | None = None,
    now_fn: Callable[[], datetime] = _utc_now,
) -> WaitDecision:
    """Given the latest Event and the number of runs so far, decide next action.

    `attempt` is 1-based: the run that just produced `event` was run #attempt.
    """
    policy = policy or WaitPolicy()

    # FATAL -- bypass the waiter entirely. No sleep, no relaunch.
    if event.type == EventType.FATAL:
        return WaitDecision(STOP, 0.0, f"fatal, not recoverable: {event.evidence}", FATAL_STATE)

    # COMPLETED is terminal and handled by the orchestrator; defensively never wait.
    if event.type == EventType.COMPLETED:
        return WaitDecision(STOP, 0.0, "already complete; nothing to wait for", None)

    # Recoverable (SESSION_LIMIT / UNKNOWN_INTERRUPTION): enforce the attempt cap.
    if attempt >= policy.max_attempts:
        return WaitDecision(
            STOP, 0.0,
            f"gave up after {attempt} attempts (cap={policy.max_attempts})",
            GAVE_UP_STATE,
        )

    wait: Optional[float] = None
    src = "backoff"
    if event.type == EventType.SESSION_LIMIT and event.reset_hint:
        wait = _wait_from_hint(event.reset_hint, policy, now_fn)
        if wait is not None:
            src = "reset-hint"
    if wait is None:
        wait = _backoff(attempt, policy)

    return WaitDecision(
        RELAUNCH, wait,
        f"relaunch attempt {attempt + 1} after {wait:.0f}s ({src}); {event.type.value}",
    )
