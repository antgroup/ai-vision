//go:build !amd64 || native_distribution

package cli

const windowsOnboardingUsage = `
Local server deployment and Agent installation use a Linux or macOS CLI.
`

func windowsOnboardingCommand(string, []string) (bool, error) {
	return false, nil
}
