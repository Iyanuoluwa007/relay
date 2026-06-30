// KeyStore: the ONLY place the ANTHROPIC_API_KEY lives at rest.
//
// Backed by VS Code SecretStorage (context.secrets), which encrypts via the OS
// keychain. We never use settings.json or globalState (those are plaintext on
// disk). Honest scope: SecretStorage protects against on-disk plaintext, not
// against a malicious co-installed extension in the same VS Code instance.
//
// House style: no em-dashes, no emojis, plain-text labels. The key value is
// never logged; only a masked preview (at most the first 4 characters).

import * as vscode from "vscode";

const SECRET_KEY = "relay.anthropicApiKey";

export class KeyStore {
  constructor(private readonly secrets: vscode.SecretStorage) {}

  async store(value: string): Promise<void> {
    await this.secrets.store(SECRET_KEY, value);
  }

  async get(): Promise<string | undefined> {
    return this.secrets.get(SECRET_KEY);
  }

  async delete(): Promise<void> {
    await this.secrets.delete(SECRET_KEY);
  }

  async has(): Promise<boolean> {
    return (await this.secrets.get(SECRET_KEY)) !== undefined;
  }
}

// Show at most the first 4 characters of a secret, never the full value.
export function maskKey(value: string | undefined): string {
  if (!value) {
    return "<empty>";
  }
  return value.slice(0, 4) + "...";
}
