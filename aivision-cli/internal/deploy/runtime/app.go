// Package runtime is the local deployment engine: it drives the blueprint
// lifecycle (gauge, forge, cast, status, logs, redeploy, destroy) against
// docker containers and supervised host processes.
package runtime

import (
	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/components"
	"github.com/antgroup/ai-vision/aivision-cli/internal/distribution"

	"context"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"
)

// App is the shared execution context threaded through every command.
type App struct {
	Log     *Logger
	Docker  *Docker
	BP      *Blueprint
	State   *State
	Vars    map[string]string
	Ctx     context.Context
	Force   bool // force rebuild artifacts and re-run bootstrap
	Verbose bool

	// Overrides is the user-supplied variable set (flags, env, vars file,
	// previously persisted) layered over the blueprint defaults; Cast persists
	// it so later commands reuse the same configuration. VarSources records
	// where each override came from, for `aiv status`.
	Overrides  map[string]string
	VarSources map[string]string
}

// NewApp wires up state, docker and the global substitution variables.
func NewApp(bp *Blueprint, log *Logger, verbose, force bool) (*App, error) {
	if distribution.NativeOnly && (bp.Metadata.Product != "aivision" || (bp.Config.Vars["NATIVE_PYTHON_RUNTIME"] != "" && bp.Config.Vars["NATIVE_PYTHON_RUNTIME"] != "true")) {
		return nil, fmt.Errorf("native distribution requires the product image blueprint and NATIVE_PYTHON_RUNTIME=true; use a source build for source/legacy runtimes")
	}

	if distribution.NativeOnly {
		for _, component := range bp.Components {
			if component.Kind != "container" || component.Repo != nil || len(component.Build) != 0 {
				return nil, fmt.Errorf("native distribution requires pre-built container components; source builds and host processes are unsupported")
			}
		}
	}

	ns := bp.Metadata.Namespace
	state := NewState(bp.Config.Workdir, ns)
	rawVars := make(map[string]string, len(bp.Config.Vars))
	for k, v := range bp.Config.Vars {
		rawVars[k] = v
	}
	literalCredentials := map[string]bool{
		"OB_TENANT_PASSWORD": true, "OB_SYS_PASSWORD": true, "SECRET_KEY": true,
		"SIGNOZ_INGEST_INTERNAL_KEY": true, "AIVISION_INGEST_SECRET": true,
		"AGENTLENS_TOKEN": true, "SIGNOZ_USER_ROOT_PASSWORD": true,
	}
	literalCredential := func(key string) bool {
		return bp.Metadata.Product == "aivision" && literalCredentials[key]
	}
	if err := datastoreDefaults(rawVars); err != nil {
		return nil, err
	}

	vars := map[string]string{}
	// 1. process environment wins last, so copy config vars first.
	for k, v := range rawVars {
		vars[k] = v
	}
	// 2. computed structural variables and defaults.
	sourceRoot := cleanPath(bp.Config.SourceRoot, vars)
	workdir := cleanPath(bp.Config.Workdir, vars)
	vars["NAMESPACE"] = ns
	vars["SOURCE_ROOT"] = sourceRoot
	vars["WORKDIR"] = workdir
	vars["STATE_DIR"] = state.StateDir
	vars["LOG_DIR"] = state.LogDir
	vars["ARTIFACT_DIR"] = state.ArtifactDir
	vars["DOCKER_NETWORK"] = bp.Config.DockerNetwork
	vars["GOLANG_IMAGE"] = bp.Config.GolangImage
	vars["GOMODCACHE_VOL"] = bp.Config.GoModCacheVol
	var serviceImages map[string]components.Component
	if bp.Metadata.Product == "aivision" {
		serviceImages = components.ServiceImageComponents()
	}
	expandVar := func(key, value string) string {
		if component, ok := serviceImages[key]; ok && value == component.DefaultImageTemplate() {
			registry := strings.TrimSuffix(vars["IMAGE_REGISTRY"], "/")
			return registry + "/" + component.ImageName(registry) + ":" + vars["IMAGE_TAG"]
		}
		return expand(value, vars)
	}
	// 3. expand config.vars values against the computed set, iterating so that
	//    vars referencing other vars resolve regardless of map order.
	for i := 0; i < 6; i++ {
		changed := false
		for k, v := range rawVars {
			// Product credentials are literal input. Keep general template
			// semantics for source/custom blueprints and keys such as SSH_KEY_PATH.
			if literalCredential(k) {
				continue
			}
			nv := expandVar(k, v)
			if vars[k] != nv {
				vars[k] = nv
				changed = true
			}
		}
		if !changed {
			break
		}
	}
	// Resolve the effective network once for creation, probes, containers and
	// generated materials, including a templated structural default.
	vars["DOCKER_NETWORK"] = expand(vars["DOCKER_NETWORK"], vars)
	if _, declared := rawVars["OB_BACKEND"]; declared {
		vars["OB_USER_URL"] = url.User(vars["OB_USER"]).String()
		// URLs are expanded only after the final username can be encoded.
		for i := 0; i < 6; i++ {
			for k, v := range rawVars {
				if literalCredential(k) {
					continue
				}
				vars[k] = expandVar(k, v)
			}
		}
		if err := configureDatastore(bp, vars, state); err != nil {
			return nil, err
		}
	}

	// 4. component enabled expressions (e.g. `enabled: ${OB_MANAGED}`) are
	//    fixed now so every consumer of OrderedComponents sees the same set.
	if err := configurePythonRuntime(bp, vars); err != nil {
		return nil, err
	}
	if err := bp.ResolveEnabled(vars); err != nil {
		return nil, err
	}
	// 5. derived helpers for blueprints: the external-OceanBase probe a
	//    preStart step can invoke when OB_MANAGED=false.
	vars["OB_PREFLIGHT_CMD"] = obPreflightCommand(vars, 20)

	return &App{
		Log:     log,
		Docker:  NewDocker(log),
		BP:      bp,
		State:   state,
		Vars:    vars,
		Ctx:     context.Background(),
		Force:   force,
		Verbose: verbose,
	}, nil
}

