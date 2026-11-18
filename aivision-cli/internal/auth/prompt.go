package auth

import (
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
)

// PromptLine reads a single non-secret line from stdin (e.g. a space id).
func PromptLine(label string) (string, error) {
	if !Interactive() {
		return "", fmt.Errorf("%s is required in non-interactive mode", label)
	}
	fmt.Fprintf(os.Stderr, "%s: ", label)
	s, err := readLine(os.Stdin)
	return strings.TrimSpace(s), err
}

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

func ReadSecretStdin() (string, error) { return readLine(os.Stdin) }

func readLine(r *os.File) (string, error) {
	// Do not buffer beyond one line: later prompts share the same stdin.
	var line []byte
	b := make([]byte, 1)
	for len(line) < 65536 {
		n, err := r.Read(b)
		if n == 1 {
			if b[0] == '\n' {
				return strings.TrimSuffix(string(line), "\r"), nil
			}
			line = append(line, b[0])
		}
		if err != nil {
			if err == io.EOF && len(line) > 0 {
				return string(line), nil
			}
			return "", err
		}
	}
	return "", fmt.Errorf("input line is too long")
}
