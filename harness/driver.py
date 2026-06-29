"""
driver.py -- Abstraction over "run Claude Code on a prompt in a directory".

ClaudeCodeDriver shells out to the real `claude` CLI in non-interactive mode.
FakeDriver simulates a session deterministically so the harness logic, the
checkpoint format, and the scorer can all be exercised without the real CLI
or any quota. Swap drivers via --driver on the CLI.

Credentials: none are handled here. The real CLI uses its own stored auth.
Never pass keys through this layer.

Python 3.11+. Standard library only.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


def _ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class RunResult:
    ok: bool
    stdout: str
    stderr: str
    returncode: int
    seconds: float


def _force_reapply(fpath, done_marker: str, banner: str, todo_marker: str) -> None:
    """Append a second header banner to a file regardless of its state.

    Simulates an agent that restarts and rewrites a file that was already
    complete, producing a doubled banner. Used only by the restart control.
    """
    text = fpath.read_text(encoding="utf-8")
    if todo_marker in text:
        text = text.replace(todo_marker, f"{done_marker}\n{banner}")
    else:
        text = f"{done_marker}\n{banner}\n" + text
    fpath.write_text(text, encoding="utf-8")


class BaseDriver:
    def run(self, prompt: str, cwd: str, timeout: int = 900) -> RunResult:
        raise NotImplementedError


ENV_BASE_URL = "ANTHROPIC_BASE_URL"
ENV_AUTH_TOKEN = "ANTHROPIC_AUTH_TOKEN"
OFFICIAL_HOST = "api.anthropic.com"


def _mask(value: str) -> str:
    """Show at most the first 4 chars of a secret -- never the full value."""
    if not value:
        return "<empty>"
    return value[:4] + "..."


def build_child_env(allow_custom_base_url: bool = False) -> tuple[dict, list[str]]:
    """Build the environment for the claude child, asserting the ENDPOINT.

    Confirm WHERE requests go, not just what key is used. A shell profile may
    export a redirect (e.g. ANTHROPIC_BASE_URL=http://localhost:11434 with
    ANTHROPIC_AUTH_TOKEN=ollama) that a bare subprocess silently inherits,
    sending claude.ai credentials to a non-Anthropic endpoint -- the exact cause
    of the harness 401s. By default we strip both vars so the child falls back to
    the CLI's own stored login and can never be redirected by accident.

    Returns (env, warnings); the caller logs the plain-text warnings. Token
    values are masked to at most 4 chars (no credentials in logs).
    """
    env = os.environ.copy()
    warnings: list[str] = []
    base = env.get(ENV_BASE_URL)

    if not allow_custom_base_url:
        if base:
            # Strip uniformly so the child can never inherit a redirect, but only
            # flag it as non-Anthropic when it actually isn't the official host
            # (an explicit official URL is harmless and shouldn't print a scary
            # false label).
            env.pop(ENV_BASE_URL, None)
            if OFFICIAL_HOST not in base:
                warnings.append(f"[WARN] stripped non-Anthropic base URL {base}")
        if ENV_AUTH_TOKEN in env:
            # The redirect token is paired with the base URL; strip it too so we
            # don't send a localhost token to the official endpoint.
            tok = env.pop(ENV_AUTH_TOKEN)
            warnings.append(f"[WARN] stripped inherited {ENV_AUTH_TOKEN} ({_mask(tok)})")
    elif base and OFFICIAL_HOST not in base:
        # Deliberately allowed, but never silent.
        warnings.append(f"[WARN] using custom base URL {base} (allow_custom_base_url=True)")

    return env, warnings


# Headless edit-permission model. Transcripts proved that
# --dangerously-skip-permissions is NOT honored by recent CLI versions in the
# subprocess: claude QUEUES edits and blocks on a permission grant that never
# comes ("edit calls are queued pending your permission grant"), exiting rc=0
# with files untouched. --permission-mode acceptEdits auto-accepts file edits
# with no human prompt -- the unattended behavior the orchestrator needs in
# production. If acceptEdits alone does not grant writes on a given CLI version,
# switch DEFAULT_PERMISSION_ARGS to ACCEPT_EDITS_WITH_TOOLS (adds an explicit
# tool allowlist). One symbol, centralized here -- the shared chokepoint.
ACCEPT_EDITS = ["--permission-mode", "acceptEdits"]
ACCEPT_EDITS_WITH_TOOLS = ["--permission-mode", "acceptEdits", "--allowedTools", "Edit,Write"]
DEFAULT_PERMISSION_ARGS = ACCEPT_EDITS


class ClaudeCodeDriver(BaseDriver):
    """Invokes the real Claude Code CLI non-interactively.

    Uses `claude -p <prompt>` with `--permission-mode acceptEdits` so the agent
    can apply file edits unattended. This is the mode an orchestrator would use.
    The exact flag set is centralized here so a CLI change is a one-line fix.

    The child runs in an endpoint-asserted environment (see build_child_env):
    inherited base-URL/auth-token redirects are stripped unless explicitly
    allowed, so the spike and the orchestrator can never silently talk to a
    non-Anthropic endpoint.
    """

    def __init__(
        self,
        extra_args: list[str] | None = None,
        allow_custom_base_url: bool = False,
    ):
        self.extra_args = list(extra_args) if extra_args is not None else list(DEFAULT_PERMISSION_ARGS)
        # Resolve the executable cross-platform. On Windows the CLI is a
        # `claude.CMD` npm shim that bare-name subprocess resolution misses
        # (it only finds `claude.exe`), raising FileNotFoundError. which()
        # honors PATHEXT and returns the real shim; fall back to the bare name
        # so the FileNotFoundError branch still reports a genuine missing CLI.
        self.exe = shutil.which("claude") or "claude"
        self.allow_custom_base_url = allow_custom_base_url
        self.env, _env_warnings = build_child_env(allow_custom_base_url)
        for w in _env_warnings:
            print(f"[{_ts()}] {w}")

    def run(self, prompt: str, cwd: str, timeout: int = 900) -> RunResult:
        # Control flags go BEFORE -p, the conventional `claude [options] -p
        # <prompt>` form, so the prompt value can never swallow them. The manual
        # probe granted edits but the harness did not; env-stripping is ruled out
        # (only endpoint vars are popped), leaving arg placement as a suspect, so
        # we make the invocation conventional AND log the flags as proof of what
        # the subprocess actually receives.
        cmd = [self.exe, *self.extra_args, "-p", prompt]
        print(f"[{_ts()}] [INFO] invoking claude in {cwd} (timeout={timeout}s)")
        print(f"[{_ts()}] [INFO] permission/control flags: {' '.join(self.extra_args) or '<none>'}")
        start = time.time()
        try:
            proc = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                # Force UTF-8 decoding. On Windows the reader thread otherwise
                # defaults to cp1252 and CRASHES on a stray non-cp1252 byte (a
                # smart quote / em-dash / box char from claude), losing the whole
                # session's output. errors="replace" guarantees worst-case a
                # replacement char, never a decode crash.
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=self.env,
            )
            elapsed = time.time() - start
            ok = proc.returncode == 0
            if ok:
                print(f"[{_ts()}] [OK] claude exited rc=0 in {elapsed:.1f}s")
            else:
                # A non-zero exit must NEVER be silent. The original retracted
                # result came from an error that left no trace. claude reports
                # some failures (e.g. auth) on stdout, not stderr, so surface
                # BOTH streams here -- a blank "failed:" line is the bug.
                print(f"[{_ts()}] [ERR] claude exited rc={proc.returncode} in {elapsed:.1f}s")
                err = (proc.stderr or "").strip()
                out = (proc.stdout or "").strip()
                print(f"[{_ts()}] [ERR] stderr: {err[:500] if err else '<empty>'}")
                print(f"[{_ts()}] [ERR] stdout: {out[:500] if out else '<empty>'}")
            return RunResult(ok, proc.stdout, proc.stderr, proc.returncode, elapsed)
        except FileNotFoundError:
            return RunResult(False, "", "claude CLI not found on PATH", 127, 0.0)
        except subprocess.TimeoutExpired:
            elapsed = time.time() - start
            print(f"[{_ts()}] [ERR] claude timed out after {elapsed:.1f}s")
            return RunResult(False, "", f"timeout after {timeout}s", 124, elapsed)


class FakeDriver(BaseDriver):
    """Deterministic stand-in for Claude Code.

    Implements the task in task/task_spec.py directly in Python so the harness
    can be validated end to end. It models the two behaviors we care about:

      - On a FRESH prompt, it processes files in order from the start.
      - On a RESUME prompt, it reads the 'ALREADY COMPLETED' line and only
        touches the remaining files -- i.e. it continues rather than restarts.

    A 'restart' bug can be simulated with restart_on_resume=True to confirm the
    scorer actually catches duplication.
    """

    def __init__(self, restart_on_resume: bool = False):
        self.restart_on_resume = restart_on_resume

    def run(self, prompt: str, cwd: str, timeout: int = 900) -> RunResult:
        from task_spec import TARGET_FILES, apply_transform  # local import
        from task_spec import DONE_MARKER, BANNER, TODO_MARKER  # for restart sim

        is_resume = "You are RESUMING" in prompt
        already_done: set[str] = set()
        if is_resume and not self.restart_on_resume:
            for line in prompt.splitlines():
                if line.startswith("ALREADY COMPLETED"):
                    payload = line.split(":", 1)[1]
                    for name in payload.split(","):
                        already_done.add(name.strip().rstrip(")").strip())

        touched: list[str] = []
        for fname in TARGET_FILES:
            if fname in already_done:
                continue
            fpath = Path(cwd) / fname
            if not fpath.exists():
                continue
            if self.restart_on_resume and is_resume:
                # Faithfully simulate a restart: an agent that ignores prior
                # state rewrites every file, re-appending the banner even where
                # the TODO marker is already gone. This is what duplication
                # looks like in the wild, and the scorer must catch it.
                _force_reapply(fpath, DONE_MARKER, BANNER, TODO_MARKER)
            else:
                apply_transform(fpath)
            touched.append(fname)

        mode = "RESUME" if is_resume else "FRESH"
        out = (
            f"[fake-claude] mode={mode} touched={touched}\n"
            f"DONE_TASK_COMPLETE\n"
        )
        return RunResult(True, out, "", 0, 0.2)


def make_driver(name: str) -> BaseDriver:
    if name == "claude":
        return ClaudeCodeDriver()
    if name == "fake":
        return FakeDriver()
    if name == "fake-restart":
        return FakeDriver(restart_on_resume=True)
    raise ValueError(f"unknown driver: {name}")
