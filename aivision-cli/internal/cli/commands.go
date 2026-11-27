//go:build !windows

// Package cli implements the aiv command tree: deployment (gauge, forge,
// cast, generate, status, logs, redeploy, destroy), data query and auth
// sub-commands. It wires user flags to the internal packages and owns process
// exit codes.
package cli

import (
	"context"
	"flag"
	"fmt"
	"github.com/antgroup/ai-vision/aivision-cli/internal/distribution"
	"os"
	"os/signal"
	"path/filepath"
	"sort"
	"strings"
	"syscall"

	"github.com/antgroup/ai-vision/aivision-cli/internal/blueprint"
	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/components"
	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/compose"
	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/runtime"
	"github.com/antgroup/ai-vision/aivision-cli/internal/runner"
	"gopkg.in/yaml.v3"
)

// defaultBlueprintYAML is the shipped AI Vision stack recipe.
var defaultBlueprintYAML string

func configurePlatform(embeddedBlueprint string) {
	defaultBlueprintYAML = embeddedBlueprint
}

func platformCommand(cmd string, args []string) (bool, error) {
	var err error
	switch cmd {
	case "gauge":
		err = cmdSimple("gauge", args, func(a *runtime.App) error { return a.Gauge() })
	case "forge":
		err = cmdSimple("forge", args, func(a *runtime.App) error { return a.Forge() })
	case "cast":
		err = cmdCast(args)
	case "generate":
		err = cmdGenerate(args)
	case "destroy":
		err = cmdDestroy(args)
	case "redeploy":
		err = cmdRedeploy(args)
	case "status":
		err = cmdSimple("status", args, func(a *runtime.App) error { return a.Status() })
	case "logs":
		err = cmdLogs(args)
	case "gen":
		err = cmdGen(args)
	case "agent":
		err = cmdAgent(args)
	case "plugin":
		err = cmdPlugin(args)
	case "connect":
		err = cmdConnect(args)
	case "seed":
		err = cmdSeed(args)
	default:
		return false, nil
	}
	return true, err
}

// globalOpts are the flags shared by every blueprint-driven command.
type globalOpts struct {
	target         string
	context        string
	composeFile    string
	composeEnvFile string
	file           string
	verbose        bool
	force          bool
	namespace      string
	sourceRoot     string
	workdir        string
	vars           varFlags
	varsFile       string
	resetVars      bool
	observeSaved   bool // status/logs/destroy use the installed Compose configuration

	// --ob-* sugar (cast/redeploy only): connect to an existing OceanBase
	// instead of starting the bundled container.
	obHost         string
	obPort         string
	obTenant       string
	obPasswordEnv  string
	obPasswordFile string
}

// envVarPrefix marks process environment variables that override blueprint
// vars: AIV_VAR_OB_HOST=10.0.0.5 sets OB_HOST. The prefix keeps unrelated host
// variables (e.g. a stray OB_PORT) from silently steering a deployment.
const envVarPrefix = "AIV_VAR_"

func registerGlobal(fs *flag.FlagSet) *globalOpts {
	g := &globalOpts{}
	// Commands such as logs reserve -f for follow; --file stays available.
	if fs.Lookup("f") == nil {
		fs.StringVar(&g.file, "f", "", "blueprint file (default: ./blueprint.yaml, ./blueprints/aivision.yaml, or built-in)")
	}
	fs.StringVar(&g.file, "file", "", "blueprint file")
	fs.BoolVar(&g.verbose, "v", false, "verbose: stream step output")
	fs.BoolVar(&g.verbose, "verbose", false, "verbose: stream step output")
	fs.BoolVar(&g.force, "force", false, "force rebuild artifacts, re-fetch repos and re-run bootstrap")
	fs.StringVar(&g.namespace, "namespace", "", "override metadata.namespace (isolated state/containers)")
	fs.StringVar(&g.sourceRoot, "source-root", "", "override config.sourceRoot (where repos live/are cloned)")
	fs.StringVar(&g.workdir, "workdir", "", "override config.workdir (state/logs root)")
	fs.Var(&g.vars, "var", "override a config.vars entry KEY=VALUE (repeatable)")
	fs.StringVar(&g.varsFile, "vars-file", "", "YAML file of KEY: VALUE overrides (template: aiv gen --vars)")
	fs.BoolVar(&g.resetVars, "reset-vars", false, "ignore overrides persisted by the previous cast in this namespace")
	return g
}

