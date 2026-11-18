package generate

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/antgroup/ai-vision/aivision-cli/internal/blueprint"
	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/components"
	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/runtime"
	"gopkg.in/yaml.v3"
)

func queryControlApp(t *testing.T, file string, values map[string]string) *runtime.App {
	t.Helper()
	// Exercise authoritative inputs before compatibility copies are refreshed.
	bp, err := blueprint.Load("../../../../deploy/blueprints/" + file)
	if err != nil {
		t.Fatal(err)
	}
	bp.Config.Workdir = t.TempDir()
	bp.Config.SourceRoot = t.TempDir()
	for key, value := range components.NormalizeVars(values) {
		bp.Config.Vars[key] = value
	}
	app, err := runtime.NewApp(bp, runtime.NewLogger(false), false, false)
	if err != nil {
		t.Fatal(err)
	}
	if file == "aivision-source.yaml" {
		env := filepath.Join(bp.Config.SourceRoot, "ai-vision-ob", "ai-vision", "aivision-py-be", ".env.development")
		if err := os.MkdirAll(filepath.Dir(env), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(env, nil, 0600); err != nil {
			t.Fatal(err)
		}
	}
	return app
}

func TestProductComposeForwardsQueryRecoveryControls(t *testing.T) {
	for _, engine := range []string{"oceanbase", "seekdb"} {
		for _, managed := range []bool{true, false} {
			for _, scenario := range []struct {
				name, query, maintenance string
				values                   map[string]string
			}{
				{"default", "auto", "true", nil},
				{"legacy-emergency", "raw", "false", map[string]string{
					"AIVISION_QUERY_MODE": "raw", "SIGNOZ_AIVISION_PROJECTION_MAINTENANCE_ENABLED": "false",
				}},
				{"role-wins", "raw", "false", map[string]string{
					"AIVISION_QUERY_MODE": "auto", "BACKEND_QUERY_MODE": "raw",
					"SIGNOZ_AIVISION_PROJECTION_MAINTENANCE_ENABLED": "true", "DATA_BACKEND_PROJECTION_MAINTENANCE_ENABLED": "false",
				}},
			} {
				t.Run(fmt.Sprintf("%s/managed=%v/%s", engine, managed, scenario.name), func(t *testing.T) {
					values := map[string]string{"OB_BACKEND": engine, "OB_MANAGED": fmt.Sprint(managed)}
					if !managed {
						values["OB_HOST"] = "external.example.test"
					}
					for key, value := range scenario.values {
						values[key] = value
					}
					doc, _ := productRender(t, queryControlApp(t, "aivision.yaml", values), Options{})
					backend, dataBackend := doc.Services["backend"], doc.Services["data-backend"]
					if got := backend.Environment["AIVISION_QUERY_MODE"]; got != scenario.query {
						t.Errorf("backend query mode = %q, want %q", got, scenario.query)
					}
					if got := dataBackend.Environment["SIGNOZ_AIVISION_PROJECTION_MAINTENANCE_ENABLED"]; got != scenario.maintenance {
						t.Errorf("data-backend maintenance = %q, want %q", got, scenario.maintenance)
					}
					if _, found := backend.Environment["SIGNOZ_INGEST_INTERNAL_KEY"]; found {
						t.Error("normal backend must not receive the operator recovery key")
					}
					if len(backend.Entrypoint) != 0 || len(backend.Command) != 0 {
						t.Error("backend must inherit its supervised image entrypoint")
					}
					bootstrap := doc.Services["backend-bootstrap"]
					if len(bootstrap.Entrypoint) != 4 || bootstrap.Entrypoint[0] != "/bin/sh" || len(bootstrap.Command) != 0 {
						t.Error("one-shot bootstrap must bypass the API/projector image entrypoint")
					}
					if _, found := backend.Environment["BACKEND_QUERY_MODE"]; found {
						t.Error("role config alias leaked into application environment")
					}
				})
			}
		}
	}
}

func TestProductComposeParameterizedQueryControlsRetainBothSpellings(t *testing.T) {
	for _, engine := range []string{"oceanbase", "seekdb"} {
		for _, managed := range []bool{true, false} {
			t.Run(fmt.Sprintf("%s/managed=%v", engine, managed), func(t *testing.T) {
				values := map[string]string{"OB_BACKEND": engine, "OB_MANAGED": fmt.Sprint(managed)}
				if !managed {
					values["OB_HOST"] = "external.example.test"
				}
				doc, _ := productRender(t, queryControlApp(t, "aivision.yaml", values), Options{Parameterized: true})
				if got := doc.Services["backend"].Environment["AIVISION_QUERY_MODE"]; got != "${BACKEND_QUERY_MODE:-${AIVISION_QUERY_MODE:-auto}}" {
					t.Errorf("reader interpolation lost role/legacy precedence: %q", got)
				}
				if got := doc.Services["data-backend"].Environment["SIGNOZ_AIVISION_PROJECTION_MAINTENANCE_ENABLED"]; got != "${DATA_BACKEND_PROJECTION_MAINTENANCE_ENABLED:-${SIGNOZ_AIVISION_PROJECTION_MAINTENANCE_ENABLED:-true}}" {
					t.Errorf("maintenance interpolation lost role/legacy precedence: %q", got)
				}
			})
		}
	}
}

func TestSourceComposeForwardsQueryControlsAndUsesBackendEntrypoint(t *testing.T) {
	for _, scenario := range []struct {
		name, query, maintenance string
		values                   map[string]string
	}{
		{"default", "auto", "true", nil},
		{"emergency", "raw", "false", map[string]string{
			"BACKEND_QUERY_MODE": "raw", "DATA_BACKEND_PROJECTION_MAINTENANCE_ENABLED": "false",
		}},
	} {
		t.Run(scenario.name, func(t *testing.T) {
			files, _, err := Render(queryControlApp(t, "aivision-source.yaml", scenario.values), Compose, Options{})
			if err != nil {
				t.Fatal(err)
			}
			var doc struct {
				Services map[string]struct {
					Environment map[string]string
					Command     []string
				}
			}
			if err := yaml.Unmarshal(files[0].Content, &doc); err != nil {
				t.Fatal(err)
			}
			backend := doc.Services["be"]
			if got := backend.Environment["AIVISION_QUERY_MODE"]; got != scenario.query {
				t.Errorf("source reader = %q, want %q", got, scenario.query)
			}
			if got := doc.Services["signoz-ob"].Environment["SIGNOZ_AIVISION_PROJECTION_MAINTENANCE_ENABLED"]; got != scenario.maintenance {
				t.Errorf("source maintenance = %q, want %q", got, scenario.maintenance)
			}
			if got := backend.Environment["BE_PORT"]; got != "8001" {
				t.Errorf("source backend entrypoint port = %q", got)
			}
			command := strings.Join(backend.Command, " ")
			if !strings.Contains(command, "cd /app") || !strings.Contains(command, "/app/.venv/bin:") || !strings.Contains(command, "exec /bin/bash ./docker-entrypoint.sh") {
				t.Errorf("source backend bypasses shared entrypoint or venv: %q", command)
			}
			if _, found := backend.Environment["SIGNOZ_INGEST_INTERNAL_KEY"]; found {
				t.Error("source backend must not receive the operator recovery key")
			}
		})
	}
}
