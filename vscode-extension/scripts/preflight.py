"""
preflight.py -- Stage 2 key gate for the Relay VS Code extension.

Runs the engine's EXACT headless path: it imports the engine's ClaudeCodeDriver
(read-only, it does NOT modify any engine module) and runs `claude -p "reply OK"`,
requiring OK in the output. A key that passes this is proven to work for real
Relay runs, because it used the same code path a run uses (the same executable
resolution, endpoint stripping, acceptEdits permission, and UTF-8 capture).

Usage: python preflight.py <harness_dir>
The ANTHROPIC_API_KEY is read from the environment, which the extension sets.
Prints a plain-text result line. Exit 0 only if OK.

House style: no em-dashes, plain-text [OK]/[ERR] labels, no key in output.
"""

import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("[PREFLIGHT][ERR] missing harness_dir argument")
        return 2
    harness = Path(sys.argv[1])
    if not (harness / "driver.py").exists():
        print(f"[PREFLIGHT][ERR] driver.py not found under {harness}")
        return 2

    sys.path.insert(0, str(harness))
    try:
        from driver import ClaudeCodeDriver  # the engine's proven driver, unmodified
    except Exception as e:  # noqa: BLE001 - report any import failure plainly
        print(f"[PREFLIGHT][ERR] cannot import ClaudeCodeDriver: {e}")
        return 2

    driver = ClaudeCodeDriver()
    res = driver.run("reply OK", cwd=str(harness))
    if res.ok and "OK" in (res.stdout or ""):
        print("[PREFLIGHT][OK] claude -p returned OK")
        return 0

    detail = (res.stdout or res.stderr or "").strip().replace("\n", " ")[:200]
    print(f"[PREFLIGHT][ERR] rc={res.returncode} ok={res.ok} detail={detail or '<empty>'}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
