package kubernetes

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os/exec"
	"strings"
)

// Runner never invokes a shell. Secret manifests are passed on stdin, not argv.
type Runner interface {
	Run(context.Context, []byte, ...string) ([]byte, error)
	Stream(context.Context, io.Writer, ...string) error
}

type Kubectl struct{}

func (Kubectl) Run(ctx context.Context, input []byte, args ...string) ([]byte, error) {
	cmd := exec.CommandContext(ctx, "kubectl", args...)
	cmd.Stdin = bytes.NewReader(input)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		if ctx.Err() != nil {
			return out, ctx.Err()
		}
		return out, fmt.Errorf("kubectl: %w: %s", err, strings.TrimSpace(stderr.String()))
	}
	return out, nil
}

func (Kubectl) Stream(ctx context.Context, out io.Writer, args ...string) error {
	cmd := exec.CommandContext(ctx, "kubectl", args...)
	cmd.Stdout, cmd.Stderr = out, out
	return cmd.Run()
}

type object map[string]any

func at(v any, keys ...string) any {
	for _, key := range keys {
		switch m := v.(type) {
		case object:
			v = m[key]
		case map[string]any:
			v = m[key]
		default:
			return nil
		}
	}
	return v
}
func str(v any, keys ...string) string { s, _ := at(v, keys...).(string); return s }
func list(v any, keys ...string) []any { s, _ := at(v, keys...).([]any); return s }
func integer(v any, keys ...string) int {
	switch n := at(v, keys...).(type) {
	case int: // decoded YAML manifests
		return n
	case float64: // Kubernetes JSON responses
		return int(n)
	default:
		return 0
	}
}

func (a *App) args(ns string, args ...string) []string {
	out := []string{"--context", a.Context, "--request-timeout=20s"}
	if ns != "" {
		out = append(out, "--namespace", ns)
	}
	return append(out, args...)
}

func (a *App) run(ctx context.Context, ns string, input []byte, args ...string) ([]byte, error) {
	b, err := a.runner.Run(ctx, input, a.args(ns, args...)...)
	if err != nil {
		return nil, fmt.Errorf("%s", a.redact(err.Error()))
	}
	return b, nil
}

func (a *App) get(ctx context.Context, ns, resource, name string) (object, error) {
	args := []string{"get", resource}
	if name != "" {
		args = append(args, name, "--ignore-not-found")
	}
	args = append(args, "-o", "json")
	b, err := a.run(ctx, ns, nil, args...)
	if err != nil {
		return nil, err
	}
	if len(bytes.TrimSpace(b)) == 0 {
		return nil, nil
	}
	var obj object
	if err := json.Unmarshal(b, &obj); err != nil {
		return nil, fmt.Errorf("decode %s response: %w", resource, err)
	}
	return obj, nil
}

func (a *App) redact(s string) string {
	for _, secret := range a.redactions {
		if secret != "" {
			s = strings.ReplaceAll(s, secret, "[REDACTED]")
		}
	}
	return s
}

// A pipe preserves streaming while redacting complete lines (including when
// a secret crosses an individual process write boundary).
func (a *App) stream(ctx context.Context, ns string, args ...string) error {
	r, w := io.Pipe()
	done := make(chan error, 1)
	go func() {
		scan := bufio.NewScanner(r)
		scan.Buffer(make([]byte, 4096), 4*1024*1024)
		for scan.Scan() {
			fmt.Fprintln(a.out, a.redact(scan.Text()))
		}
		err := scan.Err()
		r.CloseWithError(err)
		done <- err
	}()
	err := a.runner.Stream(ctx, w, a.args(ns, args...)...)
	w.Close()
	readErr := <-done
	if err != nil {
		return fmt.Errorf("%s", a.redact(err.Error()))
	}
	return readErr
}