// SourceRoot is the resolved directory holding all component repos.
func (a *App) SourceRoot() string { return a.Vars["SOURCE_ROOT"] }

// DockerNetwork is the resolved stack network after variable overrides.
func (a *App) DockerNetwork() string { return a.Vars["DOCKER_NETWORK"] }

// RepoDir returns the absolute checkout path for a component's repo, or "" if
// the component has no repo. This is the clone root (repo.path); several
// components may share it when they come from one monorepo.
func (a *App) RepoDir(c Component) string {
	if c.Repo == nil {
		return ""
	}
	p := c.Repo.Path
	if p == "" {
		p = c.Name
	}
	if filepath.IsAbs(p) {
		return expand(p, a.Vars)
	}
	return filepath.Join(a.SourceRoot(), expand(p, a.Vars))
}

// ComponentDir returns the directory a component actually builds/runs from: its
// repo checkout root joined with repo.subdir (for monorepo components), or just
// the checkout root when no subdir is declared. This is the value exposed as
// ${COMPONENT_DIR}.
func (a *App) ComponentDir(c Component) string {
	root := a.RepoDir(c)
	if root == "" || c.Repo == nil || c.Repo.Subdir == "" {
		return root
	}
	sub := expand(c.Repo.Subdir, a.Vars)
	if filepath.IsAbs(sub) {
		return sub
	}
	return filepath.Join(root, sub)
}

// ComponentVars returns the global vars plus per-component conveniences.
func (a *App) ComponentVars(c Component) map[string]string {
	v := map[string]string{}
	for k, val := range a.Vars {
		v[k] = val
	}
	v["COMPONENT"] = c.Name
	v["COMPONENT_KIND"] = c.Kind
	v["COMPONENT_REPO"] = a.RepoDir(c)
	v["COMPONENT_DIR"] = a.ComponentDir(c)
	if c.ContainerName != "" {
		v["CONTAINER_NAME"] = expand(c.ContainerName, v)
	}
	return v
}

// ContainerName resolves the effective docker container name for a component.
func (a *App) ContainerName(c Component) string {
	if c.ContainerName != "" {
		return expand(c.ContainerName, a.ComponentVars(c))
	}
	return a.BP.Metadata.Namespace + "-" + c.Name
}

// RunSteps executes a slice of steps, honouring once-markers.
func (a *App) RunSteps(comp Component, steps []Step, vars map[string]string, phase string) error {
	for _, raw := range steps {
		st := expandStep(raw, vars)
		label := st.Name
		if label == "" {
			label = firstLine(st.Shell)
		}
		if st.Once && !a.Force && a.State.MarkerDone(comp.Name, raw.Name+raw.Shell) {
			a.Log.Debug("[%s] %s: skipped (already done)", phase, label)
			continue
		}
		a.Log.Info("[%s] %s", phase, label)
		wd := st.Workdir
		if wd == "" {
			wd = vars["COMPONENT_DIR"]
		}
		if wd != "" && !filepath.IsAbs(wd) {
			wd = filepath.Join(a.SourceRoot(), wd)
		}
		env := a.ResolveEnv(comp, vars)
		for k, val := range st.Env {
			env[k] = val
		}
		if err := a.Log.RunShell(a.Ctx, st.Shell, RunOpts{Workdir: wd, Env: env, Stream: a.Verbose}); err != nil {
			return fmt.Errorf("[%s] step %q failed: %w", phase, label, err)
		}
		if st.Once {
			if err := a.State.MarkDone(comp.Name, raw.Name+raw.Shell); err != nil {
				a.Log.Warn("could not write marker for %q: %v", label, err)
			}
		}
	}
	return nil
}

