package cli

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"syscall"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/config"
	"github.com/antgroup/ai-vision/aivision-cli/internal/installprogress"
	"github.com/antgroup/ai-vision/aivision-cli/internal/sandboxplugin"
)

const pluginUsage = `Manage the optional OpenShell Sandbox plugin using native Docker.

Usage:
  aiv plugin setup openshell --session-url URL --bootstrap-token TOKEN \
      --progress-token TOKEN --enrollment-token TOKEN
  aiv plugin install openshell --image IMAGE --gateway-endpoint HOST:PORT \
      --workload-image IMAGE --token-file FILE --policy-file FILE \
      --docker-socket PATH --journal-dir DIR [--docker-socket-gid N] \
      [--port 8092] [--state-dir DIR] [--timeout 15m] \
      [--progress-url URL --progress-token TOKEN]
  aiv plugin status openshell [--state-dir DIR] [--format text|json] \
      [--progress-url URL --progress-token TOKEN]
  aiv plugin logs openshell [--state-dir DIR] [-n 100] [--follow]
  aiv plugin remove openshell [--state-dir DIR]

Prerequisites: an existing OpenShell gateway, its actual Docker daemon Unix
socket, a compatible explicit plugin image, and an operator-approved workload
image and non-root deny-network policy. This command does not install a gateway.
There is no default or assumed published plugin image/tag. The optional image
is separate from the five default AI Vision services and their release bundle.

The plugin HTTP API binds 127.0.0.1:8092 by default (container port 8091).
Access tokens and optional gateway credentials are mounted files; token bytes
never enter Docker argv or saved installation state. Use 0600 secret files.
Saved installation state is private (0600); the journal directory persists.
Remove fences new leases and refuses active, cleanup-pending or unverifiable
leases. It never purges the journal or changes the OpenShell gateway.
Install does not replace an existing installation. Preserve --state-dir for
status, logs, removal and subsequent installation.
Install progress goes to stderr, including phase durations and waiting updates.
Only the explicit plugin image is pulled; the workload image is an allowlist entry.
The install timeout includes image acquisition (15m by default; set --timeout).
`

