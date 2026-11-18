package cli

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/config"
	"github.com/antgroup/ai-vision/aivision-cli/internal/query"
	"github.com/antgroup/ai-vision/aivision-cli/internal/runner"
)

type installationStatus struct {
	Agent                     string   `json:"agent"`
	ConfigDir                 string   `json:"config_dir"`
	Installed                 bool     `json:"installed"`
	Registration              string   `json:"registration"`
	Credential                string   `json:"credential"`
	SpaceID                   string   `json:"space_id"`
	AgentUUID                 string   `json:"agent_uuid"`
	Product                   string   `json:"agent_product"`
	Version                   string   `json:"version"`
	Issues                    []string `json:"issues"`
	Data                      string   `json:"data_status"`
	DataDetail                string   `json:"data_detail"`
	CollectionMode            string   `json:"collection_mode"`
	CollectionModeSource      string   `json:"collection_mode_source"`
	RuntimeCollectionMode     string   `json:"runtime_collection_mode"`
	CollectionModeEnvironment *struct {
		Mode   string `json:"mode"`
		Source string `json:"source"`
	} `json:"collection_mode_environment"`
}

const agentUsage = `Check an Agent's installation, reporting credentials and recent telemetry.

Usage:
  aiv agent status <agent> [--profile NAME] [--config-dir DIR]
                           [--python PATH] [--format text|json]

Checks:
  Mode          Show the saved local collection mode and current process override.
                This does not verify the host environment or a running session.
  Installation  Inspect local plugin files and native hook/plugin registration.
  Credentials   Check the installed reporting credential with the ingest-token API.
  Data          With a query SpaceKey, query real traces for this Agent product in
                the current space over the last hour. No key means unverified.
                No recent traces means no_recent_data; it does not prove failure.
                Traces may come from another machine in the same space.

No simulated telemetry is sent. Failed or unverified installation/credential
checks and query errors return a nonzero exit status. Missing query keys and
no_recent_data alone do not fail the command.

Options:
  <agent>           claude-code|codex|kimi|opencode|openclaw|pi|dsh
  --profile NAME    Saved profile (default: $AIVISION_PROFILE or active profile).
  --config-dir DIR  Native config directory (default: saved connection or host default).
  --python PATH     Python 3.11+ for source-development installers (auto-detect).
  --format FORMAT   text (default) or json, including separate check results.

Requires a saved profile and access to the deployment's installer and credential
APIs. Source-development installers also need Python 3.11+. Run aiv connect
<agent> first to save the connection context.

Examples:
  aiv agent status codex
  aiv agent status codex --format json
  aiv agent status claude-code --config-dir /path/to/agent-config

After connecting, restart the host Agent, trust hooks if required, and run a real
task before checking data. For Codex, use /hooks to review and trust the hooks.
`

