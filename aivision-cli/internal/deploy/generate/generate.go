// Package generate renders deployment materials (Docker Compose, Kubernetes
// manifests, systemd units) from a resolved AI Vision blueprint.
//
// It reuses the same variable resolution and component ordering as the runtime
// engine, so generated materials carry concrete values (ports, container names,
// DSNs) rather than unresolved ${...} tokens. Coverage per target:
//
//	compose     -> kind:container components as services
//	kubernetes  -> kind:container components as Deployment + Service (+PVC)
//	systemd     -> containers as `docker run` units AND processes as native
//	               units, grouped under a .target
//
// kind:process components are host-supervised (venv/node) and have no image, so
// they cannot be expressed as compose/k8s workloads; they are reported as
// skipped for those two targets.
package generate

import (
	"fmt"
	"sort"
	"strconv"
	"strings"

	"github.com/antgroup/ai-vision/aivision-cli/internal/blueprint"
	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/runtime"
)

// Target selects the material flavour to render.
type Target string

const (
	Compose    Target = "compose"
	Kubernetes Target = "kubernetes"
	Systemd    Target = "systemd"
)

// ParseTarget validates a --target value.
func ParseTarget(s string) (Target, error) {
	switch strings.ToLower(strings.TrimSpace(s)) {
	case "compose", "docker-compose", "docker":
		return Compose, nil
	case "kubernetes", "k8s":
		return Kubernetes, nil
	case "systemd", "systemctl":
		return Systemd, nil
	default:
		return "", fmt.Errorf("unknown target %q (want compose|kubernetes|systemd)", s)
	}
}

// File is one generated artifact. Path is relative to the output directory.
type File struct {
	Path    string
	Content []byte
}

// Options tunes target-specific rendering.
type Options struct {
	// Prefix is where systemd run-scripts are installed on the host
	// (default /opt/aivision/<namespace>). Units reference scripts here.
	Prefix string
	// Parameterized emits the reviewed repository Compose templates, keeping
	// deployment values in a private .env instead of baking them into YAML.
	Parameterized bool
	BootstrapPath string
}

// Resolved is a component with every ${...} token expanded to a concrete value.
type Resolved struct {
	Name          string
	Title         string
	Kind          string
	ContainerName string
	MemoryLimit   string
	Image         string
	Network       string
	Entrypoint    []string
	Command       []string
	Ports         []string // docker-run -p specs, e.g. "127.0.0.1:18091:8080"
	Volumes       []string // docker-run -v specs, e.g. "name:/path" or "/host:/path"
	Mounts        []string // docker-run --mount specs
	DependsOn     []string // component names
	Env           map[string]string
	Health        *blueprint.Health
	Workdir       string
	Dir           string // resolved ${COMPONENT_DIR} (repo checkout / monorepo subdir)
	Run           string
}

// Render resolves the blueprint and dispatches to the target renderer. It
// returns the generated files plus non-fatal warnings (e.g. skipped process
// components for compose/kubernetes).
func Render(app *runtime.App, t Target, opts Options) ([]File, []string, error) {
	if t == Compose && app.BP.Metadata.Product == "aivision" {
		return renderProductCompose(app, opts)
	}
	// These renderers omit preStart/bootstrap hooks. Managed seekdb must run
	// its isolated credential initialization before joining the app network.
	if app.Vars["OB_BACKEND"] == "seekdb" && app.OBManaged() {
		return nil, nil, fmt.Errorf("managed seekdb requires aiv cast; generated materials do not run its credential initialization")
	}
	comps := resolve(app)
	if opts.Prefix == "" {
		opts.Prefix = "/opt/aivision/" + app.BP.Metadata.Namespace
	}
	switch t {
	case Compose:
		return renderCompose(app, comps)
	case Kubernetes:
		return renderKubernetes(app, comps)
	case Systemd:
		return renderSystemd(app, comps, opts)
	default:
		return nil, nil, fmt.Errorf("unsupported target %q", t)
	}
}

