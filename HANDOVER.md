# HANDOVER

Last updated: 2026-06-29. Read this first; it gets a new session productive in
~30 seconds instead of re-deriving 13 turns of context.

## Where things stand

**The resume-fidelity spike is conclusively positive.** A structured resume
prompt makes a fresh `claude -p` session *continue* an interrupted multi-file
task instead of restarting — confirmed by BOTH the mechanical scorer and the
session transcript:

- Clean real-CLI run: `[RESULT] end_state=[OK] (5/5 done)
  resume_behavior=CONTINUED duplicated=none lost=none`.
- `resume_session.txt` showed claude explicitly recognizing alpha/bravo as
  already-done and editing only charlie/delta/echo — instruction-following, not
  inference. All five files carry the exact marker, no doubling.
- Baseline-alone = `False`: an uninterrupted single session does NOT finish, so
  the resume loop has real work — the orchestrator is not redundant.

**The orchestrator slice is built and green against fakes.**
`orchestrator.run_until_done` assembles the proven pieces — `detector.classify`
→ tree-`verify` → `waiter.decide` → `checkpoint_builder.build_checkpoint` →
`compose_resume_prompt` (untouched) → relaunch — with the closed exit set
(`COMPLETED`/`FATAL`/`GAVE_UP`) and `UNKNOWN_INTERRUPTION` routed through the same
recovery path as `SESSION_LIMIT`. `test_orchestrator.py` (16/16) proves the
headline 2/5 → 5/5 across 2–3 simulated session-limit interruptions (scored by
the real `scorer.py`, `is_doubled` clean), FATAL short-circuit, GAVE_UP at the
attempt cap, UNKNOWN recovery, false-done-token rejection (tree-truth), and no
real sleeping.

## THE NEXT MILESTONE (do this first, in a fresh session)

**Join the proven loop logic to the real `ClaudeCodeDriver`** — a real-CLI
orchestrator run against a real interrupted task across real cycles. The fakes
prove control flow; the spike proved real-CLI resume; those two have NOT been
joined yet. The join will surface real-CLI behavior the fakes can't:

- real session-limit detection on live `claude` output (the detector's known
  strings / degradation path against actual wording),
- real reset-time parsing feeding the waiter,
- multi-cycle git checkpoint state across genuine relaunches.

`run_until_done` already accepts a driver; pass a real `ClaudeCodeDriver` instead
of a fake. The wiring pattern is in `test_orchestrator.py::_run`.

## Pre-flight before ANY real `--driver claude` run (REQUIRED)

The auth/redirect setup is the thing that cost the most time; get it right up
front. Root cause history is in `DESIGN.md` findings #1–#10.

1. **Endpoint must be clean.** A shell profile here exports
   `ANTHROPIC_BASE_URL=http://localhost:11434` + `ANTHROPIC_AUTH_TOKEN=ollama`
   (an Ollama redirect). `ClaudeCodeDriver` strips both from the child env by
   default (`build_child_env`), so the harness is safe — but verify your shell
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
5. **PRE-FLIGHT COMMAND — run this and confirm it returns before anything else:**
   ```
   claude -p "reply OK"
   ```
   If it returns `OK`, auth+endpoint are good. If it returns
   `401 Invalid authentication credentials`, your endpoint/key is wrong — fix
   (1)/(2) before running the spike. If it *queues edits* on a real task,
   that's the permission/trust issue — fix (3)/(4).

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
| `harness/checkpoint.py` | `Checkpoint` model + `compose_resume_prompt` (the PROVEN format — do NOT modify) | via others |
| `harness/detector.py` | classify a `RunResult` → `Event` (layered, never exact-match-only) | `test_detector.py` 12 |
| `harness/waiter.py` | `decide` wait/relaunch vs stop; FATAL bypass; UTC clock injectable; never sleeps | `test_waiter.py` 16 |
| `harness/checkpoint_builder.py` | commit live tree (consistency boundary) → `Checkpoint`, completed inferred from git biased to pending | `test_checkpoint_builder.py` 15 |
| `harness/orchestrator.py` | the resilient run loop; closed exit set; owns all logging | `test_orchestrator.py` 16 |
| `harness/driver.py` | real CLI + fakes; exec resolution, endpoint assertion, UTF-8 capture, error surfacing, acceptEdits | `test_driver.py` 26 |
| `harness/run_spike.py` | the spike runner (baseline + interrupt + score + transcript persistence) | `test_run_spike_abort.py` 16 |
| `harness/scorer.py` | mechanical fidelity metrics (reads the filesystem, not stdout) | via others |
| `task/task_spec.py` | the verifiable marker task | — |
| `DESIGN.md` | full design + findings #1–#10 + OPENED gate | — |

## Hard rules (carried as production requirements, proven by failing without them)

Plain-text status labels (`[OK]/[ERR]/[INFO]/[WAIT]/[WARN]/[FATAL]/[GAVE_UP]`);
no credentials in code or logs (tokens masked ≤4 chars); one-thing-per-change;
cross-platform (Windows `.CMD` exec resolution, UTF-8 decode, read-only `.git`
cleanup); UTC-timestamped logging; `compose_resume_prompt` is an invariant —
change what goes INTO the checkpoint, never the formatter.

## Deliberate, not-yet-done (safe to pick up later)

- **Real-CLI orchestrator run** — the next milestone above.
- **Second interruption type** (e.g. context-window-full) — add to `detector`
  with its own truth-table row; routes through the existing recovery path.
- **`gitutil` consolidation** — `_git` is intentionally duplicated in
  `run_spike.py` and `checkpoint_builder.py`; unify once the orchestrator runner
  needs it too.

## Not committed (by design)

`.env*` and `.cache_ggshield/` are git-ignored (credentials / scanner state).
`_workbench/` (generated repos) and `results/*.json|*.txt` (generated artifacts)
are ignored too; `results/.gitkeep` keeps the directory.
