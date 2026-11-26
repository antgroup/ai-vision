//go:build !windows || (amd64 && windows_codex_connect && !native_distribution)

package cli

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/antgroup/ai-vision/aivision-cli/internal/distribution"
	"io"
	"net/http"
	"net/url"
	"os"
	"runtime"
	"strings"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/account"
	"github.com/antgroup/ai-vision/aivision-cli/internal/config"
	"github.com/antgroup/ai-vision/aivision-cli/internal/runner"
)

// connect.go implements `aiv connect`: a thin, offline-safe wrapper around
// the server-hosted onboarding installer. The heavy per-agent plugin install
// logic lives in the backend's `connect` script (single source of truth); this
// command only assembles the arguments (from flags or the active auth profile),
// downloads a native bundle or a source-development installer and executes it.
//
// It replaces the previous `curl | bash install.sh` one-liner with a discoverable
// command the Agent market shows for the user to copy and run in their terminal.

// pluginAliases normalizes the short aliases the market/install.sh accept to the
// canonical plugin keys in agent-plugins/agents.json. connect.py applies the
// same map, so this is a convenience for validation/messaging only; the raw
// token is still forwarded to the script unchanged.
var pluginAliases = map[string]string{
	"cc": "claude-code", "claude": "claude-code",
	"cdx": "codex", "km": "kimi", "kimi-cli": "kimi",
	"ocd": "opencode", "ocw": "openclaw", "ds": "dsh", "deepseek": "dsh",
}

// cmdConnect runs `aiv connect [plugin] [flags]`: it follows the same
// convention as the other commands (stdlib flag, returns error, Run() maps to an
// exit code).
func cmdConnect(args []string) error {
	if len(args) > 0 {
		switch args[0] {
		case "help", "-h", "--help":
			fmt.Print(connectHelp())
			return nil
		}
	}
	return connectRun(args)
}

// connectOpts is the resolved argument set for `aiv connect`.
type connectOpts struct {
	plugin         string
	base           string
	spaceID        string
	agentUUID      string
	token          string
	configDir      string
	profiles       string
	python         string
	profile        string
	collectionMode string
	dryRun         bool
	verbose        bool
}

// connectValueFlags are the flags that consume the following token as a value.
var connectValueFlags = map[string]struct{}{
	"--base-url": {}, "--space-id": {}, "--agent-uuid": {},
	"--installation-token": {}, "--config-dir": {}, "--profiles": {},
	"--python": {}, "--profile": {}, "--collection-mode": {},
}

// errConnectHelp signals that help was requested anywhere in the argv.
var errConnectHelp = errors.New("connect: help requested")

// parseConnectArgs scans argv by hand (like the previous install.sh) so the
// plugin and flags may appear in any order — something the stdlib flag package
// cannot do, since it stops at the first positional argument.
func parseConnectArgs(args []string) (connectOpts, error) {
	var o connectOpts
	for i := 0; i < len(args); i++ {
		a := args[i]
		// --flag=value form for value flags.
		name, val, hasEq := strings.Cut(a, "=")
		if _, isVal := connectValueFlags[name]; hasEq && isVal {
			switch name {
			case "--collection-mode":
				o.collectionMode = strings.ToLower(strings.TrimSpace(val))
				if o.collectionMode != "full" && o.collectionMode != "privacy" {
					return o, fmt.Errorf("--collection-mode must be full or privacy")
				}
			case "--base-url":
				o.base = val
			case "--space-id":
				o.spaceID = val
			case "--agent-uuid":
				o.agentUUID = val
			case "--installation-token":
				o.token = val
			case "--config-dir":
				o.configDir = val
			case "--profiles":
				o.profiles = val
			case "--python":
				o.python = val
			case "--profile":
				o.profile = val
			}
			continue
		}
		switch a {
		case "-h", "--help", "help":
			return o, errConnectHelp
		case "--dry-run":
			o.dryRun = true
		case "-v", "--verbose":
			o.verbose = true
		case "--base-url", "--space-id", "--agent-uuid", "--installation-token",
			"--config-dir", "--profiles", "--python", "--profile", "--collection-mode":
			if i+1 >= len(args) {
				return o, fmt.Errorf("connect: %s requires a value", a)
			}
			i++
			v := args[i]
			switch a {
			case "--collection-mode":
				o.collectionMode = strings.ToLower(strings.TrimSpace(v))
				if o.collectionMode != "full" && o.collectionMode != "privacy" {
					return o, fmt.Errorf("--collection-mode must be full or privacy")
				}
			case "--base-url":
				o.base = v
			case "--space-id":
				o.spaceID = v
			case "--agent-uuid":
				o.agentUUID = v
			case "--installation-token":
				o.token = v
			case "--config-dir":
				o.configDir = v
			case "--profiles":
				o.profiles = v
			case "--python":
				o.python = v
			case "--profile":
				o.profile = v
			}
		default:
			switch {
			case a == "plugin": // tolerate the legacy `... plugin install <agent>` tail
				if i+1 < len(args) && args[i+1] == "install" {
					i++
				}
			case strings.HasPrefix(a, "-"):
				return o, fmt.Errorf("connect: unknown flag %q", a)
			case o.plugin == "":
				o.plugin = a
			default:
				return o, fmt.Errorf("connect: unexpected argument %q", a)
			}
		}
	}
	return o, nil
}

