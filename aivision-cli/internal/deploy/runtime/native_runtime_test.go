package runtime

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestNativeRuntimeSelectsSourceFreeLifecycleCommands(t *testing.T) {
	for _, native := range []bool{true, false} {
		t.Run(fmt.Sprint(native), func(t *testing.T) {
			a, err := datastoreApp(t, "aivision.yaml", map[string]string{"NATIVE_PYTHON_RUNTIME": fmt.Sprint(native)})
			if err != nil {
				t.Fatal(err)
			}
			preflight := a.Vars["OB_PREFLIGHT_CMD"]
			manage := a.Vars["BE_MANAGE_CMD"]
			if native {
				for _, command := range []string{preflight, manage} {
					if strings.Contains(command, "python") || strings.Contains(command, ".py") || strings.Contains(command, "AIV_OB_PREFLIGHT") {
						t.Fatalf("native lifecycle contains a source program: %s", command)
					}
				}
				if !strings.Contains(preflight, "--entrypoint /app/database-init") || !strings.Contains(manage, "/app/manage") {
					t.Fatal("native lifecycle did not select fixed image executables")
				}
			} else if !strings.Contains(preflight, obPreflightPython) || !strings.Contains(manage, "python manage.py") {
				t.Fatal("legacy source image fallback was lost")
			}
			be := *a.BP.Component("be")
			for _, step := range be.PostStart {
				command := expandStep(step, a.ComponentVars(be)).Shell
				if !strings.Contains(command, manage) {
					t.Fatalf("backend lifecycle bypasses selected command: %s", command)
				}
			}
			if command := expand(be.Discover[0].Shell, a.ComponentVars(be)); !strings.HasPrefix(command, manage+" ") {
				t.Fatalf("organization discovery bypasses selected command: %s", command)
			}
		})
	}
}

func TestNativeAndLegacyPreflightExecuteSelectedInitializer(t *testing.T) {
	for _, native := range []bool{true, false} {
		t.Run(fmt.Sprint(native), func(t *testing.T) {
			dir := t.TempDir()
			argsPath, inputPath := filepath.Join(dir, "args"), filepath.Join(dir, "input")
			fixture := `#!/bin/sh
case "$1" in
  image) exit 0;;
  run) printf '%s\n' "$@" > "$AIV_TEST_ARGS"; cat > "$AIV_TEST_INPUT";;
  *) exit 9;;
esac
`
			if err := os.WriteFile(filepath.Join(dir, "docker"), []byte(fixture), 0700); err != nil {
				t.Fatal(err)
			}
			a, err := datastoreApp(t, "aivision.yaml", map[string]string{"NATIVE_PYTHON_RUNTIME": fmt.Sprint(native)})
			if err != nil {
				t.Fatal(err)
			}
			cmd := exec.Command("/bin/sh", "-ec", a.Vars["OB_PREFLIGHT_CMD"])
			cmd.Env = append(os.Environ(), "PATH="+dir+":"+os.Getenv("PATH"), "AIV_TEST_ARGS="+argsPath, "AIV_TEST_INPUT="+inputPath)
			if out, err := cmd.CombinedOutput(); err != nil {
				t.Fatalf("initializer fixture failed: %v: %s", err, out)
			}
			args, err := os.ReadFile(argsPath)
			if err != nil {
				t.Fatal(err)
			}
			input, err := os.ReadFile(inputPath)
			if err != nil {
				t.Fatal(err)
			}
			if native {
				if !strings.Contains(string(args), "--entrypoint\n/app/database-init\n") || len(input) != 0 {
					t.Fatal("native initializer received a source program or did not execute the packaged binary")
				}
			} else if string(input) != obPreflightPython || !strings.Contains(string(args), "--entrypoint\n/bin/sh\n") {
				t.Fatal("legacy initializer no longer receives its source program")
			}
		})
	}
}

func TestNativeRuntimeRejectsInvalidMode(t *testing.T) {
	if _, err := datastoreApp(t, "aivision.yaml", map[string]string{"NATIVE_PYTHON_RUNTIME": "sometimes"}); err == nil || !strings.Contains(err.Error(), "NATIVE_PYTHON_RUNTIME") {
		t.Fatalf("expected explicit runtime validation, got %v", err)
	}
}
