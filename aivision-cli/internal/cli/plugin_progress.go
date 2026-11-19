//go:build !windows

package cli

import (
	"fmt"
	"io"
	"strings"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/sandboxplugin"
)

func printPluginProgress(out io.Writer, event sandboxplugin.ProgressEvent) {
	var step int
	var title, completed string
	switch event.Stage {
	case "configuration":
		step, title, completed = 1, "Checking operator files and permissions", "Operator files and permissions checked; policy validation occurs at plugin startup"
	case "docker":
		step, title, completed = 2, "Checking Docker and existing installation", "Docker and installation ownership checked"
	case "image":
		step, title, completed = 3, "Preparing the plugin image", "Plugin image available"
		if event.Detail == "cached" {
			completed = "Using cached plugin image"
		}
	case "state":
		step, title, completed = 4, "Saving installation state and journal ownership", "Private installation state and journal ownership saved"
	case "container":
		step, title, completed = 5, "Starting the plugin container", "Container started; service readiness is checked next"
	case "readiness":
		step, title, completed = 6, "Checking authenticated service and provider readiness", "Plugin service responded; the provider is not ready"
		if event.Detail == "ready" {
			completed = "Authenticated service check passed; the provider is ready"
		}
	default:
		return
	}
	elapsed := event.Elapsed.Round(100 * time.Millisecond)
	if event.Image != nil && event.Stage == "image" {
		image := event.Image
		// Image fields come only from the bounded, allowlisted Docker pull parser.
		if image.Current != "" && image.Total != "" {
			fmt.Fprintf(out, "[3/6] Layer %s: %s %s / %s (%s elapsed)\n", image.Layer, image.Status, image.Current, image.Total, elapsed)
		} else {
			fmt.Fprintf(out, "[3/6] Layer %s: %s (%s elapsed)\n", image.Layer, image.Status, elapsed)
		}
		return
	}
	switch event.State {
	case "start":
		fmt.Fprintf(out, "[%d/6] %s...\n", step, title)
	case "wait":
		if event.Detail == "pulling" {
			fmt.Fprintln(out, "[3/6] Plugin image is not cached; pulling from the registry...")
		} else {
			fmt.Fprintf(out, "[%d/6] %s: Still waiting (%s elapsed).\n", step, title, elapsed)
		}
	case "done":
		fmt.Fprintf(out, "[%d/6] %s (%s).\n", step, completed, elapsed)
	case "failed":
		outcome := "Failed"
		if event.Detail == "timeout" {
			outcome = "Timed out"
		} else if event.Detail == "canceled" {
			outcome = "Canceled"
		}
		fmt.Fprintf(out, "[%d/6] %s: %s after %s.\n", step, title, outcome, elapsed)
	}
}

func printPluginRecovery(out io.Writer, stateDir string) {
	// Keep ordinary paths readable while preserving literal POSIX shell arguments.
	// A leading '=' also needs quoting because zsh expands it as a command path.
	quoted := "'" + strings.ReplaceAll(stateDir, "'", "'\"'\"'") + "'"
	if stateDir != "" && !strings.HasPrefix(stateDir, "=") && strings.IndexFunc(stateDir, func(char rune) bool {
		return !strings.ContainsRune("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_@%+=:,./-", char)
	}) == -1 {
		quoted = stateDir
	}
	fmt.Fprintf(out, "Inspect this installation (state exists only after the save stage):\n  aiv plugin status openshell --state-dir %s\n  aiv plugin logs openshell --state-dir %s -n 100\nKeep any saved state and journal; do not delete them to bypass ownership or cleanup checks.\n", quoted, quoted)
}
