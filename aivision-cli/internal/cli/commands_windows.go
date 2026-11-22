package cli

import "fmt"

func configurePlatform(_ string) {}

func platformCommand(cmd string, args []string) (bool, error) {
	if handled, err := windowsOnboardingCommand(cmd, args); handled {
		return true, err
	}
	switch cmd {
	case "agent", "cast", "connect", "destroy", "forge", "gauge", "gen", "generate", "logs", "plugin", "redeploy", "seed", "status":
		return true, fmt.Errorf("%s is unavailable in the Windows remote client; use a Linux or macOS CLI for local deployment and Agent installation", cmd)
	default:
		return false, nil
	}
}

func usage() {
	fmt.Print(`aiv — Windows client for an existing AI Vision deployment.

Usage:
  aiv <command> [sub-command] [flags]
  aiv --version
  aiv <command> --help

Commands:
  auth    Register, sign in or manage a saved profile
  space   List, select or create spaces
  key     Create, import, list or revoke a query SpaceKey
  query   Query traces, spans, sessions, events and metrics
`)
	fmt.Print(windowsOnboardingUsage)
}