// registerOBFlags adds the external-OceanBase sugar. Only cast/redeploy take
// them; other commands pick the values up from the persisted override set.
func registerOBFlags(fs *flag.FlagSet, g *globalOpts) {
	fs.StringVar(&g.obHost, "ob-host", "", "use an existing OceanBase or seekdb at this host (seekdb: --var OB_BACKEND=seekdb)")
	fs.StringVar(&g.obPort, "ob-port", "", "port of the existing OceanBase (default 2881)")
	fs.StringVar(&g.obTenant, "ob-tenant", "", "MySQL-mode user tenant on the existing OceanBase (default: OB_TENANT)")
	fs.StringVar(&g.obPasswordEnv, "ob-password-env", "", "name of an environment variable holding the database password")
	fs.StringVar(&g.obPasswordFile, "ob-password-file", "", "file whose first line is the database password")
}

// buildApp resolves the blueprint, layers variable overrides and constructs
// the App. Precedence, lowest to highest:
//
//	blueprint config.vars < state/vars.json (last cast) < --vars-file
//	< AIV_VAR_<KEY> environment < --var / --ob-*
//
// The merged override set is kept on the App so a successful cast persists it.
func buildApp(g *globalOpts) (*runtime.App, error) {
	bp, path, err := resolveBlueprint(g.file)
	if err != nil {
		return nil, err
	}
	if g.namespace != "" {
		bp.Metadata.Namespace = g.namespace
	}
	if g.sourceRoot != "" {
		bp.Config.SourceRoot = g.sourceRoot
	}
	if g.workdir != "" {
		bp.Config.Workdir = g.workdir
	}
	if err := blueprint.ValidateNamespace(bp.Metadata.Namespace); err != nil {
		return nil, err
	}
	bp.Config.Vars = components.NormalizeVars(bp.Config.Vars)
	savedOnly := false
	if g.observeSaved {
		owner, err := compose.Ownership(runtime.NewState(bp.Config.Workdir, bp.Metadata.Namespace).StateDir)
		if err != nil {
			return nil, err
		}
		savedOnly = owner == compose.ComposeOwner
	}

	overrides := map[string]string{}
	sources := map[string]string{}
	layer := func(src string, kv map[string]string) {
		for k, v := range components.NormalizeVars(kv) {
			overrides[k] = v
			sources[k] = src
		}
	}

	// 1. overrides persisted by the previous cast in this namespace
	if !g.resetVars || savedOnly {
		saved, err := runtime.NewState(bp.Config.Workdir, bp.Metadata.Namespace).LoadVars()
		if err != nil {
			return nil, err
		}
		layer("saved", saved)
	}
	if !savedOnly {
		// 2. --vars-file
		if g.varsFile != "" {
			fileVars, err := loadVarsFile(g.varsFile)
			if err != nil {
				return nil, err
			}
			layer("file", fileVars)
		}
		// 3. AIV_VAR_<KEY> environment
		envVars := map[string]string{}
		for _, kv := range os.Environ() {
			if !strings.HasPrefix(kv, envVarPrefix) {
				continue
			}
			rest := kv[len(envVarPrefix):]
			if i := strings.IndexByte(rest, '='); i > 0 {
				envVars[rest[:i]] = rest[i+1:]
			}
		}
		layer("env", envVars)
		// 4. --var
		flagVars := map[string]string{}
		for _, kv := range g.vars {
			i := strings.IndexByte(kv, '=')
			if i <= 0 {
				return nil, fmt.Errorf("invalid --var %q (want KEY=VALUE)", kv)
			}
			flagVars[components.VarKey(kv[:i])] = kv[i+1:]
		}
		layer("flag", flagVars)
		// 5. --ob-* sugar
		obVars, err := g.externalOBVars()
		if err != nil {
			return nil, err
		}
		layer("flag", obVars)
	}

	if bp.Config.Vars == nil {
		bp.Config.Vars = map[string]string{}
	}
	for k, v := range overrides {
		bp.Config.Vars[k] = v
	}
	components.AddRoleReferences(bp.Config.Vars)
	if err := bp.Validate(); err != nil {
		return nil, err
	}
	app, err := runtime.NewApp(bp, runner.NewLogger(g.verbose), g.verbose, g.force)
	if err != nil {
		return nil, err
	}
	app.Vars["BLUEPRINT_PATH"] = path
	app.Overrides = overrides
	app.VarSources = sources
	return app, nil
}