// ensureSourceRoot creates the workspace root that holds component repos.
func (a *App) ensureSourceRoot() error {
	root := a.SourceRoot()
	if err := os.MkdirAll(root, 0o755); err != nil {
		return fmt.Errorf("create source root %s: %w", root, err)
	}
	return nil
}

// gitCloneOrFetch ensures a component repo exists at the requested branch.
// When the directory already exists and --force is not set, it is left as-is
// (so a pre-existing local workspace is reused, not clobbered).
func (a *App) gitCloneOrFetch(c Component) error {
	if c.Repo == nil {
		return nil
	}
	dir := a.RepoDir(c)
	url := expand(c.Repo.URL, a.Vars)
	branch := expand(c.Repo.Branch, a.Vars)
	if url == "" {
		if _, err := os.Stat(dir); err != nil {
			return fmt.Errorf("component %q has no repo.url and %s does not exist", components.Canonical(c.Name), dir)
		}
		a.Log.Info("reuse existing checkout %s", dir)
		return nil
	}
	if _, err := os.Stat(filepath.Join(dir, ".git")); err == nil {
		if !a.Force {
			a.Log.Info("reuse existing repo %s (use --force to fetch %s)", dir, branch)
			return nil
		}
		a.Log.Info("fetch + checkout %s @ %s", dir, branch)
		script := fmt.Sprintf(`set -e
expected_url=$(git ls-remote --get-url -- %s)
origin_url=$(git remote get-url origin)
if [ "$origin_url" != "$expected_url" ]; then
    echo 'Checkout origin differs from repo.url. Select a new checkout path or configure the intended URL.' >&2
    exit 1
fi
git fetch --prune origin && git checkout %s && git reset --hard origin/%s`,
			shq(url), shq(branch), shq(branch))
		return a.Log.RunShell(a.Ctx, script, RunOpts{Workdir: dir, Stream: a.Verbose})
	}
	a.Log.Info("clone %s @ %s -> %s", url, branch, dir)
	if err := os.MkdirAll(filepath.Dir(dir), 0o755); err != nil {
		return err
	}
	script := fmt.Sprintf("git clone --branch %s %s %s", shq(branch), shq(url), shq(dir))
	return a.Log.RunShell(a.Ctx, script, RunOpts{Stream: a.Verbose})
}

// ResolveEnv builds a component's effective environment: values loaded from
// EnvFile first (KEY=VALUE lines, comments/blank skipped), then the explicit
// Env map wins. All keys and values go through ${...} expansion. This lets a
// component inherit a shared secret from a repo-committed env file without
// hardcoding it in the blueprint.
func (a *App) ResolveEnv(c Component, vars map[string]string) map[string]string {
	out := map[string]string{}
	if c.EnvFile != "" {
		path := expand(c.EnvFile, vars)
		if !filepath.IsAbs(path) {
			path = filepath.Join(a.SourceRoot(), path)
		}
		if m, err := loadEnvFile(path); err == nil {
			for k, v := range m {
				out[expand(k, vars)] = expand(v, vars)
			}
		} else {
			a.Log.Warn("component %s: envFile %s not loaded: %v", components.Canonical(c.Name), path, err)
		}
	}
	for k, v := range c.Env {
		out[expand(k, vars)] = expand(v, vars)
	}
	return out
}

// loadEnvFile parses simple KEY=VALUE lines, ignoring comments and blanks.
// Values may be optionally single- or double-quoted.
func loadEnvFile(path string) (map[string]string, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	out := map[string]string{}
	for _, line := range strings.Split(string(b), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		line = strings.TrimPrefix(line, "export ")
		i := strings.IndexByte(line, '=')
		if i <= 0 {
			continue
		}
		k := strings.TrimSpace(line[:i])
		v := strings.TrimSpace(line[i+1:])
		if len(v) >= 2 && ((v[0] == '"' && v[len(v)-1] == '"') || (v[0] == '\'' && v[len(v)-1] == '\'')) {
			v = v[1 : len(v)-1]
		}
		out[k] = v
	}
	return out, nil
}

// shq single-quotes a shell argument safely.
func shq(s string) string {
	return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'"
}
