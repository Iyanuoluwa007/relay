# Resilient Task Orchestrator — DESIGN

Status: APPROVED 2026-06-29 with three amendments (recorded below). No
implementation in this document.

## Decisions of record (approval amendments)

1. **`completed_files` is inferred from git, biased to pending.** The
   progress-marker path is rejected, not deferred-with-a-hook: self-reported
   state from a session that just crashed is the least trustworthy source and
   directly contradicts the commit-is-the-boundary invariant (§5.2). Progress
   markers are a v2 optimization, justified only if measured redo waste demands
   it.
2. **The headline acceptance test is completion, not clean resume.** From the
   known 2/5 partial state, the control loop must drive the task to **5/5**
   (`end_state_ok: true`) across **at least 2–3** simulated `session-limit`
   interruptions, scored by the existing `scorer.py`, with `is_doubled` clean
   throughout. Not reaching 5/5 means the slice is not done. This is promoted to
   the primary test (§6), not one row in a table.
3. **The baseline question is resolved before orchestrator code.** The proven
   result was `continued:true` but `end_state_ok:false` (2/5). The spike's
   baseline prompt references `task_spec` internals the agent cannot see, so it
   is corrected and a single UNINTERRUPTED real session is measured: does it
   reach 5/5? We must know whether 2/5 was a weak prompt or a real need for the
   loop before measuring the loop against it. (Findings appended under
   "Baseline measurement" below.)

Preserved invariants: `compose_resume_prompt` untouched; closed exit set
(`COMPLETED`/`FATAL`/`GAVE_UP`); `UNKNOWN_INTERRUPTION` routes through the same
recovery path as `SESSION_LIMIT`. Hard rules unchanged.

## Baseline measurement (amendment 3 findings) — 2026-06-29

**Question:** was the spike's `continued:true / end_state_ok:false (2/5)` a weak
prompt or a real need for the loop?

**Answer: neither — it was instrumentation failure.** Two independent confounds
were hiding the real baseline; both are now fixed, and the real number is blocked
only by an environment auth limit outside the code.

1. **RETRACTED — the original real-claude result was vacuous (Windows exec
   resolution).** `ClaudeCodeDriver` invoked the bare name `["claude", ...]`. On
   Windows that resolves only `claude.exe`, but the CLI is a `claude.CMD` npm
   shim, so `subprocess.run` raised `FileNotFoundError` and the driver's
   except-branch returned a no-op `RunResult` *without* launching anything. In
   the interrupt path, `_partial_apply` had already completed 2/5 files and
   committed them; a no-op resume then touches nothing, yielding exactly
   `done_count 2/5`, `duplicated:[]`, `lost:[]`, `continued:true`,
   `end_state_ok:false` — the recorded report. **claude never ran. The 2/5
   `continued:true` result in `results/claude_report.json` is therefore RETRACTED
   in full, not amended** — it measured nothing about model behavior. This driver
   is reused verbatim by the orchestrator, so the fix was load-bearing, not
   cosmetic. Fix: resolve via `shutil.which("claude")` (honors PATHEXT, finds the
   `.CMD` shim), falling back to the bare name so a genuine missing-CLI still
   surfaces. Verified: bare name raises `FileNotFoundError`; resolved path runs
   `rc=0`.
2. **The baseline prompt was confounded.** It referenced `task_spec.all_done()`
   and an undefined "standard header transform" — symbols invisible to the agent
   — so no real session could produce the exact markers the scorer checks. Fix:
   `run_spike._baseline_prompt()` renders the literal `TODO_MARKER` /
   `DONE_MARKER` / `BANNER` / filenames from `task_spec` into self-contained
   prose (the spec stays the source of truth; the agent sees only actable text).
3. **The real number is still unmeasured in this environment.** After both
   fixes, a nested `claude -p` returns `401 Invalid authentication credentials`
   — the outer session does not propagate auth to a nested non-interactive
   invocation here. This is precisely the detector's `FATAL` case (auth failure,
   do not retry), so it was not looped on. **To obtain the real baseline, run in
   a normally-authenticated terminal:** `python harness/run_spike.py --driver
   claude` and read the `baseline reached done-state` line.

4. **The harness no longer fails silently (two defects fixed).** A subsequent
   real run (auth working) exited `rc=1` after real time — claude launched and
   *errored* — but the harness hid it: the log's `failed:` line printed only
   `res.stderr`, which was empty because claude reports some errors (auth) on
   **stdout**, and `run_interrupt` then fell through to `score()`, which printed
   `CONTINUED 2/5` on a no-op session — a false green, the retracted bug one
   layer down. Fixes: (a) `ClaudeCodeDriver` surfaces **both** stdout and stderr
   on any non-zero exit; (b) `run_interrupt` gates scoring on `res.ok` and, on
   error, prints both streams and returns `None`, so `main()` exits `2` with
   `[ABORT]` rather than scoring. Verified live: the previously-blank failure now
   shows `Failed to authenticate ... 401` and the run aborts instead of
   false-greening. Covered by `test_driver.py` and `test_run_spike_abort.py`.

