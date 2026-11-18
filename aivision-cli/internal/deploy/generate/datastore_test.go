package generate

import (
	"strings"
	"testing"

	"gopkg.in/yaml.v3"

	"github.com/antgroup/ai-vision/aivision-cli/internal/blueprint"
	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/runtime"
)

// Only the product Compose renderer includes isolated credential initialization.
// Generic Kubernetes/systemd generation still omits lifecycle hooks.
func TestSeekdbMaterialsCannotSkipCredentialInitialization(t *testing.T) {
	bp, err := blueprint.Load("../../../blueprints/aivision.yaml")
	if err != nil {
		t.Fatal(err)
	}
	bp.Config.Workdir = t.TempDir()
	bp.Config.Vars["OB_BACKEND"] = "seekdb"
	app, err := runtime.NewApp(bp, runtime.NewLogger(false), false, false)
	if err != nil {
		t.Fatal(err)
	}
	files, _, err := Render(app, Compose, Options{})
	if err != nil || len(files) == 0 || !strings.Contains(string(files[0].Content), "database-initialize:") {
		t.Fatalf("product Compose must include isolated seekdb initialization: %v", err)
	}
	for _, target := range []Target{Kubernetes, Systemd} {
		files, _, err := Render(app, target, Options{})
		if err == nil || !strings.Contains(err.Error(), "aiv cast") || len(files) != 0 {
			t.Errorf("%s must refuse seekdb materials without initialization: files=%d err=%v", target, len(files), err)
		}
	}
}

func TestProductComposeMemoryLimits(t *testing.T) {
	_, files := productRender(t, productApp(t, "seekdb", true, nil), Options{Parameterized: true})
	for _, f := range files {
		if f.Path != "compose.yaml" {
			continue
		}
		var doc struct {
			Services map[string]struct {
				Memory string            `yaml:"mem_limit"`
				Env    map[string]string `yaml:"environment"`
			} `yaml:"services"`
		}
		if err := yaml.Unmarshal(f.Content, &doc); err != nil {
			t.Fatal(err)
		}
		for _, name := range []string{"database", "database-initialize", "backend", "data-backend"} {
			if doc.Services[name].Memory == "" {
				t.Errorf("%s has no container memory limit", name)
			}
		}
		if value := doc.Services["database"].Env["AIV_SEEKDB_MEMORY_BUDGET"]; !strings.Contains(value, "SEEKDB_MEMORY_LIMIT") {
			t.Errorf("missing legacy budget fallback: %s", value)
		}
	}
}
