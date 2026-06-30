// engineBridge: spawns the proven engine (run_real_orchestrator.py) and turns its
// plain-text output into a live snapshot the status view can render.
//
// KNOWN BOUNDARY: the engine drives INJECTED interrupts, not real-limit
// detection. The bridge surfaces that plainly; it does not fake real limits.
//
// The bridge never modifies the engine. It only invokes it by subprocess with
// the key in the child env (the Ollama redirect stripped, same as the driver).
//
// House style: no em-dashes, plain-text labels, key never logged.

import * as vscode from "vscode";
import * as path from "path";
import { spawn, ChildProcess } from "child_process";
import { PythonInfo, childEnv } from "./engineEnv";

export interface CycleProgress {
  cycle: number;
  done: number;
  total: number;
}

export interface RunSnapshot {
  running: boolean;
  interrupts: number;
  cycles: CycleProgress[];
  resultLine?: string;
  sessionsLine?: string;
  verdict?: string;
  exitCode?: number;
  lastError?: string;
}

function emptySnapshot(interrupts: number): RunSnapshot {
  return { running: false, interrupts, cycles: [] };
}

export class EngineBridge {
  private child?: ChildProcess;
  private snap: RunSnapshot = emptySnapshot(2);
  private buf = "";

  private readonly _onChange = new vscode.EventEmitter<RunSnapshot>();
  readonly onChange = this._onChange.event;
  private readonly _onLine = new vscode.EventEmitter<string>();
  readonly onLine = this._onLine.event;

  get snapshot(): RunSnapshot {
    return this.snap;
  }

  isRunning(): boolean {
    return this.snap.running;
  }

  // Spawn the engine. `engineRoot` is the repo root that contains harness/.
  start(py: PythonInfo, engineRoot: string, key: string, interrupts: number): void {
    if (this.snap.running) {
      return;
    }
    const script = path.join(engineRoot, "harness", "run_real_orchestrator.py");
    this.snap = { ...emptySnapshot(interrupts), running: true };
    this.buf = "";
    this._onChange.fire(this.snap);

    const args = [...py.args, script, "--driver", "claude", "--interrupts", String(interrupts)];
    this.child = spawn(py.cmd, args, { cwd: engineRoot, env: childEnv(key) });

    this.child.stdout?.on("data", (d) => this.ingest(d.toString()));
    this.child.stderr?.on("data", (d) => this.ingest(d.toString()));
    this.child.on("error", (e) => {
      this.snap.lastError = e.message;
      this.snap.running = false;
      this._onChange.fire(this.snap);
    });
    this.child.on("close", (code) => {
      this.snap.running = false;
      this.snap.exitCode = code ?? -1;
      this._onChange.fire(this.snap);
    });
  }

  // Terminate the child (used on deactivate so a run does not orphan).
  stop(): void {
    if (this.child && this.snap.running) {
      this.child.kill();
    }
  }

  private ingest(chunk: string): void {
    this.buf += chunk;
    let idx: number;
    while ((idx = this.buf.indexOf("\n")) >= 0) {
      const line = this.buf.slice(0, idx).replace(/\r$/, "");
      this.buf = this.buf.slice(idx + 1);
      this._onLine.fire(line);
      this.parseLine(line);
    }
    this._onChange.fire(this.snap);
  }

  // Parse the engine's structured plain-text lines into the snapshot.
  private parseLine(line: string): void {
    let m: RegExpMatchArray | null;
    if ((m = line.match(/injected interruption after cycle (\d+): (\d+)\/(\d+) done/))) {
      this.snap.cycles.push({ cycle: Number(m[1]), done: Number(m[2]), total: Number(m[3]) });
    } else if (line.includes("[RESULT]")) {
      this.snap.resultLine = line.replace(/^.*\[RESULT\]\s*/, "");
    } else if (line.includes("real sessions=")) {
      this.snap.sessionsLine = line.replace(/^.*\[INFO\]\s*/, "");
    } else if (line.includes("JOIN PROVEN") || line.includes("JOIN FAILED")) {
      const tag = line.includes("JOIN PROVEN") ? "[OK]" : "[ERR]";
      this.snap.verdict = `${tag} ${line.replace(/^.*?(JOIN (?:PROVEN|FAILED))/, "$1")}`;
    }
  }
}
