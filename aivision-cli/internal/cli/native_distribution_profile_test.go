//go:build native_distribution

package cli

import (
	"context"
	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/compose"
	"github.com/antgroup/ai-vision/aivision-cli/internal/runner"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestNativeDistributionRefusesSourceInstaller(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("print('source installer must not be written')\n"))
	}))
	defer server.Close()
	path, cleanup, err := fetchConnectScript(context.Background(), server.URL, runner.NewLogger(false))
	if cleanup != nil {
		defer cleanup()
	}
	if err == nil || path != "" || !strings.Contains(err.Error(), "native distribution") {
		t.Errorf("source installer accepted: path=%q err=%v", path, err)
	}
}

func TestNativeDistributionRejectsStandaloneComposeBeforeDocker(t *testing.T) {
	dir := t.TempDir()
	marker := filepath.Join(dir, "docker-called")
	docker := filepath.Join(dir, "docker")
	if err := os.WriteFile(docker, []byte("#!/bin/sh\ntouch \"$AIV_TEST_DOCKER_MARKER\"\nexit 9\n"), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
	t.Setenv("AIV_TEST_DOCKER_MARKER", marker)
	for _, action := range []string{"up", "down", "status", "logs"} {
		err := runComposeFile(&globalOpts{composeFile: filepath.Join(dir, "source-compose.yaml")}, action, compose.Options{})
		if err == nil || !strings.Contains(err.Error(), "native distribution") {
			t.Errorf("%s standalone source accepted: %v", action, err)
		}
	}
	if _, err := os.Stat(marker); !os.IsNotExist(err) {
		t.Fatal("native rejection invoked Docker")
	}
}
