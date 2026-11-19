//go:build !windows

package auth

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
)

// PromptSecret reads a secret from stdin with terminal echo disabled when a TTY
// is available. It uses POSIX stty on Linux/macOS and refuses to echo a secret
// when echo cannot be disabled. Automation uses ReadSecretStdin explicitly.
func PromptSecret(label string) (string, error) {
	mode := exec.Command("stty", "-g")
	mode.Stdin = os.Stdin
	previous, err := mode.Output()
	if err != nil {
		return "", fmt.Errorf("%s requires a terminal; use the corresponding stdin flag", label)
	}
	disable := exec.Command("stty", "-echo")
	disable.Stdin = os.Stdin
	if err := disable.Run(); err != nil {
		return "", fmt.Errorf("cannot disable terminal echo: %w", err)
	}
	defer func() {
		restore := exec.Command("stty", strings.TrimSpace(string(previous)))
		restore.Stdin = os.Stdin
		_ = restore.Run()
		fmt.Fprintln(os.Stderr)
	}()
	fmt.Fprintf(os.Stderr, "%s: ", label)
	return readLine(os.Stdin)
}

func Interactive() bool {
	cmd := exec.Command("stty", "-g")
	cmd.Stdin = os.Stdin
	return cmd.Run() == nil
}
