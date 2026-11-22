//go:build !windows || (amd64 && windows_codex_connect && !native_distribution)

package cli

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path"
	"path/filepath"
	"runtime"
	"strings"
)

const maxNativeConnectDownload = 64 << 20
const maxNativeConnectUnpacked = 256 << 20

// Native bundles contain the installer and its upstream runtime dependencies.
// Extract only regular files and directories beneath connect/; never follow links.
func unpackNativeConnect(body []byte) (string, func(), error) {
	dir, err := os.MkdirTemp("", "aivision-connect-*")
	if err != nil {
		return "", nil, err
	}
	cleanup := func() { _ = os.RemoveAll(dir) }
	fail := func(err error) (string, func(), error) {
		cleanup()
		return "", nil, fmt.Errorf("connect: invalid native installer bundle: %w", err)
	}
	root, err := os.OpenRoot(dir)
	if err != nil {
		return fail(err)
	}
	defer root.Close()
	zr, err := gzip.NewReader(bytes.NewReader(body))
	if err != nil {
		return fail(err)
	}
	defer zr.Close()
	// Include a bounded allowance for tar headers/padding in addition to file data.
	limited := &io.LimitedReader{R: zr, N: maxNativeConnectUnpacked + (4 << 20) + 1}
	tr := tar.NewReader(limited)
	seen := map[string]bool{}
	var total int64
	for count := 0; ; count++ {
		h, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return fail(err)
		}
		if count >= 4096 {
			return fail(fmt.Errorf("too many files"))
		}
		name := strings.TrimSuffix(h.Name, "/")
		if name == "" || strings.Contains(name, "\\") || path.Clean(name) != name ||
			(name != "connect" && !strings.HasPrefix(name, "connect/")) || seen[name] {
			return fail(fmt.Errorf("unsafe or duplicate path %q", h.Name))
		}
		seen[name] = true
		local, err := filepath.Localize(name)
		if err != nil || !filepath.IsLocal(local) {
			return fail(fmt.Errorf("unsafe path %q", h.Name))
		}
		target := filepath.Join(dir, local)
		switch h.Typeflag {
		case tar.TypeDir:
			if err := os.MkdirAll(target, 0700); err != nil {
				return fail(err)
			}
		case tar.TypeReg:
			if h.Size < 0 || h.Size > maxNativeConnectUnpacked-total {
				return fail(fmt.Errorf("unpacked size exceeds limit"))
			}
			total += h.Size
			if err := os.MkdirAll(filepath.Dir(target), 0700); err != nil {
				return fail(err)
			}
			mode := os.FileMode(0600)
			if h.Mode&0111 != 0 {
				mode = 0700
			}
			f, err := root.OpenFile(local, os.O_CREATE|os.O_EXCL|os.O_WRONLY, mode)
			if err != nil {
				return fail(err)
			}
			_, copyErr := io.CopyN(f, tr, h.Size)
			closeErr := f.Close()
			if copyErr != nil {
				return fail(copyErr)
			}
			if closeErr != nil {
				return fail(closeErr)
			}
		default:
			return fail(fmt.Errorf("unsupported entry type for %q", h.Name))
		}
	}
	// Read through gzip's trailer too: truncated or corrupt compressed streams fail.
	if _, err := io.Copy(io.Discard, limited); err != nil {
		return fail(err)
	}
	if limited.N == 0 {
		return fail(fmt.Errorf("decompressed archive exceeds size limit"))
	}
	executable := filepath.Join(dir, "connect", "connect")
	info, err := os.Stat(executable)
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0100 == 0 || !nativeConnectExecutable(executable) {
		return fail(fmt.Errorf("connect/connect must be a native executable"))
	}
	return executable, cleanup, nil
}

func nativeConnectExecutable(filename string) bool {
	if runtime.GOOS == "windows" {
		return false
	}
	f, err := os.Open(filename)
	if err != nil {
		return false
	}
	defer f.Close()
	var magic [4]byte
	if _, err := io.ReadFull(f, magic[:]); err != nil {
		return false
	}
	switch magic {
	case [4]byte{0x7f, 'E', 'L', 'F'}, // Linux ELF
		[4]byte{0xcf, 0xfa, 0xed, 0xfe}, [4]byte{0xfe, 0xed, 0xfa, 0xcf}, // Mach-O 64
		[4]byte{0xca, 0xfe, 0xba, 0xbe}, [4]byte{0xca, 0xfe, 0xba, 0xbf}: // Mach-O universal
		return true
	}
	return false
}

func connectCommand(ctx context.Context, installer, python string, args ...string) (*exec.Cmd, error) {
	if nativeConnectExecutable(installer) {
		return exec.CommandContext(ctx, installer, args...), nil
	}
	interpreter, err := resolvePython(python)
	if err != nil {
		return nil, err
	}
	return sourcePythonCommand(ctx, interpreter, append([]string{installer}, args...)...), nil
}

func sourcePythonCommand(ctx context.Context, interpreter string, args ...string) *exec.Cmd {
	cmd := exec.CommandContext(ctx, interpreter, args...)
	if runtime.GOOS == "windows" {
		// Python install-manager launch aliases can install a runtime by default.
		// Onboarding only uses Python that is already present on the host.
		cmd.Env = append(cmd.Environ(), "PYTHON_MANAGER_AUTOMATIC_INSTALL=false")
	}
	return cmd
}

func connectPreflightError(installer string, err error) error {
	if nativeConnectExecutable(installer) {
		return fmt.Errorf("connect preflight failed (%w); check the host Agent and the native installer's OS, architecture and bundled runtime requirements (including GLIBC on Linux); ask the deployment maintainer for a compatible native installer. Changing --python cannot repair the bundled runtime", err)
	}
	return fmt.Errorf("connect preflight failed (%w); check host/Python/platform artifacts and that the deployment includes the current installer", err)
}
