package runtime

import (
	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/components"

	"fmt"
	"os"
	"strings"
)

// DestroyOpts controls teardown scope.
type DestroyOpts struct {
	Purge      bool // remove named volumes (datastores) + the whole state dir
	PurgeRepos bool // also delete cloned source repos
}

// Destroy tears the stack down in reverse dependency order. By default it stops
// processes and removes containers but preserves data volumes, cloned repos and
// state so a later cast comes back up fast. --purge additionally removes named
// volumes (wiping the OceanBase datastore) and the state directory.
func (a *App) Destroy(opts DestroyOpts) error {
	if err := a.checkDeploymentDriver(); err != nil {
		return err
	}
	a.Log.Step("destroy: tearing down namespace %s (purge=%v)", a.BP.Metadata.Namespace, opts.Purge)

	comps := a.BP.OrderedComponents()
	// reverse order: dependents first
	rev := make([]Component, 0, len(comps))
	for i := len(comps) - 1; i >= 0; i-- {
		rev = append(rev, comps[i])
	}

	for _, c := range rev {
		vars := a.ComponentVars(c)
		if len(c.Teardown) > 0 {
			a.Log.Info("teardown hooks for %s", components.Canonical(c.Name))
			_ = a.RunSteps(c, c.Teardown, vars, "teardown")
		}
		switch c.Kind {
		case "process":
			pid := a.State.ReadPid(c.Name)
			if pid > 0 && PidAlive(pid) {
				a.Log.Info("stopping process %s (pid %d)", components.Canonical(c.Name), pid)
				if err := terminateTree(pid); err != nil {
					a.Log.Warn("terminate %s: %v", components.Canonical(c.Name), err)
				}
			} else {
				a.Log.Info("process %s not running", components.Canonical(c.Name))
			}
			a.State.RemovePid(c.Name)
		case "container":
			name := a.ContainerName(c)
			exists := a.Docker.ContainerExists(a.Ctx, name)
			if a.BP.Metadata.Product == "aivision" {
				var err error
				exists, err = a.productContainerExists(a.Ctx, name)
				if err != nil {
					return err
				}
			}
			if exists {
				a.Log.Info("removing %s container", components.Canonical(c.Name))
				if err := a.removeContainerWithChecks(c, true); err != nil {
					if a.managedDatastore(c) || a.BP.Metadata.Product == "aivision" {
						return fmt.Errorf("stop %s: %w", components.Canonical(c.Name), err)
					}
					a.Log.Warn("remove container %s: %v", name, err)
				}
			} else {
				a.Log.Info("%s container absent", components.Canonical(c.Name))
			}
			if opts.Purge {
				for _, v := range c.Volumes {
					vol := expand(v, vars)
					if idx := strings.IndexByte(vol, ':'); idx > 0 {
						vol = vol[:idx]
					}
					if vol == "" {
						continue
					}
					a.Log.Info("removing volume %s", vol)
					_, _ = a.Docker.Run(a.Ctx, false, "volume", "rm", "-f", vol)
				}
			}
		}
	}

	if opts.Purge {
		a.Log.Info("purging state dir %s", a.State.Root)
		if err := a.State.RemoveAll(); err != nil {
			a.Log.Warn("remove state: %v", err)
		}
	}
	if opts.PurgeRepos {
		for _, c := range rev {
			if c.Repo == nil {
				continue
			}
			dir := a.RepoDir(c)
			if dir == "" || dir == a.SourceRoot() {
				continue
			}
			if _, err := os.Stat(dir); err == nil {
				a.Log.Info("removing repo %s", dir)
				if err := os.RemoveAll(dir); err != nil {
					a.Log.Warn("remove repo %s: %v", dir, err)
				}
			}
		}
	}
	a.Log.OK("destroy complete")
	return nil
}

// Redeploy is destroy (non-purge) followed by a full cast.
func (a *App) Redeploy(runGauge, runForge bool) error {
	if err := a.Destroy(DestroyOpts{}); err != nil {
		return fmt.Errorf("redeploy: %w", err)
	}
	return a.Cast(runGauge, runForge)
}