// externalOBVars maps --ob-* flags onto blueprint vars. Passing --ob-host is
// what switches the stack to an existing OceanBase; the other flags refine it.
func (g *globalOpts) externalOBVars() (map[string]string, error) {
	out := map[string]string{}
	if g.obHost == "" {
		if g.obPort != "" || g.obTenant != "" || g.obPasswordEnv != "" || g.obPasswordFile != "" {
			return nil, fmt.Errorf("--ob-port/--ob-tenant/--ob-password-* require --ob-host")
		}
		return out, nil
	}
	out["OB_MANAGED"] = "false"
	out["OB_HOST"] = g.obHost
	// The source blueprint runs agentlens on the host and addresses OceanBase
	// through OB_HOST_LOCAL/OB_PORT_LOCAL; an external instance is the same
	// address from both sides. Harmless where the blueprint ignores them.
	out["OB_HOST_LOCAL"] = g.obHost
	if g.obPort != "" {
		out["OB_HOST_PORT"] = g.obPort
		out["OB_PORT_LOCAL"] = g.obPort
	} else {
		out["OB_PORT_LOCAL"] = "2881"
	}
	if g.obTenant != "" {
		out["OB_TENANT"] = g.obTenant
	}
	if g.obPasswordEnv != "" && g.obPasswordFile != "" {
		return nil, fmt.Errorf("use either --ob-password-env or --ob-password-file, not both")
	}
	if g.obPasswordEnv != "" {
		v, ok := os.LookupEnv(g.obPasswordEnv)
		if !ok {
			return nil, fmt.Errorf("--ob-password-env: environment variable %s is not set", g.obPasswordEnv)
		}
		out["OB_TENANT_PASSWORD"] = v
	}
	if g.obPasswordFile != "" {
		b, err := os.ReadFile(g.obPasswordFile)
		if err != nil {
			return nil, fmt.Errorf("--ob-password-file: %w", err)
		}
		line := strings.SplitN(string(b), "\n", 2)[0]
		out["OB_TENANT_PASSWORD"] = strings.TrimRight(line, "\r")
	}
	return out, nil
}

// loadVarsFile reads a flat YAML mapping of KEY: VALUE overrides.
func loadVarsFile(path string) (map[string]string, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read vars file: %w", err)
	}
	var m map[string]any
	if err := yaml.Unmarshal(raw, &m); err != nil {
		return nil, fmt.Errorf("parse vars file %s: %w", path, err)
	}
	out := map[string]string{}
	for k, v := range m {
		switch t := v.(type) {
		case nil:
			out[k] = ""
		case string:
			out[k] = t
		case bool, int, int64, float64:
			out[k] = fmt.Sprint(t)
		default:
			return nil, fmt.Errorf("vars file %s: %s must be a scalar", path, k)
		}
	}
	return out, nil
}

// resolveBlueprint loads from -f, else a conventional local file, else built-in.
func resolveBlueprint(file string) (*blueprint.Blueprint, string, error) {
	if file != "" {
		bp, err := blueprint.Load(file)
		if err != nil {
			return nil, "", err
		}
		abs, _ := filepath.Abs(file)
		return bp, abs, nil
	}
	for _, cand := range []string{"blueprint.yaml", filepath.Join("blueprints", "aivision.yaml")} {
		if _, err := os.Stat(cand); err == nil {
			bp, err := blueprint.Load(cand)
			if err != nil {
				return nil, "", err
			}
			abs, _ := filepath.Abs(cand)
			return bp, abs, nil
		}
	}
	// built-in default
	bp, err := blueprint.Parse([]byte(defaultBlueprintYAML))
	if err != nil {
		return nil, "", err
	}
	return bp, "(built-in)", nil
}

func cmdSimple(command string, args []string, fn func(*runtime.App) error) error {
	fs := flag.NewFlagSet(command, flag.ContinueOnError)
	g := registerGlobal(fs)
	registerTarget(fs, g)
	if err := fs.Parse(args); err != nil {
		return err
	}
	if err := validateTarget(command, fs, g); err != nil {
		return err
	}
	if g.target == "kubernetes" {
		return runKubernetes(command, g, "", 0, false)
	}
	if g.composeFile != "" {
		if command == "forge" {
			return fmt.Errorf("forge does not apply to Compose; use cast --target compose")
		}
		action := command
		if action == "gauge" {
			action = "check"
		}
		return runComposeFile(g, action, compose.Options{})
	}
	g.observeSaved = g.target == "compose" && command == "status"
	app, err := buildApp(g)
	if err != nil {
		return err
	}
	if use, err := compose.Select(app, g.target == "compose", command); err != nil {
		return err
	} else if use {
		action := command
		if command == "gauge" {
			action = "check"
		}
		return runComposeProject(app, action, compose.Options{})
	}
	return fn(app)
}