5. **Endpoint assertion — the 401 root cause was an inherited base-URL redirect
   (driver fix, not a workaround).** With `ANTHROPIC_BASE_URL` / `AUTH_TOKEN` /
   `API_KEY` confirmed empty, bare `claude -p` still 401'd; the shell profile
   exports `ANTHROPIC_BASE_URL=http://localhost:11434` +
   `ANTHROPIC_AUTH_TOKEN=ollama` on every shell, and the harness subprocess
   silently inherited it — sending claude.ai credentials to Ollama, which
   rejects them. Fix in `ClaudeCodeDriver` (the one chokepoint the spike and the
   orchestrator share): it builds an explicit child env via `build_child_env()`
   that, by default, strips `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN` so
   the child falls back to the CLI's stored login and can never be redirected by
   accident. A stripped non-official base URL logs `[WARN] stripped non-Anthropic
   base URL <value>`; a retained custom URL (opt-in `allow_custom_base_url=True`)
   still warns — a redirect is never silent. Token values are masked to ≤4 chars
   (no credentials in logs). This is the design's "confirm WHERE requests go, not
   just what key" rule made concrete. Verified by `test_driver.py` (15/15),
   including that both vars are removed from the child env even when the parent
   sets them, and that the token is never logged in full. (This environment can
   only unit-prove it: its own `ANTHROPIC_BASE_URL` is already the official host
   with no propagated credentials, a separate auth condition, so the real
   end-to-end baseline still awaits an authenticated terminal with the
   folder-trust prompt cleared.)

6. **Output capture hardened against a Windows cp1252 decode crash.** With auth
   solved (valid `ANTHROPIC_API_KEY` + redirect stripped), claude launched for
   real (baseline `rc=0` in 7.4s, resume `rc=0` in 36.4s) — but the subprocess
   reader thread hit `UnicodeDecodeError: 'charmap' codec can't decode byte 0x8f`
   because claude emitted a non-cp1252 byte (smart quote / em-dash / box char)
   and Windows defaulted to cp1252. The session *succeeded* but its output was
   lost, so that run's `2/5` is **retracted as decode-corrupted**. Fix: all four
   `subprocess.run` sites (`driver.py`; the `_git` helpers in `run_spike.py`,
   `checkpoint_builder.py`, and `test_checkpoint_builder.py` — a git diff can
   carry non-cp1252 file-content bytes, so the same crash would have surfaced
   there later) now pass `encoding="utf-8", errors="replace"`: a stray byte
   becomes a replacement char, never a crash. Also fixed a fragile
   trailing-space separator in `scorer.summary()` that whitespace-trimming
   collapsed into `nonelost`; separators now lead each segment. Verified by
   `test_driver.py` (22/22), which drives a real child emitting a lone invalid
   `0x8f` plus valid UTF-8 and asserts capture succeeds without raising.

   *Independence note (for the orchestrator design):* the scorer reads the
   **filesystem** (`is_done` on the actual files), not captured stdout, so the
   encoding crash could only ever destroy CAPTURE, never the SCORE. The last run
   showed `ERR/2/5` solely because the crash aborted the run *before* scoring,
   not because the score was wrong. Score-correctness and capture-robustness are
   independent properties — the orchestrator must not conflate "couldn't read the
   session" with "the work is incomplete"; it confirms completion from the tree,
   and treats a capture failure as a retryable/abortable run, not a result.

