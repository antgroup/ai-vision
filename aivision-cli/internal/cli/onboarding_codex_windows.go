//go:build amd64 && windows_codex_connect && !native_distribution

package cli

const windowsOnboardingUsage = `  connect Install the Codex plugin from a source-development deployment (experimental)
  agent   Inspect the source-installed Codex plugin

Local server deployment uses a Linux or macOS CLI.
`

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
