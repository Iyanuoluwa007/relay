// setupView: the SETTINGS PAGE with the required API-key gate (a webview view).
//
// The user pastes an ANTHROPIC_API_KEY here. The webview posts it to the
// extension, which stores it in SecretStorage and runs two-stage validation.
// Run actions stay gated until validation passes. The honest boundaries are
// shown verbatim, they are a feature, not a hedge.
//
// House style: no em-dashes, no emojis, plain-text [OK]/[ERR]/[INFO] labels.

import * as vscode from "vscode";

export interface SetupHandlers {
  saveAndValidate(key: string): Promise<void>;
  clearKey(): Promise<void>;
  startRun(): Promise<void>;
  statusText(): Promise<string>;
}

export class RelaySetupViewProvider implements vscode.WebviewViewProvider {
  private view?: vscode.WebviewView;

  constructor(private readonly handlers: SetupHandlers) {}

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = { enableScripts: true };
    view.webview.html = this.html(view.webview);
    view.webview.onDidReceiveMessage(async (msg: { type: string; key?: string }) => {
      if (msg.type === "save" && typeof msg.key === "string" && msg.key.trim().length > 0) {
        await this.handlers.saveAndValidate(msg.key.trim());
      } else if (msg.type === "clear") {
        await this.handlers.clearKey();
      } else if (msg.type === "start") {
        await this.handlers.startRun();
      }
      await this.postStatus();
    });
    void this.postStatus();
  }

  refresh(): void {
    void this.postStatus();
  }

  private async postStatus(): Promise<void> {
    const text = await this.handlers.statusText();
    void this.view?.webview.postMessage({ type: "status", text });
  }

  private html(webview: vscode.Webview): string {
    const nonce = getNonce();
    const csp = `default-src 'none'; style-src ${webview.cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}';`;
    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="${csp}">
  <style>
    body { font-family: var(--vscode-font-family); font-size: var(--vscode-font-size); padding: 10px; }
    h3 { margin: 0 0 6px 0; }
    input { width: 100%; box-sizing: border-box; margin: 4px 0; padding: 4px;
            background: var(--vscode-input-background); color: var(--vscode-input-foreground);
            border: 1px solid var(--vscode-input-border, transparent); }
    button { margin: 4px 4px 4px 0; padding: 4px 8px;
             background: var(--vscode-button-background); color: var(--vscode-button-foreground);
             border: none; cursor: pointer; }
    button.secondary { background: var(--vscode-button-secondaryBackground); color: var(--vscode-button-secondaryForeground); }
    #status { margin: 8px 0; padding: 6px; border: 1px solid var(--vscode-panel-border); white-space: pre-wrap; }
    .note { color: var(--vscode-descriptionForeground); font-size: 0.9em; }
    ul { padding-left: 16px; }
  </style>
</head>
<body>
  <h3>Relay API key</h3>
  <p class="note">Relay requires an ANTHROPIC_API_KEY. The interactive Claude login
  does not authorize headless runs (engine finding #11), so a key is required.</p>

  <input id="key" type="password" placeholder="Paste your ANTHROPIC_API_KEY" aria-label="Anthropic API key" />
  <div>
    <button id="save">Save and Validate</button>
    <button id="clear" class="secondary">Clear Key</button>
    <button id="start">Start Run</button>
  </div>

  <div id="status">[INFO] loading...</div>

  <h3>Honest boundaries</h3>
  <ul class="note">
    <li>Injected interrupts, not real-limit detection. This scaffold drives the
    engine's proven injected-interrupt path. Real session-limit detection against
    live CLI output is not built yet.</li>
    <li>SecretStorage protects against on-disk plaintext, not against a malicious
    co-installed extension. The key is stored in VS Code SecretStorage (OS
    keychain), never in settings.json or globalState.</li>
    <li>Python-interpreter discovery with a clear error. The extension locates a
    Python interpreter (python, py -3, python3). If none is found it reports a
    plain [ERR].</li>
  </ul>
  <p class="note">Pause and resume are disabled in this scaffold. Live pause/resume
  arrives when the engine exposes a long-lived resumable task.</p>

  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    const keyEl = document.getElementById('key');
    document.getElementById('save').addEventListener('click', () => {
      vscode.postMessage({ type: 'save', key: keyEl.value });
      keyEl.value = '';
    });
    document.getElementById('clear').addEventListener('click', () => vscode.postMessage({ type: 'clear' }));
    document.getElementById('start').addEventListener('click', () => vscode.postMessage({ type: 'start' }));
    window.addEventListener('message', (e) => {
      if (e.data && e.data.type === 'status') {
        document.getElementById('status').textContent = e.data.text;
      }
    });
  </script>
</body>
</html>`;
  }
}

function getNonce(): string {
  let text = "";
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  for (let i = 0; i < 32; i++) {
    text += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return text;
}
