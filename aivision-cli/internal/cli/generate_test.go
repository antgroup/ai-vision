package cli

import (
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestNativeGenerateRejectsSourceExportBeforeWriting(t *testing.T) {
	out, work := t.TempDir(), t.TempDir()
	args := []string{"--file", "../../blueprints/aivision.yaml", "--target", "compose", "--workdir", work, "--output", out}
	if err := cmdGenerate(args); err != nil {
		t.Fatal(err)
	}
	composePath := filepath.Join(out, "compose.yaml")
	marker := []byte("existing export must stay untouched")
	if err := os.WriteFile(composePath, marker, 0600); err != nil {
		t.Fatal(err)
	}
	err := cmdGenerate(append(args, "--var", "NATIVE_PYTHON_RUNTIME=true"))
	if err == nil || !strings.Contains(err.Error(), "fresh output directory") {
		t.Fatalf("native export accepted a stale source bundle: %v", err)
	}
	content, err := os.ReadFile(composePath)
	if err != nil || string(content) != string(marker) {
		t.Fatal("native refusal overwrote the existing export")
	}
	for _, name := range []string{"database-init.py", "backend-bootstrap.py", "wait-http.py"} {
		if _, err := os.Stat(filepath.Join(out, "bootstrap", name)); err != nil {
			t.Fatal("native refusal deleted existing source helpers")
		}
	}
}

func TestNativeGenerateFreshAndRepeatedExportOmitsSourcePrograms(t *testing.T) {
	out := t.TempDir()
	args := []string{"--file", "../../blueprints/aivision.yaml", "--target", "compose", "--workdir", t.TempDir(), "--output", out, "--var", "NATIVE_PYTHON_RUNTIME=true"}
	for range 2 {
		if err := cmdGenerate(args); err != nil {
			t.Fatal(err)
		}
	}
	if err := filepath.WalkDir(out, func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if !entry.IsDir() && strings.HasSuffix(path, ".py") {
			t.Errorf("native export contains a source program: %s", path)
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
}
