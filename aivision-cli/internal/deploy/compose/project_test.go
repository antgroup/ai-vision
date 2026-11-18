package compose

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/antgroup/ai-vision/aivision-cli/internal/blueprint"
	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/runtime"
	"github.com/antgroup/ai-vision/aivision-cli/internal/runner"
)

// A mutable bundle would let a second cast rewrite scripts mounted by running
// services. Real rendering, publication, permissions and hash verification run.
func TestBundlesArePrivateImmutableAndSavedOperationsVerifyIntegrity(t *testing.T) {
	app := productApp(t)
	project := New(app)
	bundle, err := project.Publish()
	if err != nil {
		t.Fatal(err)
	}
	if again, err := project.Publish(); err != nil || again != bundle {
		t.Fatalf("identical configuration must reuse immutable bundle: %q %v", again, err)
	}
	for _, path := range []string{bundle, filepath.Join(bundle, "compose.yaml"), filepath.Join(bundle, ".cli-vars.json")} {
		info, err := os.Stat(path)
		if err != nil || info.Mode().Perm()&0077 != 0 {
			t.Fatalf("private bundle permissions: %s %v", path, err)
		}
	}
	data, err := os.ReadFile(filepath.Join(bundle, ".cli-vars.json"))
	if err != nil {
		t.Fatal(err)
	}
	var config struct {
		Vars map[string]string `json:"vars"`
	}
	if err := json.Unmarshal(data, &config); err != nil {
		t.Fatal(err)
	}
	if config.Vars["FE_IMAGE"] != "ghcr.io/antgroup/ai-vision/frontend:latest" || config.Vars["OB_IMAGE"] != app.Vars["OB_IMAGE"] || !strings.Contains(config.Vars["OCEANBASE_DSN"], "${OB_TENANT_PASSWORD}") {
		t.Fatal("saved configuration must pin selected images but preserve derived templates")
	}
	if _, ok := config.Vars["STATE_DIR"]; ok {
		t.Fatal("saved config contains derived state directory")
	}
	writePointer(t, app, bundle)
	if saved, err := project.Saved(); err != nil || saved != bundle {
		t.Fatalf("saved bundle was not recovered: %q %v", saved, err)
	}
	if err := os.WriteFile(filepath.Join(bundle, "bootstrap", "seekdb.sh"), []byte("tampered"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := project.Saved(); err == nil {
		t.Fatal("saved project accepted modified lifecycle code")
	}
}

func TestSavedBundleRejectsEscapesAndSymlinks(t *testing.T) {
	app := productApp(t)
	project := New(app)
	outside := t.TempDir()
	writePointer(t, app, outside)
	if _, err := project.Saved(); err == nil {
		t.Fatal("saved pointer escaped namespace bundles")
	}
	bundle, err := project.Publish()
	if err != nil {
		t.Fatal(err)
	}
	script := filepath.Join(bundle, "bootstrap", "seekdb.sh")
	if err := os.Remove(script); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(outside, "compose.py"), script); err != nil {
		t.Fatal(err)
	}
	writePointer(t, app, bundle)
	if _, err := project.Saved(); err == nil {
		t.Fatal("saved bundle followed a script symlink")
	}
}

func productApp(t *testing.T) *runtime.App {
	t.Helper()
	bp, err := blueprint.Load("../../../blueprints/aivision.yaml")
	if err != nil {
		t.Fatal(err)
	}
	bp.Config.Workdir = t.TempDir()
	bp.Metadata.Namespace = "test-project"
	bp.Config.Vars["AIVISION_NATIVE_TRACE_HISTORY_START"] = "2026-01-01T00:00:00Z"
	app, err := runtime.NewApp(bp, runner.NewLogger(false), false, false)
	if err != nil {
		t.Fatal(err)
	}
	return app
}

func writePointer(t *testing.T, app *runtime.App, bundle string) {
	t.Helper()
	if err := os.MkdirAll(app.State.StateDir, 0700); err != nil {
		t.Fatal(err)
	}
	data, err := json.Marshal(map[string]any{"schema": 1, "bundle": bundle, "status": "ready"})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(app.State.StateDir, ".compose-cli.json"), data, 0600); err != nil {
		t.Fatal(err)
	}
}
