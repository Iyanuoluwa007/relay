# HANDOVER

Last updated: 2026-06-30. Read this first; it gets a new session productive in
~30 seconds instead of re-deriving the context.

## Where things stand

**The resume-fidelity spike is conclusively positive.** A structured resume
prompt makes a fresh `claude -p` session *continue* an interrupted multi-file
task instead of restarting, confirmed by BOTH the mechanical scorer and the
session transcript:

- Clean real-CLI run: `[RESULT] end_state=[OK] (5/5 done)
  resume_behavior=CONTINUED duplicated=none lost=none`.
- `resume_session.txt` showed claude explicitly recognizing alpha/bravo as
  already-done and editing only charlie/delta/echo, instruction-following, not
  inference. All five files carry the exact marker, no doubling.
- Baseline-alone = `False`: an uninterrupted single session does NOT finish, so
  the resume loop has real work, the orchestrator is not redundant.

**The orchestrator slice is built and green against fakes.**
`orchestrator.run_until_done` assembles the proven pieces, `detector.classify`
→ tree-`verify` → `waiter.decide` → `checkpoint_builder.build_checkpoint` →
`compose_resume_prompt` (untouched) → relaunch, with the closed exit set
(`COMPLETED`/`FATAL`/`GAVE_UP`) and `UNKNOWN_INTERRUPTION` routed through the same
recovery path as `SESSION_LIMIT`. `test_orchestrator.py` (16/16) proves the
headline 2/5 → 5/5 across 2–3 simulated session-limit interruptions (scored by
the real `scorer.py`, `is_doubled` clean), FATAL short-circuit, GAVE_UP at the
attempt cap, UNKNOWN recovery, false-done-token rejection (tree-truth), and no
real sleeping.

## MILESTONE DONE, real-CLI multi-cycle JOIN PROVEN

The join (orchestrator loop driving the REAL Claude Code CLI across cycles) is
implemented in `harness/run_real_orchestrator.py` and PROVEN green on the real
CLI: `--driver claude --interrupts 2` ran 3 real `claude -p` sessions, done-count
climbed `[3, 4, 5]`, `duplicated=none`, `[OK] JOIN PROVEN (multi-cycle)`, exit 0.
The orchestrator re-checkpointed from real accumulated git state and resumed a
fresh real session each cycle. `orchestrator.py` was NOT modified, the injection
lives in a driver wrapper stack:
`InjectedInterruptDriver( TranscriptPersistingDriver( ClaudeCodeDriver ) )`.

Interruptions are INJECTED (a synthetic SESSION_LIMIT after real partial work),
because we can't summon a real quota reset on demand, same partial-then-kill
mechanism the spike used. (Live session-limit DETECTION against real quota output
is the additive follow-on, below.)

