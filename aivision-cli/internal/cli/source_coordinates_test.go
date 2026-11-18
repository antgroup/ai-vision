package cli

import (
	"testing"

	"github.com/antgroup/ai-vision/aivision-cli/internal/blueprint"
)

func TestPublicSourceBlueprintCoordinatesAndExplicitForkOverrides(t *testing.T) {
	for _, file := range []string{"../../blueprints/aivision-source.yaml", "../../../deploy/blueprints/aivision-source.yaml"} {
		for _, custom := range []bool{false, true} {
			t.Run(file+map[bool]string{false: "/defaults", true: "/overrides"}[custom], func(t *testing.T) {
				g := &globalOpts{file: file, namespace: "source-coordinates", workdir: t.TempDir()}
				product, signoz, branch := "https://github.com/antgroup/ai-vision.git", "https://github.com/ob-labs/signoz.git", "main"
				if custom {
					product, signoz, branch = "ssh://internal.example/product.git", "https://github.com/example/telemetry.git", "feature/source-test"
					g.vars = varFlags{"AIVISION_REPO=" + product, "SIGNOZ_REPO=" + signoz, "AIVISION_BRANCH=" + branch, "SIGNOZ_BRANCH=" + branch}
				}
				app, err := buildApp(g)
				if err != nil {
					t.Fatal(err)
				}
				count := 0
				for _, component := range app.BP.Components {
					if component.Repo == nil {
						continue
					}
					count++
					want := product
					if component.Name == "signoz-ob" {
						want = signoz
					}
					vars := app.ComponentVars(component)
					if got := blueprint.Expand(component.Repo.URL, vars); got != want {
						t.Errorf("%s clone source = %q, want %q", component.Name, got, want)
					}
					if component.Name != "signoz-ob" && blueprint.Expand(component.Repo.Path, vars) != "ai-vision" {
						t.Errorf("%s must use the public product checkout directory", component.Name)
					}
					if got := blueprint.Expand(component.Repo.Branch, vars); got != branch {
						t.Errorf("%s source branch = %q, want %q", component.Name, got, branch)
					}
				}
				if count != 5 {
					t.Fatalf("resolved %d source components, want the two-repository five-component stack", count)
				}
			})
		}
	}
}
