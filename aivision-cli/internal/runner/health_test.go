package runner

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/blueprint"
	"gopkg.in/yaml.v3"
)

func argvHealth(t *testing.T, argv []string) *blueprint.Health {
	t.Helper()
	raw, err := yaml.Marshal(map[string]any{"execArgv": argv})
	if err != nil {
		t.Fatal(err)
	}
	var h blueprint.Health
	if err := yaml.Unmarshal(raw, &h); err != nil {
		t.Fatal(err)
	}
	return &h
}

func TestNativeHealthcheckPassesLiteralArgv(t *testing.T) {
	binary, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	file := filepath.Join(t.TempDir(), "argv.json")
	t.Setenv("AIV_NATIVE_HEALTH_HELPER", "record")
	want := []string{file, "literal space", "$HOME; exit 9", "'quoted'"}
	argv := append([]string{binary, "-test.run=^TestNativeHealthcheckHelper$", "--"}, want...)
	if ok, detail := NewLogger(false).Probe(context.Background(), argvHealth(t, argv), time.Second); !ok {
		t.Fatalf("native probe failed: %s", detail)
	}
	raw, err := os.ReadFile(file)
	if err != nil {
		t.Fatalf("probe did not run executable: %v", err)
	}
	var got []string
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, want[1:]) {
		t.Fatalf("executable argv = %q, want %q", got, want[1:])
	}
}

func TestNativeHealthcheckReportsNonzeroExit(t *testing.T) {
	binary, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	t.Setenv("AIV_NATIVE_HEALTH_HELPER", "fail")
	h := argvHealth(t, []string{binary, "-test.run=^TestNativeHealthcheckHelper$"})
	if ok, detail := NewLogger(false).Probe(context.Background(), h, time.Second); ok || !strings.Contains(detail, "native probe failed") {
		t.Fatalf("nonzero exit must fail with stderr: ok=%v detail=%q", ok, detail)
	}
}

func TestNativeHealthcheckMissingExecutableFails(t *testing.T) {
	h := argvHealth(t, []string{filepath.Join(t.TempDir(), "missing-collector"), "healthcheck"})
	if ok, detail := NewLogger(false).Probe(context.Background(), h, time.Second); ok || detail == "" {
		t.Fatalf("missing executable must be unhealthy: ok=%v detail=%q", ok, detail)
	}
}

func TestNativeHealthcheckHonorsProbeTimeout(t *testing.T) {
	binary, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	t.Setenv("AIV_NATIVE_HEALTH_HELPER", "wait")
	h := argvHealth(t, []string{binary, "-test.run=^TestNativeHealthcheckHelper$"})
	start := time.Now()
	ok, detail := NewLogger(false).Probe(context.Background(), h, 100*time.Millisecond)
	if ok || time.Since(start) > time.Second {
		t.Fatalf("probe must fail at timeout: ok=%v duration=%s detail=%q", ok, time.Since(start), detail)
	}
}

func TestNativeHealthcheckHelper(t *testing.T) {
	switch os.Getenv("AIV_NATIVE_HEALTH_HELPER") {
	case "record":
		for i, arg := range os.Args {
			if arg == "--" {
				raw, _ := json.Marshal(os.Args[i+2:])
				if err := os.WriteFile(os.Args[i+1], raw, 0o600); err != nil {
					os.Exit(8)
				}
				os.Exit(0)
			}
		}
		os.Exit(9)
	case "fail":
		fmt.Fprintln(os.Stderr, "native probe failed")
		os.Exit(7)
	case "wait":
		time.Sleep(2 * time.Second)
		os.Exit(0)
	}
}