7. **The real blocker surfaced: `claude -p` exits rc=0 but edits no files.** The
   first fully clean run (auth ok, redirect stripped, UTF-8 capture) gave
   baseline `False` and a resume that ran ~40s, exited `rc=0`, and touched
   nothing — charlie/delta/echo still held the raw TODO marker, zero edits.
   `claude_report.json` scored `done_count 2` (both pre-seeded) with
   `CONTINUED` — technically true (nothing duplicated/lost) but substantively
   false: **claude did not act.** The baseline almost certainly failed the same
   way. So every fidelity number to date was measured on top of an invocation
   that doesn't write files; **resume fidelity is still UNMEASURED.** Two
   responses: (a) persist every session's stdout/stderr to
   `results/<phase>_session.txt` regardless of exit code, so a rc=0-but-did-
   nothing run is debuggable (the finding-#6 principle made operational; the log
   line's stdout char-count flags a silent no-op at a glance); (b) determine
   whether `claude -p` can edit files here at all — if it CAN with an explicit
   imperative prompt, the baseline/resume prompts must be rewritten to command
   the edit ("edit the file on disk now") rather than describe the transform; if
   it CANNOT, headless file-editing needs the right flag/permission-mode in the
   driver (the shared chokepoint) before any fidelity measurement is meaningful.
   Until (b) resolves, **file-editing in `-p` mode is UNPROVEN and no fidelity
   verdict is recorded.** Fix (a) covered by `test_run_spike_abort.py` (16/16:
   transcript persists for errored, happy, and baseline phases).

8. **RESOLVED: `claude -p` edits files — the blocker was prompt imperativeness,
   not the driver.** A direct probe edited a file on disk with:
   *"Edit the file alpha.py ... replace the line '<TODO>' with '<DONE>'. Make the
   edit to the file on disk now."* What made it act, versus the harness prompt
   that didn't: it (a) names the file explicitly, not "every target file" / "the
   transform"; (b) gives the literal before/after strings; (c) commands the edit
   imperatively ("make the edit to the file on disk now"); (d) is a concrete file
   operation with no abstraction layer. Both prompt seams were rewritten to this
   shape, rendering literals from `task_spec` (still spec-synced): `
   _baseline_prompt()` now enumerates each target file with a literal per-file
   replace command ending "Make these edits to the files on disk now", and
   `checkpoint_builder`'s resume `next_action` shifted from "Apply the header
   transform to charlie.py" to "Edit charlie.py on disk now: replace the literal
   line '<TODO>' with '<DONE>' followed by '<BANNER>'. Make the edit to the file
   on disk now." `compose_resume_prompt` stays untouched — this is purely what
   goes INTO the prompt content. Driver, permissions, and flags are confirmed
   correct and unchanged. The transcript persistence (finding #7a) will confirm
   from `baseline_session.txt` that claude now edits rather than narrates.

9. **Permission model: `--dangerously-skip-permissions` is silently ignored;
   switched to `--permission-mode acceptEdits`.** The transcript (fix #7a) showed
   claude was neither narrating nor misformatting — it QUEUED the edits and
   blocked on a permission grant that never arrives headlessly ("the edit calls
   are queued pending your permission grant"), exiting `rc=0` with files
   untouched. The manual single-file probe wrote because it was run by hand;
   the harness subprocess invocation did not honor the skip flag. Fix in the
   shared chokepoint `ClaudeCodeDriver`: default `extra_args` is now
   `--permission-mode acceptEdits` (auto-accepts file edits with no human prompt
   — the unattended behavior the orchestrator needs in production). A one-symbol
   fallback `ACCEPT_EDITS_WITH_TOOLS` (adds `--allowedTools "Edit,Write"`) is
   pre-defined if acceptEdits alone proves insufficient on some CLI version.
   Verified by `test_driver.py` (25/25): the flag reaches the actual command and
   the skip flag is gone.

   *Diagnosed, not yet fixed — resume-content gap (run_spike inline checkpoint).*
   The resume session reported its context "arrived WITHOUT the original task
   instructions, so I inferred the task from file contents and commit history."
   Root cause: `run_interrupt` builds its Checkpoint INLINE
   (`run_spike.py:196-205`) with the OLD weak content (`objective="Apply the
   standard header transform..."`, `next_action="Apply the header transform to
   <file>"`) — the describe-the-transform shape finding #8 proved claude does not
   act on — and includes the git diff, so claude infers from commit history. The
   imperative/literal rewrite (finding #8) was applied to `checkpoint_builder.py`
   (the orchestrator path) but NOT to this inline spike path. This does NOT affect
   the BASELINE gate (the baseline uses `_baseline_prompt()`, already imperative);
   it affects resume-fidelity quality. Fix is the same proven pattern; queued
   pending confirmation that permissions now let edits apply.

10. **Refined permission diagnosis (a split) + inline resume content fixed.** A
    follow-up probe showed `--dangerously-skip-permissions` DID grant edits when
    run by hand interactively, but NOT through the harness subprocess. So the flag
    is not universally ignored — it fails to survive the harness invocation path.
    `build_child_env` is ruled out as the cause: it pops only the two endpoint
    vars and preserves `PATH`/`USERPROFILE`/`HOME`/`CLAUDE_*`, so nothing
    permission- or trust-related is stripped. Remaining suspects are arg placement
    and folder-trust on the fresh temp repo. Responses: (a) control flags now
    precede `-p` (conventional `claude [options] -p <prompt>`), so the prompt
    value can't swallow them; (b) every run logs `[INFO] permission/control
    flags: ...` and `test_driver.py` (26/26) asserts the flag is in argv AND
    precedes `-p` — argv verified, not assumed: `[claude, --permission-mode,
    acceptEdits, -p, <prompt>]`. The acceptEdits switch (finding #9) is retained
    as the correct unattended model but must still be PROVEN through the harness
    by the next transcript (does claude edit, not queue?), since it was first
    chosen on the now-corrected "flag universally ignored" theory. Folder-trust
    remains an open suspect the transcript will expose. Separately, the inline
    resume checkpoint (`run_spike.py`) was rewritten to the imperative/literal/
    on-disk-now shape (the finding-#8 fix, previously only in
    `checkpoint_builder.py`), closing the resume-content gap from finding #9 — the
    resume `OBJECTIVE` and `next_action` now carry the named file and literal
    before/after markers, so a resume can no longer claim it lacked instructions.
    These two fixes surface in independent parts of the result (baseline 5/5 ⇒
    permissions; resume acts on our `next_action` ⇒ content), so batching them
    does not confound.

**Design consequence carried into implementation.** Because the agent cannot see
`task_spec`, the *content* placed into a `Checkpoint` (objective, `next_action`,
notes) must itself carry the literal, agent-visible task spec — exactly as
`_baseline_prompt()` now does. This is consistent with the invariant that
`compose_resume_prompt` is untouched: we change what we put *into* the checkpoint,
never the formatter. `checkpoint_builder.py` owns this responsibility.

**GATE (OPENED 2026-06-29).** Both conditions are MET. A fully clean
`--driver claude` run produced `[RESULT] end_state=[OK] (5/5 done)
resume_behavior=CONTINUED duplicated=none lost=none`: acceptEdits survived the
harness (claude edited, did not queue), the resume drove the task to 5/5, and
`duplicated=none` proves the completed-list was respected (alpha/bravo not
re-edited). Baseline-alone = False answers amendment 3: an uninterrupted session
does NOT finish, so the loop has real work and the orchestrator is not redundant
— the 2/5 → 5/5 test is meaningful. `orchestrator.py` is now implemented and its
headline acceptance test passes (see below).

**ORCHESTRATOR — implemented and green.** `orchestrator.run_until_done` assembles
the proven pieces — `classify` → tree-`verify` → `decide` → `build_checkpoint` →
`compose_resume_prompt` (untouched) → relaunch — with the closed exit set
(`COMPLETED`/`FATAL`/`GAVE_UP`) and `UNKNOWN_INTERRUPTION` routed through the same
recovery path as `SESSION_LIMIT`. `test_orchestrator.py` (16/16) proves: the
headline 2/5 → 5/5 across 2 and 3 simulated session-limit interruptions, scored
by the existing `scorer.py` with `is_doubled` clean and no lost files; FATAL
short-circuits on the first attempt with no wait; GAVE_UP fires exactly at the
attempt cap; UNKNOWN recovers to completion; a false done-token (rc=0 + token but
tree not done) is NOT accepted as complete (tree-truth, finding #6); and NO real
sleeping (injected sleep recorder; waits computed as 60s, 120s backoff). Every
spike hardening is carried in as a production requirement: endpoint-asserted env,
UTF-8 capture, transcript persistence, imperative on-disk-now prompt content, and
the acceptEdits permission model.

This design builds on a **proven** result from the resume-fidelity spike: a
structured resume prompt (`harness/checkpoint.py::compose_resume_prompt`, output
captured in `results/claude_resume_prompt.txt`) makes a fresh Claude Code session
*continue* an interrupted task instead of restarting it. The spike's
`results/claude_report.json` recorded `continued: true`, `duplicated_files: []`,
`lost_files: []`.

What the spike proved, and what it did **not**, frames everything below:

- **Proved:** resume *content* — the prompt format — steers continuation over
  restart. This is the hard, model-dependent part, and it works.
- **Not proved:** the *control loop* around it. The spike never detects a real
  interruption (it simulates one with `_partial_apply`), never waits, never
  relaunches, and notably ended at `done_count: 2/5` with `end_state_ok: false`
  — it continued but did not *finish*. "Continued" is not "completed."

The orchestrator's entire reason to exist is to close that second gap. So the
design is deliberately conservative about reusing the proven pieces untouched and
spends its risk budget only on the unproven loop.

Hard rules honored throughout: plain-text status labels (`[OK]`, `[ERR]`,
`[INFO]`, `[WAIT]`, `[FATAL]`), no credentials in code, one-thing-per-change,
cross-platform, UTC-timestamped logging.

---

## 1. The thinnest vertical slice

**Slice:** Detect exactly one interruption type — `session-limit-reached` —
emitted by one real `claude` invocation against **one** project; checkpoint the
partial work to git; wait until relaunch is sensible; relaunch a fresh session
with the proven resume prompt; verify completion; loop if still incomplete.
Terminate on one of three explicit states: `COMPLETED`, `FATAL`, `GAVE_UP`.

**Explicitly out of the slice:**
- No work queue, no scheduling across tasks.
- No parallelism — one session at a time, one project.
- No notifications beyond a single UTC-stamped console line per state transition.
- No detection of *other* interruption types (network drop, manual kill,
  context-window-full). Those are recognized only as the generic
  `UNKNOWN_INTERRUPTION` degradation path (§3), not handled specially.
- No multi-machine, no persistence of the loop across orchestrator restarts
  (the git checkpoint survives, but the running loop does not — see §5.3 cap).

**Why this is the right boundary.** The slice is chosen so it touches *every
architectural seam exactly once* — detect → checkpoint → wait → relaunch →
resume → verify → loop — while keeping each seam at its simplest possible form.

- `session-limit-reached` is the *highest-value* real interruption: it is
  predictable, recurring, and non-fatal, and it is the exact condition the
  resume-fidelity result was implicitly motivated by. If the loop can't survive
  a usage limit, nothing downstream matters.
- Everything cut from the slice is **additive**: a queue, parallelism, more event
  types, and notifications all bolt onto the loop without changing its control
  flow. Cutting them removes surface without removing any seam.
- The cut therefore isolates the **only unproven thing** — the control loop
  itself — from the already-proven thing (resume content). A green slice means
  the loop is sound; we then scale it, not redesign it.

The slice delivers value standalone: "kick off a long task, walk away, and it
finishes itself across a usage-limit reset without redoing work" is a complete,
demonstrable capability.

---

## 2. Module boundaries and the data that crosses them

```
 TaskDefinition ─► orchestrator ──prompt,cwd──► driver ──RunResult──► detector
       ▲                │  ▲                                              │
       │                │  └──────────────── Event ◄──────────────────────┘
       │          Checkpoint+diff                 │
       │                │                          ▼
       └──todos/verify──┘                       waiter (Event.reset_hint ► sleep)
                        │
        compose_resume_prompt(Checkpoint, diff) ─► prompt  (loops back to driver)
```

### Reused verbatim from the spike (do NOT reinvent)

| Component | File | Role in orchestrator |
|---|---|---|
| `Checkpoint` dataclass | `harness/checkpoint.py` | The durable state record. Unchanged. |
| `compose_resume_prompt()` | `harness/checkpoint.py` | The proven resume-prompt format. **Unchanged** — this is the asset we are protecting. |
| `ClaudeCodeDriver` / `RunResult` | `harness/driver.py` | Runs the real CLI; already returns `returncode`, `stdout`, `stderr`, `seconds` — the exact detector inputs. |
| `FakeDriver` family | `harness/driver.py` | Extended (not modified) with new deterministic modes for testing the loop (§6). |
| git helpers (`_git`, `_init_project`, `_rmtree`, `_on_rm_error`) | `harness/run_spike.py` | The checkpoint store mechanics, including the Windows read-only-object fix. Promoted to a shared module. |

### New modules (the unproven loop)

| Module | Responsibility | Input → Output |
|---|---|---|
| `detector.py` | Classify a finished run into an `Event`. Pure function, no I/O. | `RunResult` (+ task's done-token) → `Event` |
| `waiter.py` | Decide how long to sleep before relaunch; enforce floor/cap. Clock injectable. | `Event` (+ attempt #) → sleep duration / `GAVE_UP` |
| `checkpoint_builder.py` | Snapshot the live git tree and build a `Checkpoint` + diff from the **task definition** and **git evidence**, not from in-memory claims. | `TaskDefinition` + `cwd` → `Checkpoint`, `git_diff` |
| `orchestrator.py` | The control loop and the three terminal states. Owns all logging. | `TaskDefinition` → terminal `LoopResult` |

### Data shapes that cross boundaries (contracts, not code)

```
TaskDefinition
  objective:   str                 # one sentence, becomes Checkpoint.objective
  working_dir: str                 # the one project
  todos:       list[str]           # ordered units of work (the planned steps)
  done_token:  str = "DONE_TASK_COMPLETE"   # success sentinel the agent emits
  verify:      Callable[[Path], bool] | None # optional mechanical done-check

Event
  type:        EventType           # enum, plain-text values (below)
  confidence:  str                 # "HIGH" | "MEDIUM" | "LOW" (plain-text labels)
  evidence:    str                 # the matched line / exit-code note, for the log
  reset_hint:  Optional[str]       # parsed reset time/window if the CLI gave one

EventType  (closed set for the slice)
  COMPLETED              # done_token present AND rc == 0
  SESSION_LIMIT          # the one interruption we handle specially
  UNKNOWN_INTERRUPTION   # abnormal exit we treat as a transient, cautiously
  FATAL                  # do not retry (CLI missing, auth failure, …)

LoopResult
  state:    str          # "COMPLETED" | "FATAL" | "GAVE_UP"
  attempts: int
  reason:   str          # plain-text, actionable on non-COMPLETED
```

The boundary discipline: the **driver** knows nothing about limits; the
**detector** knows nothing about git or waiting; the **waiter** knows nothing
about prompts; the **orchestrator** is the only module that sequences them and
the only one that writes log lines. `Event` is the single value that carries
detector knowledge to the loop — everything the orchestrator decides about a run
comes through that one struct, which keeps the detector independently testable
(§6).

---

## 3. The event detector

The detector is the part most exposed to forces outside our control: Anthropic
can re-word the limit message at any time, and exit codes are not contractually
documented. So it is explicitly **layered**, and it **never relies solely on
exact text**. It degrades from precise to coarse, and the coarse path is still
safe.

Input is the full `RunResult` plus the task's `done_token`. The detector inspects
the **tail** (last N lines, e.g. N=15) of combined stdout+stderr, not the whole
transcript — see false-positive defense below.

### Layer 0 — success, checked first
`done_token` present in stdout **AND** `rc == 0` → `COMPLETED` / HIGH. Checking
success first means no interruption pattern can ever shadow a genuine completion.

### Layer 1 — known-string fast path (HIGH confidence)
A small **curated table** of literal substrings Anthropic currently uses, e.g.
`"usage limit reached"`, `"session limit"`, `"resets at"`, `"reached your usage
limit"`. A tail hit with an abnormal exit → `SESSION_LIMIT` / HIGH, and we attempt
to parse `reset_hint` from the same region. The table lives in one constants
block so a wording change is a **one-line edit** (honors one-thing-per-change).

### Layer 2 — degradation path (MEDIUM confidence)
Used when Layer 1 misses. Keyed on **exit code + loose regex family**, not exact
strings:
- a limit-shaped exit code (a small known set), **or**
- the tail matches relaxed patterns such as
  `(usage|rate|session).{0,20}limit` or `resets?\s+(at|in)\b`,

**and** the run did not exit cleanly with the done-token. → `SESSION_LIMIT` /
MEDIUM. This catches "they reworded it but it's obviously still a limit."

### Layer 3 — generic fallback (LOW confidence)
Any abnormal termination that is *not* a recognized fatal (below) and *not* a
completion → `UNKNOWN_INTERRUPTION` / LOW. Crucially, the orchestrator treats
this the **same** as `SESSION_LIMIT` for control-flow purposes (checkpoint +
backoff + one cautious relaunch), just with a more conservative wait and a louder
log line. So a *type* miss degrades to the generic recovery path — never to
"pretend it succeeded."

### Fatal classification (breaks the loop, never retried)
`rc == 127` (CLI not on PATH — see `driver.py` `FileNotFoundError` → 127) and a
small set of recognized auth-failure strings → `FATAL`. `rc == 124` (driver's
timeout sentinel) is its own case: classified as `UNKNOWN_INTERRUPTION` so we
still checkpoint, but it counts toward the attempt cap (§5.3).

### False-positive risks (saying "limit" when there wasn't one)
- **The agent's own output discusses rate limits** (it edited a file about
  throttling, or printed docs). Defenses: (1) inspect only the **tail**, where a
  real CLI limit notice lands, not the body where agent content lives; (2)
  require an **abnormal exit** to co-occur — a clean `rc==0` is never a limit
  regardless of text; (3) Layer 0 wins first, so `rc==0 + done_token` is always
  `COMPLETED`. Residual risk: an agent that crashes (nonzero) right after
  printing limit-ish text in its last lines. Cost of that false positive is
  bounded — one wasted checkpoint + one backoff + a relaunch that either
  succeeds or re-classifies; it cannot corrupt state.

### False-negative risks (missing a real limit)
- **Reworded message + generic exit code + no pattern hit.** Caught by Layer 3
  as `UNKNOWN_INTERRUPTION`, which still checkpoints and relaunches. The
  *dangerous* false negative — calling an interruption `COMPLETED` — is blocked
  because `COMPLETED` requires the literal agent-emitted `done_token` **and**
  `rc==0` together; that token is specific enough not to appear by accident.
- **A limit masked as a timeout** (124): still routed to recovery, not success.

The asymmetry is intentional: the detector is allowed to be *imprecise about the
type* but is engineered to be *nearly never wrong about success vs not-success*,
because only the latter error is unrecoverable.

---

## 4. Git as the checkpoint store

**Why git.** This is inherited from the spike's proven approach and is the right
call for the orchestrator:

- A commit SHA is a **durable, content-addressed, atomic** pointer to an exact
  tree. It survives process death, which is the whole point — the orchestrator
  may be killed alongside the session.
- `git diff <start_sha> HEAD` **reconstructs "work so far"** for the resume
  prompt for free (this exact diff appears in `claude_resume_prompt.txt` and is
  part of what worked). No bespoke snapshot format to maintain or version.
- It is **human-inspectable and recoverable** — a developer can `git log` the
  checkpoints and see precisely what the loop captured at each interruption.
- The cross-platform sharp edge is already solved: `_on_rm_error` / `_rmtree`
  in `run_spike.py` handle Windows' refusal to unlink read-only `.git/objects`.

The checkpoint operation is: at detection time, `git add -A && git commit` the
partial tree, record the resulting SHA into `Checkpoint.git_sha`, and capture the
diff. The commit **is** the consistency boundary (see §5.2).

**Deliberately NOT serialized:**
- **The agent's conversation / chain-of-thought.** It isn't available to us and
  isn't needed — `compose_resume_prompt` *reconstructs* intent from structured
  state. Storing a transcript would be a fragile, larger, privacy-laden artifact
  that the proven format doesn't use.
- **Credentials / auth.** The CLI owns its own stored auth (`driver.py` states
  this). Nothing auth-related ever enters the checkpoint — this is both the
  no-credentials rule and a security boundary.
- **Ephemeral process state** — PIDs, in-memory buffers, the live loop's own
  variables. Reconstructable or irrelevant.
- **Untracked scratch outside the repo** and the raw token-by-token output. The
  diff and the structured `Checkpoint` JSON carry everything the resume prompt
  consumes; anything else is noise that would only invite drift between what we
  store and what the prompt actually uses.

The serialized surface is intentionally tiny: the git tree (file bytes) + one
`Checkpoint` JSON (objective, todo deltas, `next_action`, decisions, notes).

---

## 5. Three failure modes most likely to break this — and the defenses

Adversarial about our own design:

### 5.1 Relaunch storm against a still-exhausted quota
**The attack on us:** we detect `SESSION_LIMIT`, immediately relaunch, the quota
is *still* exhausted, we get re-interrupted instantly, and we spin — burning the
moment the window does reset on a tight retry loop, possibly escalating
rate-limit penalties. This is the single most likely way the slice becomes
*worse* than doing nothing.

**Defense:**
- `waiter.py` honors `Event.reset_hint` when the CLI gave one (wait until the
  stated reset, plus a small margin).
- When no hint is parseable, **bounded exponential backoff with a hard floor**
  (never relaunch faster than the floor, e.g. minutes, not seconds) and a
  **per-task max-attempts cap**.
- Relaunch is **idempotent by construction**: each relaunch first re-checkpoints
  from the *current* git tree, so even a fast retry cannot double work — it just
  re-resumes from wherever things actually are. The resume prompt's "do not redo"
  contract is the second line of defense.

### 5.2 Checkpoint of a torn / mid-write working tree
**The attack on us:** a real kill lands mid-edit. A file is half-written, or work
is done but uncommitted, or a todo is "started" but not verifiably complete. If we
naively diff that tree and declare `completed_files`, the resumed agent either
**redoes** finished work (duplication — the exact failure the spike hunts) or
**skips** unfinished work (silent loss).

**Defense:**
- The checkpoint **commit is the boundary**: `git add -A && commit` snapshots
  whatever the tree actually is into one consistent SHA. We never trust
  in-memory claims about progress — only committed bytes.
- `completed_files` is derived **conservatively**: a todo is counted done only
  when its *verifiable signature* is present in the committed tree (the same
  discipline as `task_spec.is_done` — presence of the done-marker, absence of the
  todo-marker). A borderline file biases to **pending**, not done.
- That bias means the worst case is a *benign re-do of one borderline unit*,
  which the resume prompt's "ALREADY COMPLETED / do not redo" guard further
  suppresses — strictly safer than silent loss. Uncommitted dirty content is
  swept into the commit so nothing is dropped on the floor.

### 5.3 Misclassification: fatal mistaken for transient (or the reverse)
**The attack on us:** auth expired (a human must act) is read as `SESSION_LIMIT`
and we wait-and-retry forever, silently. Or a genuine recoverable limit is read
as `FATAL` and we abandon a task that would have finished. Or "continued but not
finished" (the real `claude_report.json`: `done_count 2/5`, `end_state_ok:false`)
is mistaken for done.

**Defense:**
- An explicit **FATAL set** (127 CLI-missing, recognized auth strings) breaks the
  loop immediately with a plain-text actionable line — never silent, never
  retried.
- **Every terminal path is bounded.** Even a fatal-misread-as-transient case
  terminates: max-attempts **and** a max wall-clock cap both end the loop as
  `GAVE_UP(reason)`. There is no implicit infinite retry anywhere.
- **Completion is verified, not assumed.** A run that "continued" is only
  `COMPLETED` if `done_token + rc==0` (and the optional `verify` predicate
  passes). Otherwise it is simply *another iteration* — the loop re-checkpoints
  and resumes. This directly addresses the spike's `continued-but-incomplete`
  result: the orchestrator keeps going until the task is *done*, not until it
  merely *continued*.
- **Every transition is logged** with a UTC timestamp + the `Event.evidence`, so
  a wrong classification is auditable after the fact rather than invisible.

The loop has exactly three exits — `COMPLETED`, `FATAL`, `GAVE_UP` — and no
fourth. That closed set is the structural defense: there is nowhere for "stuck
forever" or "quietly wrong" to live.

---

## 6. Test plan — mechanical proof of each claim, in the spirit of the spike

Same philosophy as the spike: deterministic, no quota, the scorer is mechanical,
"did it work" is a boolean. Each module ships with `python -m py_compile` (the
syntax check) **and** its own test, added one module at a time.

### Detector — a truth table (no CLI, pure function)
A parametrized table of `(tail_text, returncode)` → expected `(type, confidence)`:

| Input | Expected |
|---|---|
| done-token present, rc 0 | `COMPLETED` / HIGH |
| known literal "usage limit reached", rc nonzero | `SESSION_LIMIT` / HIGH |
| reworded "you have hit your session cap", rc nonzero | `SESSION_LIMIT` / MEDIUM (Layer 2 regex) |
| generic stderr, rc 1, no pattern | `UNKNOWN_INTERRUPTION` / LOW |
| rc 127 | `FATAL` |
| **agent body mentions "rate limit", but rc 0 + done-token** | `COMPLETED` (false-positive guard) |
| limit text in early body only, clean tail, rc 0 + done-token | `COMPLETED` (tail-only guard) |

This proves §3's layering and both false-positive/negative guards mechanically.

### Loop — new deterministic FakeDriver modes (extends, not modifies, `driver.py`)
- **`fake-limit`**: first `run()` emits a session-limit message + nonzero exit;
  subsequent `run()` behaves like the proven continuing fake (reads
  `ALREADY COMPLETED`, touches only remaining files). With the waiter's clock
  injected to zero delay, assert the full loop: run → detect `SESSION_LIMIT` →
  checkpoint → relaunch → resume → finish. **Scored with the existing
  `scorer.py`:** expect `continued: true`, `end_state_ok: true`,
  `duplicated_files: []`, `lost_files: []`. This is the slice's headline proof.
- **`fake-limit-twice`**: interrupts on the first *two* calls. Proves the loop
  survives repeated interruptions and still terminates `COMPLETED` with no
  duplication (re-checkpoint idempotency, §5.1 and §5.2).
- **`fake-fatal`** (rc 127): proves the loop terminates `FATAL` immediately, with
  **zero** waits and **zero** relaunches (assert the waiter was never called).
- **`fake-never-finishes`**: always continues but never emits the done-token.
  Proves the attempt/wall-clock cap fires and the loop ends `GAVE_UP(reason)` —
  the §5.3 "continued ≠ completed" guard.

### Waiter — injected fake clock
- Assert the backoff sequence respects the **floor** and grows to the **cap**.
- Assert a parseable `reset_hint` is honored over backoff.
- Assert exceeding max-attempts returns `GAVE_UP` without sleeping further.

### Idempotency — duplication regression
Run `fake-limit` where the *resume* is itself interrupted once more, then score:
assert `duplicated_files: []` via the scorer's existing `is_doubled`. This reuses
the spike's own duplication detector to prove §5.2's conservative bias holds.

### Cross-platform / hard-rule conformance (asserted, not assumed)
- Tests run on Windows and POSIX; project teardown uses the spike's
  `_rmtree`/`_on_rm_error`.
- A log-capture test asserts every transition line is **UTC** (`...Z`) and uses a
  **plain-text** label from the fixed set (`[OK] [ERR] [INFO] [WAIT] [FATAL]`) —
  no emoji, no color codes.
- A grep-style test asserts no credential/token literals exist in any new module.

### Exit-code gating (CI-friendly, like `run_spike.py::main`)
The orchestrator's test entrypoint exits `0` only on `COMPLETED` with a clean
scorer report, non-zero otherwise — so the loop is gateable in CI exactly as the
spike is.

---

## Implementation order (after approval — one module per change)

1. `checkpoint_builder.py` — git snapshot → `Checkpoint` (+ its conservative
   `completed_files` test). Reuses `Checkpoint`, git helpers.
2. `detector.py` — the truth-table test is its acceptance gate.
3. `waiter.py` — fake-clock test is its gate.
4. `orchestrator.py` — wires them with `compose_resume_prompt` (reused, untouched)
   and the new FakeDriver modes; the `fake-limit` scorer run is the headline gate.

Each step: `py_compile` syntax check, then its test, before the next. No step
touches `compose_resume_prompt` — protecting the proven asset is a design
invariant, not a preference.
