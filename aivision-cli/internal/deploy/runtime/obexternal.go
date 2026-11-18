package runtime

import (
	"fmt"
	"sort"
	"strings"

	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/components"
)

// OBManaged reports whether the blueprint runs its own oceanbase container.
// OB_MANAGED is unset in blueprints that predate external-datastore support,
// which keeps their behaviour (always managed).
func (a *App) OBManaged() bool {
	switch strings.ToLower(strings.TrimSpace(a.Vars["OB_MANAGED"])) {
	case "false", "0", "no", "off":
		return false
	}
	return true
}

// printOverrides lists the effective variable overrides and where each came
// from. Values whose key looks secret are masked.
func (a *App) printOverrides() {
	if len(a.Overrides) == 0 {
		return
	}
	keys := make([]string, 0, len(a.Overrides))
	for k := range a.Overrides {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	a.Log.Info("variable overrides (%d):", len(keys))
	for _, k := range keys {
		v := a.Overrides[k]
		if isSecretKey(k) && v != "" {
			v = "****"
		}
		a.Log.Raw("  %-24s %-32s (%s)", components.DisplayVar(k), v, a.VarSources[k])
	}
}

func isSecretKey(k string) bool {
	u := strings.ToUpper(k)
	for _, marker := range []string{"PASSWORD", "SECRET", "TOKEN", "_KEY"} {
		if strings.Contains(u, marker) {
			return true
		}
	}
	return false
}

// Blueprints name the image (and, optionally, the python launcher inside it)
// used to run the probe, so no extra client image is needed: the image
// blueprint reuses agentlens (ships PyMySQL), the source blueprint the uv base
// image with `uv run --with pymysql`.
const (
	obPreflightImageVar  = "OB_PREFLIGHT_IMAGE"
	obPreflightPythonVar = "OB_PREFLIGHT_PYTHON" // default: python
)

// obPreflightCommand returns a shell snippet that runs the probe inside a
// throwaway container attached to the stack network. It is exported to
// blueprints as ${OB_PREFLIGHT_CMD} (so a preStart step can call it) and run
// directly by gauge. The password travels via the container environment,
// never on the command line. Empty when the vars do not describe an OceanBase
// target (legacy blueprints).
func obPreflightCommand(vars map[string]string, attempts int) string {
	image := vars[obPreflightImageVar]
	if image == "" || vars["OB_HOST"] == "" {
		return ""
	}
	env := []string{"OB_HOST", "OB_HOST_PORT", "OB_TENANT", "OB_USER", "OB_TENANT_PASSWORD"}
	var b strings.Builder
	b.WriteString("set -e\n")
	b.WriteString(fmt.Sprintf("if [ %s = always ] || ! docker image inspect %s >/dev/null 2>&1; then docker pull %s; fi\n", shq(vars["IMAGE_PULL"]), shq(image), shq(image)))
	for _, k := range env {
		b.WriteString(fmt.Sprintf("%s=%s ", k, shq(vars[k])))
	}
	b.WriteString(fmt.Sprintf("OB_DATABASES=%s OB_ATTEMPTS=%d ", shq("aivision,signoz,agentlens"), attempts))
	b.WriteString("docker run --rm -i")
	for _, k := range env {
		b.WriteString(" -e " + k)
	}
	b.WriteString(" -e OB_DATABASES -e OB_ATTEMPTS")
	if net := vars["DOCKER_NETWORK"]; net != "" {
		b.WriteString(" --network " + shq(net))
	}
	if vars["NATIVE_PYTHON_RUNTIME"] == "true" {
		b.WriteString(" --entrypoint /app/database-init " + shq(image) + "\n")
		return b.String()
	}
	launcher := strings.Fields(vars[obPreflightPythonVar])
	if len(launcher) == 0 {
		launcher = []string{"python"}
	}
	b.WriteString(" --entrypoint /bin/sh " + shq(image) + " -ec " + shq(`if [ -x /app/database-init ]; then exec /app/database-init; fi; exec "$@"`) + " aiv-database-init")
	for _, arg := range launcher {
		b.WriteString(" " + shq(arg))
	}
	b.WriteString(" - <<'AIV_OB_PREFLIGHT'\n")
	b.WriteString(obPreflightPython)
	b.WriteString("AIV_OB_PREFLIGHT\n")
	return b.String()
}

// OBPreflight runs the external-OceanBase probe (gauge / cast entry point).
func (a *App) OBPreflight() error {
	cmd := obPreflightCommand(a.Vars, 5)
	if cmd == "" {
		return fmt.Errorf("blueprint does not define OB_HOST/%s; cannot probe an external OceanBase", obPreflightImageVar)
	}
	// The probe joins the stack network so it sees exactly what the services
	// will see; gauge runs before Deploy creates it.
	if net := a.DockerNetwork(); net != "" {
		if err := a.Docker.EnsureNetwork(a.Ctx, net); err != nil {
			return fmt.Errorf("ensure docker network %s: %w", net, err)
		}
	}
	out, err := a.Log.CaptureShell(a.Ctx, cmd, RunOpts{Quiet: true})
	if err != nil {
		return fmt.Errorf("%s", strings.TrimSpace(out))
	}
	lines := strings.Split(strings.TrimSpace(out), "\n")
	a.Log.OK("%s", strings.TrimSpace(lines[len(lines)-1]))
	return nil
}
