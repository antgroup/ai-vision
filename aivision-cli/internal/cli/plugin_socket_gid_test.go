package cli

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestPluginSocketGIDFlagHelp(t *testing.T) {
	_, diagnostic, _ := runPluginCommand(t, "install", "openshell", "--help")
	for _, want := range []string{"--docker-socket-gid", "container-side", "2147483647"} {
		if !strings.Contains(diagnostic, want) {
			t.Errorf("install help missing %q: %s", want, diagnostic)
		}
	}
}

func TestPluginSocketGIDFlagParsingNeverInvokesDockerForInvalidInstall(t *testing.T) {
	dir := t.TempDir()
	marker := filepath.Join(dir, "docker-was-called")
	bin := filepath.Join(dir, "docker")
	if err := os.WriteFile(bin, []byte("#!/bin/sh\ntouch '"+marker+"'\nexit 1\n"), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("DOCKER_BIN", bin)
	for _, value := range []string{"0", "2147483647", "-1", "2147483648", "not-a-gid", "999999999999999999999999999999"} {
		t.Run(value, func(t *testing.T) {
			_, diagnostic, err := runPluginCommand(t, "install", "openshell", "--docker-socket-gid", value, "--state-dir", filepath.Join(dir, "state"))
			if err == nil || strings.Contains(diagnostic, "flag provided but not defined") {
				t.Fatalf("socket GID flag was not parsed: %v %s", err, diagnostic)
			}
			if value == "0" || value == "2147483647" {
				if !strings.Contains(diagnostic, "explicit --image") {
					t.Fatalf("valid GID did not reach operator-input validation: %s", diagnostic)
				}
			} else if !strings.Contains(diagnostic, "docker-socket-gid") || !strings.Contains(diagnostic, "2147483647") {
				t.Fatalf("invalid GID lacks bounded validation error: %s", diagnostic)
			}
			if _, err := os.Stat(marker); !os.IsNotExist(err) {
				t.Fatalf("Docker invoked before valid operator configuration: %v", err)
			}
		})
	}
}
