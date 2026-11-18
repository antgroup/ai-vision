package runtime

import (
	"fmt"
	"strings"
)

// Gauge validates prerequisites: required tools, a
// responsive docker daemon, the container images the stack needs, and whether
// component sources are already present. The external-OceanBase probe prepares
// the stack network and any missing application databases before service startup.
func (a *App) Gauge() error {
	if err := a.checkDeploymentDriver(); err != nil {
		return err
	}
	a.Log.Step("gauge: validating prerequisites")
	failures := []string{}

	// docker is always required for a container-bearing stack.
	dockerPath, ok := LookPath(a.Docker.Bin)
	if !ok {
		failures = append(failures, "docker not found in PATH")
		a.Log.Err("docker: NOT FOUND")
	} else {
		a.Log.OK("docker: %s", dockerPath)
		// daemon responsive?
		if out, err := a.Docker.Run(a.Ctx, true, "version", "--format", "{{.Server.Version}}"); err != nil {
			failures = append(failures, "docker daemon not reachable: "+firstLine(out+" "+err.Error()))
			a.Log.Err("docker daemon: UNREACHABLE (%v)", err)
		} else {
			a.Log.OK("docker daemon: server %s", out)
		}
	}

	// declared tools
	for _, t := range a.BP.Config.Tools {
		check := t.Check
		if check == "" {
			check = "command -v " + shq(t.Name)
		}
		check = expand(check, a.Vars)
		out, err := a.Log.CaptureShell(a.Ctx, check, RunOpts{Quiet: true})
		if err != nil {
			if t.Required {
				failures = append(failures, fmt.Sprintf("required tool %q missing", t.Name))
				a.Log.Err("%s: MISSING%s", t.Name, noteSuffix(t.Note))
			} else {
				a.Log.Warn("%s: missing (optional)%s", t.Name, noteSuffix(t.Note))
			}
			continue
		}
		a.Log.OK("%s: %s", t.Name, strings.TrimSpace(firstLine(out)))
	}

	// images required by container components
	images := map[string]bool{}
	for _, c := range a.BP.OrderedComponents() {
		if c.Kind == "container" && c.Image != "" {
			images[expand(c.Image, a.ComponentVars(c))] = true
		}
	}
	for img := range images {
		if a.Docker.ImageExists(a.Ctx, img) {
			a.Log.OK("image %s: present", img)
		} else {
			a.Log.Warn("image %s: not local (will be pulled/built on cast)", img)
		}
	}

	// sources
	if missing := a.SourcesPresent(); len(missing) > 0 {
		a.Log.Warn("sources not present yet (will be cloned on forge): %s", strings.Join(missing, ", "))
	}

	// external OceanBase: fail here, before any container is started, if the
	// stack network cannot reach it or the tenant cannot host the databases.
	if !a.OBManaged() && len(failures) == 0 {
		a.Log.Info("external OceanBase %s:%s tenant=%s — probing from the docker network…",
			a.Vars["OB_HOST"], a.Vars["OB_HOST_PORT"], a.Vars["OB_TENANT"])
		if err := a.OBPreflight(); err != nil {
			failures = append(failures, "external OceanBase: "+err.Error())
			a.Log.Err("external OceanBase: FAILED\n%s", err.Error())
		}
	}

	if len(failures) > 0 {
		return fmt.Errorf("gauge failed:\n  - %s", strings.Join(failures, "\n  - "))
	}
	a.Log.OK("gauge passed")
	return nil
}

func noteSuffix(note string) string {
	if note == "" {
		return ""
	}
	return " — " + note
}
