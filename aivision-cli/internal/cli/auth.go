package cli

import (
	"context"
	"flag"
	"fmt"
	"os"
	"runtime"
	"strings"

	"github.com/antgroup/ai-vision/aivision-cli/internal/account"
	"github.com/antgroup/ai-vision/aivision-cli/internal/auth"
	"github.com/antgroup/ai-vision/aivision-cli/internal/config"
	"github.com/antgroup/ai-vision/aivision-cli/internal/runner"
)

// cmdAuth keeps account login and legacy SpaceKey import as distinct modes.
func cmdAuth(args []string) error {
	if len(args) == 0 {
		fmt.Fprint(os.Stderr, authUsage)
		return fmt.Errorf("auth: missing sub-command (register|login|status|print-env|logout)")
	}
	sub, rest := args[0], args[1:]
	switch sub {
	case "register":
		return authAccountLogin(rest, true)
	case "login":
		return authLogin(rest)
	case "status":
		return authStatus(rest)
	case "print-env":
		return authPrintEnv(rest)
	case "logout":
		return authLogout(rest)
	case "help", "-h", "--help":
		fmt.Print(authUsage)
		return nil
	default:
		fmt.Fprint(os.Stderr, authUsage)
		return fmt.Errorf("auth: unknown sub-command %q", sub)
	}
}

func authLogin(args []string) error {
	keyMode, accountMode, spaceFlag := false, false, false
	for _, arg := range args {
		flagName, _, _ := strings.Cut(arg, "=")
		switch flagName {
		case "--space-key":
			keyMode = true
		case "--email", "--password-stdin", "--name":
			accountMode = true
		case "--space":
			spaceFlag = true
		}
	}
	if keyMode && accountMode {
		return fmt.Errorf("account login and SpaceKey import flags cannot be combined")
	}
	if keyMode || (!accountMode && spaceFlag && os.Getenv(config.EnvSpaceKey) != "") {
		return authSpaceKeyLogin(args)
	}
	return authAccountLogin(args, false)
}

func authAccountLogin(args []string, register bool) error {
	fs := flag.NewFlagSet("auth login/register", flag.ContinueOnError)
	fs.Usage = func() { fmt.Fprint(fs.Output(), authUsage) }
	endpoint := fs.String("endpoint", "", "AI Vision platform URL")
	email := fs.String("email", "", "account email")
	name := fs.String("name", "", "display name (registration)")
	profile := fs.String("profile", os.Getenv(config.EnvProfile), "profile name")
	stdin := fs.Bool("password-stdin", false, "read one password line from stdin")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if fs.NArg() != 0 {
		return fmt.Errorf("unexpected arguments: %v", fs.Args())
	}
	ep := firstNonEmpty(*endpoint, storedEndpoint(*profile), os.Getenv(config.EnvEndpoint))
	if ep == "" || *email == "" {
		return fmt.Errorf("--endpoint and --email are required for account login/registration")
	}
	if register && *name == "" {
		if *stdin || !auth.Interactive() {
			return fmt.Errorf("--name is required for non-interactive registration")
		}
		var err error
		*name, err = auth.PromptLine("Full name")
		if err != nil {
			return err
		}
	}
	var password string
	var err error
	if *stdin {
		password, err = auth.ReadSecretStdin()
	} else {
		password, err = auth.PromptSecret("Password")
	}
	if err != nil {
		return err
	}
	if password == "" {
		return fmt.Errorf("password is required (use --password-stdin for non-interactive login)")
	}
	c, p, err := account.Login(context.Background(), *profile, ep, *email, *name, password, register)
	if err != nil {
		return err
	}
	if err := syncEnv(); err != nil {
		return err
	}
	fmt.Printf("Logged in as %s (profile %s)\nCurrent space: %s\n", p.Account.Email, c.Profile, p.SpaceID)
	if runtime.GOOS == "windows" {
		fmt.Println("Configure a query key: aiv key create --name cli or aiv key import --key-stdin")
	} else {
		fmt.Println("Connect an Agent: aiv connect <agent>; configure a query key: aiv key create --name cli or aiv key import --key-stdin")
	}
	return nil
}

