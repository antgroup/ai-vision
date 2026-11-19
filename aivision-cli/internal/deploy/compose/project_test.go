package compose

import (
	"bytes"
	"crypto/sha256"
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
	if !strings.HasPrefix(filepath.Base(bundle), "hmac-v1-") {
		t.Fatal("new bundles must have a keyed content identity")
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

func TestConcurrentPublishersUseOnePrivateIdentityKey(t *testing.T) {
	composeDir := filepath.Join(t.TempDir(), "compose")
	bundlesDir := filepath.Join(composeDir, "bundles")
	if err := os.MkdirAll(bundlesDir, 0700); err != nil {
		t.Fatal(err)
	}
	type result struct {
		key []byte
		err error
	}
	const publishers = 8
	results := make(chan result, publishers)
	start := make(chan struct{})
	for i := 0; i < publishers; i++ {
		go func() {
			<-start
			key, err := loadOrCreateBundleKey(bundlesDir)
			results <- result{key, err}
		}()
	}
	close(start)
	var expected []byte
	for i := 0; i < publishers; i++ {
		result := <-results
		if result.err != nil || len(result.key) != sha256.Size {
			t.Fatalf("initialize private bundle identity key: %v", result.err)
		}
		if expected == nil {
			expected = result.key
		} else if !bytes.Equal(result.key, expected) {
			t.Fatal("concurrent publishers did not reuse the same key")
		}
	}
	info, err := os.Stat(filepath.Join(composeDir, bundleKeyName))
	if err != nil || info.Mode().Perm() != 0600 {
		t.Fatalf("saved identity key must be private: %v", err)
	}
}

func TestLegacySavedBundleRemainsReadableAfterPublishingKeyedBundle(t *testing.T) {
	app := productApp(t)
	project := New(app)
	root, err := canonicalPath(app.State.StateDir)
	if err != nil {
		t.Fatal(err)
	}
	files, err := project.files()
	if err != nil {
		t.Fatal(err)
	}
	bundle := filepath.Join(root, "compose", "bundles", digest(files))
	if err := os.MkdirAll(bundle, 0700); err != nil {
		t.Fatal(err)
	}
	if err := writeFiles(bundle, files); err != nil {
		t.Fatal(err)
	}
	writePointer(t, app, bundle)
	if got, err := project.Saved(); err != nil || got != bundle {
		t.Fatalf("legacy project not recovered: %q %v", got, err)
	}
	keyed, err := project.Publish()
	if err != nil || !strings.HasPrefix(filepath.Base(keyed), bundleIdentityPrefix) {
		t.Fatalf("recast did not publish a keyed bundle: %v", err)
	}
	if got, err := project.Saved(); err != nil || got != bundle {
		t.Fatalf("publishing must retain the original pointer until acceptance: %q %v", got, err)
	}
}

func TestKeyedBundleRequiresOriginalKeyEvenAfterAnIncompleteRestore(t *testing.T) {
	app := productApp(t)
	project := New(app)
	bundle, err := project.Publish()
	if err != nil {
		t.Fatal(err)
	}
	writePointer(t, app, bundle)
	keyPath := filepath.Join(filepath.Dir(filepath.Dir(bundle)), bundleKeyName)
	if err := os.Remove(keyPath); err != nil {
		t.Fatal(err)
	}
	if _, err := project.Saved(); err == nil || !strings.Contains(err.Error(), "original private identity key") {
		t.Fatalf("reading keyed state without the original key must fail: %v", err)
	}
	if _, err := project.Publish(); err == nil || !strings.Contains(err.Error(), "original private identity key") {
		t.Fatalf("publishing must preserve keyed state until its key is restored: %v", err)
	}
	if err := os.RemoveAll(bundle); err != nil {
		t.Fatal(err)
	}
	if _, err := project.Publish(); err == nil || !strings.Contains(err.Error(), "original private identity key") {
		t.Fatalf("the recorded project identity survives a partial restore: %v", err)
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