func cmdCast(args []string) error {
	fs := flag.NewFlagSet("cast", flag.ContinueOnError)
	g := registerGlobal(fs)
	registerTarget(fs, g)
	registerOBFlags(fs, g)
	noGauge := fs.Bool("no-gauge", false, "skip Docker runtime gauge; mandatory Compose prerequisites and ownership checks still run")
	noForge := fs.Bool("no-forge", false, "skip source/custom clone/build (product Compose uses released images)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if err := validateTarget("cast", fs, g); err != nil {
		return err
	}
	if g.target == "kubernetes" {
		return runKubernetes("cast", g, "", 0, false)
	}
	if g.composeFile != "" {
		return runComposeFile(g, "up", compose.Options{})
	}
	app, err := buildApp(g)
	if err != nil {
		return err
	}
	if use, err := compose.Select(app, g.target == "compose", "cast"); err != nil {
		return err
	} else if use {
		return runComposeProject(app, "up", compose.Options{})
	}
	// The user plane authenticates with an aivision SpaceKey they supply via
	// `aiv auth login`; there is no post-deploy credential bootstrap.
	return app.Cast(!*noGauge, !*noForge)
}

func cmdRedeploy(args []string) error {
	fs := flag.NewFlagSet("redeploy", flag.ContinueOnError)
	g := registerGlobal(fs)
	registerTarget(fs, g)
	registerOBFlags(fs, g)
	noGauge := fs.Bool("no-gauge", false, "skip Docker runtime gauge; mandatory Compose prerequisites and ownership checks still run")
	noForge := fs.Bool("no-forge", false, "skip source/custom clone/build")
	purge := fs.Bool("purge", false, "Docker runtime only: destroy --purge first; unsupported for Compose")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if err := validateTarget("redeploy", fs, g); err != nil {
		return err
	}
	if g.composeFile != "" {
		if *purge {
			return fmt.Errorf("Compose redeploy --purge is unsupported; metadata and deployment identity are retained (no resources changed)")
		}
		return runComposeFile(g, "up", compose.Options{})
	}
	app, err := buildApp(g)
	if err != nil {
		return err
	}
	if use, err := compose.Select(app, g.target == "compose", "redeploy"); err != nil {
		return err
	} else if use {
		if *purge {
			return fmt.Errorf("Compose redeploy --purge is unsupported: retained metadata and deployment identity require an explicit migration/reset plan; no resources changed")
		}
		return runComposeProject(app, "up", compose.Options{})
	}
	if *purge {
		if err := app.Destroy(runtime.DestroyOpts{Purge: true}); err != nil {
			return err
		}
		return app.Cast(!*noGauge, !*noForge)
	}
	return app.Redeploy(!*noGauge, !*noForge)
}