func authSpaceKeyLogin(args []string) error {
	fs := flag.NewFlagSet("auth login", flag.ContinueOnError)
	fs.Usage = func() { fmt.Fprint(fs.Output(), authUsage) }
	endpoint := fs.String("endpoint", "", "AI Vision base URL (default: $"+config.EnvEndpoint+" or active profile)")
	spaceKey := fs.String("space-key", "", "aivision SpaceKey, sk-aiv-… (default: $"+config.EnvSpaceKey+" or secure prompt)")
	space := fs.String("space", "", "space id the key was issued for (default: $"+config.EnvSpaceID+" or prompt)")
	product := fs.String("agent-product", "", "default agent product for queries (default: $"+config.EnvAgentProduct+")")
	profile := fs.String("profile", os.Getenv(config.EnvProfile), "profile name to write (default: active)")
	noVerify := fs.Bool("no-verify", false, "skip the online key↔space check (store the key as-is)")
	noEnvFile := fs.Bool("no-env-file", false, "do not write ~/.aivision/env.sh")
	noShell := fs.Bool("no-shell", false, "do not install the managed source block into the shell rc")
	verbose := fs.Bool("v", false, "verbose: print verification detail")
	if err := fs.Parse(args); err != nil {
		return err
	}
	log := runner.NewLogger(*verbose)

	ep := firstNonEmpty(*endpoint, os.Getenv(config.EnvEndpoint), storedEndpoint(*profile))
	if ep == "" {
		return fmt.Errorf("auth login: --endpoint is required (or set %s)", config.EnvEndpoint)
	}
	sk := firstNonEmpty(*spaceKey, os.Getenv(config.EnvSpaceKey))
	if sk == "" {
		var err error
		if sk, err = auth.PromptSecret("SpaceKey"); err != nil || sk == "" {
			return fmt.Errorf("auth login: a space key is required")
		}
	}
	// Fail fast on an obviously wrong key shape before any network call.
	if err := config.ValidateSpaceKey(sk); err != nil {
		return err
	}
	sp := firstNonEmpty(*space, os.Getenv(config.EnvSpaceID))
	if sp == "" {
		var err error
		if sp, err = auth.PromptLine("Space id"); err != nil || sp == "" {
			return fmt.Errorf("auth login: --space is required (or set %s)", config.EnvSpaceID)
		}
	}
	ap := firstNonEmpty(*product, os.Getenv(config.EnvAgentProduct))

	logf := func(f string, a ...any) { log.Debug(f, a...) }
	if *verbose {
		logf = func(f string, a ...any) { log.Info(f, a...) }
	}
	log.Step("auth: importing SpaceKey for %s (space %s)", ep, sp)
	res, err := auth.Login(context.Background(), auth.LoginParams{
		Endpoint:     ep,
		SpaceKey:     sk,
		SpaceID:      sp,
		AgentProduct: ap,
		Profile:      *profile,
		Verify:       !*noVerify,
		WriteEnvFile: runtime.GOOS != "windows" && !*noEnvFile,
		InstallShell: runtime.GOOS != "windows" && !*noShell,
		Log:          logf,
	})
	if err != nil {
		return err
	}
	if res.Verified {
		log.OK("SpaceKey verified: endpoint=%s space=%s profile=%s key=%s",
			res.Saved.Endpoint, res.Saved.SpaceID, res.Profile, res.Saved.MaskedKey())
		if len(res.Agents) > 0 {
			names := make([]string, 0, len(res.Agents))
			for _, a := range res.Agents {
				names = append(names, a.AgentProduct)
			}
			log.Info("Available Agent products: %v", names)
		}
	} else {
		log.OK("SpaceKey saved without server verification: endpoint=%s space=%s profile=%s key=%s",
			res.Saved.Endpoint, res.Saved.SpaceID, res.Profile, res.Saved.MaskedKey())
	}
	log.Info("config: %s", config.Path())
	if res.EnvFile != "" {
		log.Info("apply to this shell:  source %s   (or: eval \"$(aiv auth print-env)\")", res.EnvFile)
	}
	if res.ShellRC != "" {
		log.Info("Added a managed source block to %s (loaded in new shells; removed on logout)", res.ShellRC)
	}
	if w := config.CheckPerms(); w != "" {
		log.Warn("%s", w)
	}
	return nil
}

