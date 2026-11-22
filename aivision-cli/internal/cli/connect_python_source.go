//go:build !native_distribution && (!windows || (amd64 && windows_codex_connect))

package cli

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"runtime"
	"strings"
)

// resolvePython finds a Python 3.11+ interpreter, mirroring connect.py's own
// discovery so the wrapper fails fast with a clear message rather than a
// cryptic SyntaxError from an old interpreter.
func resolvePython(explicit string) (string, error) {
	if explicit != "" {
		if !pythonAtLeast311(explicit) {
			return "", fmt.Errorf("--python must point to a working Python 3.11+ interpreter")
		}
		return explicit, nil
	}
	if env := os.Getenv("AIVISION_PYTHON"); env != "" {
		if !pythonAtLeast311(env) {
			return "", fmt.Errorf("AIVISION_PYTHON must point to Python 3.11+")
		}
		return env, nil
	}
	candidates := []string{"python3.14", "python3.13", "python3.12", "python3.11", "python3", "python"}
	// Prefer an uv-managed interpreter when uv is present (matches connect.py).
	if _, err := exec.LookPath("uv"); err == nil {
		out, err := exec.Command("uv", "python", "find", "--no-project", "--no-python-downloads", ">=3.11").Output()
		if err == nil {
			if p := strings.TrimSpace(string(out)); p != "" {
				candidates = append([]string{p}, candidates...)
			}
		}
	}
	if runtime.GOOS == "windows" {
		out, err := sourcePythonCommand(context.Background(), "py", "-3", "-c", "import sys; print(sys.executable)").Output()
		if path := strings.TrimSpace(string(out)); err == nil && path != "" {
			candidates = append(candidates, path)
		}
	}
	for _, c := range candidates {
		path := c
		if resolved, err := exec.LookPath(c); err == nil {
			path = resolved
		} else {
			continue
		}
		if pythonAtLeast311(path) {
			return path, nil
		}
	}
	return "", fmt.Errorf("connect: Python 3.11+ is required, but no compatible interpreter was found; install Python 3.12 (with uv: uv python install 3.12) and retry, or specify --python")
}

// pythonAtLeast311 reports whether the interpreter runs a version >= 3.11.
func pythonAtLeast311(path string) bool {
	cmd := sourcePythonCommand(context.Background(), path, "-c", "import sys; sys.exit(sys.version_info < (3, 11))")
	return cmd.Run() == nil
}