func cmdAgent(args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("%s", strings.TrimSpace(agentUsage))
	}
	if args[0] == "help" || args[0] == "--help" || args[0] == "-h" {
		fmt.Print(agentUsage)
		return nil
	}
	if args[0] != "status" {
		return fmt.Errorf("unknown agent command %q", args[0])
	}
	rest := args[1:]
	plugin := ""
	if len(rest) > 0 && !strings.HasPrefix(rest[0], "-") {
		plugin, rest = rest[0], rest[1:]
	}
	fs := flag.NewFlagSet("agent status", flag.ContinueOnError)
	fs.Usage = func() { fmt.Fprint(fs.Output(), agentUsage) }
	profile := fs.String("profile", os.Getenv(config.EnvProfile), "profile")
	directory := fs.String("config-dir", "", "native config directory (default: most recent connection or host default)")
	python := fs.String("python", "", "Python 3.11+ interpreter")
	format := fs.String("format", "text", "text|json")
	if err := fs.Parse(rest); err != nil {
		return err
	}
	if plugin == "" && fs.NArg() == 1 {
		plugin = fs.Arg(0)
	} else if fs.NArg() != 0 {
		return fmt.Errorf("unexpected arguments: %v", fs.Args())
	}
	if plugin == "" {
		return fmt.Errorf("agent status requires an Agent name")
	}
	if *format != "text" && *format != "json" {
		return fmt.Errorf("--format must be text or json")
	}
	if canonical := pluginAliases[plugin]; canonical != "" {
		plugin = canonical
	}
	cfg, err := config.Load()
	if err != nil {
		return err
	}
	p, ok := cfg.Get(*profile)
	if !ok {
		return fmt.Errorf("profile not found; run aiv auth login first")
	}
	connection := p.Spaces[p.SpaceID].Agents[plugin]
	base := firstNonEmpty(connection.BaseURL, p.Endpoint)
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()
	script, cleanup, err := fetchConnectScript(ctx, base, runner.NewLogger(false))
	if err != nil {
		return err
	}
	defer cleanup()
	argv := []string{"--status", "--format", "json", "--agent", plugin, "--base-url", base}
	argv = append(argv, passThrough("--space-id", p.SpaceID)...)
	argv = append(argv, passThrough("--agent-uuid", connection.UUID)...)
	argv = append(argv, passThrough("--config-dir", firstNonEmpty(*directory, connection.ConfigDir))...)
	cmd, err := connectCommand(ctx, script, *python, argv...)
	if err != nil {
		return err
	}
	cmd.Stderr = os.Stderr
	out, err := cmd.Output()
	if err != nil {
		return fmt.Errorf("installer status failed (%w); update the deployment if --status is unsupported", err)
	}
	var result installationStatus
	if json.Unmarshal(out, &result) != nil || result.Agent != plugin || result.Registration == "" || result.Credential == "" {
		return fmt.Errorf("installer returned an invalid status response")
	}
	result.RuntimeCollectionMode = "unverified"
	checkAgentData(ctx, p, &result)
	if *format == "json" {
		b, _ := json.MarshalIndent(result, "", "  ")
		fmt.Println(string(b))
	} else {
		fmt.Printf("Agent: %s\nConfig directory: %s\nInstalled locally: %v (version %s)\nNative registration: %s\nIngest credential: %s\n", result.Agent, result.ConfigDir, result.Installed, valueOrDash(result.Version), result.Registration, result.Credential)
		fmt.Printf("Configured collection mode: %s (source: %s)\n", valueOrDash(result.CollectionMode), valueOrDash(result.CollectionModeSource))
		if override := result.CollectionModeEnvironment; override != nil {
			fmt.Printf("Current process environment override: %s (%s); the host environment may differ\n", override.Mode, override.Source)
		}
		fmt.Println("Runtime collection mode: unverified. After changing mode, restart the host and start a new session; inspect that session's trace to verify.")
		for _, issue := range result.Issues {
			fmt.Printf("Issue: %s\n", issue)
		}
		fmt.Printf("Reported data: %s\n", result.DataDetail)
		if plugin == "codex" {
			fmt.Println("Restart Codex and use /hooks to check the hooks' trust status.")
		}
	}
	if !result.Installed || result.Registration != "verified" || result.Credential != "valid" || result.Data == "error" || result.CollectionMode == "unknown" {
		return fmt.Errorf("Agent checks failed or remain unverified; see the status output")
	}
	return nil
}

func checkAgentData(ctx context.Context, p config.Profile, result *installationStatus) {
	result.Data = "unverified"
	if p.SpaceKey == "" {
		result.DataDetail = "Not verified: no query SpaceKey configured for the current space"
		return
	}
	if result.SpaceID != p.SpaceID || result.Product == "" {
		result.DataDetail = "Not verified: the local installation does not match the current space or Agent context is missing"
		return
	}
	c := query.New(p.Endpoint, p.SpaceKey, p.SpaceID, result.Product)
	now := time.Now()
	raw, err := c.Post(ctx, c.AgentURL("traces/list"), map[string]any{"from": now.Add(-time.Hour).UnixMilli(), "to": now.UnixMilli(), "limit": 1})
	if err != nil {
		result.Data = "error"
		result.DataDetail = "Not verified: query failed: " + err.Error()
		return
	}
	count, err := traceCount(raw)
	if err != nil {
		result.Data = "error"
		result.DataDetail = "Not verified: " + err.Error()
		return
	}
	if count == 0 {
		result.Data = "no_recent_data"
		result.DataDetail = "No traces for this Agent in the current space in the last hour; restart the host Agent, run a real task, and retry"
	} else {
		result.Data = "found"
		result.DataDetail = "Traces found for this Agent in the current space in the last hour; they may come from another machine"
	}
}

func traceCount(raw json.RawMessage) (int, error) {
	var value any
	if err := json.Unmarshal(raw, &value); err != nil {
		return 0, err
	}
	var count func(any) (int, bool)
	count = func(v any) (int, bool) {
		switch x := v.(type) {
		case []any:
			return len(x), true
		case map[string]any:
			for _, key := range []string{"list", "traces", "items", "data", "rows", "results", "records"} {
				if nested, exists := x[key]; exists {
					if n, ok := count(nested); ok {
						return n, true
					}
				}
			}
		}
		return 0, false
	}
	if n, ok := count(value); ok {
		return n, nil
	}
	return 0, fmt.Errorf("trace API returned an unrecognized list structure")
}
