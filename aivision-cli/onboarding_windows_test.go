//go:build windows && amd64 && !native_distribution && !windows_codex_connect

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/config"
)

// This exercises the untagged release build and its Windows Python handoff.
// The HTTP server and the installer response are fixtures.
func TestWindowsMiniMaxOnboarding(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	root := t.TempDir()
	binary := filepath.Join(root, "aiv.exe")
	build := exec.CommandContext(ctx, "go", "build", "-mod=vendor", "-o", binary, ".")
	build.Env = append(build.Environ(), "GOFLAGS=", "CGO_ENABLED=0")
	if out, err := build.CombinedOutput(); err != nil {
		t.Fatalf("build stock CLI: %v\n%s", err, out)
	}
	python, err := exec.LookPath("python")
	if err != nil {
		t.Fatal("this test requires the Windows release workflow's Python 3.11+")
	}
	t.Setenv(config.EnvConfigDir, filepath.Join(root, "CLI settings"))
	t.Setenv(config.EnvProfile, "")
	t.Setenv("PYTHON_MANAGER_AUTOMATIC_INSTALL", "true")
	var requests atomic.Int32
	var native atomic.Bool
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		query := r.URL.Query()
		if r.Method != http.MethodGet || r.URL.Path != "/api/public/onboarding/scripts/connect" ||
			query.Get("platform") != "windows" || query.Get("arch") != "x64" || r.Header.Get("Authorization") != "" {
			t.Errorf("unexpected installer request: %s %s", r.Method, r.URL)
			http.NotFound(w, r)
			return
		}
		if native.Load() {
			w.Header().Set("X-AIV-Installer-Format", "native-tar-v1")
		}
		_, _ = fmt.Fprint(w, windowsMiniMaxInstaller)
	}))
	defer server.Close()

	if err := config.Update(ctx, func(cfg *config.File) error {
		cfg.Set(config.DefaultName, config.Profile{Endpoint: server.URL, SpaceID: "fixture-space"})
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	invoke := func(args ...string) (string, error) {
		t.Helper()
		out, err := exec.CommandContext(ctx, binary, args...).CombinedOutput()
		return string(out), err
	}
	requireSuccess := func(args ...string) string {
		t.Helper()
		out, err := invoke(args...)
		if err != nil {
			t.Fatalf("aiv %s: %v\n%s", strings.Join(args[:2], " "), err, out)
		}
		return out
	}
	for _, args := range [][]string{{"connect", "codex"}, {"agent", "status", "codex"}, {"connect", "claude-code"}} {
		if out, err := invoke(args...); err == nil || !strings.Contains(out, "unavailable in the Windows remote client") {
			t.Fatalf("stock CLI accepted unsupported Agent: %v, %s", err, out)
		}
	}
	if requests.Load() != 0 {
		t.Fatal("unsupported Agent contacted the installer")
	}

	profile := filepath.Join(root, "MiniMax test profile")
	args := []string{"connect", "minimax-code", "--base-url", server.URL, "--space-id", "fixture-space",
		"--agent-uuid", "81f1d85b-47c1-4c85-a0ee-b0d17711f575", "--installation-token", "aiv-install-fixture",
		"--config-dir", profile, "--python", python, "--collection-mode", "privacy"}
	preflight := append(append([]string(nil), args...), "--dry-run")
	native.Store(true)
	if out, err := invoke(preflight...); err == nil || !strings.Contains(out, "source-compatible installer") {
		t.Fatalf("Windows accepted an incompatible native installer: %v, %s", err, out)
	}
	native.Store(false)
	if out := requireSuccess(preflight...); !strings.Contains(out, "Preflight checks passed") {
		t.Fatalf("missing preflight confirmation: %s", out)
	}
	if _, err := os.Stat(profile); !os.IsNotExist(err) {
		t.Fatalf("dry-run created profile: %v", err)
	}
	requireSuccess(args...)
	raw := requireSuccess("agent", "status", "minimax-code", "--format", "json",
		"--config-dir", profile, "--python", python)
	var status struct {
		Agent      string `json:"agent"`
		ConfigDir  string `json:"config_dir"`
		Installed  bool   `json:"installed"`
		Credential string `json:"credential"`
	}
	if err := json.Unmarshal([]byte(raw), &status); err != nil {
		t.Fatalf("status output: %v\n%s", err, raw)
	}
	if status.Agent != "minimax-code" || status.ConfigDir != profile || !status.Installed || status.Credential != "valid" {
		t.Fatalf("invalid scoped installation status: %+v", status)
	}
}

const windowsMiniMaxInstaller = `
import argparse
import json
import os
from pathlib import Path

parser = argparse.ArgumentParser()
for name in ("agent", "base-url", "space-id", "agent-uuid", "installation-token",
             "config-dir", "collection-mode", "format"):
    parser.add_argument("--" + name)
parser.add_argument("--dry-run", action="store_true")
parser.add_argument("--status", action="store_true")
args = parser.parse_args()
assert args.agent == "minimax-code"
assert os.environ.get("PYTHON_MANAGER_AUTOMATIC_INSTALL") == "false"
profile = Path(args.config_dir)
receipt = profile / "test-fixture"
if args.status:
    assert args.space_id == "fixture-space"
    assert receipt.is_file()
    print(json.dumps({"agent": args.agent, "config_dir": args.config_dir, "installed": True,
                      "registration": "verified", "credential": "valid", "collection_mode": "privacy"}))
elif args.dry_run:
    assert not args.installation_token
    assert args.collection_mode == "privacy"
    print(json.dumps({"agent": args.agent, "agent_product": "MiniMaxCode",
                      "config_dir": args.config_dir, "ready": True, "collection_mode": "privacy"}))
else:
    assert args.space_id == "fixture-space"
    assert args.agent_uuid == "81f1d85b-47c1-4c85-a0ee-b0d17711f575"
    assert args.installation_token == "aiv-install-fixture"
    profile.mkdir()
    receipt.touch()
`
