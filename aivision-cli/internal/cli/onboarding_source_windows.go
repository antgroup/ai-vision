//go:build amd64 && !native_distribution

package cli

import "fmt"

const windowsOnboardingUsage = `  connect Install MiniMax Code CLI telemetry from a source-compatible deployment
  agent   Inspect the MiniMax Code CLI installation

` + windowsCodexUsage + "Local server deployment uses a Linux or macOS CLI.\n"

func windowsOnboardingCommand(cmd string, args []string) (bool, error) {
	switch cmd {
	case "connect":
		return true, cmdConnect(args)
	case "agent":
		return true, cmdAgent(args)
	default:
		return false, nil
	}
}

func validateOnboardingTarget(command, plugin string) error {
	if canonical := pluginAliases[plugin]; canonical != "" {
		plugin = canonical
	}
	if plugin == "" || plugin == "minimax-code" || windowsCodexOnboarding && plugin == "codex" {
		return nil
	}
	if windowsCodexOnboarding {
		return fmt.Errorf("experimental Windows source onboarding supports only Codex and MiniMax Code")
	}
	return fmt.Errorf("%s is unavailable in the Windows remote client for %s; Windows x64 Agent installation supports minimax-code", command, plugin)
}

func connectHelp() string {
	help := `Install MiniMax Code CLI telemetry on Windows x64.

Usage:
  aiv connect minimax-code [--profile NAME] [--config-dir DIR]
                           [--collection-mode full|privacy] [--dry-run] [--python PATH]
  aiv connect minimax-code --base-url URL --space-id SPACE_ID --agent-uuid UUID
                           --installation-token TOKEN [--config-dir DIR]

Requires mcode, a compatible Node.js runtime, Python 3.11+, and a deployment
serving the source-compatible installer and MiniMax Code telemetry artifact.
Sign in and select a space for account onboarding, or pass the authorization
from the Agent market. Dry runs check prerequisites without changing state.
`
	if windowsCodexOnboarding {
		help += "\nThis experimental build also supports aiv connect codex when the deployment serves the Windows x64 Codex hook.\n"
	}
	return help
}

func agentHelp() string {
	help := `Check MiniMax Code CLI installation, credentials, and recent traces on Windows x64.

Usage:
  aiv agent status minimax-code [--profile NAME] [--config-dir DIR]
                               [--python PATH] [--format text|json]

Requires a saved profile and the deployment's source-compatible installer.
Trace lookup uses the current space and an authorized query SpaceKey.
`
	if windowsCodexOnboarding {
		help += "\nThis experimental build also supports aiv agent status codex.\n"
	}
	return help
}
