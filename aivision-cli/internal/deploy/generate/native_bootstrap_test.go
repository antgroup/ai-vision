package generate

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestBootstrapNativeAndSourceEntrypointsPreserveArguments(t *testing.T) {
	for _, native := range []bool{true, false} {
		t.Run(map[bool]string{true: "native", false: "source"}[native], func(t *testing.T) {
			dir := t.TempDir()
			for name, text := range map[string]string{
				"python": "#!/bin/sh\nprintf 'source:%s:%s' \"$1\" \"$2\"\n",
				"helper": "#!/bin/sh\nprintf 'native:%s' \"$1\"\n",
			} {
				if name == "helper" && !native {
					continue
				}
				if err := os.WriteFile(filepath.Join(dir, name), []byte(text), 0700); err != nil {
					t.Fatal(err)
				}
			}
			entry := bootstrapEntrypoint("helper")
			entry[2] = strings.ReplaceAll(entry[2], "/app/helper", filepath.Join(dir, "helper"))
			cmd := exec.Command(entry[0], append(entry[1:], "an argument with spaces")...)
			cmd.Env = append(os.Environ(), "PATH="+dir)
			out, err := cmd.CombinedOutput()
			if err != nil {
				t.Fatalf("bootstrap failed: %v: %s", err, out)
			}
			want := "native:an argument with spaces"
			if !native {
				want = "source:/opt/aivision/bootstrap/helper.py:an argument with spaces"
			}
			if string(out) != want {
				t.Fatalf("bootstrap selected wrong runtime or split arguments: %q", out)
			}
		})
	}
}
