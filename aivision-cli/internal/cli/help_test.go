package cli

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/antgroup/ai-vision/aivision-cli/internal/config"
)

// Exercise Run in a subprocess: flag.ErrHelp must exit successfully, before
// reading credentials or running any command's normal operations.
func TestHelpWithoutConfiguration(t *testing.T) {
	if os.Getenv("AIV_TEST_HELP_PROCESS") == "1" {
		for i, arg := range os.Args {
			if arg == "--" {
				os.Args = append([]string{"aiv"}, os.Args[i+1:]...)
				Run("")
				return
			}
		}
		t.Fatal("missing subprocess arguments")
	}
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	directory := filepath.Join(t.TempDir(), "config")
	t.Setenv(config.EnvConfigDir, directory)
	t.Setenv(config.EnvProfile, "help-only")
	t.Setenv(config.EnvEndpoint, "http://127.0.0.1:1")
	commands := []string{
		"help", "--help", "-h",
		"auth help", "auth register --help", "auth login --help",
		"auth login --space-key sk-aiv-example --space example --help",
		"auth status --help", "auth print-env --help", "auth logout --help",
		"space -h", "space list --help", "space use example --help", "space create --help",
		"key -h", "key create --help", "key import --help", "key list --help", "key revoke 123 --help",
		"connect --help", "connect codex --help",
		"agent -h", "agent status --help", "agent status codex --help",
		"plugin --help", "plugin install openshell --help", "plugin status openshell --help",
		"plugin logs openshell --help", "plugin remove openshell --help",
		"query help", "query trace list --help", "query trace get example --help", "query trace metrics --help",
		"query metadata help", "query metadata --help",
		"cast --help", "destroy --help", "logs --help", "logs be --help",
		"generate --help", "generate --target kubernetes --help",
		"cast --target kubernetes --help", "gauge --target kubernetes --help",
		"status --target kubernetes --help", "logs be --target kubernetes --help",
		"destroy --target kubernetes --help", "redeploy --target kubernetes --help",
	}
	for _, phase := range []string{"missing", "invalid"} {
		if phase == "invalid" {
			if err := os.MkdirAll(directory, 0700); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(config.Path(), []byte("invalid config\n"), 0600); err != nil {
				t.Fatal(err)
			}
		}
		for _, command := range commands {
			t.Run(phase+"/"+command, func(t *testing.T) {
				args := append([]string{"-test.run=^TestHelpWithoutConfiguration$", "--"}, strings.Fields(command)...)
				child := exec.Command(executable, args...)
				child.Env = append(os.Environ(), "AIV_TEST_HELP_PROCESS=1")
				output, err := child.CombinedOutput()
				if err != nil {
					t.Fatalf("help failed: %v\n%s", err, output)
				}
				if command == "help" || command == "--help" || command == "-h" ||
					strings.HasPrefix(command, "cast ") || strings.HasPrefix(command, "generate ") {
					if !strings.Contains(string(output), "Helm support is planned but not available yet") {
						t.Fatalf("deployment help must distinguish planned Helm support from current availability:\n%s", output)
					}
				}
			})
		}
		if phase == "missing" {
			if _, err := os.Stat(directory); !os.IsNotExist(err) {
				t.Fatalf("help must not create configuration: %v", err)
			}
		} else if data, err := os.ReadFile(config.Path()); err != nil || string(data) != "invalid config\n" {
			t.Fatalf("help modified configuration: %v", err)
		}
	}
}
