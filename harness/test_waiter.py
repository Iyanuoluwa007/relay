"""
test_waiter.py -- Proof for waiter.decide (DESIGN.md sec.6, conditions on it).

No real sleeping: the waiter never sleeps, and absolute-time math uses an
INJECTED fixed UTC clock so the computed durations are deterministic.

Headline (negative) case FIRST: a FATAL event -- the canonical instance being
the 401 auth failure we actually hit -- must bypass the waiter completely: no
sleep, no relaunch, immediate STOP/FATAL. Then the happy-path wait math.

Run: python harness/test_waiter.py   # plain-text [OK]/[ERR], non-zero on fail.
Standard library only; UTC throughout.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from driver import RunResult
from detector import classify, Event, EventType, HIGH
from waiter import (
    decide, WaitPolicy, WaitDecision,
    RELAUNCH, STOP, FATAL_STATE, GAVE_UP_STATE,
)


def _ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# A fixed, injected UTC clock. Tests that touch absolute time pass this in.
FIXED_NOW = datetime(2026, 6, 29, 12, 0, 0, tzinfo=timezone.utc)
def _now() -> datetime:
    return FIXED_NOW


RESULTS: list[tuple[bool, str]] = []
def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((cond, name))
    tag = "[OK]" if cond else "[ERR]"
    print(f"{tag} {name}" + (f"  -- {detail}" if (detail and not cond) else ""))


# --- HEADLINE NEGATIVE CASE: FATAL (the real 401) must bypass the waiter -------
# Build the FATAL event the way the detector actually produces it from a 401,
# so the bypass is proven against the real instance, not a hand-made stub.
fatal_event = classify(
    RunResult(False, "Failed to authenticate. API Error: 401 Invalid authentication credentials", "", 1, 0.1)
)
check("401 is classified FATAL (precondition)", fatal_event.type == EventType.FATAL)
d = decide(fatal_event, attempt=1, policy=WaitPolicy(), now_fn=_now)
check("FATAL -> action STOP", d.action == STOP, d.action)
check("FATAL -> stop_state FATAL", d.stop_state == FATAL_STATE, str(d.stop_state))
check("FATAL -> zero sleep (no wait)", d.sleep_seconds == 0.0, str(d.sleep_seconds))
# Even if a stray reset_hint were attached, FATAL must still bypass.
fatal_with_hint = Event(EventType.FATAL, HIGH, "auth failure", reset_hint="2 hours")
d2 = decide(fatal_with_hint, attempt=1, now_fn=_now)
check("FATAL with stray reset_hint still STOP/0s",
      d2.action == STOP and d2.sleep_seconds == 0.0 and d2.stop_state == FATAL_STATE)


# --- Happy path: SESSION_LIMIT backoff (no hint) -------------------------------
pol = WaitPolicy(floor_seconds=60, cap_seconds=3600, max_attempts=10)
limit = Event(EventType.SESSION_LIMIT, HIGH, "limit", reset_hint=None)
seq = [decide(limit, attempt=a, policy=pol, now_fn=_now).sleep_seconds for a in (1, 2, 3, 4)]
check("backoff sequence 60,120,240,480", seq == [60, 120, 240, 480], str(seq))
check("backoff relaunches", decide(limit, 1, pol, _now).action == RELAUNCH)

# Cap: large attempt clamps to cap_seconds.
capped = decide(limit, attempt=8, policy=pol, now_fn=_now).sleep_seconds
check("backoff clamps to cap (3600)", capped == 3600, str(capped))

# Floor: attempt 1 never below floor.
check("backoff respects floor", decide(limit, 1, WaitPolicy(floor_seconds=90), _now).sleep_seconds == 90)


# --- reset_hint, absolute UTC (timezone discipline) ----------------------------
# now = 12:00:00Z, reset at 13:00:00Z  ->  exactly 3600s, computed in UTC.
abs_limit = Event(EventType.SESSION_LIMIT, HIGH, "limit", reset_hint="2026-06-29T13:00:00Z")
abs_wait = decide(abs_limit, attempt=1, policy=WaitPolicy(max_wait_seconds=6*3600), now_fn=_now).sleep_seconds
check("absolute UTC reset_hint -> 3600s", abs_wait == 3600.0, str(abs_wait))

# A reset already in the past -> floor only, never negative.
past_limit = Event(EventType.SESSION_LIMIT, HIGH, "limit", reset_hint="2026-06-29T11:00:00Z")
past_wait = decide(past_limit, attempt=1, policy=WaitPolicy(floor_seconds=60), now_fn=_now).sleep_seconds
check("past reset_hint -> floor, not negative", past_wait == 60.0, str(past_wait))


# --- reset_hint, relative duration ---------------------------------------------
rel_limit = Event(EventType.SESSION_LIMIT, HIGH, "limit", reset_hint="2 hours")
rel_wait = decide(rel_limit, attempt=1, policy=WaitPolicy(max_wait_seconds=6*3600), now_fn=_now).sleep_seconds
check("relative '2 hours' -> 7200s", rel_wait == 7200.0, str(rel_wait))


# --- UNKNOWN_INTERRUPTION ignores any hint, uses backoff -----------------------
unknown = Event(EventType.UNKNOWN_INTERRUPTION, "LOW", "abnormal", reset_hint="2 hours")
uw = decide(unknown, attempt=1, policy=WaitPolicy(floor_seconds=60), now_fn=_now)
check("UNKNOWN ignores hint, backoff floor", uw.action == RELAUNCH and uw.sleep_seconds == 60)


# --- Attempt cap -> GAVE_UP, no sleep ------------------------------------------
gp = decide(limit, attempt=5, policy=WaitPolicy(max_attempts=5), now_fn=_now)
check("attempt==max_attempts -> STOP/GAVE_UP", gp.action == STOP and gp.stop_state == GAVE_UP_STATE)
check("GAVE_UP -> zero sleep", gp.sleep_seconds == 0.0, str(gp.sleep_seconds))


# --- COMPLETED defensively bypasses (orchestrator handles it) ------------------
comp = Event(EventType.COMPLETED, HIGH, "done")
cd = decide(comp, attempt=1, now_fn=_now)
check("COMPLETED -> STOP/0s, no stop_state", cd.action == STOP and cd.sleep_seconds == 0.0 and cd.stop_state is None)


def main() -> int:
    failures = sum(1 for ok, _ in RESULTS if not ok)
    total = len(RESULTS)
    verdict = "[OK]" if failures == 0 else "[ERR]"
    print(f"[{_ts()}] {verdict} waiter: {total - failures}/{total} passed")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
