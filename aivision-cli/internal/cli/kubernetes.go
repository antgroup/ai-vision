//go:build !windows

package cli

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"strings"
	"syscall"

	"github.com/antgroup/ai-vision/aivision-cli/internal/auth"
	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/components"
	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/kubernetes"
)

func registerTarget(fs *flag.FlagSet, g *globalOpts) {
	fs.StringVar(&g.target, "target", "docker", "deployment target: docker (default)|compose|kubernetes (fixed YAML); Helm support is planned but not available yet")
	fs.StringVar(&g.context, "context", "", "Kubernetes context (default: current kubeconfig context)")
	fs.StringVar(&g.composeFile, "compose-file", "", "existing product Compose YAML (requires --target compose; do not combine with blueprint/variable selectors)")
	fs.StringVar(&g.composeEnvFile, "compose-env-file", "", "env file for --compose-file (default .env; private resolved exports use /dev/null)")
}

func validateTarget(command string, fs *flag.FlagSet, g *globalOpts) error {
	if g.target != "docker" && g.target != "compose" && g.target != "kubernetes" {
		return fmt.Errorf("unknown deployment target %q (use docker, compose or kubernetes)", g.target)
	}
	if (g.composeFile != "" || g.composeEnvFile != "") && g.target != "compose" {
		return fmt.Errorf("--compose-file/--compose-env-file require --target compose")
	}
	if g.composeEnvFile != "" && g.composeFile == "" {
		return fmt.Errorf("--compose-env-file requires --compose-file")
	}
	if g.composeFile != "" {
		var incompatible string
		fs.Visit(func(f *flag.Flag) {
			if command == "logs" && f.Name == "f" {
				return
			}
			switch f.Name {
			case "f", "file", "namespace", "workdir", "source-root", "vars-file", "var", "reset-vars", "ob-host", "ob-port", "ob-tenant", "ob-password-env", "ob-password-file":
				incompatible = f.Name
			}
		})
		if incompatible != "" {
			return fmt.Errorf("--%s cannot be combined with --compose-file; the selected YAML and env file own deployment configuration", incompatible)
		}
	}
	if g.target == "docker" || g.target == "compose" {
		if g.context != "" {
			return fmt.Errorf("--context requires --target kubernetes")
		}
		return nil
	}
	if command != "cast" && command != "gauge" && command != "status" && command != "logs" {
		return fmt.Errorf("%s is not supported for Kubernetes; use cast to deploy/update (no resources changed)", command)
	}
	if command != "logs" && len(fs.Args()) != 0 {
		return fmt.Errorf("unexpected arguments: %s", strings.Join(fs.Args(), " "))
	}
	// Refuse Docker-only options rather than silently ignoring the user's
	// selected blueprint, host database or teardown behavior.
	var unsupported string
	fs.Visit(func(f *flag.Flag) {
		if command == "logs" && f.Name == "f" {
			return
		}
		switch f.Name {
		case "f", "file", "force", "source-root", "workdir", "no-gauge", "no-forge", "ob-host", "ob-port", "ob-tenant", "ob-password-env", "ob-password-file":
			unsupported = f.Name
		}
	})
	if unsupported != "" {
		return fmt.Errorf("--%s does not apply to the fixed Kubernetes deployment", unsupported)
	}
	return nil
}

func kubernetesOverrides(g *globalOpts) (map[string]string, error) {
	values := map[string]string{}
	if g.varsFile != "" {
		var err error
		values, err = loadVarsFile(g.varsFile)
		if err != nil {
			return nil, err
		}
		values = components.NormalizeVars(values)
	}
	envValues := map[string]string{}
	for _, kv := range os.Environ() {
		if !strings.HasPrefix(kv, envVarPrefix) {
			continue
		}
		pair := strings.SplitN(strings.TrimPrefix(kv, envVarPrefix), "=", 2)
		if len(pair) == 2 {
			envValues[pair[0]] = pair[1]
		}
	}
	for key, value := range components.NormalizeVars(envValues) {
		values[key] = value
	}
	for _, kv := range g.vars {
		pair := strings.SplitN(kv, "=", 2)
		if len(pair) != 2 || pair[0] == "" {
			return nil, fmt.Errorf("invalid --var (want KEY=VALUE)")
		}
		values[components.VarKey(pair[0])] = pair[1]
	}
	return values, nil
}

func runKubernetes(command string, g *globalOpts, component string, tail int, follow bool) error {
	values, err := kubernetesOverrides(g)
	if err != nil {
		return err
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	app, err := kubernetes.New(ctx, kubernetes.Options{
		Context: g.context, Namespace: g.namespace, Overrides: values, Reset: g.resetVars,
		Credentials: func() (string, string, error) {
			if !auth.Interactive() {
				return "", "", fmt.Errorf("set AIV_GHCR_USERNAME and AIV_GHCR_TOKEN, or --var IMAGE_PULL_SECRET=<existing-secret>; public images can use --var IMAGE_AUTH=anonymous")
			}
			username, err := auth.PromptLine("GHCR username")
			if err != nil {
				return "", "", err
			}
			token, err := auth.PromptSecret("GHCR PAT (read:packages)")
			return username, token, err
		},
	})
	if err != nil {
		return err
	}
	switch command {
	case "cast":
		return app.Cast(ctx)
	case "gauge":
		return app.Gauge(ctx)
	case "status":
		return app.Status(ctx)
	case "logs":
		return app.Logs(ctx, component, tail, follow)
	}
	return fmt.Errorf("unsupported Kubernetes command %s", command)
}
