//go:build native_distribution

package runtime

import (
	"github.com/antgroup/ai-vision/aivision-cli/internal/blueprint"
	"strings"
	"testing"
)

func TestNativeDistributionRejectsSourceRuntime(t *testing.T) {
	for _, file := range []string{"aivision.yaml", "aivision-source.yaml"} {
		if _, err := datastoreApp(t, file, map[string]string{"NATIVE_PYTHON_RUNTIME": "false"}); err == nil || !strings.Contains(err.Error(), "native distribution") {
			t.Errorf("%s source mode accepted: %v", file, err)
		}
	}
}
func TestNativeDistributionUsesPackagedExecutables(t *testing.T) {
	a, err := datastoreApp(t, "aivision.yaml", map[string]string{"NATIVE_PYTHON_RUNTIME": "true"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(a.Vars["BE_MANAGE_CMD"], "/app/manage") || !strings.Contains(a.Vars["OB_PREFLIGHT_CMD"], "--entrypoint /app/database-init") {
		t.Fatal("compiled entry points missing")
	}
	for _, command := range []string{a.Vars["BE_MANAGE_CMD"], a.Vars["OB_PREFLIGHT_CMD"]} {
		if strings.Contains(command, "python") || strings.Contains(command, "AIV_OB_PREFLIGHT") {
			t.Fatalf("source fallback: %s", command)
		}
	}
}

func TestNativeDistributionRejectsBuildAndProcessBlueprints(t *testing.T) {
	for _, field := range []string{"process", "repo", "build"} {
		bp, err := blueprint.Load("../../../blueprints/aivision.yaml")
		if err != nil {
			t.Fatal(err)
		}
		bp.Config.Vars["NATIVE_PYTHON_RUNTIME"] = "true"
		switch field {
		case "process":
			bp.Components[0].Kind = "process"
		case "repo":
			bp.Components[0].Repo = &blueprint.Repo{URL: "https://example.invalid/private-source"}
		case "build":
			bp.Components[0].Build = []blueprint.Step{{Shell: "echo should-never-build"}}
		}
		if _, err := NewApp(bp, NewLogger(false), false, false); err == nil || !strings.Contains(err.Error(), "native distribution") {
			t.Errorf("source %s capability accepted: %v", field, err)
		}
	}
}
