# Relay

### Session Continuity for Claude Code

> Like a relay runner handing off the baton mid-stride — the work continues
> across the interruption without dropping a step.

**Relay** is a fault-tolerant orchestration system that lets a coding agent
(Claude Code) **resume a long-running task exactly where it stopped** after an
interruption — a session limit, a crash, a network drop, a reboot — instead of
starting over.

This repository is built around one principle: **prove the hard assumption with
a measurement before building anything on top of it.** The hard assumption here
is that an interrupted agent, handed a structured summary of its own progress,
will *continue* mid-task rather than *restart and duplicate work*. That claim is
not obvious, so this project measures it first, then builds the orchestrator on
the proven result.

---

## The idea

Autonomous coding agents hit interruptions: usage limits reset hours later,
sessions time out, machines reboot, connections drop. The naive recovery —
relaunch and re-prompt — makes the agent forget where it was and redo (or undo)
completed work. A reliable continuation system must instead:

1. **Detect** the interruption robustly (not by brittle exact-string matching).
2. **Checkpoint** durable task state (git is the source of truth, not a custom
   snapshot scheme).
3. **Wait** correctly until the resource is available (timezone-aware, no busy
   loops, exponential backoff when no reset time is given).
4. **Relaunch and resume** by handing the agent a structured summary that makes
   it continue from the exact point of interruption.
5. **Verify completion from the working tree**, never from the agent's own
   claim of success.
6. **Stop** only on a closed set of terminal outcomes — `COMPLETED`, `FATAL`,
   or `GAVE_UP` — so "stuck forever" and "quietly wrong" have nowhere to live.

The end goal is a near-continuous autonomous development workflow that survives
session limits and faults with minimal human intervention, while staying
recoverable and under clear user control.

---

## Why a "spike" first

A spike is a small, throwaway-quality experiment that answers one question
cheaply before committing to a full build. The question here:

> When an interrupted session is handed a structured resume prompt, does the
> agent **continue** the task, or **restart and duplicate/lose** work?

The spike is a **measurement harness**, not a wrapper. It runs a deterministic,
mechanically-verifiable task, interrupts it, builds a resume prompt from the
partial state, runs a second session with that prompt, and **scores the outcome
objectively** — did it continue, did it re-do completed work, did it reach the
same end state? No LLM judges the result; the score is computed from the files
on disk.

**The verdict: resume works.** A halted session, given the structured prompt,
continued mid-task and respected already-completed work (`5/5` complete,
`duplicated=none`, `lost=none`), confirmed both by the mechanical score and by
the session transcript in which the agent explicitly recognised the
already-done files and edited only the remaining ones.

Because that result is positive, the orchestrator was built — on evidence
rather than hope.

---

## What's in the box

```
harness/
  checkpoint.py          # Checkpoint state model + compose_resume_prompt (the proven core)
  driver.py              # Claude Code driver (+ fake drivers for quota-free testing)
  detector.py            # Pure-function interruption classifier (closed Event set)
  waiter.py              # Wait/relaunch/stop decisions (clock-injected, no real sleep)
  checkpoint_builder.py  # Builds a Checkpoint from a live git tree at interruption
  scorer.py              # Objective fidelity metrics (reads the tree, not stdout)
  run_spike.py           # The measurement experiment runner
  orchestrator.py        # Assembles the loop: detect -> wait -> checkpoint -> resume
  test_*.py              # Mechanical tests for every module
task/
  task_spec.py           # The scripted, verifiable task
results/                 # Generated transcripts and reports (gitignored)
DESIGN.md                # Design of record + findings #1-#10 (the build story)
HANDOVER.md              # Cold-start guide for the next session
```

### Design principles carried through every module

- **Git is the checkpoint store.** A commit SHA names a coherent snapshot; the
  diff reconstructs "work so far". Nothing is custom-serialised that git already
  tracks.
- **Completion is verified, not assumed.** The scorer reads the working tree
  (`is_done` on actual files). Capture-robustness and score-correctness are
  independent: a failure to read the agent's output can never silently flip a
  completion verdict.
- **The closed exit set.** Every run ends in exactly one of `COMPLETED`,
  `FATAL`, `GAVE_UP`. There is no fourth outcome, by construction.
- **Conservative state inference.** When it is uncertain whether a file was
  finished before interruption, it is treated as *pending* (a benign re-do),
  never as *done* (a silent loss).