func cmdDestroy(args []string) error {
	fs := flag.NewFlagSet("destroy", flag.ContinueOnError)
	g := registerGlobal(fs)
	registerTarget(fs, g)
	purge := fs.Bool("purge", false, "Docker: remove local state and deployment named volumes; Compose: remove named volumes, retaining bind-mounted metadata and identity")
	purgeRepos := fs.Bool("purge-repos", false, "also delete source repos aiv cloned (implies --purge)")
	yes := fs.Bool("y", false, "skip the confirmation prompt")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if err := validateTarget("destroy", fs, g); err != nil {
		return err
	}
	if g.composeFile != "" {
		if *purgeRepos {
			return fmt.Errorf("--purge-repos does not apply to Compose (no resources changed)")
		}
		if !*yes {
			fmt.Fprintf(os.Stderr, "This stops/removes the Compose project selected by %q.\n", g.composeFile)
			if *purge {
				fmt.Fprintln(os.Stderr, "--purge deletes named volumes; bind-mounted metastore, identity and external databases are retained.")
			}
			fmt.Fprint(os.Stderr, "Continue? [y/N] ")
			var answer string
			_, _ = fmt.Fscanln(os.Stdin, &answer)
			if !strings.EqualFold(strings.TrimSpace(answer), "y") {
				fmt.Fprintln(os.Stderr, "aborted")
				return nil
			}
		}
		return runComposeFile(g, "down", compose.Options{Purge: *purge})
	}
	g.observeSaved = g.target == "compose"
	app, err := buildApp(g)
	if err != nil {
		return err
	}
	useCompose, err := compose.Select(app, g.target == "compose", "destroy")
	if err != nil {
		return err
	}
	if useCompose && *purgeRepos {
		return fmt.Errorf("--purge-repos is for source/custom development deployments; Compose metadata and deployment identity are retained (no resources changed)")
	}
	opts := runtime.DestroyOpts{Purge: *purge || *purgeRepos, PurgeRepos: *purgeRepos}
	if !*yes {
		warn := "This stops all AI Vision components in namespace '" + app.BP.Metadata.Namespace + "'."
		if opts.Purge && !useCompose {
			if err := app.VerifyStatePurge(); err != nil {
				return err
			}
			warn += "\n  State directory to delete: " + app.State.Root
		}
		if useCompose && opts.Purge {
			warn += "\n  --purge ALSO deletes this Compose project's named volumes: DATA WILL BE LOST."
			warn += "\n  Bind-mounted metastore, deployment identity and private configuration are retained. External databases are not touched."
		} else if opts.Purge {
			warn += "\n  --purge deletes local state and any named volumes used by this deployment."
			if app.OBManaged() {
				warn += " This includes managed OceanBase data."
			} else {
				warn += " The external OceanBase at " + app.Vars["OB_HOST"] + " is retained."
			}
		}
		if opts.PurgeRepos {
			repos, err := app.PurgeRepoPlan()
			if err != nil {
				return err
			}
			if len(repos) == 0 {
				warn += "\n  --purge-repos: no verified aiv-cloned checkouts to delete; other records and checkouts will be kept."
			} else {
				warn += "\n  --purge-repos ALSO deletes these aiv-cloned repos:"
				for _, repo := range repos {
					warn += "\n    " + repo.Path
				}
			}
			opts.ConfirmedRepos = repos
		}
		fmt.Fprintln(os.Stderr, warn)
		fmt.Fprint(os.Stderr, "Continue? [y/N] ")
		var ans string
		_, _ = fmt.Fscanln(os.Stdin, &ans)
		if !strings.EqualFold(strings.TrimSpace(ans), "y") {
			fmt.Fprintln(os.Stderr, "aborted")
			return nil
		}
	}
	if useCompose {
		return runComposeProject(app, "down", compose.Options{Purge: opts.Purge})
	}
	return app.Destroy(opts)
}

func cmdLogs(args []string) error {
	g, component, lines, follow, err := parseLogsArgs(args)
	if err != nil {
		return err
	}
	if g.target == "kubernetes" {
		return runKubernetes("logs", g, component, lines, follow)
	}
	if g.composeFile != "" {
		return runComposeFile(g, "logs", compose.Options{Component: component, Tail: lines, Follow: follow})
	}
	g.observeSaved = g.target == "compose"
	app, err := buildApp(g)
	if err != nil {
		return err
	}
	if use, err := compose.Select(app, g.target == "compose", "logs"); err != nil {
		return err
	} else if use {
		return runComposeProject(app, "logs", compose.Options{Component: component, Tail: lines, Follow: follow})
	}
	return app.Logs(component, lines, follow)
}

func runComposeFile(g *globalOpts, action string, opts compose.Options) error {
	if distribution.NativeOnly {
		return fmt.Errorf("native distribution does not accept standalone Compose files; use CLI-managed --target compose with native release variables")
	}
	envFile := g.composeEnvFile
	if envFile == "" {
		envFile = ".env"
	}
	docker := runner.NewDocker(runner.NewLogger(g.verbose))
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	return compose.RunFile(ctx, docker.Bin, g.composeFile, envFile, action, opts)
}

func runComposeProject(app *runtime.App, action string, opts compose.Options) error {
	ctx, cancel := signal.NotifyContext(app.Ctx, os.Interrupt, syscall.SIGTERM)
	defer cancel()
	app.Ctx = ctx
	return compose.New(app).Run(action, opts)
}

