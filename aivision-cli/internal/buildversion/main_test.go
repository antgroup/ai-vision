package main

import (
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func invoke(t *testing.T, arguments ...string) (string, error) {
	t.Helper()
	previousArgs, previousStdout := os.Args, os.Stdout
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		os.Args, os.Stdout = previousArgs, previousStdout
		r.Close()
		w.Close()
	})
	os.Args = append([]string{"buildversion"}, arguments...)
	os.Stdout = w
	err = run()
	w.Close()
	output, readErr := io.ReadAll(r)
	if readErr != nil {
		t.Fatal(readErr)
	}
	return string(output), err
}

func TestVersionFileProvidesBuildVersion(t *testing.T) {
	for _, tc := range []struct {
		name, content, want string
	}{
		{"product baseline", "0.0.1\n", "0.0.1\n"},
		{"release candidate", "0.1.0-rc.1\n", "0.1.0-rc.1\n"},
		{"prerelease segments", "0.0.1-daily-build.20261016\n", "0.0.1-daily-build.20261016\n"},
		{"CRLF", "1.0.0\r\n", "1.0.0\n"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "VERSION")
			if err := os.WriteFile(path, []byte(tc.content), 0600); err != nil {
				t.Fatal(err)
			}
			output, err := invoke(t, path)
			if err != nil {
				t.Fatalf("read version: %v", err)
			}
			if output != tc.want {
				t.Fatalf("version output = %q, want %q", output, tc.want)
			}
		})
	}
}

func TestInvalidVersionCannotBeStamped(t *testing.T) {
	for _, content := range []string{
		"", "  \n", "0.0.1\n0.2.0\n", "dev", "v0.0.1",
		`{"version":"9.9.9"}`, "0.0.1 -X other=value", "0.0.1\x00",
		"00.0.1", "0.01.0", "1.0.01", "0.1.0+sha.abc123",
		"0.1.0-rc.1+sha.abc123", "0.1.0-rc..1", "0.1.0-rc--1",
		"0.1.0-rc.", "0.1.0-rc-", "0.1.0-", "1.0",
	} {
		t.Run(content, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "VERSION")
			if err := os.WriteFile(path, []byte(content), 0600); err != nil {
				t.Fatal(err)
			}
			output, err := invoke(t, path)
			if err == nil {
				t.Fatal("invalid version accepted")
			}
			if output != "" {
				t.Fatalf("invalid version wrote build input %q", output)
			}
		})
	}
}

func TestMissingFileDoesNotInventVersion(t *testing.T) {
	output, err := invoke(t, filepath.Join(t.TempDir(), "VERSION"))
	if err == nil || !strings.Contains(err.Error(), "read AI Vision version") {
		t.Fatalf("missing file error = %v", err)
	}
	if output != "" {
		t.Fatalf("missing file wrote build input %q", output)
	}
}

func TestUsageIdentifiesVersionFile(t *testing.T) {
	output, err := invoke(t)
	if err == nil || !strings.Contains(err.Error(), "<VERSION>") {
		t.Fatalf("usage error = %v, want VERSION file guidance", err)
	}
	if output != "" {
		t.Fatalf("invalid invocation wrote build input %q", output)
	}
}
