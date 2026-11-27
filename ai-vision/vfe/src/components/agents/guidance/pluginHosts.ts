// Host CLI binary for each built-in plugin. `agents.json` is the source of
// truth and the generated guide carries `host`; this map keeps saved guides
// (created before the field existed) working without a data migration.
export const PLUGIN_HOSTS: Record<string, string> = {
  "claude-code": "claude",
  codex: "codex",
  kimi: "kimi",
  opencode: "opencode",
  openclaw: "openclaw",
  pi: "pi",
  dsh: "dsh",
  "minimax-code": "mcode",
}