// parseLogsArgs preserves the logs-specific -f follow flag and accepts flags
// before, after, or around the component name.
func parseLogsArgs(args []string) (g *globalOpts, component string, lines int, follow bool, err error) {
	fs := flag.NewFlagSet("logs", flag.ContinueOnError)
	fs.BoolVar(&follow, "f", false, "follow the log stream")
	g = registerGlobal(fs)
	registerTarget(fs, g)
	fs.BoolVar(&follow, "F", false, "follow the log stream")
	fs.BoolVar(&follow, "follow", false, "follow the log stream")
	n := fs.Int("n", 50, "number of trailing lines")
	flags, positionals := splitInterspersedFlags(fs, args)
	if err = fs.Parse(flags); err != nil {
		return nil, "", 0, false, err
	}
	if len(positionals) != 1 {
		return nil, "", 0, false, fmt.Errorf("usage: aiv logs <component> [-n lines] [-f|-F|--follow] [--file blueprint.yaml]")
	}
	if *n < 0 {
		return nil, "", 0, false, fmt.Errorf("log tail must be zero or greater")
	}
	if err = validateTarget("logs", fs, g); err != nil {
		return nil, "", 0, false, err
	}
	return g, positionals[0], *n, follow, nil
}

func cmdGen(args []string) error {
	fs := flag.NewFlagSet("gen", flag.ContinueOnError)
	out := fs.String("o", "", "output path (default blueprint.yaml, or aivision.vars.yaml with --vars; '-' for stdout)")
	varsOnly := fs.Bool("vars", false, "write a KEY: VALUE overrides template for --vars-file instead of the full blueprint")
	if err := fs.Parse(args); err != nil {
		return err
	}
	content := defaultBlueprintYAML
	next := "Edit config.vars (IMAGE_REGISTRY/IMAGE_TAG/ports/secrets), then run: aiv cast -f %s\n"
	if *out == "" {
		*out = "blueprint.yaml"
	}
	if *varsOnly {
		tpl, err := varsTemplate()
		if err != nil {
			return err
		}
		content = tpl
		next = "Uncomment the keys you want to change, then run: aiv cast --vars-file %s\n"
		if *out == "blueprint.yaml" {
			*out = "aivision.vars.yaml"
		}
	}
	if *out == "-" {
		fmt.Print(content)
		return nil
	}
	if _, err := os.Stat(*out); err == nil {
		return fmt.Errorf("%s already exists (refusing to overwrite; use -o)", *out)
	}
	if dir := filepath.Dir(*out); dir != "" && dir != "." {
		_ = os.MkdirAll(dir, 0o755)
	}
	if err := os.WriteFile(*out, []byte(content), 0o644); err != nil {
		return err
	}
	fmt.Printf("wrote %s\n"+next, *out, *out)
	return nil
}

// varsTemplate renders an overrides file for --vars-file: every config.vars
// key of the built-in blueprint with its default, commented out, plus a worked
// external-OceanBase section up front.
func varsTemplate() (string, error) {
	bp, err := blueprint.Parse([]byte(defaultBlueprintYAML))
	if err != nil {
		return "", err
	}
	keys := make([]string, 0, len(bp.Config.Vars))
	for k := range bp.Config.Vars {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool {
		return components.DisplayVar(keys[i]) < components.DisplayVar(keys[j])
	})

	var b strings.Builder
	b.WriteString(`# aiv variable overrides — pass with: aiv cast --vars-file <this file>
#
# Only keys you uncomment are applied; everything else keeps the blueprint
# default. Precedence (low -> high): blueprint < overrides persisted by the
# last cast < this file < AIV_VAR_<KEY> environment < --var / --ob-* flags.
# A successful cast persists the effective overrides per namespace, so later
# status/destroy/redeploy do not need this file again.
#
# Secrets: prefer AIV_VAR_OB_TENANT_PASSWORD=... (environment) or
# --ob-password-env over writing passwords here.

# --- Choose the Docker datastore (default: oceanbase) -----------------------
#DATABASE_BACKEND: seekdb
# seekdb uses root; OceanBase uses root@OB_TENANT. DATABASE_USER overrides either.
# OB_TENANT_PASSWORD is the database password for both backends.
# Choose the backend for a new deployment; this does not migrate existing data.
# Kubernetes uses its fixed OceanBase Operator deployment.

# --- Use an existing OceanBase or seekdb instead of the bundled container ---
# The address must be reachable FROM the docker network: for an OceanBase on
# this host use the bridge gateway (usually 172.17.0.1) or the LAN IP, never
# 127.0.0.1. The tenant must be MySQL-mode; aiv creates the databases
# aivision / signoz / agentlens (or they must pre-exist).
#DATABASE_MANAGED: "false"
#DATABASE_HOST: 10.0.0.5
#DATABASE_HOST_PORT: "2881"
#OB_TENANT: aivision
#OB_TENANT_PASSWORD: change-me

# --- Expose the stack to other machines -------------------------------------
#BIND_ADDR: 0.0.0.0
#PUBLIC_HOST: 192.168.1.20

# --- All blueprint variables with their defaults ----------------------------
`)
	for _, k := range keys {
		v := bp.Config.Vars[k]
		for _, component := range components.All() {
			for role, legacy := range component.ConfigAliases {
				v = strings.ReplaceAll(v, "${"+legacy+"}", "${"+role+"}")
			}
		}
		if v == "" || strings.ContainsAny(v, ":#{}\"'") || strings.TrimSpace(v) != v {
			v = fmt.Sprintf("%q", v)
		}
		b.WriteString(fmt.Sprintf("#%s: %s\n", components.DisplayVar(k), v))
	}
	return b.String(), nil
}