Re-run any time (after the pre-flight below: `claude -p "reply OK"`, clean
endpoint, trusted folder, valid `ANTHROPIC_API_KEY`, finding #11):

```
python harness/run_real_orchestrator.py --driver claude --interrupts 2  # the real join
python harness/run_real_orchestrator.py --driver fake   --interrupts 2  # wiring, no CLI
python harness/run_real_orchestrator.py --driver greedy --interrupts 2  # must FAIL, exit 1
```

**Multi-cycle is ENFORCED.** Injected cycles REPLACE the prompt with a tight
single-file command (`_single_file_prompt`: names one file, forbids the others,
"do NOT continue") so the real session edits exactly one file and the orchestrator
must re-checkpoint and run another real session. With `--interrupts 2` there must
be exactly 3 real sessions and the done-count must climb `3 -> 4 -> 5`. The run
ASSERTS this (`real sessions=N (expected M)`, `per-cycle done-count=[...]`); a
finish-too-early run prints `MULTI-CYCLE NOT EXERCISED` and exits 1 -- it can no
longer falsely report a proven join. (Proven by the `greedy` negative control:
`python harness/run_real_orchestrator.py --driver greedy` finishes in one session
and FAILS loudly, exit 1.)

Healthy per cycle: `injected interruption after cycle N: K/5 done (inner rc=0)`
with K climbing one per cycle; the cycle transcript shows the single-file prompt
and real edits with substantial `stdout chars` (NOT "queued pending your
permission grant"); `event=SESSION_LIMIT`. Headline success:
`real sessions=3 (expected 3)`, `done-count=[3, 4, 5]`,
`[RESULT] ... (5/5 done) ... duplicated=none lost=none`, `[OK] JOIN PROVEN
(multi-cycle)`, exit 0. Unhealthy: `0 stdout chars` / "queued"
(permission/trust -> flip to `ACCEPT_EDITS_WITH_TOOLS`); `MULTI-CYCLE NOT
EXERCISED` with `sessions=1` (the tight prompt still didn't constrain claude ->
escalate to kill-after-first-file injection); `FATAL` (auth -> 401 in transcript);
`duplicated=` non-empty (resume re-did work). Read
`results/claude_real_orch_cycle{1,2,3}_session.txt` for any unhealthy cycle.

## THE NEXT MILESTONE (fresh session)

1. **Startup credential assertion (finding #11 implementation).** Relay requires
   an explicit `ANTHROPIC_API_KEY` because the interactive login does not
   authorize headless `claude -p` (DESIGN.md finding #11). Implement the
   companion to the endpoint assertion: at startup, resolve a single credential,
   assert a valid `ANTHROPIC_API_KEY` is present, log its SOURCE (never the
   value), and refuse to run otherwise, so a missing/expired key fails BEFORE a
   cycle, not by 401-ing mid-run. Small, well-specified: driver/entrypoint + one
   test, same one-module discipline. (The orchestrator's FATAL path already
   handles a live mid-run 401 gracefully; this is the preventive complement.)
2. **Live session-limit DETECTION against real quota output (additive).** The
   join used INJECTED limits; validate `detector.classify` against REAL `claude`
   limit-output samples (captured when a real quota limit is hit) so the
   known-strings / degradation path matches the actual wording.

Other follow-ons: a harder real task (stress test, one variable at a time), and
the `gitutil` consolidation below.

## Pre-flight before ANY real `--driver claude` run (REQUIRED)

The auth/redirect setup is the thing that cost the most time; get it right up
front. Root cause history is in `DESIGN.md` findings #1–#11.

1. **Endpoint must be clean.** A shell profile here exports
   `ANTHROPIC_BASE_URL=http://localhost:11434` + `ANTHROPIC_AUTH_TOKEN=ollama`
   (an Ollama redirect). `ClaudeCodeDriver` strips both from the child env by
   default (`build_child_env`), so the harness is safe, but verify your shell
   isn't pointing the *interactive* CLI at Ollama either.
2. **Single credential source.** Use a valid `ANTHROPIC_API_KEY` (or a clean
   claude.ai login). Confirm `ANTHROPIC_BASE_URL` resolves to
   `api.anthropic.com` (or is unset) and no stray `ANTHROPIC_AUTH_TOKEN`.
3. **Trust the folder.** Headless edits need the working folder trusted; clear
   the folder-trust prompt once interactively if it appears.
4. **Permission model.** The driver uses `--permission-mode acceptEdits`
   (auto-accepts file edits, no human prompt). If a CLI version still queues
   edits, flip `DEFAULT_PERMISSION_ARGS` to `ACCEPT_EDITS_WITH_TOOLS` in
   `harness/driver.py` (adds `--allowedTools "Edit,Write"`).
5. **PRE-FLIGHT COMMAND, run this and confirm it returns before anything else:**
   ```
   claude -p "reply OK"
   ```
   If it returns `OK`, auth+endpoint are good. If it returns
   `401 Invalid authentication credentials`, your endpoint/key is wrong, fix
   (1)/(2) before running the spike. If it *queues edits* on a real task,
   that's the permission/trust issue, fix (3)/(4).

## How to run

```bash
# Validate all harness/loop logic (no CLI, no quota):
python harness/test_detector.py
python harness/test_waiter.py
python harness/test_driver.py
python harness/test_run_spike_abort.py
python harness/test_checkpoint_builder.py
python harness/test_orchestrator.py
python harness/run_spike.py --driver fake          # expect CONTINUED, exit 0
python harness/run_spike.py --driver fake-restart  # expect DUPLICATED, exit 1

# The real measurement (after the pre-flight above):
python harness/run_spike.py --driver claude
# Then read: results/baseline_session.txt (does it EDIT, not queue?),
#            the [RESULT] line (5/5?), results/resume_session.txt (follows
#            our next_action, or infers?).
```

## Module map

| File | Role | Tests |
|---|---|---|
| `harness/checkpoint.py` | `Checkpoint` model + `compose_resume_prompt` (the PROVEN format, do NOT modify) | via others |
| `harness/detector.py` | classify a `RunResult` → `Event` (layered, never exact-match-only) | `test_detector.py` 12 |
| `harness/waiter.py` | `decide` wait/relaunch vs stop; FATAL bypass; UTC clock injectable; never sleeps | `test_waiter.py` 16 |
| `harness/checkpoint_builder.py` | commit live tree (consistency boundary) → `Checkpoint`, completed inferred from git biased to pending | `test_checkpoint_builder.py` 15 |
| `harness/orchestrator.py` | the resilient run loop; closed exit set; owns all logging | `test_orchestrator.py` 16 |
| `harness/driver.py` | real CLI + fakes; exec resolution, endpoint assertion, UTF-8 capture, error surfacing, acceptEdits | `test_driver.py` 26 |
| `harness/run_spike.py` | the spike runner (baseline + interrupt + score + transcript persistence) | `test_run_spike_abort.py` 16 |
| `harness/scorer.py` | mechanical fidelity metrics (reads the filesystem, not stdout) | via others |
| `task/task_spec.py` | the verifiable marker task |, |
| `DESIGN.md` | full design + findings #1–#10 + OPENED gate |, |

## Hard rules (carried as production requirements, proven by failing without them)

Plain-text status labels (`[OK]/[ERR]/[INFO]/[WAIT]/[WARN]/[FATAL]/[GAVE_UP]`);
no credentials in code or logs (tokens masked ≤4 chars); one-thing-per-change;
cross-platform (Windows `.CMD` exec resolution, UTF-8 decode, read-only `.git`
cleanup); UTC-timestamped logging; `compose_resume_prompt` is an invariant,
change what goes INTO the checkpoint, never the formatter.

## Deliberate, not-yet-done (safe to pick up later)

- **Real-CLI orchestrator run**, the next milestone above.
- **Second interruption type** (e.g. context-window-full), add to `detector`
  with its own truth-table row; routes through the existing recovery path.
- **`gitutil` consolidation**, `_git` is intentionally duplicated in
  `run_spike.py` and `checkpoint_builder.py`; unify once the orchestrator runner
  needs it too.

## Not committed (by design)

`.env*` and `.cache_ggshield/` are git-ignored (credentials / scanner state).
`_workbench/` (generated repos) and `results/*.json|*.txt` (generated artifacts)
are ignored too; `results/.gitkeep` keeps the directory.
