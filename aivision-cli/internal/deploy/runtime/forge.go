package runtime

import (
	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/components"

	"fmt"
	"os"
)

// Forge prepares every artifact the stack needs to run: it clones or updates
// each component's repo at the declared branch, then runs the build steps.
// Build steps are skipped when the component's artifactCheck already passes
// (so a warm workspace is not needlessly rebuilt) unless --force is given.
func (a *App) Forge() error {
	if err := a.checkDeploymentDriver(); err != nil {
		return err
	}
	a.Log.Step("forge: preparing sources and artifacts")
	if err := a.ensureSourceRoot(); err != nil {
		return err
	}
	comps := a.BP.OrderedComponents()
	for _, c := range comps {
		if c.Repo == nil && len(c.Build) == 0 {
			continue
		}
		a.Log.Step("component %s", components.Canonical(c.Name))
		if err := a.gitCloneOrFetch(c); err != nil {
			return err
		}
		if err := a.buildComponent(c); err != nil {
			return err
		}
	}
	a.Log.OK("forge complete")
	return nil
}

func (a *App) buildComponent(c Component) error {
	if len(c.Build) == 0 {
		return nil
	}
	vars := a.ComponentVars(c)
	// Reuse warm artifacts unless forced to rebuild.
	if c.ArtifactCheck != "" && !a.Force {
		check := expand(c.ArtifactCheck, vars)
		if _, err := a.Log.CaptureShell(a.Ctx, check, RunOpts{Quiet: true}); err == nil {
			a.Log.Info("artifacts present, skipping build (use --force to rebuild)")
			return nil
		}
	}
	if err := a.RunSteps(c, c.Build, vars, "build"); err != nil {
		return err
	}
	// Post-build verification when declared.
	if c.ArtifactCheck != "" {
		check := expand(c.ArtifactCheck, vars)
		if _, err := a.Log.CaptureShell(a.Ctx, check, RunOpts{Quiet: true}); err != nil {
			return fmt.Errorf("component %q: artifact check failed after build: %w", components.Canonical(c.Name), err)
		}
	}
	a.Log.OK("%s artifacts ready", components.Canonical(c.Name))
	return nil
}

// GaugeOnly runs the forge source-preparation phase without building, used by
// gauge to confirm repos are reachable/present.
func (a *App) SourcesPresent() []string {
	missing := []string{}
	for _, c := range a.BP.OrderedComponents() {
		if c.Repo == nil {
			continue
		}
		dir := a.RepoDir(c)
		if _, err := os.Stat(dir); err != nil {
			missing = append(missing, fmt.Sprintf("%s (%s)", components.Canonical(c.Name), dir))
		}
	}
	return missing
}