func cmdPlugin(args []string) error {
	if len(args) == 0 || args[0] == "help" || args[0] == "--help" || args[0] == "-h" {
		fmt.Print(pluginUsage)
		return nil
	}
	action := args[0]
	if action == "setup" {
		return cmdPluginSetup(args[1:])
	}
	if action != "install" && action != "status" && action != "logs" && action != "remove" {
		return fmt.Errorf("unknown plugin command %q", action)
	}
	fs := flag.NewFlagSet("plugin "+action, flag.ContinueOnError)
	fs.Usage = func() {
		fmt.Fprint(fs.Output(), pluginUsage)
		fmt.Fprintln(fs.Output(), "\nOptions:")
		fs.PrintDefaults()
	}
	stateDir := fs.String("state-dir", filepath.Join(config.Dir(), "plugins", "openshell"), "private installation state directory; reuse it for every command")
	format := "text"
	if action == "status" {
		fs.StringVar(&format, "format", "text", "text|json")
	}
	progressURL, progressToken := "", ""
	if action == "install" || action == "status" {
		fs.StringVar(&progressURL, "progress-url", "", "optional AI Vision installation reporting URL; requires --progress-token")
		fs.StringVar(&progressToken, "progress-token", "", "short-lived reporting credential; requires --progress-url")
	}
	var options sandboxplugin.Options
	installTimeout := 15 * time.Minute
	if action == "install" {
		fs.DurationVar(&installTimeout, "timeout", installTimeout, "total installation timeout including image acquisition (positive duration)")
		fs.StringVar(&options.Image, "image", "", "explicit compatible plugin image reference (required)")
		fs.StringVar(&options.Gateway, "gateway-endpoint", "", "existing OpenShell gateway host:port reachable from the plugin container (required)")
		fs.StringVar(&options.WorkloadImage, "workload-image", "", "operator-approved workload image containing the native Rule worker (required)")
		fs.StringVar(&options.TokenFile, "token-file", "", "owner-only file containing plugin HTTP bearer token (required)")
		fs.StringVar(&options.PolicyFile, "policy-file", "", "fixed non-root deny-network policy file (required)")
		fs.StringVar(&options.DockerSocket, "docker-socket", "", "actual gateway Docker daemon Unix socket for cleanup readback (required)")
		fs.Func("docker-socket-gid", "optional container-side socket group (0..2147483647); defaults to the host socket group", func(value string) error {
			gid, err := strconv.Atoi(value)
			if err != nil || gid < 0 || gid > 2147483647 {
				return fmt.Errorf("--docker-socket-gid must be between 0 and 2147483647")
			}
			options.SocketGIDOverride = &gid
			return nil
		})
		fs.StringVar(&options.JournalDir, "journal-dir", "", "persistent owner-only lease journal directory (required)")
		fs.IntVar(&options.Port, "port", 8092, "localhost plugin HTTP port")
		fs.StringVar(&options.Workspace, "workspace", "default", "operator-configured gateway workspace")
		fs.StringVar(&options.GatewayTokenFile, "gateway-token-file", "", "optional owner-only OpenShell gateway bearer token file")
		fs.StringVar(&options.GatewayCAFile, "gateway-ca-file", "", "optional gateway TLS CA certificate file")
		fs.StringVar(&options.GatewayCertFile, "gateway-cert-file", "", "optional gateway TLS client certificate file (requires key)")
		fs.StringVar(&options.GatewayKeyFile, "gateway-key-file", "", "optional owner-only gateway TLS client key file (requires certificate)")
	}
	lines, follow := 100, false
	if action == "logs" {
		fs.IntVar(&lines, "n", 100, "number of recent log lines (1..10000)")
		fs.BoolVar(&follow, "follow", false, "stream logs until interrupted")
		fs.BoolVar(&follow, "f", false, "stream logs until interrupted")
	}
	flags, positionals := splitInterspersedFlags(fs, args[1:])
	if err := fs.Parse(flags); err != nil {
		return err
	}
	if len(positionals) != 1 || positionals[0] != "openshell" {
		return fmt.Errorf("plugin %s requires the supported provider name openshell", action)
	}
	if format != "text" && format != "json" {
		return fmt.Errorf("--format must be text or json")
	}
	if lines < 1 || lines > 10000 {
		return fmt.Errorf("-n must be between 1 and 10000")
	}
	if installTimeout <= 0 {
		return fmt.Errorf("--timeout must be positive")
	}
	progressURLSet, progressTokenSet := false, false
	fs.Visit(func(value *flag.Flag) {
		if value.Name == "progress-url" {
			progressURLSet = true
		}
		if value.Name == "progress-token" {
			progressTokenSet = true
		}
	})
	if progressURLSet != progressTokenSet {
		return fmt.Errorf("--progress-url and --progress-token must be provided together")
	}
	var reporter *installprogress.Reporter
	if progressURLSet {
		var err error
		reporter, err = installprogress.New(progressURL, progressToken, action, os.Stderr)
		if err != nil {
			return err
		}
		defer reporter.Close()
	}
	manager := sandboxplugin.New(*stateDir)
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	cancel := func() {}
	if action != "logs" || !follow {
		timeout := 3 * time.Minute
		if action == "install" {
			timeout = installTimeout
		}
		ctx, cancel = context.WithTimeout(ctx, timeout)
	}
	defer cancel()
	switch action {
	case "install":
		manager.Progress = func(event sandboxplugin.ProgressEvent) {
			printPluginProgress(os.Stderr, event)
			reporter.Observe(event)
		}
		status, err := manager.Install(ctx, options)
		if status != nil {
			printPluginStatus(status, "text")
		}
		if err != nil {
			printPluginRecovery(os.Stderr, manager.StateDir)
			return err
		}
		if status != nil && status.Health != nil && status.Health.Ready != nil && *status.Health.Ready {
			fmt.Println("Installation complete; the provider is ready. Space binding is a separate step.")
		} else {
			fmt.Fprintln(os.Stderr, "The container is installed, but the provider is not ready. Inspect status and logs before using this connection.")
			printPluginRecovery(os.Stderr, manager.StateDir)
		}
		fmt.Printf("State: %s\nConfigure the plugin HTTP endpoint in Integrations > Plugins > Sandbox > OpenShell.\n", manager.StateDir)
	case "status":
		started := time.Now()
		reporter.Observe(sandboxplugin.ProgressEvent{Stage: "readiness", State: "start"})
		status, err := manager.Status(ctx)
		if reporter != nil {
			reportPluginStatus(reporter, status, err, ctx, time.Since(started))
		}
		if status != nil {
			printPluginStatus(status, format)
		}
		return err
	case "logs":
		return manager.Logs(ctx, os.Stdout, lines, follow)
	case "remove":
		if err := manager.Remove(ctx); err != nil {
			return err
		}
		fmt.Println("Optional plugin removed (or not installed); journal and operator files retained. OpenShell gateway unchanged.")
	}
	return nil
}

func printPluginStatus(status *sandboxplugin.Status, format string) {
	if format == "json" {
		data, _ := json.MarshalIndent(status, "", "  ")
		fmt.Println(string(data))
		return
	}
	if !status.Installed {
		fmt.Println("OpenShell plugin: not installed")
		return
	}
	ready, draining, leases := "unverified", "unverified", "unverified"
	if status.Health != nil {
		ready = fmt.Sprint(*status.Health.Ready)
		draining = fmt.Sprint(*status.Health.Draining)
		leases = fmt.Sprint(*status.Health.ActiveLeases)
	}
	fmt.Printf("OpenShell plugin: installed\nContainer: %s\nEndpoint: %s\nRunning: %t\nReadiness: %s\nDraining: %s\nActive or cleanup-pending leases: %s\n", status.ContainerID, status.Endpoint, status.Running, ready, draining, leases)
}

// Status reporting summarizes one real readiness check without reinstalling or
// changing the status command's stdout or exit behavior.
func reportPluginStatus(reporter *installprogress.Reporter, status *sandboxplugin.Status, err error, ctx context.Context, elapsed time.Duration) {
	event := sandboxplugin.ProgressEvent{Stage: "readiness", State: "done", Detail: "not_ready", Elapsed: elapsed}
	if err != nil {
		event.State, event.Detail = "failed", ""
		if ctx.Err() == context.DeadlineExceeded {
			event.Detail = "timeout"
		} else if ctx.Err() == context.Canceled {
			event.Detail = "canceled"
		}
	} else if status == nil || !status.Installed {
		event.State, event.Detail = "failed", "not_installed"
	} else if status.Health != nil && status.Health.Ready != nil && *status.Health.Ready {
		event.Detail = "ready"
	}
	reporter.Observe(event)
}
