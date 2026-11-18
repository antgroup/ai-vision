package runtime

import (
	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/components"

	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// Cast is the full pipeline, mirroring foundry: gauge (validate) + forge
// (build) + deploy (run). Individual phases can be invoked separately.
func (a *App) Cast(runGauge, runForge bool) error {
	if err := a.checkDeploymentDriver(); err != nil {
		return err
	}
	if runGauge {
		if err := a.Gauge(); err != nil {
			return err
		}
	}
	if runForge {
		if err := a.Forge(); err != nil {
			return err
		}
	}
	return a.Deploy()
}

// Deploy starts every enabled component in dependency order, waits for health,
// and runs bootstrap hooks. It is idempotent: existing containers/processes are
// replaced.
func (a *App) Deploy() error {
	if err := a.checkDeploymentDriver(); err != nil {
		return err
	}
	a.Log.Step("deploy: starting AI Vision stack (namespace %s)", a.BP.Metadata.Namespace)
	if start, declared := a.Vars["AIVISION_NATIVE_TRACE_HISTORY_START"]; declared && strings.TrimSpace(start) == "" {
		a.Log.Warn("native v1 SDK reads require AIVISION_NATIVE_TRACE_HISTORY_START; set it once to a fixed UTC timestamp covering all retained history and keep it across upgrades (other features remain available)")
	}
	if err := a.State.EnsureDirs(); err != nil {
		return err
	}
	if err := a.reserveDatastoreBackend(); err != nil {
		return err
	}
	if err := a.prepareDatastoreAssets(); err != nil {
		return fmt.Errorf("prepare database lifecycle: %w", err)
	}
	// Stop consumers before any database replacement. Restarting in dependency
	// order while old consumers remain active interrupts accepted work.
	if err := a.stopProductConsumers(); err != nil {
		return fmt.Errorf("stop existing application services: %w", err)
	}
	net := a.DockerNetwork()
	if err := a.Docker.EnsureNetwork(a.Ctx, net); err != nil {
		return fmt.Errorf("ensure docker network %s: %w", net, err)
	}

	meta := DeployMeta{
		DatastoreBackend: a.Vars["OB_BACKEND"],
		Namespace:        a.BP.Metadata.Namespace,
		SourceRoot:       a.SourceRoot(),
		DeployedAt:       time.Now().Format(time.RFC3339),
		Components:       map[string]string{},
		Containers:       map[string]string{},
		Discovered:       map[string]string{},
	}

	// Seed previously discovered values (e.g. ORG_ID) so a redeploy resolves
	// downstream env even before this run's discovery hooks re-confirm them.
	// Discovery below overwrites these with fresh values; bootstrap is idempotent
	// so the org id is stable across redeploys.
	if prior, err := a.State.LoadMeta(); err == nil {
		for k, v := range prior.Discovered {
			if _, exists := a.Vars[k]; !exists {
				a.Vars[k] = v
				meta.Discovered[k] = v
			}
		}
	}

	for _, c := range a.BP.OrderedComponents() {
		a.Log.Step("start %s (%s)", components.Canonical(c.Name), c.Kind)
		vars := a.ComponentVars(c)
		// preStart hooks run after dependencies are healthy but BEFORE this
		// component's own process/container starts. Use them for datastore
		// schema migrations the app expects to exist at boot (alembic / django
		// migrate). postStart runs after the component is healthy instead.
		if len(c.PreStart) > 0 {
			if err := a.RunSteps(c, c.PreStart, vars, "prestart"); err != nil {
				return err
			}
		}
		if err := a.startComponent(c, vars); err != nil {
			return err
		}
		if c.Health != nil {
			a.Log.Info("waiting for %s health…", components.Canonical(c.Name))
			if err := a.Log.WaitHealthy(a.Ctx, components.Canonical(c.Name), a.componentHealth(c, vars)); err != nil {
				a.dumpLogs(c)
				return err
			}
			a.Log.OK("%s healthy", components.Canonical(c.Name))
		}
		if len(c.PostStart) > 0 {
			if err := a.RunSteps(c, c.PostStart, vars, "bootstrap"); err != nil {
				return err
			}
		}
		// discover hooks run last: the component is healthy and bootstrapped, so
		// values they reveal (e.g. the system-produced org id) are exported into
		// a.Vars for downstream components' env resolution.
		if len(c.Discover) > 0 {
			if err := a.runDiscovery(c, vars, meta.Discovered); err != nil {
				a.dumpLogs(c)
				return err
			}
		}
		meta.Components[c.Name] = c.Kind
		if c.Kind == "container" {
			meta.Containers[c.Name] = a.ContainerName(c)
		}
	}

	meta.Blueprint = a.Vars["BLUEPRINT_PATH"]
	if err := a.State.SaveMeta(meta); err != nil {
		a.Log.Warn("could not persist deploy metadata: %v", err)
	}
	// Persist the override set so status/destroy/redeploy in this namespace
	// resolve the same containers, ports and datastore without re-passing --var.
	if err := a.State.SaveVars(a.Overrides); err != nil {
		a.Log.Warn("could not persist variable overrides: %v", err)
	}
	a.Log.OK("deploy complete — AI Vision stack is up")
	return nil
}

func expandHealth(h *Health, vars map[string]string) *Health {
	out := *h
	out.TCP = expand(h.TCP, vars)
	out.HTTP = expand(h.HTTP, vars)
	out.Exec = expand(h.Exec, vars)
	out.ExecArgv = expandSlice(h.ExecArgv, vars)
	return &out
}

func (a *App) componentHealth(c Component, vars map[string]string) *Health {
	h := expandHealth(c.Health, vars)
	if c.Kind == "container" && len(h.ExecArgv) > 0 {
		h.ExecArgv = append([]string{a.Docker.Bin, "exec", a.ContainerName(c)}, h.ExecArgv...)
	}
	return h
}

// runDiscovery executes a component's discover hooks after it is healthy and
// bootstrapped, exporting each captured value into a.Vars (and the deploy meta)
// so downstream components can reference it via ${VAR}. Each hook is retried
// until it yields a non-empty last line, because the value it reveals (e.g. the
// org id produced by the backend bootstrap) may not be ready on the first try.
func (a *App) runDiscovery(c Component, vars map[string]string, out map[string]string) error {
	for _, raw := range c.Discover {
		d := expandDiscovery(raw, vars)
		retries := d.Retries
		if retries <= 0 {
			retries = 30
		}
		interval := parseDur(d.Interval, 5*time.Second)
		env := a.ResolveEnv(c, vars)
		for k, v := range d.Env {
			env[k] = v
		}
		wd := d.Workdir
		if wd != "" && !filepath.IsAbs(wd) {
			wd = filepath.Join(a.SourceRoot(), wd)
		}

		var value string
		var lastErr error
		for attempt := 0; attempt <= retries; attempt++ {
			if attempt > 0 {
				select {
				case <-a.Ctx.Done():
					return a.Ctx.Err()
				case <-time.After(interval):
				}
			}
			a.Log.Debug("[discover] %s: %s (attempt %d/%d)", d.Var, d.Shell, attempt+1, retries+1)
			captured, err := a.Log.CaptureShell(a.Ctx, d.Shell, RunOpts{Workdir: wd, Env: env, Quiet: true})
			if err != nil {
				lastErr = err
				continue
			}
			lastErr = nil
			if v := strings.TrimSpace(lastLine(captured)); v != "" {
				value = v
				break
			}
		}

		if value == "" {
			if d.Optional {
				a.Log.Warn("[discover] %s: no value from %s (optional); leaving $%s unset", components.Canonical(c.Name), d.Shell, d.Var)
				continue
			}
			if lastErr != nil {
				return fmt.Errorf("[discover] %q for %s failed: %w", d.Var, components.Canonical(c.Name), lastErr)
			}
			return fmt.Errorf("[discover] %q for %s returned empty after %d attempts", d.Var, components.Canonical(c.Name), retries+1)
		}

		a.Vars[d.Var] = value
		vars[d.Var] = value // visible to later hooks on the same component
		if out != nil {
			out[d.Var] = value
		}
		a.Log.OK("[discover] %s=%s (from %s)", d.Var, value, components.Canonical(c.Name))
	}
	return nil
}

func (a *App) startComponent(c Component, vars map[string]string) error {
	if c.Kind == "container" {
		return a.startContainer(c, vars)
	}
	return a.startProcess(c, vars)
}

// startContainer removes any stale container then `docker run -d` a new one.
func (a *App) startContainer(c Component, vars map[string]string) error {
	if c.Name == "oceanbase" && vars["OB_BACKEND"] == "seekdb" && a.OBManaged() {
		// Keep the blueprint's backend selection and host-process behavior.
		// Only the actual CLI container uses the privately embedded lifecycle.
		c.Entrypoint = []string{"/bin/sh", "/opt/aivision/seekdb.sh"}
		c.Command = []string{"serve"}
		c.Mounts = append(append([]string{}, c.Mounts...), "type=bind,source=${SEEKDB_SCRIPT},target=/opt/aivision/seekdb.sh,readonly")
	}
	if a.managedOceanbase(c) {
		c.Entrypoint = []string{"/bin/sh", "/opt/aivision/oceanbase.sh"}
		c.Mounts = append(append([]string{}, c.Mounts...), "type=bind,source=${OCEANBASE_SCRIPT},target=/opt/aivision/oceanbase.sh,readonly")
	}
	name := a.ContainerName(c)
	for _, v := range c.Volumes {
		ev := expand(v, vars)
		// Only pre-create named volumes; bind sources are handled separately.
		if volName := volumeName(ev); volName != "" {
			if err := a.Docker.EnsureVolume(a.Ctx, volName); err != nil {
				return err
			}
		}
	}
	// Auto-create bind-mount sources that live under our managed dirs (state,
	// logs, artifacts). Repo-relative sources (binaries, configs) are NOT
	// created here so a missing artifact fails loudly instead of mounting an
	// empty directory over it.
	a.ensureManagedBindSources(c.Mounts, vars)
	if err := a.removeContainer(c); err != nil {
		return fmt.Errorf("remove stale container %s: %w", name, err)
	}
	args := []string{"run", "-d", "--name", name}
	if limit := expand(c.MemoryLimit, vars); limit != "" {
		args = append(args, "--memory", limit)
	}
	if c.Name == "oceanbase" && vars["OB_BACKEND"] == "seekdb" {
		args = append(args, "--ulimit", "nofile=65536:65536")
	}
	net := c.Network
	if net == "" {
		net = a.DockerNetwork()
	}
	if net = expand(net, vars); net != "" {
		args = append(args, "--network", net)
	}
	args = append(args, "--label", "aivision.forge.namespace="+a.BP.Metadata.Namespace)
	args = append(args, "--label", "aivision.forge.component="+c.Name)
	if a.BP.Metadata.Product == "aivision" {
		args = append(args, "--label", "io.aivision.product=aivision",
			"--label", "io.aivision.component="+components.Canonical(c.Name))
	}
	if a.managedOceanbase(c) {
		args = append(args, "--label", oceanbaseLifecycleLabel+"=ce-v1")
	} else if a.managedDatastore(c) {
		args = append(args, "--label", oceanbaseLifecycleLabel+"=seekdb-v1")
	}
	if a.managedDatastore(c) {
		args = append(args, "--stop-timeout", "120")
	} else if budget, signal, enabled := a.productContainerStop(c); enabled {
		args = append(args, "--stop-timeout", strconv.Itoa(int(budget/time.Second)), "--stop-signal", signal)
	}
	if len(c.Entrypoint) > 0 {
		args = append(args, "--entrypoint", expand(c.Entrypoint[0], vars))
	}
	for _, p := range c.Ports {
		args = append(args, "-p", expand(p, vars))
	}
	for _, m := range c.Mounts {
		args = append(args, "--mount", expand(m, vars))
	}
	for _, v := range c.Volumes {
		args = append(args, "-v", expand(v, vars))
	}
	for k, val := range a.ResolveEnv(c, vars) {
		args = append(args, "-e", k+"="+val)
	}
	args = append(args, expand(c.Image, vars))
	// command (and extra entrypoint args) go last
	if len(c.Entrypoint) > 1 {
		args = append(args, expandSlice(c.Entrypoint[1:], vars)...)
	}
	args = append(args, expandSlice(c.Command, vars)...)

	// Every application may carry credentials in -e arguments. Verbose output
	// describes the operation without printing environment values or commands.
	a.Log.Debug("docker run: container=%s image=%s network=%s", name, expand(c.Image, vars), net)
	if _, err := a.Docker.Run(a.Ctx, false, args...); err != nil {
		return fmt.Errorf("start container %s: %w", name, err)
	}
	a.Log.Info("%s container started", components.Canonical(c.Name))
	return nil
}

// startProcess supervises a host process via a run script + pidfile.
func (a *App) startProcess(c Component, vars map[string]string) error {
	name := c.Name
	// Stop any previous instance first.
	a.stopProcess(c)

	wd := expand(c.Workdir, vars)
	if wd == "" {
		wd = vars["COMPONENT_DIR"]
	}
	if wd != "" && !filepath.IsAbs(wd) {
		wd = filepath.Join(a.SourceRoot(), wd)
	}
	if wd != "" {
		if _, err := os.Stat(wd); err != nil {
			return fmt.Errorf("component %q workdir %s missing: %w", components.Canonical(name), wd, err)
		}
	}

	runScript := filepath.Join(a.State.StateDir, name+".run.sh")
	body := "#!/bin/bash\nset -euo pipefail\n"
	if wd != "" {
		body += "cd " + shq(wd) + "\n"
	}
	// The run block is written verbatim; it should end with `exec <cmd>` so the
	// captured pid is the real service process (not an intermediate shell).
	body += expand(c.Run, vars) + "\n"
	if err := os.WriteFile(runScript, []byte(body), 0o700); err != nil {
		return fmt.Errorf("write run script: %w", err)
	}

	logFile := expand(c.LogFile, vars)
	if logFile == "" {
		logFile = a.State.LogFile(name)
	} else if !filepath.IsAbs(logFile) {
		logFile = filepath.Join(a.State.LogDir, logFile)
	}
	_ = os.MkdirAll(filepath.Dir(logFile), 0o755)

	env := a.ResolveEnv(c, vars)
	startCmd := fmt.Sprintf("nohup bash %s >>%s 2>&1 </dev/null & echo $!", shq(runScript), shq(logFile))
	out, err := a.Log.CaptureShell(a.Ctx, startCmd, RunOpts{Env: env, Quiet: true})
	if err != nil {
		return fmt.Errorf("start process %s: %w", components.Canonical(name), err)
	}
	pid, convErr := strconv.Atoi(strings.TrimSpace(lastLine(out)))
	if convErr != nil || pid <= 0 {
		return fmt.Errorf("start process %s: could not capture pid (got %q)", components.Canonical(name), out)
	}
	if err := a.State.WritePid(name, pid); err != nil {
		return err
	}
	a.Log.Info("process %s started (pid %d, log %s)", components.Canonical(name), pid, logFile)
	return nil
}

// stopProcess terminates a supervised process if its pidfile is alive.
func (a *App) stopProcess(c Component) {
	pid := a.State.ReadPid(c.Name)
	if pid > 0 && PidAlive(pid) {
		_ = terminateTree(pid)
	}
	a.State.RemovePid(c.Name)
}

func (a *App) dumpLogs(c Component) {
	if c.Kind == "container" {
		a.Log.Warn("last logs for %s:", a.ContainerName(c))
		_, _ = a.Docker.Run(a.Ctx, false, "logs", "--tail", "30", a.ContainerName(c))
		return
	}
	logFile := a.State.LogFile(c.Name)
	if b, err := os.ReadFile(logFile); err == nil {
		a.Log.Warn("last logs for %s (%s):", components.Canonical(c.Name), logFile)
		lines := strings.Split(strings.TrimSpace(string(b)), "\n")
		if len(lines) > 30 {
			lines = lines[len(lines)-30:]
		}
		for _, l := range lines {
			a.Log.Raw("    %s", l)
		}
	}
}

func lastLine(s string) string {
	s = strings.TrimSpace(s)
	if i := strings.LastIndexByte(s, '\n'); i >= 0 {
		return s[i+1:]
	}
	return s
}

// ensureManagedBindSources pre-creates bind-mount source *directories* that live
// under aiv-managed paths (state / logs / artifacts / workdir). Docker would
// otherwise silently auto-create a missing bind source as an empty directory,
// masking a path typo — but we only do this for paths we own. Repo-relative
// sources (built binaries, committed configs) are intentionally left untouched so
// a missing artifact fails loudly at `docker run` instead of mounting an empty dir
// over the real thing. This is what lets a component point a writable mount (e.g.
// the signoz metastore) at ${STATE_DIR} and get per-namespace isolation for free.
func (a *App) ensureManagedBindSources(mounts []string, vars map[string]string) {
	managed := []string{a.Vars["STATE_DIR"], a.Vars["LOG_DIR"], a.Vars["ARTIFACT_DIR"], a.Vars["WORKDIR"]}
	for _, raw := range mounts {
		m := expand(raw, vars)
		typ, src := "", ""
		for _, kv := range strings.Split(m, ",") {
			kv = strings.TrimSpace(kv)
			if v, ok := strings.CutPrefix(kv, "type="); ok {
				typ = v
			} else if v, ok := strings.CutPrefix(kv, "source="); ok {
				src = v
			} else if v, ok := strings.CutPrefix(kv, "src="); ok {
				src = v
			}
		}
		if typ != "" && typ != "bind" {
			continue // volume/tmpfs sources are docker-managed
		}
		if src == "" || !filepath.IsAbs(src) {
			continue // relative bind sources are resolved by docker; not ours to create
		}
		if !underAny(src, managed) {
			continue // repo-relative / user path: must already exist (fail loudly)
		}
		if _, err := os.Stat(src); err == nil {
			continue // already present
		}
		if err := os.MkdirAll(src, 0o755); err != nil {
			a.Log.Warn("could not pre-create bind source %s: %v", src, err)
		} else {
			a.Log.Debug("pre-created managed bind source %s", src)
		}
	}
}

// underAny reports whether path is one of roots or nested inside any of them.
func underAny(path string, roots []string) bool {
	for _, r := range roots {
		if r == "" {
			continue
		}
		if path == r || strings.HasPrefix(path, r+string(os.PathSeparator)) {
			return true
		}
	}
	return false
}

// volumeName extracts the named-volume part of a docker `-v` spec
// ("name:/container/path" -> "name"). It returns "" for bind mounts, whose
// source is a host path (starting with /, . or ~) rather than a volume name.
func volumeName(spec string) string {
	i := strings.IndexByte(spec, ':')
	if i <= 0 {
		return ""
	}
	name := spec[:i]
	if strings.HasPrefix(name, "/") || strings.HasPrefix(name, ".") || strings.HasPrefix(name, "~") {
		return "" // bind-mount source, not a named volume
	}
	return name
}
