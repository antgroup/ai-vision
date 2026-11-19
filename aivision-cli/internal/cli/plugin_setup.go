//go:build !windows

package cli

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/config"
	"github.com/antgroup/ai-vision/aivision-cli/internal/installprogress"
	"github.com/antgroup/ai-vision/aivision-cli/internal/sandboxbootstrap"
	"github.com/antgroup/ai-vision/aivision-cli/internal/sandboxplugin"
)

func cmdPluginSetup(args []string) error {
	flags := flag.NewFlagSet("plugin setup", flag.ContinueOnError)
	sessionURL := flags.String("session-url", "", "one-time AI Vision bootstrap session URL")
	bootstrapToken := flags.String("bootstrap-token", "", "short-lived bootstrap plan capability")
	progressToken := flags.String("progress-token", "", "short-lived progress reporting capability")
	enrollmentToken := flags.String("enrollment-token", "", "short-lived Space enrollment capability")
	timeout := flags.Duration("timeout", 30*time.Minute, "total setup timeout including pinned image acquisition")
	named, positionals := splitInterspersedFlags(flags, args)
	if err := flags.Parse(named); err != nil {
		return err
	}
	if len(positionals) != 1 || positionals[0] != "openshell" {
		return fmt.Errorf("plugin setup requires the supported provider name openshell")
	}
	if *sessionURL == "" || *bootstrapToken == "" || *progressToken == "" || *enrollmentToken == "" {
		return fmt.Errorf("--session-url, --bootstrap-token, --progress-token and --enrollment-token are required")
	}
	if *timeout <= 0 {
		return fmt.Errorf("--timeout must be positive")
	}
	client, err := sandboxbootstrap.NewClient(*sessionURL, *bootstrapToken, *enrollmentToken)
	if err != nil {
		return err
	}
	reporter, err := installprogress.New(*sessionURL+"/events", *progressToken, "bootstrap", os.Stderr)
	if err != nil {
		return err
	}
	defer reporter.Close()
	signalCtx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	ctx, cancel := context.WithTimeout(signalCtx, *timeout)
	defer cancel()
	return sandboxbootstrap.Setup(ctx, client, reporter, sandboxplugin.NativeDocker{}, config.Dir(), os.Stderr)
}