// connectRun resolves the plugin + connection coordinates, fetches the server's
// installer and executes the native entry point (or legacy source interpreter).
func connectRun(args []string) error {
	opts, err := parseConnectArgs(args)
	if errors.Is(err, errConnectHelp) {
		fmt.Print(connectHelp())
		return nil
	}
	if err != nil {
		return err
	}
	if runtime.GOOS == "windows" && opts.plugin != "" && opts.plugin != "codex" && pluginAliases[opts.plugin] != "codex" {
		return fmt.Errorf("experimental Windows source onboarding supports only Codex; use aiv connect codex")
	}
	log := runner.NewLogger(opts.verbose)
	opts.profile = firstNonEmpty(opts.profile, os.Getenv(config.EnvProfile))
	explicit := opts.agentUUID != "" || opts.token != ""
	if explicit && (opts.agentUUID == "" || opts.token == "") {
		return fmt.Errorf("explicit connect requires both --agent-uuid and --installation-token; omit both for account onboarding")
	}
	var client *account.Client
	var selected config.Profile
	if !explicit {
		client, selected, err = account.Open(opts.profile)
		if err != nil {
			return err
		}
		if selected.SpaceID == "" {
			return fmt.Errorf("select a space first: aiv space use SPACE_ID")
		}
		if (opts.base != "" && strings.TrimRight(opts.base, "/") != selected.Endpoint) || (opts.spaceID != "" && opts.spaceID != selected.SpaceID) {
			return fmt.Errorf("automatic connect uses the account's current space; use aiv space use or --profile first")
		}
		opts.base, opts.spaceID = selected.Endpoint, selected.SpaceID
	}

	// Fall back to the active auth profile / env for the non-secret coordinates.
	if opts.base == "" || opts.spaceID == "" {
		ep, sp := profileCoords(opts.profile)
		opts.base = firstNonEmpty(opts.base, ep)
		opts.spaceID = firstNonEmpty(opts.spaceID, sp)
	}
	opts.base = firstNonEmpty(opts.base, os.Getenv(config.EnvEndpoint))
	opts.spaceID = firstNonEmpty(opts.spaceID, os.Getenv(config.EnvSpaceID))

	if opts.plugin == "" {
		fmt.Fprint(os.Stderr, connectHelp())
		return fmt.Errorf("connect: an Agent plugin is required (e.g. codex, claude-code, kimi)")
	}
	if canonical := pluginAliases[opts.plugin]; canonical != "" {
		log.Debug("plugin %q → %s", opts.plugin, canonical)
	}
	if err := validateBaseURL(opts.base); err != nil {
		return err
	}

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
	defer cancel()

	script, cleanup, err := fetchConnectScript(ctx, opts.base, log)
	if err != nil {
		return err
	}
	defer cleanup()

	var preflight struct {
		Agent                string `json:"agent"`
		Product              string `json:"agent_product"`
		ConfigDir            string `json:"config_dir"`
		Ready                bool   `json:"ready"`
		CollectionMode       string `json:"collection_mode"`
		CollectionModeSource string `json:"collection_mode_source"`
		EnvironmentOverride  *struct {
			Mode   string `json:"mode"`
			Source string `json:"source"`
		} `json:"collection_mode_environment"`
	}
	{
		checkArgs := []string{"--agent", opts.plugin, "--base-url", opts.base, "--dry-run", "--format", "json"}
		checkArgs = append(checkArgs, passThrough("--config-dir", opts.configDir)...)
		checkArgs = append(checkArgs, passThrough("--profiles", opts.profiles)...)
		checkArgs = append(checkArgs, passThrough("--collection-mode", opts.collectionMode)...)
		check, err := connectCommand(ctx, script, opts.python, checkArgs...)
		if err != nil {
			return err
		}
		check.Stderr = os.Stderr
		out, err := check.Output()
		if err != nil {
			return connectPreflightError(script, err)
		}
		if json.Unmarshal(out, &preflight) != nil || !preflight.Ready || preflight.Product == "" || preflight.ConfigDir == "" || (preflight.CollectionMode != "full" && preflight.CollectionMode != "privacy") {
			return fmt.Errorf("installer returned no structured preflight result; update the deployment image")
		}
		if opts.collectionMode != "" && preflight.CollectionMode != opts.collectionMode {
			return fmt.Errorf("installer did not honor the requested collection mode; update the deployment image")
		}
		if opts.dryRun {
			fmt.Printf("Preflight checks passed: %s (%s), config directory %s; no Agent registration, installation grant, or configuration changes\n", preflight.Agent, preflight.Product, preflight.ConfigDir)
			fmt.Printf("Collection mode: %s (source: %s)\n", valueOrDash(preflight.CollectionMode), valueOrDash(preflight.CollectionModeSource))
			if override := preflight.EnvironmentOverride; override != nil {
				fmt.Printf("Current process environment override: %s (%s); saved configuration is unchanged\n", override.Mode, override.Source)
			}
			return nil
		}
	}
	if !explicit {
		var agent struct {
			UUID    string `json:"agent_uuid"`
			Product string `json:"agent_product"`
		}
		spacePath := "/api/v1/spaces/" + url.PathEscape(selected.SpaceID) + "/agents"
		if err := client.Do(ctx, "POST", spacePath+"/create", map[string]string{"agent_product": preflight.Product}, &agent); err != nil {
			return err
		}
		if agent.UUID == "" || agent.Product != preflight.Product {
			return fmt.Errorf("server returned an unexpected Agent identity")
		}
		var grant struct {
			BaseURL   string `json:"base_url"`
			Token     string `json:"installation_token"`
			AgentUUID string `json:"agent_uuid"`
			SpaceID   string `json:"space_id"`
		}
		if err := client.Do(ctx, "POST", spacePath+"/"+url.PathEscape(agent.UUID)+"/installation-tokens", map[string]any{}, &grant); err != nil {
			return err
		}
		if grant.AgentUUID != agent.UUID || grant.SpaceID != selected.SpaceID || !strings.HasPrefix(grant.Token, "aiv-install-") {
			return fmt.Errorf("installation authorization does not match the selected Agent and space")
		}
		canonical, err := account.NormalizeEndpoint(grant.BaseURL)
		if err != nil {
			return fmt.Errorf("server returned an invalid installation base_url: %w", err)
		}
		if canonical != opts.base {
			var cleanupCanonical func()
			script, cleanupCanonical, err = fetchConnectScript(ctx, canonical, log)
			if err != nil {
				return err
			}
			defer cleanupCanonical()
		}
		opts.base, opts.agentUUID, opts.token = canonical, agent.UUID, grant.Token
		// Keep the directory resolved by Python, including host-specific env overrides.
		opts.configDir = preflight.ConfigDir
	}

	// Forward the raw plugin token; connect.py normalizes aliases itself.
	cmdArgs := append([]string{"--agent", opts.plugin, "--base-url", opts.base},
		passThrough("--space-id", opts.spaceID)...)
	cmdArgs = append(cmdArgs, passThrough("--agent-uuid", opts.agentUUID)...)
	cmdArgs = append(cmdArgs, passThrough("--installation-token", opts.token)...)
	cmdArgs = append(cmdArgs, passThrough("--config-dir", opts.configDir)...)
	cmdArgs = append(cmdArgs, passThrough("--profiles", opts.profiles)...)
	cmdArgs = append(cmdArgs, passThrough("--collection-mode", opts.collectionMode)...)
	if opts.dryRun {
		cmdArgs = append(cmdArgs, "--dry-run")
	}

	log.Step("connect: installing %s from %s", opts.plugin, opts.base)
	cmd, err := connectCommand(ctx, script, opts.python, cmdArgs...)
	if err != nil {
		return err
	}
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("connect: installation did not complete (%w); check the Agent installer output above", err)
	}
	if !explicit {
		if err := client.Update(ctx, func(p *config.Profile) error {
			s := p.Spaces[selected.SpaceID]
			if s.Agents == nil {
				s.Agents = map[string]config.AgentContext{}
			}
			s.Agents[preflight.Agent] = config.AgentContext{UUID: opts.agentUUID, ConfigDir: opts.configDir, BaseURL: opts.base}
			s.AgentProduct = preflight.Product
			p.SetSpaceContext(selected.SpaceID, s)
			return nil
		}); err != nil {
			return fmt.Errorf("installation succeeded, but saving query context failed: %w", err)
		}
		if err := syncEnv(); err != nil {
			return err
		}
	}
	log.OK("Connected %s", opts.plugin)
	return nil
}

