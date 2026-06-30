// Relay VS Code extension, entry point.
//
// House style: no em-dashes anywhere, no emojis/icons in code, plain-text status
// labels [OK]/[ERR]/[WARN]/[INFO]. The extension WRAPS the proven engine and
// never modifies it.
//
// Pieces: scaffold, key store (SecretStorage), two-stage validator, engine
// bridge, START + STATUS UI. The required API-key gate is the extension's answer
// to engine finding #11 (the interactive Claude login does not authorize
// headless `claude -p`, so Relay needs an explicit key).

import * as vscode from "vscode";
import { KeyStore, maskKey } from "./keyStore";
import { findPython, harnessDir, preflightScript, engineRoot } from "./engineEnv";
import { validateStage1, validateStage2 } from "./validator";
import { EngineBridge } from "./engineBridge";
import { RelayStatusProvider } from "./statusView";
import { RelaySetupViewProvider } from "./setupView";

const TAG = "[Relay]";
const INTERRUPTS = 2;
const PAUSE_RESUME_NOTE =
  "Live pause/resume arrives when the engine exposes a long-lived resumable task.";

export function activate(context: vscode.ExtensionContext): void {
  console.log(`${TAG} [INFO] extension activated`);

  const keys = new KeyStore(context.secrets);
  const bridge = new EngineBridge();
  const channel = vscode.window.createOutputChannel("Relay");

  // In-memory gate state. A key is re-validated per session; START requires it.
  const state = { validated: false, reportedExit: true };

  const statusProvider = new RelayStatusProvider(() => bridge.snapshot);

  async function statusText(): Promise<string> {
    const has = await keys.has();
    const lines: string[] = [];
    lines.push(has ? "[OK] Key stored in SecretStorage." : "[ERR] No key set.");
    lines.push(state.validated ? "[OK] Validated (Stage 1 + Stage 2 passed)." : "[INFO] Not validated yet.");
    if (bridge.isRunning()) {
      lines.push("[INFO] A run is in progress.");
    }
    return lines.join("\n");
  }

  // The two-stage gate. Stage 1 is a fast API ping; Stage 2 runs the engine's
  // real headless path. Only after Stage 2 passes is `state.validated` true.
  async function runValidation(): Promise<boolean> {
    const key = await keys.get();
    if (!key) {
      vscode.window.showWarningMessage(`${TAG} [ERR] No API key set. Set up a key first.`);
      return false;
    }
    const ok = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: "Relay: validating API key", cancellable: false },
      async (progress) => {
        progress.report({ message: "Stage 1: pinging the Anthropic API" });
        const s1 = await validateStage1(key);
        if (!s1.ok) {
          vscode.window.showErrorMessage(`${TAG} ${s1.message}`);
          return false;
        }
        const py = findPython();
        if (!py) {
          vscode.window.showErrorMessage(
            `${TAG} [ERR] No Python interpreter found (tried python, py -3, python3). Install Python 3.11+ and reload.`,
          );
          return false;
        }
        progress.report({ message: "Stage 2: engine preflight (claude -p reply OK)" });
        const s2 = await validateStage2(key, py, preflightScript(context), harnessDir(context));
        if (s2.ok) {
          vscode.window.showInformationMessage(`${TAG} ${s2.message} You can now start a run.`);
        } else {
          vscode.window.showErrorMessage(`${TAG} ${s2.message}`);
        }
        return s2.ok;
      },
    );
    state.validated = ok;
    setupProvider.refresh();
    return ok;
  }

  async function saveAndValidate(key: string): Promise<void> {
    await keys.store(key);
    state.validated = false;
    vscode.window.showInformationMessage(`${TAG} [OK] Key stored in SecretStorage (${maskKey(key)}).`);
    await runValidation();
  }

  async function setupKeyViaInput(): Promise<void> {
    const value = await vscode.window.showInputBox({
      title: "Relay: Anthropic API key",
      prompt: "Paste your ANTHROPIC_API_KEY. Stored only in VS Code SecretStorage (OS keychain), never in settings.",
      password: true,
      ignoreFocusOut: true,
      placeHolder: "Paste your ANTHROPIC_API_KEY",
      validateInput: (v) => (v.trim().length === 0 ? "Key cannot be empty." : undefined),
    });
    if (value === undefined) {
      return; // cancelled
    }
    await saveAndValidate(value.trim());
  }

  async function clearKey(): Promise<void> {
    await keys.delete();
    state.validated = false;
    setupProvider.refresh();
    vscode.window.showInformationMessage(`${TAG} [OK] Key cleared from SecretStorage.`);
  }

  // START is gated on a present, validated key, then spawns the proven engine.
  async function doStart(): Promise<void> {
    if (!(await keys.has())) {
      const pick = await vscode.window.showWarningMessage(
        `${TAG} [ERR] No API key set. Relay requires an ANTHROPIC_API_KEY (engine finding #11).`,
        "Set Up API Key",
      );
      if (pick === "Set Up API Key") {
        await setupKeyViaInput();
      }
      return;
    }
    if (!state.validated) {
      const ok = await runValidation();
      if (!ok) {
        return;
      }
    }
    if (bridge.isRunning()) {
      vscode.window.showInformationMessage(`${TAG} [INFO] A run is already in progress.`);
      return;
    }
    const py = findPython();
    if (!py) {
      vscode.window.showErrorMessage(
        `${TAG} [ERR] No Python interpreter found (tried python, py -3, python3). Install Python 3.11+ and reload.`,
      );
      return;
    }
    const key = await keys.get();
    if (!key) {
      vscode.window.showErrorMessage(`${TAG} [ERR] Key disappeared from SecretStorage. Set up a key again.`);
      return;
    }
    state.reportedExit = false;
    channel.clear();
    channel.show(true);
    channel.appendLine(`${TAG} [INFO] starting engine: run_real_orchestrator.py --driver claude --interrupts ${INTERRUPTS}`);
    channel.appendLine(`${TAG} [INFO] mode: INJECTED interrupts (not real-limit detection).`);
    bridge.start(py, engineRoot(context), key, INTERRUPTS);
  }

  const setupProvider = new RelaySetupViewProvider({
    saveAndValidate,
    clearKey,
    startRun: doStart,
    statusText,
  });

  // Stream raw engine output to the Relay channel; refresh views on every change.
  context.subscriptions.push(
    channel,
    { dispose: () => bridge.stop() },
    bridge.onLine((line) => channel.appendLine(line)),
    bridge.onChange((s) => {
      statusProvider.refresh();
      setupProvider.refresh();
      if (!s.running && s.exitCode !== undefined && !state.reportedExit) {
        state.reportedExit = true;
        if (s.verdict?.startsWith("[OK]")) {
          vscode.window.showInformationMessage(`${TAG} ${s.verdict} (exit ${s.exitCode}).`);
        } else {
          vscode.window.showErrorMessage(
            `${TAG} [ERR] Run did not prove the join (exit ${s.exitCode}). ${s.verdict ?? ""} See the Relay output channel.`,
          );
        }
      }
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("relay.setupKey", () => setupKeyViaInput()),
    vscode.commands.registerCommand("relay.validateKey", () => runValidation()),
    vscode.commands.registerCommand("relay.clearKey", () => clearKey()),
    vscode.commands.registerCommand("relay.start", () => doStart()),
    vscode.commands.registerCommand("relay.showStatus", () => channel.show(true)),
    vscode.commands.registerCommand("relay.pause", () => {
      vscode.window.showInformationMessage(`${TAG} [INFO] Pause is disabled. ${PAUSE_RESUME_NOTE}`);
    }),
    vscode.commands.registerCommand("relay.resume", () => {
      vscode.window.showInformationMessage(`${TAG} [INFO] Resume is disabled. ${PAUSE_RESUME_NOTE}`);
    }),
  );

  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider("relay.setupView", setupProvider),
    vscode.window.registerTreeDataProvider("relay.statusView", statusProvider),
  );
}

export function deactivate(): void {
  // no-op (the bridge is stopped via the disposable registered in activate)
}
