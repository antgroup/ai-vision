// Package runner provides the low-level execution primitives shared by the
// deployment runtime: colored logging, shell execution, docker helpers and
// component health probes.
package runner

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/blueprint"
)

// ---------------------------------------------------------------------------
// Logging
// ---------------------------------------------------------------------------

type Logger struct {
	color bool
	w     io.Writer
	verb  bool
}

func NewLogger(verbose bool) *Logger {
	useColor := os.Getenv("NO_COLOR") == "" && isTTY(os.Stdout)
	return &Logger{color: useColor, w: os.Stdout, verb: verbose}
}

func isTTY(f *os.File) bool {
	fi, err := f.Stat()
	if err != nil {
		return false
	}
	return fi.Mode()&os.ModeCharDevice != 0
}

func (l *Logger) paint(code, s string) string {
	if !l.color {
		return s
	}
	return "\033[" + code + "m" + s + "\033[0m"
}

func (l *Logger) Step(format string, a ...any) {
	fmt.Fprintf(l.w, "%s %s\n", l.paint("1;36", "==>"), fmt.Sprintf(format, a...))
}
func (l *Logger) OK(format string, a ...any) {
	fmt.Fprintf(l.w, "%s %s\n", l.paint("1;32", " ok"), fmt.Sprintf(format, a...))
}
func (l *Logger) Warn(format string, a ...any) {
	fmt.Fprintf(l.w, "%s %s\n", l.paint("1;33", "warn"), fmt.Sprintf(format, a...))
}
func (l *Logger) Err(format string, a ...any) {
	fmt.Fprintf(l.w, "%s %s\n", l.paint("1;31", "fail"), fmt.Sprintf(format, a...))
}
func (l *Logger) Info(format string, a ...any) {
	fmt.Fprintf(l.w, "    %s\n", fmt.Sprintf(format, a...))
}
func (l *Logger) Debug(format string, a ...any) {
	if l.verb {
		fmt.Fprintf(l.w, "%s %s\n", l.paint("0;90", "  •"), fmt.Sprintf(format, a...))
	}
}
func (l *Logger) Raw(format string, a ...any) {
	fmt.Fprintf(l.w, format+"\n", a...)
}

// ---------------------------------------------------------------------------
// Shell execution
// ---------------------------------------------------------------------------

// RunOpts controls a shell invocation.
type RunOpts struct {
	Workdir string
	Env     map[string]string
	// Stream sends combined output to the logger's writer as it arrives.
	Stream bool
	// Capture returns stdout instead of streaming.
	Capture bool
	// Quiet suppresses the command echo line.
	Quiet bool
}

// RunShell executes `bash -c script` and streams output to stdout/stderr.
func (l *Logger) RunShell(ctx context.Context, script string, opts RunOpts) error {
	_, err := l.runShell(ctx, script, opts)
	return err
}

// CaptureShell executes `bash -c script` and returns trimmed stdout.
func (l *Logger) CaptureShell(ctx context.Context, script string, opts RunOpts) (string, error) {
	opts.Capture = true
	opts.Stream = false
	out, err := l.runShell(ctx, script, opts)
	return strings.TrimSpace(out), err
}

func (l *Logger) runShell(ctx context.Context, script string, opts RunOpts) (string, error) {
	if strings.TrimSpace(script) == "" {
		return "", nil
	}
	cmd := exec.CommandContext(ctx, "bash", "-c", script)
	if opts.Workdir != "" {
		cmd.Dir = opts.Workdir
	}
	cmd.Env = buildEnv(opts.Env)
	if !opts.Quiet {
		l.Debug("$ %s", FirstLine(script))
		if opts.Workdir != "" {
			l.Debug("  (in %s)", opts.Workdir)
		}
	}
	var capture bytes.Buffer
	if opts.Capture {
		cmd.Stdout = &capture
		cmd.Stderr = &capture
	} else if opts.Stream {
		cmd.Stdout = l.w
		cmd.Stderr = l.w
	} else {
		cmd.Stdout = io.Discard
		cmd.Stderr = io.Discard
	}
	err := cmd.Run()
	return capture.String(), err
}

// FirstLine returns the first line of s, ellipsized when there are more.
func FirstLine(s string) string {
	s = strings.TrimSpace(s)
	if i := strings.IndexByte(s, '\n'); i >= 0 {
		return s[:i] + " …"
	}
	return s
}

// buildEnv returns the current environment plus overrides.
func buildEnv(extra map[string]string) []string {
	env := os.Environ()
	for k, v := range extra {
		env = append(env, k+"="+v)
	}
	return env
}

// LookPath resolves a binary in PATH, returning ("", false) when absent.
func LookPath(name string) (string, bool) {
	p, err := exec.LookPath(name)
	if err != nil {
		return "", false
	}
	return p, true
}

// ---------------------------------------------------------------------------
// Docker helpers
// ---------------------------------------------------------------------------

type Docker struct {
	Bin string
	l   *Logger
}

func NewDocker(l *Logger) *Docker {
	bin := os.Getenv("DOCKER_BIN")
	if bin == "" {
		if p, ok := LookPath("docker"); ok {
			bin = p
		} else {
			bin = "docker"
		}
	}
	return &Docker{Bin: bin, l: l}
}