// passThrough renders "--flag value" as argv pieces when value is non-empty, so
// optional flags are simply omitted rather than passed empty.
func passThrough(flagName, value string) []string {
	if value == "" {
		return nil
	}
	return []string{flagName, value}
}

// profileCoords returns the endpoint and space id stored for a profile ("" name
// resolves to the active profile).
func profileCoords(name string) (endpoint, spaceID string) {
	cfg, err := config.Load()
	if err != nil {
		return "", ""
	}
	if p, ok := cfg.Get(name); ok {
		return p.Endpoint, p.SpaceID
	}
	return "", ""
}

// validateBaseURL rejects obviously unusable bases before any network call.
func validateBaseURL(raw string) error {
	if raw == "" {
		return fmt.Errorf("connect: --base-url is required (or run `aiv auth login`, or set %s)", config.EnvEndpoint)
	}
	u, err := url.Parse(raw)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
		return fmt.Errorf("connect: --base-url must be an absolute http(s) URL (got %q)", raw)
	}
	return nil
}

// fetchConnectScript downloads the server's connect installer to a temp file and
// returns its path plus a cleanup func. Mirrors what install.sh does.
func fetchConnectScript(ctx context.Context, base string, log *runner.Logger) (string, func(), error) {
	endpoint := strings.TrimRight(base, "/") + "/api/public/onboarding/scripts/connect"
	platform, arch := runtime.GOOS, runtime.GOARCH
	if platform == "linux" {
		platform = "linux-gnu"
	}
	if arch == "amd64" {
		arch = "x64"
	}
	endpoint += "?" + url.Values{"platform": {platform}, "arch": {arch}}.Encode()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return "", nil, err
	}
	resp, err := (&http.Client{Timeout: 60 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}).Do(req)
	if err != nil {
		return "", nil, fmt.Errorf("connect: could not download installer from %s: %w", endpoint, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		if runtime.GOOS == "windows" {
			return "", nil, fmt.Errorf("connect: source-development installer request returned %s (%s)", resp.Status, endpoint)
		}
		return "", nil, fmt.Errorf("connect: installer endpoint returned %s (%s)", resp.Status, endpoint)
	}
	format := resp.Header.Get("X-AIV-Installer-Format")
	if runtime.GOOS == "windows" && format != "" {
		return "", nil, fmt.Errorf("experimental Windows Codex onboarding requires a source-development installer; got %q", format)
	}
	if distribution.NativeOnly && format != "native-tar-v1" {
		return "", nil, fmt.Errorf("connect: native distribution requires a native-tar-v1 installer; source installers are unsupported")
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, maxNativeConnectDownload+1))
	if err != nil {
		return "", nil, fmt.Errorf("connect: could not read installer: %w", err)
	}
	if len(body) > maxNativeConnectDownload {
		return "", nil, fmt.Errorf("connect: installer exceeds download size limit")
	}
	if format == "native-tar-v1" {
		sum := sha256.Sum256(body)
		if resp.Header.Get("X-AIV-Installer-SHA256") != fmt.Sprintf("%x", sum) {
			return "", nil, fmt.Errorf("connect: native installer SHA-256 mismatch")
		}
		return unpackNativeConnect(body)
	}
	if format != "" {
		return "", nil, fmt.Errorf("connect: unsupported installer format %q; update aiv", format)
	}
	if len(body) > 8<<20 {
		return "", nil, fmt.Errorf("connect: source installer exceeds download size limit")
	}
	f, err := os.CreateTemp("", "aivision-connect-*.py")
	if err != nil {
		return "", nil, err
	}
	path := f.Name()
	cleanup := func() { _ = os.Remove(path) }
	if _, err := f.Write(body); err != nil {
		f.Close()
		cleanup()
		return "", nil, fmt.Errorf("connect: could not write temporary installer: %w", err)
	}
	if err := f.Close(); err != nil {
		cleanup()
		return "", nil, err
	}
	log.Debug("connect: downloaded installer (%d bytes) to %s", len(body), path)
	return path, cleanup, nil
}

