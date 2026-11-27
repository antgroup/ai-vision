//go:build !windows

package cli

func validateOnboardingTarget(string, string) error { return nil }
func connectHelp() string                           { return connectUsage }
func agentHelp() string                             { return agentUsage }