func usage() {
	fmt.Print(`aiv — register, connect Agents, query telemetry and deploy AI Vision.

Use an existing AI Vision deployment entirely from your terminal, or deploy a
stack from a blueprint. Account sessions manage spaces and Agents; SpaceKeys
authorize queries; installed hooks hold their own reporting credentials.

Usage:
  aiv <command> [sub-command] [flags]
  aiv help                       Show this overview
  aiv <command> --help            Explain a command and its options

Account, spaces and query keys:
  auth register   Register, log in and select the personal space
  auth login      Log in with email/password; legacy SpaceKey import is supported
  auth status     Verify the server session and show the saved query context
  auth print-env  Print shell exports, including the saved query key
  auth logout     Revoke the account session and remove its local profile
  space           List, select or create spaces (list/use/create)
  key             Create/import/save, list or revoke query SpaceKeys

Agent onboarding:
  connect <agent>      Register/reuse an Agent and install its telemetry hook
  agent status <agent> Check local installation, ingest credentials and recent traces
  Hook installation needs Python 3.11+, an installed host Agent and a reachable
  deployment. Supported platforms: Linux/macOS on x64/arm64. No query key is
  required to connect. Use aiv connect --help for prerequisites and examples.

Query (AI Vision OpenAPI; uses the current space's SpaceKey and Agent context):
  query trace     List/get/export traces     (aiv query trace list --limit 5)
  query span      List/get spans of a trace  (aiv query span list <traceId>)
  query session   List/get/io/usage sessions (aiv query session list)
  query log       List/query/export events   (aiv query log list --since 30m)
  query metric    Run a timeseries query     (aiv query metric query --sql ...)
  query metadata  Space/agent metadata       (aiv query metadata events)

Deploy:
  plugin      Install/status/logs/remove an optional OpenShell Sandbox plugin
  gauge       Validate the selected target's prerequisites and configuration
  forge       Docker: clone/update repos and build declared source artifacts
  generate    Render deployment materials: --target compose|kubernetes|systemd
  cast        Deploy/update the stack (default Docker; optional Compose/Kubernetes)
  status      Show selected target state; Compose uses its exact saved project
  logs        Tail a component's logs: aiv logs <component> [-n N] [-f|-F|--follow]
  redeploy    Docker: destroy + cast; Compose: reapply its guarded lifecycle
  destroy     Stop/remove the selected target's deployment
              Docker --purge wipes data/state; Compose --purge removes named volumes
              and retains bind-mounted metastore, identity and private config

Seed (bulk-import demo telemetry through the real ingest path):
  seed       Mint a v2 token for a registered Agent and push OTLP traces to the
             gateway, filling trace/session/metric dashboards
             (aiv seed --agent-uuid … [--sessions 8 --turns 4])

Other:
  gen         Write the built-in example blueprint: aiv gen [-o path]
              or an overrides template for --vars-file: aiv gen --vars [-o path]
  version     Print the aiv version

Deployment target (gauge/cast/status/logs):
      --target string     docker (default), compose, or kubernetes
      --context string    Kubernetes context (default: current kubeconfig context)
      --compose-file PATH Operate this existing product Compose YAML explicitly
      --compose-env-file PATH  Its env file (default .env; resolved exports: /dev/null)
  Kubernetes uses embedded fixed YAML, independently of the Docker blueprint.
  Kubernetes destroy/redeploy/forge are not supported; repeat cast to update.
  generate --target kubernetes is a deprecated generic blueprint conversion,
  not the supported Kubernetes installation. Use cast --target kubernetes.
  Helm support is planned but not available yet; no chart or Helm target is shipped.
  Components: frontend, backend, diagnosis, ingest-gateway, data-backend, database.
  Legacy component selectors remain accepted; existing resources keep their names.
  Docker remains the default Go runtime and requires no Compose or host Python.
  Optional Compose uses native docker compose (2.27+) with aiv lifecycle guards;
  no host Python control script is used. Keep a fixed UTC trace-history bound.
  Compose status/logs/destroy use the saved immutable project, independent of
  ambient vars. --compose-file instead selects an external project's own YAML/env;
  do not combine it with blueprint, variable, namespace or workdir selectors.
  Targets never adopt each other's installations. Source/process and custom
  blueprints keep the Docker development runtime; explicit Compose rejects them.

Global flags (deployment commands):
  -f, --file string        Blueprint file (default ./blueprint.yaml, ./blueprints/aivision.yaml, or built-in)
  -v, --verbose            Stream build/deploy step output
      --force              Re-fetch repos, rebuild artifacts, re-run bootstrap
      --namespace string   Override metadata.namespace (isolated state/containers)
      --source-root string Override config.sourceRoot (repo checkout location)
      --workdir string     Override config.workdir (state/logs root)
      --var KEY=VALUE      Override a blueprint variable (repeatable)
      --vars-file string   YAML file of KEY: VALUE overrides (aiv gen --vars)
      --reset-vars         Ignore overrides persisted by the previous cast
  For logs, -f, -F and --follow mean follow; use --file for a blueprint.
  Precedence (low -> high): blueprint config.vars < overrides persisted by the last
  cast in this namespace < --vars-file < AIV_VAR_<KEY> environment < --var/--ob-*.
  Docker cast persists effective overrides; accepted Compose cast persists private
  retry configuration under its lock. Keep target/namespace/workdir selectors.
  Compose output shows the private project path and native inspection command.
  Role config keys such as DATA_BACKEND_IMAGE, DIAGNOSIS_IMAGE and BACKEND_IMAGE
  accept legacy spellings. Role keys win within one file/environment layer;
  repeated --var flags use the last value, regardless of spelling.

Docker datastore: --var OB_BACKEND=oceanbase|seekdb (default oceanbase).
  Select on a new deployment; switching backends does not migrate data.
  Kubernetes uses its fixed OceanBase Operator deployment.

Existing OceanBase / seekdb (cast/redeploy; skips the bundled database):
      --ob-host string          Host reachable FROM the docker network (not 127.0.0.1)
      --ob-port string          Port (default 2881)
      --ob-tenant string        OceanBase MySQL-mode user tenant (default aivision)
      --ob-password-env NAME    Read the database password from $NAME
      --ob-password-file PATH   Read it from the first line of PATH

Quick start with an existing deployment:
  aiv auth register --endpoint https://vision.example.com --email you@example.com
  # Already registered? Use: aiv auth login --endpoint URL --email EMAIL
  aiv space list
  aiv connect codex --dry-run
  aiv connect codex
  # Restart Codex, trust the hooks via /hooks, and run a real task.
  aiv key create --name cli      # or: aiv key import --key-stdin < query-key.txt
  aiv agent status codex
  aiv query trace list --since 1h --limit 5
  Registration must be enabled on the deployment. Use --profile NAME to select
  another saved account. See aiv auth help, aiv space help and aiv key help.

Deploy your own stack:
  aiv gen -o blueprint.yaml      # write an editable blueprint
  aiv cast  -f blueprint.yaml    # deploy the whole stack locally
  aiv cast --var OB_BACKEND=seekdb # deploy with OceanBase seekdb
  aiv cast --ob-host 10.0.0.5 --ob-tenant aivision --ob-password-env OB_PW   # reuse your OceanBase
  aiv cast --target kubernetes --context <context>  # full stack from fixed YAML
  # Then register against your reachable platform URL and follow the steps above.
  aiv generate --target kubernetes -o pours
  aiv destroy -f blueprint.yaml --purge
`)
}
