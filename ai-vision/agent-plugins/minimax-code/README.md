# MiniMax Code CLI PoC

This integration collects traces from `mcode exec` tasks started with the AI Vision launcher. The PoC requires MiniMax Code v0.7.2 and uses a version-specific history format. macOS and glibc Linux use the GitHub release. The Windows x64 source installer recognizes the official Windows installer and npm launcher layouts for the pinned version. Configure a model in the same MiniMax Code profile before running a task.

## Enable it in a source deployment

From the repository root, build the preview artifact into the deployment's `AIVISION_PLUGIN_ARTIFACTS_DIR`. Source deployments use `ai-vision/agent-plugins/dist` by default.

```sh
python3 ai-vision/agent-plugins/scripts/build_plugins.py \
  --agent minimax-code --output "${AIVISION_PLUGIN_ARTIFACTS_DIR:-ai-vision/agent-plugins/dist}"
```

Run the existing deployment command `manage.py configure_third_party_agents --endpoint <SigNoz URL>` to register the market entry. In the target AI Vision space, open **MiniMax Code CLI (PoC)** in Agent Market and run its generated installation command. It exchanges the one-use grant for a scoped Agent credential.

Run the launcher printed by the installer. It accepts `mcode exec` arguments and writes the resulting Trace ID to stderr. Use the session ID from stdout with `--session` to continue that session.

```sh
~/.minimax/aivision/minimax-code/run --cwd /path/to/project "Read README.md and count its lines."
~/.minimax/aivision/minimax-code/run --cwd /path/to/project --session SESSION_ID "Read a second file."
```

On Windows x64, run the PowerShell launcher printed by the source installer.

```powershell
& "$env:USERPROFILE\.minimax\aivision\minimax-code\run.ps1" --cwd "C:\path\to\project" "Read README.md and count its lines."
```

Run the launcher directly in the Windows console to cancel a task with Ctrl+C. The launcher and host receive that console event together. PowerShell pipelines and external process termination can stop the launcher before it exports the Trace.

## Trace behavior

Each turn produces a Trace with separate model and tool spans. Token counts come from individual committed MiniMax Code responses. The launcher also takes the terminal outcome from the structured CLI stream and closes any running tool span when a turn is cancelled. Calls interrupted before MiniMax Code reports usage have missing counts.

For producer-side validation, the launcher uses the scoped ingest token's opaque observation subject for `user.id` and `ant.username`. AI Vision authenticates the token and sets the authoritative Resource identity at ingestion. Weaver Guard accepts those Resource fields when validating stored traces.

The installer defaults to Privacy. Full collection includes prompt, response, and tool content. The launcher uses temporary host diagnostics for model timings and removes them after the run. It checks the CLI version before reading the version-specific session history. Interactive TUI, ACP, desktop, and web runs require separate integrations.

Run the focused tests with `npm test` from this directory.
On native Windows, run `npm run build` followed by `python tests/test_windows_minimax_console.py` to exercise console cancellation with a synthetic CLI and local ingest endpoint. The managed `run.ps1` test uses PowerShell 7.