func authStatus(args []string) error {
	fs := flag.NewFlagSet("auth status", flag.ContinueOnError)
	fs.Usage = func() { fmt.Fprint(fs.Output(), authUsage) }
	profile := fs.String("profile", os.Getenv(config.EnvProfile), "profile to inspect (default: active)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	cfg, err := config.Load()
	if err != nil {
		return err
	}
	name := firstNonEmpty(*profile, cfg.Active())
	fmt.Printf("config:       %s\n", config.Path())
	fmt.Printf("active:       %s\n", cfg.Active())
	p, ok := cfg.Get(name)
	if !ok {
		fmt.Printf("profile:      %s (not found — run: aiv auth login)\n", name)
		return fmt.Errorf("profile %q not found", name)
	}
	if p.Account != nil {
		c, _, err := account.Open(name)
		if err != nil {
			return err
		}
		var result struct {
			User config.Account `json:"user"`
		}
		if err := c.Do(context.Background(), "GET", "/api/v1/auth/me", nil, &result); err != nil {
			return err
		}
		if result.User.ID != p.Account.ID {
			return fmt.Errorf("server session identity differs from saved account; log in again")
		}
		fmt.Printf("account:      %s (server session valid)\n", p.Account.Email)
	} else if _, err := auth.VerifySpaceKey(context.Background(), p.Endpoint, p.SpaceKey, p.SpaceID); err != nil {
		return err
	}
	fmt.Printf("profile:      %s\n", name)
	fmt.Printf("endpoint:     %s\n", valueOrDash(p.Endpoint))
	fmt.Printf("spaceId:      %s\n", valueOrDash(p.SpaceID))
	fmt.Printf("agentProduct: %s\n", valueOrDash(p.AgentProduct))
	fmt.Printf("spaceKey:     %s\n", p.MaskedKey())
	fmt.Printf("updated:      %s\n", valueOrDash(p.UpdatedAt))
	if w := config.CheckPerms(); w != "" {
		fmt.Printf("warning:      %s\n", w)
	}
	return nil
}

// authPrintEnv writes export lines to stdout so `eval "$(aiv auth
// print-env)"` can apply the active profile to the current shell.
func authPrintEnv(args []string) error {
	fs := flag.NewFlagSet("auth print-env", flag.ContinueOnError)
	fs.Usage = func() { fmt.Fprint(fs.Output(), authUsage) }
	profile := fs.String("profile", os.Getenv(config.EnvProfile), "profile to emit (default: active)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	cfg, err := config.Load()
	if err != nil {
		return err
	}
	name := firstNonEmpty(*profile, cfg.Active())
	p, ok := cfg.Get(name)
	if !ok {
		return fmt.Errorf("profile %q not found (run: aiv auth login)", name)
	}
	for _, line := range p.EnvExports(name) {
		fmt.Println(line)
	}
	return nil
}

func authLogout(args []string) error {
	fs := flag.NewFlagSet("auth logout", flag.ContinueOnError)
	fs.Usage = func() { fmt.Fprint(fs.Output(), authUsage) }
	profile := fs.String("profile", os.Getenv(config.EnvProfile), "profile to remove (default: active)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	log := runner.NewLogger(false)
	cfg, err := config.Load()
	if err != nil {
		return err
	}
	name := firstNonEmpty(*profile, os.Getenv(config.EnvProfile), cfg.Active())
	if p, ok := cfg.Get(name); ok && p.Account != nil {
		c, _, err := account.Open(name)
		if err != nil {
			return err
		}
		if err := c.Logout(context.Background()); err != nil {
			return err
		}
		if err := syncEnv(); err != nil {
			return err
		}
		log.OK("Account session revoked and profile %q removed; installed Agent ingest credentials remain valid", name)
		return nil
	}
	name, rc, err := auth.Logout(name)
	if err != nil {
		return err
	}
	if runtime.GOOS == "windows" {
		log.OK("Removed profile %q", name)
	} else {
		log.OK("Removed profile %q and %s", name, config.EnvFilePath())
	}
	if rc != "" {
		log.Info("Removed the managed source block from %s", rc)
	}
	return nil
}

// Refresh an existing shell export file. CLI commands resolve the profile on
// every invocation, so a parent shell's stale exports cannot select another key.
func syncEnv() error {
	if runtime.GOOS == "windows" {
		return nil
	}
	return config.Locked(context.Background(), func(f *config.File) error {
		if _, err := os.Stat(config.EnvFilePath()); os.IsNotExist(err) {
			return nil
		}
		p, ok := f.Get("")
		if !ok {
			config.RemoveEnvFile()
			_, err := config.RemoveShellBlock()
			return err
		}
		return config.WriteEnvFile(f.Active(), p)
	})
}

// storedEndpoint returns the endpoint saved in a profile ("" when unset).
func storedEndpoint(name string) string {
	cfg, err := config.Load()
	if err != nil {
		return ""
	}
	if p, ok := cfg.Get(name); ok {
		return p.Endpoint
	}
	return ""
}

func valueOrDash(s string) string {
	if s == "" {
		return "-"
	}
	return s
}

func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if v != "" {
			return v
		}
	}
	return ""
}

const authUsage = `Manage account sessions and query-only profiles.

Usage:
  aiv auth register --endpoint URL --email EMAIL [--name NAME] [--password-stdin] [--profile NAME]
  aiv auth login --endpoint URL --email EMAIL [--password-stdin] [--profile NAME]
  aiv auth status [--profile NAME]
  aiv auth print-env [--profile NAME]
  aiv auth logout [--profile NAME]

Commands:
  register   Create an account, log in and select its personal space.
             Requires registration to be enabled on the deployment.
  login      Log in and restore the selected space if it is still accessible;
             otherwise select the personal space.
  status     Verify the account session with the server (or verify a query-only
             profile's SpaceKey) and show the endpoint, space and masked key.
  print-env  Print shell exports, including the query key, for tools that need
             environment variables: eval "$(aiv auth print-env)"
  logout     Revoke the account session and clear the corresponding local profile.
             Installed Agent reporting credentials remain valid. Server-side query
             keys remain valid until revoked with aiv key revoke KEY_ID.

Account options:
  --endpoint URL    Reachable AI Vision platform URL; saved profile or
                    $AIVISION_ENDPOINT may supply it on later logins.
  --email EMAIL     Required account email.
  --name NAME       Display name for registration; prompted for if omitted.
  --password-stdin  Read one password line from stdin instead of a hidden prompt.
                    Non-interactive registration also requires --name.
  --profile NAME    Select a profile (default: $AIVISION_PROFILE or active profile).
                    Use distinct profiles for different accounts or deployments.

Legacy query-only login:
  aiv auth login --endpoint URL --space-key sk-aiv-... --space SPACE_ID
                 [--agent-product PRODUCT] [--profile NAME]
                 [--no-verify] [--no-env-file] [--no-shell] [-v]
  --no-verify    Skip online key verification.
  --no-env-file  Skip writing ~/.aivision/env.sh.
  --no-shell     Skip adding the managed source block to the shell configuration.
  -v            Show verification details.

Account sessions manage spaces and Agents; SpaceKeys authorize queries only.
Passwords are never saved. Account login and SpaceKey import are separate modes.
With an account session, use aiv key create/import to save query credentials for
the selected space. A SpaceKey alone cannot register Agents or manage spaces.

Examples:
  aiv auth register --endpoint https://vision.example.com --email you@example.com
  aiv auth login --endpoint https://vision.example.com --email you@example.com --profile work
  aiv auth login --endpoint https://vision.example.com --email you@example.com --password-stdin < password.txt
  aiv auth status --profile work
  aiv space help
`
