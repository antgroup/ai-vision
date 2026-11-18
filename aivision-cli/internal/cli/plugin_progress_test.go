package cli

import (
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/sandboxplugin"
)

func TestPluginCommandProcess(t *testing.T) {
	if os.Getenv("AIV_PLUGIN_TEST_PROCESS") != "1" {
		return
	}
	for i, arg := range os.Args {
		if arg == "--" {
			os.Args = append([]string{"aiv", "plugin"}, os.Args[i+1:]...)
			Run("")
			os.Exit(0)
		}
	}
	t.Fatal("missing helper arguments")
}

func runPluginCommand(t *testing.T, args ...string) (string, string, error) {
	t.Helper()
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	child := exec.Command(exe, append([]string{"-test.run=^TestPluginCommandProcess$", "--"}, args...)...)
	child.Env = append(os.Environ(), "AIV_PLUGIN_TEST_PROCESS=1")
	var stdout, stderr bytes.Buffer
	child.Stdout, child.Stderr = &stdout, &stderr
	err = child.Run()
	return stdout.String(), stderr.String(), err
}

func TestPluginInstallReportsValidationFailureOnStderr(t *testing.T) {
	out, diagnostic, err := runPluginCommand(t, "install", "openshell", "--state-dir", t.TempDir())
	if err == nil || out != "" {
		t.Fatalf("invalid install should fail without a success result: %q %v", out, err)
	}
	for _, want := range []string{"[1/6]", "Checking operator files", "Failed", "plugin status openshell", "plugin logs openshell"} {
		if !strings.Contains(diagnostic, want) {
			t.Errorf("missing %q in diagnostics: %s", want, diagnostic)
		}
	}
}

func TestPluginInstallTimeoutMustBePositive(t *testing.T) {
	for _, timeout := range []string{"0s", "-1s"} {
		_, diagnostic, err := runPluginCommand(t, "install", "openshell", "--timeout="+timeout)
		if err == nil || !strings.Contains(diagnostic, "--timeout must be positive") {
			t.Fatalf("invalid timeout %s: %v %s", timeout, err, diagnostic)
		}
	}
	_, diagnostic, err := runPluginCommand(t, "install", "openshell", "--timeout", "1s")
	if err == nil || strings.Contains(diagnostic, "flag provided but not defined") || !strings.Contains(diagnostic, "[1/6]") {
		t.Fatalf("positive timeout did not reach validation: %v %s", err, diagnostic)
	}
}

func TestPluginStatusJSONHasNoProgress(t *testing.T) {
	out, diagnostic, err := runPluginCommand(t, "status", "openshell", "--format", "json", "--state-dir", filepath.Join(t.TempDir(), "state"))
	var status struct {
		Installed bool `json:"installed"`
	}
	if err != nil || json.Unmarshal([]byte(out), &status) != nil || status.Installed || diagnostic != "" {
		t.Fatalf("status stdout must be pure JSON, stderr empty: %q %q %v", out, diagnostic, err)
	}
}

func TestPluginProgressDistinguishesCacheWaitAndNotReady(t *testing.T) {
	var out bytes.Buffer
	for _, event := range []sandboxplugin.ProgressEvent{
		{Stage: "image", State: "done", Detail: "cached", Elapsed: 2 * time.Second},
		{Stage: "container", State: "wait", Elapsed: 5 * time.Second},
		{Stage: "readiness", State: "done", Detail: "not_ready", Elapsed: time.Second},
	} {
		printPluginProgress(&out, event)
	}
	for _, want := range []string{"cached plugin image", "Still waiting", "5s", "provider is not ready"} {
		if !strings.Contains(out.String(), want) {
			t.Errorf("missing %q: %s", want, &out)
		}
	}
	if strings.Contains(out.String(), "provider is ready") {
		t.Fatal("not-ready service must not be presented as ready")
	}
}

func TestPluginRecoveryQuotesStatePath(t *testing.T) {
	var out bytes.Buffer
	printPluginRecovery(&out, "/tmp/operator's $(touch nope) state")
	if !strings.Contains(out.String(), "--state-dir '/tmp/operator'\"'\"'s $(touch nope) state'") {
		t.Fatalf("recovery command must safely quote operator paths: %s", &out)
	}
}

func TestPluginRecoveryLeavesSafeStatePathUnquoted(t *testing.T) {
	var out bytes.Buffer
	printPluginRecovery(&out, "/opt/ai-vision/openshell/installation")
	for _, command := range []string{
		"aiv plugin status openshell --state-dir /opt/ai-vision/openshell/installation",
		"aiv plugin logs openshell --state-dir /opt/ai-vision/openshell/installation -n 100",
	} {
		if !strings.Contains(out.String(), command) {
			t.Errorf("ordinary recovery paths should not need quotes: missing %q in %s", command, &out)
		}
	}
}

func TestPluginRecoveryPreservesLiteralStatePath(t *testing.T) {
	shells := []string{"/bin/sh"}
	if _, err := os.Stat("/bin/zsh"); err == nil {
		shells = append(shells, "/bin/zsh")
	}
	for _, stateDir := range []string{
		"", "/tmp/two words", "/tmp/operator's sandbox", "/tmp/$HOME",
		"/tmp/$(printf injected)", "/tmp/`printf injected`", "/tmp/value; printf injected",
		"/tmp/*.go", "~root", "=ls", "/tmp/line one\nline two",
	} {
		var out bytes.Buffer
		printPluginRecovery(&out, stateDir)
		prefix := "  aiv plugin status openshell --state-dir "
		_, suffix, ok := strings.Cut(out.String(), prefix)
		if !ok {
			t.Fatal("recovery status command is missing")
		}
		quoted := strings.TrimSuffix(strings.Split(suffix, "\n  aiv plugin logs")[0], "\n")
		for _, shell := range shells {
			args, err := exec.Command(shell, "-c", "set -- "+quoted+"; printf '%s\\0' \"$@\"").Output()
			if err != nil || string(args) != stateDir+"\x00" {
				t.Errorf("%s did not preserve state path %q: got %q, error %v", shell, stateDir, args, err)
			}
		}
	}
}
