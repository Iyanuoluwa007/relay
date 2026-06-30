// engineEnv: shared helpers for invoking the engine by subprocess.
//
// Used by the validator (Stage 2 preflight) and the engine bridge (the run).
// Three concerns: find a Python interpreter, resolve the engine paths, and build
// the child environment that carries the key and strips the Ollama redirect.
//
// House style: no em-dashes, plain-text labels, key never logged.

import * as vscode from "vscode";
import * as path from "path";
import { spawnSync } from "child_process";

export interface PythonInfo {
  cmd: string;
  args: string[];
}

// Try python, then the Windows launcher py -3, then python3. Return the first
// that runs `--version` successfully, or undefined so the caller can report a
// clear [ERR] instead of failing obscurely.
export function findPython(): PythonInfo | undefined {
  const candidates: PythonInfo[] = [
    { cmd: "python", args: [] },
    { cmd: "py", args: ["-3"] },
    { cmd: "python3", args: [] },
  ];
  for (const c of candidates) {
    try {
      const r = spawnSync(c.cmd, [...c.args, "--version"], { encoding: "utf8", timeout: 8000 });
      if (r.status === 0) {
        return c;
      }
    } catch {
      // try the next candidate
    }
  }
  return undefined;
}

// The engine repo root is the folder that contains harness/. The extension lives
// in <root>/vscode-extension, so the root is one level up from extensionPath.
export function engineRoot(context: vscode.ExtensionContext): string {
  return path.resolve(context.extensionPath, "..");
}

export function harnessDir(context: vscode.ExtensionContext): string {
  return path.join(engineRoot(context), "harness");
}

export function preflightScript(context: vscode.ExtensionContext): string {
  return path.join(context.extensionPath, "scripts", "preflight.py");
}

// Child environment for any engine subprocess: inherit the current env, set the
// key, and delete the Ollama redirect vars. The engine's build_child_env also
// strips these; doing it here too is defense in depth.
export function childEnv(key: string): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env };
  delete env.ANTHROPIC_BASE_URL;
  delete env.ANTHROPIC_AUTH_TOKEN;
  env.ANTHROPIC_API_KEY = key;
  return env;
}
