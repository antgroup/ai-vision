package cli

import (
	"fmt"
	"os"
	"path/filepath"
	"testing"
)

func TestBuildAppRejectsUnsafeNamespaceOverride(t *testing.T) {
	dir := t.TempDir()
	bpPath := filepath.Join(dir, "blueprint.yaml")
	content := fmt.Sprintf(`metadata: {name: ns-test, namespace: aivision}
config:
  workdir: %s
  sourceRoot: %s
components:
  - {name: demo, kind: process, run: "true"}
`, dir, filepath.Join(dir, "src"))
	if err := os.WriteFile(bpPath, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}

	for _, namespace := range []string{".", "..", "../victim", "/absolute", `..\victim`, "a/b", "_hidden"} {
		t.Run(namespace, func(t *testing.T) {
			if _, err := buildApp(&globalOpts{file: bpPath, namespace: namespace}); err == nil {
				t.Fatalf("buildApp accepted namespace %q", namespace)
			}
		})
	}

	app, err := buildApp(&globalOpts{file: bpPath, namespace: "existing_"})
	if err != nil {
		t.Fatalf("existing Compose-compatible selector was rejected: %v", err)
	}
	if want := filepath.Join(dir, "existing_"); app.State.Root != want {
		t.Fatalf("state root = %q, want %q", app.State.Root, want)
	}
}
