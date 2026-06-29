"""
orchestrator.py -- The resilient run loop: detect -> wait -> checkpoint -> resume.

Assembles the proven, side-effect-free pieces into the control loop that survives
interruptions and drives a task to completion:

  run -> classify(event) -> verify(tree) ->
      tree complete        : COMPLETED (done)
      FATAL                : unrecoverable (auth, missing CLI) -> stop, no retry
      recoverable          : waiter.decide -> wait -> build_checkpoint ->
                             compose_resume_prompt -> relaunch
      attempt cap reached  : GAVE_UP

Closed exit set: COMPLETED | FATAL | GAVE_UP. There is no fourth outcome, so
"stuck forever" and "quietly wrong" have nowhere to live. UNKNOWN_INTERRUPTION is
routed through the SAME recovery path as SESSION_LIMIT (a type-miss degrades to
generic recovery, never to a false success).

Completion is confirmed from the TREE (the `verify` predicate), never from
captured stdout alone -- capture-robustness and score-correctness are independent
(finding #6). A session that emits the done-token but whose tree still has pending
work is treated as recoverable, not complete.

This module owns ALL logging (UTC, plain-text labels: [INFO] [OK] [WAIT] [WARN]
[FATAL] [GAVE_UP]). The detector, waiter, and checkpoint_builder stay pure;
orchestration is where the effects (subprocess, sleep, git, logs) live.

Python 3.11+. Standard library only (plus local modules).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from detector import classify, Event, EventType
from waiter import decide, WaitPolicy, STOP, FATAL_STATE
from checkpoint_builder import build_checkpoint, TaskDef
from checkpoint import compose_resume_prompt


def _ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# Terminal states (closed set). Plain-text labels; these match waiter's stop_state
# strings ("FATAL"/"GAVE_UP") so a waiter STOP maps straight through.
COMPLETED = "COMPLETED"
FATAL = "FATAL"
GAVE_UP = "GAVE_UP"


@dataclass
class LoopResult:
    state: str        # COMPLETED | FATAL | GAVE_UP
    attempts: int
    reason: str


def run_until_done(
    task: TaskDef,
    driver,
    start_sha: str,
    is_unit_done: Callable[[Path, str], bool],
    verify: Callable[[Path], bool],
    initial_prompt: str,
    policy: Optional[WaitPolicy] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Optional[Callable[[], object]] = None,
    done_token: str = "DONE_TASK_COMPLETE",
) -> LoopResult:
    """Drive `task` to completion across interruptions, or to a bounded stop.

    The first session uses `initial_prompt`; every relaunch uses a freshly built
    resume prompt (checkpoint of the live tree -> compose_resume_prompt, untouched).
    Returns one of the closed terminal states.
    """
    policy = policy or WaitPolicy()
    wd = Path(task.working_dir)
    prompt = initial_prompt
    attempt = 0

    while True:
        attempt += 1
        print(f"[{_ts()}] [INFO] attempt {attempt}: launching session")
        res = driver.run(prompt, cwd=str(wd))

        # Tree-truth completion check FIRST: confirm from the filesystem, never
        # from captured stdout (finding #6 independence).
        if verify(wd):
            print(f"[{_ts()}] [OK] objective verified complete on disk after "
                  f"{attempt} attempt(s)")
            return LoopResult(COMPLETED, attempt, "objective verified complete on disk")

        event = classify(res, done_token=done_token)
        print(f"[{_ts()}] [INFO] event={event.type.value} ({event.confidence}): "
              f"{event.evidence}")

        # FATAL: never retried, never waited on.
        if event.type == EventType.FATAL:
            print(f"[{_ts()}] [FATAL] {event.evidence}")
            return LoopResult(FATAL, attempt, event.evidence)

        # Done-token but the tree disagrees -> a false claim. Recover, don't stop.
        if event.type == EventType.COMPLETED:
            print(f"[{_ts()}] [WARN] completion token present but tree still has "
                  f"pending work; recovering")
            event = Event(
                EventType.UNKNOWN_INTERRUPTION, "LOW",
                "session emitted done-token but tree still has pending work",
            )

        # Recoverable (SESSION_LIMIT / UNKNOWN_INTERRUPTION): wait then relaunch,
        # unless the attempt cap is hit.
        decide_kwargs = {"policy": policy}
        if now_fn is not None:
            decide_kwargs["now_fn"] = now_fn
        decision = decide(event, attempt, **decide_kwargs)

        if decision.action == STOP:
            # FATAL is handled above, so a STOP here is the attempt cap -> GAVE_UP.
            label = "[FATAL]" if decision.stop_state == FATAL_STATE else "[GAVE_UP]"
            print(f"[{_ts()}] {label} {decision.reason}")
            return LoopResult(decision.stop_state, attempt, decision.reason)

        print(f"[{_ts()}] [WAIT] {decision.reason}")
        sleep_fn(decision.sleep_seconds)

        # Re-checkpoint the live tree (the consistency boundary) and build the
        # next resume prompt from it. compose_resume_prompt stays untouched.
        cp, diff = build_checkpoint(task, start_sha, is_unit_done)
        prompt = compose_resume_prompt(cp, diff)
        print(f"[{_ts()}] [INFO] re-checkpointed: {len(cp.completed_files)} done, "
              f"{len(cp.pending_files)} pending; relaunching as resume")
