package cli

import (
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/generate"
)

// cmdGenerate renders deployment materials (compose / kubernetes / systemd) from
// the resolved blueprint. It only writes files; it never applies them.
func cmdGenerate(args []string) error {
	fs := flag.NewFlagSet("generate", flag.ContinueOnError)
	g := registerGlobal(fs)
	target := fs.String("target", "", "material flavour: compose|kubernetes|systemd (required); kubernetes is deprecated generic conversion, use cast --target kubernetes to install; Helm support is planned but not available yet")
	out := fs.String("o", "", "output directory (default pours/<target>)")
	fs.StringVar(out, "output", "", "output directory (default pours/<target>)")
	prefix := fs.String("prefix", "", "systemd install prefix for env/run-script files (default /opt/aivision/<namespace>)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if strings.TrimSpace(*target) == "" {
		return fmt.Errorf("usage: aiv generate --target compose|kubernetes|systemd [-o DIR]")
	}
	t, err := generate.ParseTarget(*target)
	if err != nil {
		return err
	}
	app, err := buildApp(g)
	if err != nil {
		return err
	}
	outDir := *out
	if outDir == "" {
		outDir = filepath.Join("pours", string(t))
	}

	files, warns, err := generate.Render(app, t, generate.Options{Prefix: *prefix})
	if err != nil {
		return err
	}
	if t == generate.Compose && app.NativePythonRuntime() {
		// A native render omits legacy helpers. Reusing a source export must
		// not silently leave those programs in the delivered directory.
		for _, name := range []string{"database-init.py", "backend-bootstrap.py", "wait-http.py"} {
			path := filepath.Join(outDir, "bootstrap", name)
			if _, err := os.Lstat(path); err == nil {
				return fmt.Errorf("native Compose export requires a fresh output directory: source bootstrap file %s already exists", path)
			} else if !os.IsNotExist(err) {
				return fmt.Errorf("inspect native Compose output directory: %w", err)
			}
		}
	}
	for _, w := range warns {
		fmt.Fprintf(os.Stderr, "warning: %s\n", w)
	}
	for _, f := range files {
		// Clean the relative path (strip any leading "/" or "..") and re-anchor it
		// under outDir, then verify containment before writing.
		cleaned := strings.TrimPrefix(filepath.Clean("/"+f.Path), "/")
		p := filepath.Join(outDir, cleaned)
		rel, err := filepath.Rel(outDir, p)
		if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
			return fmt.Errorf("refusing to write outside output dir: %q", f.Path)
		}
		if dir := filepath.Dir(p); dir != "" && dir != "." {
			if err := os.MkdirAll(dir, 0o700); err != nil {
				return err
			}
		}
		mode := os.FileMode(0o600)
		if strings.HasSuffix(f.Path, ".sh") {
			mode = 0o700
		}
		// WriteFile preserves existing permissions; tighten previous exports too.
		if err := os.Chmod(p, mode); err != nil && !os.IsNotExist(err) {
			return err
		}
		if err := os.WriteFile(p, f.Content, mode); err != nil {
			return err
		}
		fmt.Printf("wrote %s\n", p)
	}
	fmt.Printf("\ngenerated %d file(s) for target %q in %s\n", len(files), string(t), outDir)
	return nil
}
