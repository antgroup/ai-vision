// Package blueprint defines the AI Vision deployment blueprint schema: one
// declarative file describing every component's source, build, run, health and
// bootstrap. It is the aivision-cli equivalent of foundry's casting.yaml.
package blueprint

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"gopkg.in/yaml.v3"
)

// namespacePattern keeps metadata.namespace to one safe path component while
// accepting every selector Docker and Compose already allow, including
// uppercase Docker names and trailing '-' or '_'. The first character must be
// alphanumeric, which rejects ".", ".." and hidden names; separators,
// absolute paths and backslashes are rejected as well.
var namespacePattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]*$`)

// ValidateNamespace rejects namespace values that could escape the workdir or
// are unusable as a Docker, Compose or Kubernetes name.
func ValidateNamespace(namespace string) error {
	if namespace == "" {
		return fmt.Errorf("metadata.namespace must not be empty")
	}
	if !namespacePattern.MatchString(namespace) {
		return fmt.Errorf("metadata.namespace %q must start with a letter or digit and contain only letters, digits, '.', '_' or '-'", namespace)
	}
	return nil
}

// Blueprint is the single deployment configuration for an AI Vision stack.
// It is the aiv equivalent of foundry's casting.yaml: one file that
// declares every component, its source repo/branch, how to build it, how to
// run it, how to health-check it and how to bootstrap its datastore.
type Blueprint struct {
	APIVersion string      `yaml:"apiVersion"`
	Metadata   Metadata    `yaml:"metadata"`
	Config     Config      `yaml:"config"`
	Components []Component `yaml:"components"`
}

// Metadata identifies the deployment. Namespace prefixes container names,
// state directories and log files so multiple stacks can coexist.
type Metadata struct {
	Name      string `yaml:"name"`
	Namespace string `yaml:"namespace"`
	Product   string `yaml:"product,omitempty"` // opt in to the supported product lifecycle
}

// Config holds deployment-wide settings and shared variables. Every value in
// Vars is available to ${...} substitution across the whole blueprint.
type Config struct {
	Workdir       string            `yaml:"workdir"`
	SourceRoot    string            `yaml:"sourceRoot"`
	DockerNetwork string            `yaml:"dockerNetwork"`
	GolangImage   string            `yaml:"golangImage"`
	GoModCacheVol string            `yaml:"goModCacheVolume"`
	Vars          map[string]string `yaml:"vars"`
	Tools         []ToolReq         `yaml:"tools"`
	BootstrapOnly bool              `yaml:"-"`
}

// ToolReq declares a prerequisite binary that gauge verifies. Check overrides
// the default `command -v <name>` probe (exit 0 means present).
type ToolReq struct {
	Name     string `yaml:"name"`
	Required bool   `yaml:"required"`
	Check    string `yaml:"check"`
	Note     string `yaml:"note"`
}

// Repo is a git source location for a component. Path is the directory name
// under Config.SourceRoot where the repo is checked out. Subdir is the optional
// component root *within* that checkout, so several components can share one
// monorepo clone (identical URL+Path) while each builds/runs from its own
// subdirectory. COMPONENT_DIR resolves to Path/Subdir; the clone itself lives
// at Path.
type Repo struct {
	URL    string `yaml:"url"`
	Branch string `yaml:"branch"`
	Path   string `yaml:"path"`
	Subdir string `yaml:"subdir,omitempty"`
}

// Step is a shell action executed during forge (build), preStart (schema setup
// before the component boots), postStart (bootstrap after it is healthy) or
// teardown. When Once is true, a success marker is written to the state dir and
// the step is skipped on later runs unless forced.
type Step struct {
	Name    string            `yaml:"name"`
	Shell   string            `yaml:"shell"`
	Workdir string            `yaml:"workdir"`
	Env     map[string]string `yaml:"env"`
	Once    bool              `yaml:"once"`
}

// Health describes how to decide a component is ready. ExecArgv runs without
// a shell inside a container, or on the host for a process component. Exec
// retains its legacy host-side shell semantics.
type Health struct {
	TCP          string   `yaml:"tcp"`
	HTTP         string   `yaml:"http"`
	ExpectStatus int      `yaml:"expectStatus"`
	Exec         string   `yaml:"exec"`
	ExecArgv     []string `yaml:"execArgv,omitempty"`
	Retries      int      `yaml:"retries"`
	Interval     string   `yaml:"interval"`
	Timeout      string   `yaml:"timeout"`
}

// Discovery is a post-health hook that runs a command (typically inside the
// component's own container), captures its stdout and exports it as a
// deployment variable so downstream components can reference it via ${VAR}.
// This is how the system-produced org id flows from the backend bootstrap into
// agentlens without ever being hardcoded: be becomes healthy, its bootstrap has
// produced the org, and `signoz_integration_status --print-org` reveals it.
//
// The hook is retried until it yields a non-empty value (bootstrap may still be
// finishing). When Optional is true an empty result is tolerated and the
// variable is simply left unset; otherwise discovery failure aborts the deploy.
type Discovery struct {
	Var      string            `yaml:"var"`
	Shell    string            `yaml:"shell"`
	Workdir  string            `yaml:"workdir,omitempty"`
	Env      map[string]string `yaml:"env,omitempty"`
	Retries  int               `yaml:"retries,omitempty"`
	Interval string            `yaml:"interval,omitempty"`
	Optional bool              `yaml:"optional,omitempty"`
}

// Component is a single deployable unit: either a docker container or a host
// process supervised by aiv through a pidfile.
type Component struct {
	Name  string `yaml:"name"`
	Title string `yaml:"title"`
	Kind  string `yaml:"kind"` // container | process
	// Enabled may be a literal (true/false) or a ${VAR} reference resolved
	// against the effective vars by ResolveEnabled. Empty means enabled.
	Enabled   string   `yaml:"enabled,omitempty"`
	DependsOn []string `yaml:"dependsOn,omitempty"`
	Repo      *Repo    `yaml:"repo,omitempty"`

	// disabled is the resolved form of Enabled (set by ResolveEnabled).
	disabled bool

	// forge / build phase
	Build         []Step `yaml:"build,omitempty"`
	ArtifactCheck string `yaml:"artifactCheck,omitempty"`

	// container runtime
	Image         string   `yaml:"image,omitempty"`
	ContainerName string   `yaml:"containerName,omitempty"`
	Entrypoint    []string `yaml:"entrypoint,omitempty"`
	Command       []string `yaml:"command,omitempty"`
	Ports         []string `yaml:"ports,omitempty"`
	Mounts        []string `yaml:"mounts,omitempty"`
	Volumes       []string `yaml:"volumes,omitempty"`
	Network       string   `yaml:"network,omitempty"`
	MemoryLimit   string   `yaml:"memoryLimit,omitempty"`

	// process runtime
	Workdir string `yaml:"workdir,omitempty"`
	Run     string `yaml:"run,omitempty"`
	LogFile string `yaml:"logFile,omitempty"`

	// shared
	Env       map[string]string `yaml:"env,omitempty"`
	EnvFile   string            `yaml:"envFile,omitempty"`
	PreStart  []Step            `yaml:"preStart,omitempty"`
	PostStart []Step            `yaml:"postStart,omitempty"`
	Discover  []Discovery       `yaml:"discover,omitempty"`
	Teardown  []Step            `yaml:"teardown,omitempty"`
	Health    *Health           `yaml:"health,omitempty"`
}

// IsEnabled reports whether the component participates in the deployment.
// Before ResolveEnabled runs, only literal values are honoured.
func (c Component) IsEnabled() bool {
	if c.disabled {
		return false
	}
	on, ok := parseEnabled(c.Enabled)
	return !ok || on
}

// parseEnabled interprets an enabled value. ok is false when the string is not
// a recognised literal (e.g. an unexpanded ${VAR}).
func parseEnabled(s string) (on bool, ok bool) {
	switch strings.ToLower(strings.TrimSpace(s)) {
	case "", "true", "1", "yes", "on":
		return true, true
	case "false", "0", "no", "off":
		return false, true
	}
	return true, false
}

// ResolveEnabled expands every component's enabled expression against vars
// and fixes the result, so OrderedComponents can filter on it. Unrecognised
// values are reported so a typo does not silently enable/disable a component.
func (bp *Blueprint) ResolveEnabled(vars map[string]string) error {
	for i := range bp.Components {
		c := &bp.Components[i]
		on, ok := parseEnabled(Expand(c.Enabled, vars))
		if !ok {
			return fmt.Errorf("component %q: enabled=%q does not resolve to a boolean", c.Name, c.Enabled)
		}
		c.disabled = !on
	}
	return nil
}

// Load reads and validates a blueprint file from disk.
func Load(path string) (*Blueprint, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read blueprint: %w", err)
	}
	return Parse(raw)
}

// Parse decodes and validates blueprint YAML/bytes.
func Parse(raw []byte) (*Blueprint, error) {
	var bp Blueprint
	dec := yaml.NewDecoder(strings.NewReader(string(raw)))
	dec.KnownFields(false)
	if err := dec.Decode(&bp); err != nil {
		return nil, fmt.Errorf("parse blueprint: %w", err)
	}
	if err := bp.Validate(); err != nil {
		return nil, err
	}
	return &bp, nil
}

// Validate enforces the structural invariants aiv relies on.
func (bp *Blueprint) Validate() error {
	if bp.Metadata.Namespace == "" {
		bp.Metadata.Namespace = "aivision"
	}
	if err := ValidateNamespace(bp.Metadata.Namespace); err != nil {
		return err
	}
	if bp.Config.SourceRoot == "" {
		return fmt.Errorf("config.sourceRoot is required")
	}
	if len(bp.Components) == 0 {
		return fmt.Errorf("blueprint declares no components")
	}
	seen := map[string]bool{}
	for i := range bp.Components {
		c := &bp.Components[i]
		if c.Name == "" {
			return fmt.Errorf("component[%d] missing name", i)
		}
		if seen[c.Name] {
			return fmt.Errorf("duplicate component name %q", c.Name)
		}
		seen[c.Name] = true
		switch c.Kind {
		case "container":
			if c.Image == "" {
				return fmt.Errorf("component %q: container kind requires image", c.Name)
			}
		case "process":
			if c.Run == "" {
				return fmt.Errorf("component %q: process kind requires run", c.Name)
			}
		default:
			return fmt.Errorf("component %q: kind must be container or process, got %q", c.Name, c.Kind)
		}
		if c.Repo != nil && c.Repo.Path == "" {
			c.Repo.Path = c.Name
		}
		if h := c.Health; h != nil && h.ExecArgv != nil {
			if len(h.ExecArgv) == 0 || strings.TrimSpace(h.ExecArgv[0]) == "" {
				return fmt.Errorf("component %q: health.execArgv requires an executable", c.Name)
			}
			if h.Exec != "" || h.HTTP != "" || h.TCP != "" {
				return fmt.Errorf("component %q: health.execArgv cannot be combined with exec, http or tcp", c.Name)
			}
		}
		for j := range c.Discover {
			d := &c.Discover[j]
			if strings.TrimSpace(d.Var) == "" {
				return fmt.Errorf("component %q: discover[%d] requires a var name", c.Name, j)
			}
			if strings.TrimSpace(d.Shell) == "" {
				return fmt.Errorf("component %q: discover[%d] (var %s) requires a shell command", c.Name, j, d.Var)
			}
		}
	}
	// dependency references must resolve and be acyclic
	for _, c := range bp.Components {
		for _, d := range c.DependsOn {
			if !seen[d] {
				return fmt.Errorf("component %q depends on unknown component %q", c.Name, d)
			}
		}
	}
	if _, err := bp.topoOrder(); err != nil {
		return err
	}
	return nil
}

// topoOrder returns component names in dependency order (dependencies first).
func (bp *Blueprint) topoOrder() ([]string, error) {
	indeg := map[string]int{}
	graph := map[string][]string{}
	names := []string{}
	for _, c := range bp.Components {
		names = append(names, c.Name)
		indeg[c.Name] = len(c.DependsOn)
		for _, d := range c.DependsOn {
			graph[d] = append(graph[d], c.Name)
		}
	}
	sort.Strings(names)
	var queue []string
	for _, n := range names {
		if indeg[n] == 0 {
			queue = append(queue, n)
		}
	}
	var order []string
	for len(queue) > 0 {
		n := queue[0]
		queue = queue[1:]
		order = append(order, n)
		children := append([]string{}, graph[n]...)
		sort.Strings(children)
		for _, ch := range children {
			indeg[ch]--
			if indeg[ch] == 0 {
				queue = append(queue, ch)
			}
		}
	}
	if len(order) != len(names) {
		return nil, fmt.Errorf("dependency cycle detected among components")
	}
	return order, nil
}

// Component returns a pointer to the named component, or nil.
func (bp *Blueprint) Component(name string) *Component {
	for i := range bp.Components {
		if bp.Components[i].Name == name {
			return &bp.Components[i]
		}
	}
	return nil
}

// OrderedComponents returns enabled components in dependency order.
func (bp *Blueprint) OrderedComponents() []Component {
	order, err := bp.topoOrder()
	if err != nil {
		return bp.Components
	}
	out := []Component{}
	for _, n := range order {
		if c := bp.Component(n); c != nil && c.IsEnabled() {
			out = append(out, *c)
		}
	}
	return out
}

// expand replaces ${KEY} and $KEY tokens using vars first, then the process
// environment. Unknown tokens expand to the empty string.
func Expand(s string, vars map[string]string) string {
	return os.Expand(s, func(key string) string {
		if key == "$" {
			return "$"
		}
		if v, ok := vars[key]; ok {
			return v
		}
		return os.Getenv(key)
	})
}

// ExpandStep applies variable substitution to every string field of a step.
func ExpandStep(st Step, vars map[string]string) Step {
	st.Name = Expand(st.Name, vars)
	st.Shell = Expand(st.Shell, vars)
	st.Workdir = Expand(st.Workdir, vars)
	if st.Env != nil {
		out := map[string]string{}
		for k, v := range st.Env {
			out[Expand(k, vars)] = Expand(v, vars)
		}
		st.Env = out
	}
	return st
}

// ExpandDiscovery applies variable substitution to a discovery hook's fields.
func ExpandDiscovery(d Discovery, vars map[string]string) Discovery {
	d.Var = Expand(d.Var, vars)
	d.Shell = Expand(d.Shell, vars)
	d.Workdir = Expand(d.Workdir, vars)
	d.Env = ExpandMap(d.Env, vars)
	return d
}

func ExpandMap(m map[string]string, vars map[string]string) map[string]string {
	if m == nil {
		return nil
	}
	out := map[string]string{}
	for k, v := range m {
		out[Expand(k, vars)] = Expand(v, vars)
	}
	return out
}

func ExpandSlice(ss []string, vars map[string]string) []string {
	if ss == nil {
		return nil
	}
	out := make([]string, 0, len(ss))
	for _, s := range ss {
		out = append(out, Expand(s, vars))
	}
	return out
}

// CleanPath expands ~ and ${...} then makes the path absolute.
func CleanPath(p string, vars map[string]string) string {
	p = Expand(p, vars)
	if strings.HasPrefix(p, "~/") {
		if home, err := os.UserHomeDir(); err == nil {
			p = filepath.Join(home, p[2:])
		}
	}
	if abs, err := filepath.Abs(p); err == nil {
		return abs
	}
	return p
}