- **Fail loud, never a false green.** A non-zero exit aborts before scoring and
  surfaces the captured output, rather than scoring a no-op as success.

---

## Getting started

### Requirements

- Python 3.11+ (standard library only — no pip install needed for the harness)
- git
- For real runs: the Claude Code CLI (`claude`) on PATH, authenticated

### 1. Validate the harness with no quota

The fake drivers exercise the entire experiment without the real CLI:

```bash
python harness/run_spike.py --driver fake          # expect: CONTINUED, exit 0
python harness/run_spike.py --driver fake-restart  # expect: DUPLICATED, exit 1
```

The first proves a clean resume; the second is a negative control that proves
the scorer actually catches a restart. If both behave, the harness logic is
sound.

### 2. Run the real measurement

```bash
python harness/run_spike.py --driver claude
```

Read `results/baseline_session.txt`, `results/resume_session.txt`, and
`results/claude_report.json`. The headline numbers are `done_count` (did it
finish) and `duplicated` (did it respect completed work).

> **Pre-flight for real runs.** Confirm the CLI authenticates and targets the
> real endpoint before a full run:
> ```bash
> claude -p "reply OK"
> ```
> If this returns `OK`, you're good. If it returns a 401, your environment has a
> redirected `ANTHROPIC_BASE_URL` or a stale credential — see HANDOVER.md for the
> full decision tree. (This project's driver explicitly strips non-Anthropic
> endpoint redirects so an unattended run can't be silently pointed elsewhere.)

### 3. Run the tests

```bash
for t in harness/test_*.py; do python "$t"; done
```

---

## How the orchestrator works

```
run agent
  -> detector.classify(result)
       success + tree complete -> COMPLETED
       FATAL (e.g. auth failure) -> stop, no retry, no wait
       recoverable / UNKNOWN    -> waiter.decide
                                     -> sleep until reset (or backoff)
                                     -> checkpoint_builder.build_checkpoint(tree)
                                     -> compose_resume_prompt(checkpoint)
                                     -> relaunch
       attempt cap reached       -> GAVE_UP
```

The orchestrator's headline acceptance test drives a task from a `2/5` partial
state to `5/5` across multiple simulated interruptions, scored by the real
scorer, with no duplicated work throughout.

---

## Status

| Component | State |
|---|---|
| Resume-fidelity spike | **Positive** — resume continues, verified mechanically and by transcript |
| Orchestrator (thinnest vertical slice) | **Built and green** against deterministic fakes |
| Real-CLI orchestrator run | Next milestone — joining the proven loop to a live multi-cycle run |

The thinnest vertical slice from the design is complete: detect one interruption
type, checkpoint, wait, relaunch, resume, drive to completion, on one project,
with the closed exit set. Everything deliberately deferred (work queue,
parallel projects, additional event types, notifications) is *additive* and
bolts onto this loop without changing its control flow.

---

## Roadmap

- **Real-CLI orchestrator run** — live session-limit detection, real reset-time
  parsing, multi-cycle git state against the actual CLI.
- **Additional interruption types** — context-window-full, network drop, each a
  new detector row routing through the existing recovery path.
- **Notifications** — desktop, then one webhook (paused, resumed, completed,
  unrecoverable).
- **Work queue** — multiple projects with optional task-switching while one
  waits for reset.

### Relay for VS Code — coming soon

A VS Code extension is planned to bring Relay's orchestration loop into the
editor: start, pause, resume, and monitor long-running autonomous tasks; live
status and progress; and one-click resume after a session limit — all without
leaving your workspace. The CLI/daemon core in this repository is the engine it
will wrap.

---

## A note on methodology

Relay is as much about *how* it was built as what it builds. It began as a
**resume-fidelity spike** — a throwaway-quality experiment to measure the one
load-bearing assumption before building anything on it. That spike caught a long
series of real defects on a five-file toy task — Windows
executable resolution, swallowed error output, false-green scoring, a confounded
task prompt, an endpoint redirect, a stale credential, a folder-trust gate, a
UTF-8 decode crash, headless permission gating, and weak resume instructions —
each of which would have been a silent, load-bearing failure in an unattended
orchestrator running for days. `DESIGN.md` records each as a numbered finding.
Finding them early, on something cheap, is the entire reason the spike exists.

---

## License & attribution

Independent project by Oke Iyanuoluwa E. See `DESIGN.md` for the full design
record and `HANDOVER.md` for contributor cold-start instructions.