// resolve expands every enabled component in dependency order.
func resolve(app *runtime.App) []Resolved {
	out := make([]Resolved, 0, len(app.BP.Components))
	for _, c := range app.BP.OrderedComponents() {
		vars := app.ComponentVars(c)
		net := c.Network
		if net == "" {
			net = app.DockerNetwork()
		}
		r := Resolved{
			Name:          c.Name,
			Title:         c.Title,
			Kind:          c.Kind,
			ContainerName: app.ContainerName(c),
			Image:         blueprint.Expand(c.Image, vars),
			MemoryLimit:   blueprint.Expand(c.MemoryLimit, vars),
			Network:       blueprint.Expand(net, vars),
			Entrypoint:    blueprint.ExpandSlice(c.Entrypoint, vars),
			Command:       blueprint.ExpandSlice(c.Command, vars),
			Ports:         blueprint.ExpandSlice(c.Ports, vars),
			Volumes:       blueprint.ExpandSlice(c.Volumes, vars),
			Mounts:        blueprint.ExpandSlice(c.Mounts, vars),
			DependsOn:     append([]string{}, c.DependsOn...),
			Env:           app.ResolveEnv(c, vars),
			Workdir:       blueprint.Expand(c.Workdir, vars),
			Dir:           vars["COMPONENT_DIR"],
			Run:           blueprint.Expand(c.Run, vars),
		}
		if c.Health != nil {
			h := *c.Health
			h.TCP = blueprint.Expand(h.TCP, vars)
			h.HTTP = blueprint.Expand(h.HTTP, vars)
			h.Exec = blueprint.Expand(h.Exec, vars)
			h.ExecArgv = blueprint.ExpandSlice(h.ExecArgv, vars)
			r.Health = &h
		}
		out = append(out, r)
	}
	return out
}

// containers returns only kind:container components.
func containers(comps []Resolved) []Resolved {
	out := make([]Resolved, 0, len(comps))
	for _, c := range comps {
		if c.Kind == "container" {
			out = append(out, c)
		}
	}
	return out
}

// skippedProcesses lists process component names (for compose/k8s warnings).
func skippedProcesses(comps []Resolved) []string {
	var names []string
	for _, c := range comps {
		if c.Kind != "container" {
			names = append(names, c.Name)
		}
	}
	sort.Strings(names)
	return names
}

// --- docker-run spec parsers (shared by all three renderers) -----------------

// portSpec is a parsed docker `-p` mapping.
type portSpec struct {
	HostIP        string
	HostPort      string
	ContainerPort string
	Proto         string // tcp|udp (default tcp)
}

// parsePort handles "ip:host:container[/proto]", "host:container[/proto]" and
// "container[/proto]".
func parsePort(p string) portSpec {
	proto := "tcp"
	if i := strings.IndexByte(p, '/'); i >= 0 {
		proto = p[i+1:]
		p = p[:i]
	}
	parts := strings.Split(p, ":")
	switch len(parts) {
	case 1:
		return portSpec{ContainerPort: parts[0], Proto: proto}
	case 2:
		return portSpec{HostPort: parts[0], ContainerPort: parts[1], Proto: proto}
	default: // 3+ : ip:host:container
		return portSpec{HostIP: parts[0], HostPort: parts[1], ContainerPort: parts[2], Proto: proto}
	}
}

// mountSpec is a parsed docker `--mount` (long syntax).
type mountSpec struct {
	Type     string // bind|volume|tmpfs
	Source   string
	Target   string
	ReadOnly bool
}

func parseMount(m string) mountSpec {
	var ms mountSpec
	for _, kv := range strings.Split(m, ",") {
		kv = strings.TrimSpace(kv)
		key, val, _ := strings.Cut(kv, "=")
		switch key {
		case "type":
			ms.Type = val
		case "source", "src":
			ms.Source = val
		case "target", "dst", "destination":
			ms.Target = val
		case "readonly":
			ms.ReadOnly = true
		}
	}
	if ms.Type == "" {
		ms.Type = "bind"
	}
	return ms
}

// volumeName extracts the named-volume part of a `-v` spec, or "" for binds.
func volumeName(spec string) string {
	i := strings.IndexByte(spec, ':')
	if i <= 0 {
		return ""
	}
	name := spec[:i]
	if strings.HasPrefix(name, "/") || strings.HasPrefix(name, ".") || strings.HasPrefix(name, "~") {
		return ""
	}
	return name
}

// sortedEnvKeys returns deterministic env key order for stable output.
func sortedEnvKeys(env map[string]string) []string {
	keys := make([]string, 0, len(env))
	for k := range env {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

func atoi(s string) int {
	n, _ := strconv.Atoi(s)
	return n
}
