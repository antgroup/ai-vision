// Command renderproduct refreshes the reviewed Compose entry points.
package main

import (
	"bytes"
	"flag"
	"fmt"
	"os"
	"path/filepath"

	"github.com/antgroup/ai-vision/aivision-cli/internal/blueprint"
	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/generate"
	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/runtime"
)

func main() {
	check := flag.Bool("check", false, "fail if generated product Compose files are stale")
	root := flag.String("root", "..", "monorepo root")
	flag.Parse()
	err := run(*root, *check)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run(root string, check bool) error {
	// The lifecycle controller is the Go CLI. Remove the former generated host
	// Python entrypoint so stale installations are not taught two controllers.
	obsolete := filepath.Join(root, "deploy", "docker", "compose.py")
	if _, err := os.Lstat(obsolete); err == nil {
		if check {
			return fmt.Errorf("%s is obsolete; run go run -mod=vendor ./internal/deploy/renderproduct", obsolete)
		}
		if err := os.Remove(obsolete); err != nil {
			return err
		}
	} else if !os.IsNotExist(err) {
		return err
	}
	for _, target := range []struct {
		name, backend, managed string
	}{
		{"compose.yaml", "oceanbase", "true"},
		{"compose.seekdb.yaml", "seekdb", "true"},
		{"compose.external-oceanbase.yaml", "oceanbase", "false"},
		{"compose.external-seekdb.yaml", "seekdb", "false"},
	} {
		bp, err := blueprint.Load(filepath.Join(root, "deploy", "blueprints", "aivision.yaml"))
		if err != nil {
			return err
		}
		bp.Config.Workdir = filepath.Join(os.TempDir(), "aivision-compose-render")
		bp.Config.Vars["OB_BACKEND"], bp.Config.Vars["OB_MANAGED"] = target.backend, target.managed
		app, err := runtime.NewApp(bp, runtime.NewLogger(false), false, false)
		if err != nil {
			return err
		}
		files, _, err := generate.Render(app, generate.Compose, generate.Options{Parameterized: true, BootstrapPath: "../bootstrap"})
		if err != nil {
			return err
		}
		for _, file := range files {
			name := file.Path
			if name == "compose.yaml" {
				name = target.name
			} else {
				continue // Bootstrap files already live in their authoritative location.
			}
			path := filepath.Join(root, "deploy", "docker", name)
			if check {
				actual, err := os.ReadFile(path)
				if err != nil || !bytes.Equal(actual, file.Content) {
					return fmt.Errorf("%s is stale; run go run -mod=vendor ./internal/deploy/renderproduct", path)
				}
			} else {
				if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
					return err
				}
				if err := os.WriteFile(path, file.Content, 0o644); err != nil {
					return err
				}
			}
		}
	}
	return nil
}
