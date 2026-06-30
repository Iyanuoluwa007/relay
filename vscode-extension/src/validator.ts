// validator: the two-stage API-key gate.
//
// Stage 1 (fast): GET https://api.anthropic.com/v1/models with the key. 200 means
// the key authenticates, 401 means it is invalid. No token cost. Instant feedback.
//
// Stage 2 (the real gate): run the engine's exact headless path via preflight.py,
// which imports the engine's ClaudeCodeDriver (read-only) and runs
// `claude -p "reply OK"`, requiring OK. A key that passes Stage 2 is proven to
// work for real runs, because it used the same code path a run uses.
//
// House style: no em-dashes, plain-text [OK]/[ERR] labels, key never logged.

import * as https from "https";
import { spawn } from "child_process";
import { PythonInfo, childEnv } from "./engineEnv";

export interface ValidationResult {
  ok: boolean;
  message: string;
}

const ANTHROPIC_VERSION = "2023-06-01";

export function validateStage1(key: string): Promise<ValidationResult> {
  return new Promise((resolve) => {
    const req = https.request(
      {
        host: "api.anthropic.com",
        path: "/v1/models?limit=1",
        method: "GET",
        headers: {
          "x-api-key": key,
          "anthropic-version": ANTHROPIC_VERSION,
        },
      },
      (res) => {
        const code = res.statusCode ?? 0;
        res.resume(); // drain the response so the socket can close
        if (code === 200) {
          resolve({ ok: true, message: "[OK] Stage 1: key authenticates with the Anthropic API." });
        } else if (code === 401) {
          resolve({ ok: false, message: "[ERR] Stage 1: 401 Unauthorized. The key is invalid." });
        } else {
          resolve({ ok: false, message: `[ERR] Stage 1: unexpected HTTP ${code} from api.anthropic.com.` });
        }
      },
    );
    req.setTimeout(15000, () => {
      req.destroy();
      resolve({ ok: false, message: "[ERR] Stage 1: timed out reaching api.anthropic.com (network?)." });
    });
    req.on("error", (e) => {
      resolve({ ok: false, message: `[ERR] Stage 1: network error (${e.message}).` });
    });
    req.end();
  });
}

export function validateStage2(
  key: string,
  py: PythonInfo,
  preflightScript: string,
  harnessDir: string,
): Promise<ValidationResult> {
  return new Promise((resolve) => {
    const child = spawn(py.cmd, [...py.args, preflightScript, harnessDir], { env: childEnv(key) });
    let out = "";
    let err = "";
    child.stdout.on("data", (d) => (out += d.toString()));
    child.stderr.on("data", (d) => (err += d.toString()));

    const timer = setTimeout(() => {
      child.kill();
      resolve({ ok: false, message: "[ERR] Stage 2: preflight timed out (claude -p did not return)." });
    }, 120000);

    child.on("error", (e) => {
      clearTimeout(timer);
      resolve({ ok: false, message: `[ERR] Stage 2: could not launch the preflight (${e.message}).` });
    });

    child.on("close", (code) => {
      clearTimeout(timer);
      const tail = (out + "\n" + err)
        .trim()
        .split(/\r?\n/)
        .filter((l) => l.length > 0)
        .slice(-3)
        .join(" | ");
      if (code === 0) {
        resolve({ ok: true, message: "[OK] Stage 2: engine preflight returned OK. The key works for real headless runs." });
      } else {
        resolve({ ok: false, message: `[ERR] Stage 2: preflight failed (exit ${code}). ${tail || "<no output>"}` });
      }
    });
  });
}