func connectHelp() string {
	if runtime.GOOS == "windows" {
		return `Install the Codex plugin on Windows x64 from a source-development deployment (experimental).

Usage:
  aiv connect codex [--profile NAME] [--config-dir DIR]
                    [--collection-mode full|privacy] [--dry-run] [--python PATH] [-v]
  aiv connect codex --base-url URL --space-id SPACE_ID --agent-uuid UUID
                    --installation-token TOKEN [--config-dir DIR]
                    [--collection-mode full|privacy] [--dry-run] [--python PATH] [-v]

Requires native Codex, Python 3.11+, and a deployment serving the Windows x64 Codex hook.
Sign in and select a space for account onboarding. A command generated by the Agent
market uses its installation authorization instead. A dry run performs preflight
checks without registering an Agent or changing its configuration.
`
	}
	return connectUsage
}

const connectUsage = `Install a telemetry hook into a coding Agent.

Usage:
  aiv connect <agent> [--profile NAME] [--config-dir DIR] [--profiles LIST]
                      [--collection-mode full|privacy] [--dry-run] [--python PATH] [-v]

Use the selected account and space to check prerequisites, register or reuse the
server-side Agent, request a one-use installation grant, and run the deployment's
installer. The installer downloads the matching hook and verifies its SHA-256.
A successful connection saves the Agent context for aiv agent status and queries.
No Agent UUID, installation token or query SpaceKey is needed for this flow.
Repeating the command reuses the Agent and avoids duplicate hook registration.
New installations default to Full, with Privacy for MiniMax Code CLI (PoC).
Existing installations retain their mode.
Privacy removes content locally while retaining trace structure, timing and usage.
Restart the host and start a new session after changing modes. Historical data is
unchanged. AIVISION_COLLECTION_MODE in the host environment overrides saved mode.

Options:
  <agent>           claude-code|codex|kimi|opencode|openclaw|pi|dsh|minimax-code
                    Aliases: cc/claude, cdx, km/kimi-cli, ocd, ocw, ds/deepseek.
  --profile NAME    Saved account (default: $AIVISION_PROFILE or active profile).
  --config-dir DIR  Native Agent configuration directory (default: host default).
  --profiles LIST   Comma-separated dsh profiles (default: tui,web; dsh only).
  --collection-mode MODE  full|privacy (case-insensitive).
  --python PATH     Python 3.11+ for source-development installers (auto-detect).
  --dry-run         Check catalog, platform and host Agent; do not create
                    an Agent, issue an installation grant or write native config.
  -v, --verbose     Show download and installer details.

Prerequisites:
  Linux/macOS on x64/arm64 and the target host Agent installed and configured.
  The deployment must contain installer and hook artifacts for your platform.
  Source-development deployments additionally require Python 3.11+ on the host.
  Its platform URL and the reporting gateway URL must be reachable from this host.
  Sign in with aiv auth login or register with aiv auth register. Select a space
  with aiv space use.

Legacy explicit installation (commands from the Agent market still work):
  aiv connect <agent> --base-url URL --space-id SPACE_ID --agent-uuid UUID
                      --installation-token TOKEN [--config-dir DIR] [--dry-run]
  --base-url URL             Platform URL (default: saved profile or $AIVISION_ENDPOINT).
  --space-id SPACE_ID        Space ID (default: saved profile or $AIVISION_SPACE_ID).
  --agent-uuid UUID          Agent already registered in that space.
  --installation-token TOKEN One-use installation authorization.
  Provide both UUID and token for explicit installation; account login is optional.

Examples:
  aiv connect codex --dry-run
  aiv connect codex
  aiv connect codex --collection-mode privacy
  aiv connect codex --collection-mode full
  # Restart Codex, trust the hooks via /hooks, and run a real task.
  aiv key create --name cli
  aiv agent status codex
  aiv query trace list --since 1h --limit 5
`
