# Resume-Fidelity Spike

A small, self-contained experiment that measures one thing: **when an
interrupted coding-agent session is handed a structured resume prompt, does it
continue the task mid-stream, or restart and duplicate work?**

This is a de-risking spike for a larger resilient task-orchestrator project.
The orchestrator is only worth building if resume actually works, so this
measures that cheaply and objectively before any further investment.

## What it does

1. Seeds a small git project of marker files (a deterministic, mechanically
   verifiable stand-in for a real multi-file refactor).
2. Runs the task to completion once to establish a **baseline** end-state.
3. Runs it again but **interrupts** after N files (a simulated session kill),
   builds a `Checkpoint` from the partial state plus the git diff, composes a
   **resume prompt**, and runs a second session with it.
4. **Scores** the final state mechanically:
   - `end_state_ok` -- did the task reach its verifiable done-state?
   - `continued` -- did resume continue (no re-done files) or restart?
   - `duplicated_files` -- files where work was redone.
   - `lost_files` -- previously-complete files that regressed.

No LLM judges the outcome. The result is a number you can trust.

## Why these design choices

- **Git is the state store.** The checkpoint references a commit SHA and the
  diff reconstructs "what changed", rather than snapshotting files. More robust
  and more honest than a custom snapshot scheme.
- **The task is mechanically verifiable.** "Did resume reach the same state"
  is a boolean, not a judgment, which is the whole point of a spike.
- **Pluggable driver.** `fake` and `fake-restart` validate the harness with no
  quota; `claude` runs the real measurement.

## Running it

```bash
# Validate the harness logic (no CLI, no quota):
python harness/run_spike.py --driver fake          # expect: CONTINUED, exit 0
python harness/run_spike.py --driver fake-restart  # expect: DUPLICATED, exit 1

# The real measurement (requires Claude Code CLI on PATH, run locally):
python harness/run_spike.py --driver claude
```

Results land in `results/`:
- `<driver>_checkpoint.json` -- the state captured at interruption
- `<driver>_resume_prompt.txt` -- the exact prompt the resumed session saw
- `<driver>_report.json` -- the fidelity metrics

## Interpreting the result

- **CONTINUED + end_state_ok** -> resume works; the orchestrator is worth
  building, and the resume-prompt format in `checkpoint.py` is the thing that
  made it work.
- **RESTARTED/DUPLICATED** or **lost files** -> resume is unreliable as-is.
  That finding -- plus the `resume_prompt.txt` that produced it -- is itself
  the valuable output: it tells you what to fix before building anything.

## Layout

```
harness/
  checkpoint.py   # state model + resume-prompt generator (the core)
  driver.py       # Claude Code driver + fake drivers for testing
  scorer.py       # objective fidelity metrics
  run_spike.py    # experiment runner
task/
  task_spec.py    # the scripted, verifiable task
results/          # generated artifacts
```

## Notes

- Python 3.11+, standard library only. No credentials are handled by this code;
  the real driver relies on the Claude Code CLI's own stored auth.
- Status labels in output are plain-text (`[OK]`, `[ERR]`, `[INFO]`).
