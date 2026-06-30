// statusView: a live tree of the current run, read from the engine bridge
// snapshot. It shows real per-cycle progress parsed from the engine's plain-text
// output, and states the injected-interrupt boundary plainly.
//
// House style: no em-dashes, plain-text [OK]/[ERR]/[INFO] labels.

import * as vscode from "vscode";
import { RunSnapshot } from "./engineBridge";

export class RelayStatusProvider implements vscode.TreeDataProvider<string> {
  private readonly _onDidChangeTreeData = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  constructor(private readonly getSnap: () => RunSnapshot) {}

  refresh(): void {
    this._onDidChangeTreeData.fire();
  }

  getTreeItem(label: string): vscode.TreeItem {
    return new vscode.TreeItem(label);
  }

  getChildren(): string[] {
    const s = this.getSnap();
    const lines: string[] = [];

    const phase = s.running ? "running" : s.exitCode !== undefined ? "finished" : "idle";
    lines.push(`Run: ${phase}`);
    lines.push("Mode: injected interrupts (not real-limit detection)");

    if (!s.running && s.exitCode === undefined && s.cycles.length === 0) {
      lines.push("[INFO] No run yet. Use Relay: Start Run.");
      return lines;
    }

    for (const c of s.cycles) {
      lines.push(`Cycle ${c.cycle}: ${c.done}/${c.total} done (injected interrupt)`);
    }
    if (s.sessionsLine) {
      lines.push(s.sessionsLine);
    }
    if (s.resultLine) {
      lines.push(`Result: ${s.resultLine}`);
    }
    if (s.verdict) {
      lines.push(s.verdict);
    }
    if (s.exitCode !== undefined) {
      lines.push(`Exit: ${s.exitCode}`);
    }
    if (s.lastError) {
      lines.push(`[ERR] ${s.lastError}`);
    }
    return lines;
  }
}
