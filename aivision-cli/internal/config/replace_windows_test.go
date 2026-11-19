package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestWindowsReplaceRetriesOpenReader(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "config.json")
	replacement := filepath.Join(dir, "replacement.json")
	if err := os.WriteFile(path, []byte("old"), filePerms); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(replacement, []byte("new"), filePerms); err != nil {
		t.Fatal(err)
	}

	// os.ReadFile opens the same type of handle when another command reads
	// the profile. Keep it open until the first replacement attempt.
	reader, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()

	firstAttempt := true
	if err := retryWindowsReplace(func() error {
		err := os.Rename(replacement, path)
		if firstAttempt {
			firstAttempt = false
			if err == nil {
				t.Fatal("replaced profile while the reader held it open")
			}
			if closeErr := reader.Close(); closeErr != nil {
				t.Fatal(closeErr)
			}
		}
		return err
	}); err != nil {
		t.Fatal(err)
	}
	if contents, err := os.ReadFile(path); err != nil || string(contents) != "new" {
		t.Fatalf("replaced config = %q, %v", contents, err)
	}
}
