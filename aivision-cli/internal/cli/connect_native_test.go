package cli

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/antgroup/ai-vision/aivision-cli/internal/runner"
)

func nativeConnectArchive(t *testing.T, headers []*tar.Header, bodies [][]byte) []byte {
	t.Helper()
	var output bytes.Buffer
	gzipWriter := gzip.NewWriter(&output)
	writer := tar.NewWriter(gzipWriter)
	for i, header := range headers {
		if err := writer.WriteHeader(header); err != nil {
			t.Fatal(err)
		}
		if header.Typeflag == tar.TypeReg && len(bodies[i]) > 0 {
			if _, err := writer.Write(bodies[i]); err != nil {
				t.Fatal(err)
			}
		}
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	if err := gzipWriter.Close(); err != nil {
		t.Fatal(err)
	}
	return output.Bytes()
}

func TestNativeConnectBundleExtractAndCleanup(t *testing.T) {
	content := []byte("\x7fELFnative-fixture")
	archive := nativeConnectArchive(t, []*tar.Header{
		{Name: "connect/", Mode: 0755, Typeflag: tar.TypeDir},
		{Name: "connect/connect", Mode: 0755, Typeflag: tar.TypeReg, Size: int64(len(content))},
		{Name: "connect/lib/libpython.so", Mode: 0644, Typeflag: tar.TypeReg, Size: 3},
	}, [][]byte{nil, content, []byte("lib")})
	path, cleanup, err := unpackNativeConnect(archive)
	if err != nil {
		t.Fatal(err)
	}
	defer cleanup()
	if !nativeConnectExecutable(path) {
		t.Fatal("extracted installer was not recognized as native")
	}
	if got, err := os.ReadFile(filepath.Join(filepath.Dir(path), "lib", "libpython.so")); err != nil || string(got) != "lib" {
		t.Fatalf("runtime dependency was not retained: %q, %v", got, err)
	}
	info, err := os.Stat(path)
	if err != nil || info.Mode().Perm()&0111 == 0 {
		t.Fatal("native installer is not executable")
	}
	cleanup()
	if _, err := os.Stat(filepath.Dir(path)); !os.IsNotExist(err) {
		t.Fatal("cleanup retained temporary native bundle")
	}
}

func TestNativeConnectBundleRejectsUnsafeEntries(t *testing.T) {
	for _, name := range []string{"../outside", "/outside", "connect/../../outside", "connect/../outside", "other/connect", "connect\\outside"} {
		t.Run(name, func(t *testing.T) {
			archive := nativeConnectArchive(t, []*tar.Header{{Name: name, Typeflag: tar.TypeReg, Size: 1, Mode: 0755}}, [][]byte{[]byte("x")})
			_, cleanup, err := unpackNativeConnect(archive)
			if cleanup != nil {
				cleanup()
			}
			if err == nil {
				t.Fatal("accepted unsafe archive path")
			}
		})
	}
	for _, kind := range []byte{tar.TypeSymlink, tar.TypeLink} {
		archive := nativeConnectArchive(t, []*tar.Header{{Name: "connect/connect", Typeflag: kind, Linkname: "../outside"}}, [][]byte{nil})
		if _, cleanup, err := unpackNativeConnect(archive); err == nil {
			cleanup()
			t.Fatal("accepted archive link")
		}
	}
}

func TestNativeConnectBundleRejectsDuplicateOrSourceEntry(t *testing.T) {
	for _, bodies := range [][][]byte{{[]byte("\x7fELF"), []byte("\x7fELF")}, {[]byte("#!/usr/bin/env python3\nprint('source')\n")}} {
		var headers []*tar.Header
		for _, body := range bodies {
			headers = append(headers, &tar.Header{Name: "connect/connect", Typeflag: tar.TypeReg, Mode: 0755, Size: int64(len(body))})
		}
		archive := nativeConnectArchive(t, headers, bodies)
		if _, cleanup, err := unpackNativeConnect(archive); err == nil {
			cleanup()
			t.Fatal("accepted duplicate or source installer")
		}
	}
}

func TestFetchNativeConnectChecksPlatformAndChecksum(t *testing.T) {
	content := []byte("\x7fELFfixture")
	archive := nativeConnectArchive(t, []*tar.Header{{Name: "connect/connect", Typeflag: tar.TypeReg, Mode: 0755, Size: int64(len(content))}}, [][]byte{content})
	wrong := false
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("platform") == "" || r.URL.Query().Get("arch") == "" {
			t.Error("client did not request its native platform")
		}
		w.Header().Set("X-AIV-Installer-Format", "native-tar-v1")
		sum := sha256.Sum256(archive)
		if wrong {
			sum = sha256.Sum256([]byte("another bundle"))
		}
		w.Header().Set("X-AIV-Installer-SHA256", fmt.Sprintf("%x", sum))
		_, _ = w.Write(archive)
	}))
	defer server.Close()
	path, cleanup, err := fetchConnectScript(context.Background(), server.URL, &runner.Logger{})
	if err != nil {
		t.Fatal(err)
	}
	cleanup()
	if filepath.Base(path) != "connect" {
		t.Fatal("native archive was saved as a Python script")
	}
	wrong = true
	if _, cleanup, err := fetchConnectScript(context.Background(), server.URL, &runner.Logger{}); err == nil {
		cleanup()
		t.Fatal("accepted a corrupted installer bundle")
	}
}

func TestNativeConnectCommandDoesNotResolvePython(t *testing.T) {
	filename := filepath.Join(t.TempDir(), "connect")
	if err := os.WriteFile(filename, []byte("\x7fELFfixture"), 0700); err != nil {
		t.Fatal(err)
	}
	cmd, err := connectCommand(context.Background(), filename, "/nonexistent/python", "--dry-run")
	if err != nil {
		t.Fatal(err)
	}
	if cmd.Path != filename || len(cmd.Args) != 2 || cmd.Args[1] != "--dry-run" {
		t.Fatalf("native installer requires an interpreter: %v", cmd.Args)
	}
}

func TestConnectPreflightErrorDistinguishesBundledAndSourceRuntime(t *testing.T) {
	for _, native := range []bool{true, false} {
		t.Run(fmt.Sprint(native), func(t *testing.T) {
			filename := filepath.Join(t.TempDir(), "connect")
			content := []byte("#!/usr/bin/env python3\n")
			if native {
				content = []byte("\x7fELFfixture")
			}
			if err := os.WriteFile(filename, content, 0700); err != nil {
				t.Fatal(err)
			}
			message := connectPreflightError(filename, fmt.Errorf("exit status 1")).Error()
			if native {
				if !strings.Contains(message, "bundled runtime") || !strings.Contains(message, "GLIBC") || !strings.Contains(message, "compatible native installer") {
					t.Fatalf("native runtime failure lacks actionable guidance: %s", message)
				}
				if strings.Contains(message, "host/Python") {
					t.Fatal("native runtime failure incorrectly asks to repair host Python")
				}
			} else if !strings.Contains(message, "host/Python") {
				t.Fatalf("source runtime guidance was lost: %s", message)
			}
		})
	}
}
