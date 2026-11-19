package auth

import (
	"fmt"
	"io"
	"os"
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
