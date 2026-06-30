# Relay for VS Code

A thin VS Code extension that drives the proven Relay engine (in `../harness`).
The extension wraps the engine by subprocess and never modifies it.

This is a scaffold. It is built one piece at a time: scaffold, key store
(SecretStorage), two-stage key validator, engine bridge, then the START and
STATUS user interface.

## What it does (when complete)

- A required API-key gate. You must enter an `ANTHROPIC_API_KEY` and it must
  pass validation before any run action is allowed. This is the extension's
  answer to engine finding #11: the interactive Claude login does not authorize
  headless `claude -p`, so Relay needs an explicit key. The gate surfaces that
  up front with a clear message instead of a raw mid-run 401.
- Two-stage key validation. Stage 1 is a fast ping to the Anthropic API for
  instant feedback. Stage 2 runs the engine's real headless path
  (`claude -p "reply OK"`, via the engine's own `ClaudeCodeDriver`) and requires
  `OK` in the output, so a key that passes Stage 2 is proven to work for real
  runs.
- START a run and read real per-cycle STATUS.

## Honest boundaries (these callouts are a feature, not a hedge)

- Injected interrupts, not real-limit detection. This scaffold drives the
  engine's proven injected-interrupt path (`run_real_orchestrator.py`). Real
  session-limit detection against live CLI output is not built yet, so the
  extension does not claim it.
- SecretStorage protects against on-disk plaintext, not against a malicious
  co-installed extension. The key is stored in VS Code SecretStorage (encrypted
  via the OS keychain), never in `settings.json` or `globalState` (those are
  plaintext). SecretStorage does not protect against another extension running
  in the same VS Code instance.
- Python-interpreter discovery with a clear error. The extension locates a
  Python interpreter (`python`, `py -3`, or `python3`). If none is found it
  reports a plain `[ERR]` rather than failing obscurely.

## Pause and resume are intentionally disabled in this scaffold

START and STATUS are wired. Pause and resume are shown as disabled with this
note: live pause/resume arrives when the engine exposes a long-lived resumable
task. The current engine entrypoint runs a self-contained demo to completion and
re-seeds its workbench each launch, so there is no in-flight task to pause.
Mapping resume to relaunch would restart from the 2/5 partial state, which is a
restart, not a resume. Labeling a restart as resume in a resume-fidelity tool
would be a trust-eroding overclaim, so we do not.

## Develop

```
npm install
npm run compile
npm run lint
```

Then press F5 in VS Code (with this `vscode-extension` folder open) to launch the
Extension Development Host.