// Run executes a docker CLI invocation. When capture is true the combined
// output is returned; otherwise it is streamed to the logger's writer.
func (d *Docker) Run(ctx context.Context, capture bool, args ...string) (string, error) {
	cmd := exec.CommandContext(ctx, d.Bin, args...)
	var out bytes.Buffer
	if capture {
		cmd.Stdout = &out
		cmd.Stderr = &out
	} else {
		cmd.Stdout = d.l.w
		cmd.Stderr = d.l.w
	}
	err := cmd.Run()
	return strings.TrimSpace(out.String()), err
}

// ContainerState returns the container state string ("" when it does not exist).
func (d *Docker) ContainerState(ctx context.Context, name string) string {
	out, err := d.Run(ctx, true, "inspect", "-f", "{{.State.Status}}", name)
	if err != nil {
		return ""
	}
	return out
}

func (d *Docker) ContainerExists(ctx context.Context, name string) bool {
	_, err := d.Run(ctx, true, "inspect", name)
	return err == nil
}

func (d *Docker) ImageExists(ctx context.Context, image string) bool {
	_, err := d.Run(ctx, true, "image", "inspect", image)
	return err == nil
}

func (d *Docker) EnsureNetwork(ctx context.Context, net string) error {
	if net == "" {
		return nil
	}
	if _, err := d.Run(ctx, true, "network", "inspect", net); err == nil {
		return nil
	}
	_, err := d.Run(ctx, false, "network", "create", net)
	return err
}

func (d *Docker) EnsureVolume(ctx context.Context, vol string) error {
	if vol == "" {
		return nil
	}
	if _, err := d.Run(ctx, true, "volume", "inspect", vol); err == nil {
		return nil
	}
	_, err := d.Run(ctx, false, "volume", "create", vol)
	return err
}

func (d *Docker) RemoveContainer(ctx context.Context, name string) error {
	if !d.ContainerExists(ctx, name) {
		return nil
	}
	_, err := d.Run(ctx, false, "rm", "-f", name)
	return err
}

// ---------------------------------------------------------------------------
// Health checks
// ---------------------------------------------------------------------------

// ParseDur parses a duration string, falling back to def when empty/invalid.
func ParseDur(s string, def time.Duration) time.Duration {
	if s == "" {
		return def
	}
	if d, err := time.ParseDuration(s); err == nil {
		return d
	}
	return def
}

// WaitHealthy blocks until the component's health probe passes or retries run out.
func (l *Logger) WaitHealthy(ctx context.Context, name string, h *blueprint.Health) error {
	if h == nil {
		return nil
	}
	retries := h.Retries
	if retries <= 0 {
		retries = 40
	}
	interval := ParseDur(h.Interval, 3*time.Second)
	timeout := ParseDur(h.Timeout, 5*time.Second)
	var last string
	for i := 0; i < retries; i++ {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		ok, detail := l.Probe(ctx, h, timeout)
		if ok {
			return nil
		}
		last = detail
		l.Debug("health %s: %s (attempt %d/%d)", name, detail, i+1, retries)
		select {
		case <-time.After(interval):
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	return fmt.Errorf("component %q not healthy after %d attempts: %s", name, retries, last)
}

// Probe runs a single health check attempt, reporting success and a detail string.
func (l *Logger) Probe(ctx context.Context, h *blueprint.Health, timeout time.Duration) (bool, string) {
	if h.TCP != "" {
		conn, err := net.DialTimeout("tcp", h.TCP, timeout)
		if err != nil {
			return false, "tcp " + h.TCP + ": " + err.Error()
		}
		_ = conn.Close()
		return true, ""
	}
	if h.HTTP != "" {
		client := &http.Client{Timeout: timeout}
		req, _ := http.NewRequestWithContext(ctx, http.MethodGet, h.HTTP, nil)
		resp, err := client.Do(req)
		if err != nil {
			return false, "http " + h.HTTP + ": " + err.Error()
		}
		defer resp.Body.Close()
		want := h.ExpectStatus
		if want == 0 {
			want = 200
		}
		if resp.StatusCode == want {
			return true, ""
		}
		return false, fmt.Sprintf("http %s: status %d (want %d)", h.HTTP, resp.StatusCode, want)
	}
	if len(h.ExecArgv) > 0 {
		probeCtx, cancel := context.WithTimeout(ctx, timeout)
		defer cancel()
		out, err := exec.CommandContext(probeCtx, h.ExecArgv[0], h.ExecArgv[1:]...).CombinedOutput()
		if err != nil {
			return false, "execArgv: " + firstNonEmpty(strings.TrimSpace(string(out)), err.Error())
		}
		return true, ""
	}
	if h.Exec != "" {
		out, err := l.CaptureShell(ctx, h.Exec, RunOpts{Quiet: true})
		if err != nil {
			return false, "exec: " + firstNonEmpty(out, err.Error())
		}
		return true, ""
	}
	return true, ""
}

func firstNonEmpty(a, b string) string {
	if strings.TrimSpace(a) != "" {
		return a
	}
	return b
}
