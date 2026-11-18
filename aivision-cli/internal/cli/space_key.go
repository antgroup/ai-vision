package cli

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"net/url"
	"os"
	"strconv"
	"strings"
	"text/tabwriter"

	"github.com/antgroup/ai-vision/aivision-cli/internal/account"
	"github.com/antgroup/ai-vision/aivision-cli/internal/auth"
	"github.com/antgroup/ai-vision/aivision-cli/internal/config"
)

const spaceUsage = `Manage the current account's spaces.

Usage:
  aiv space list [--profile NAME]
  aiv space use [SPACE_ID] [--profile NAME]
  aiv space create --name NAME [--description TEXT] [--profile NAME]

Commands:
  list    List accessible spaces and mark the current space with *.
  use     Select a space by ID, or list spaces and prompt for an ID.
          In non-interactive use, SPACE_ID is required.
  create  Create a team space and select it immediately.

Sign in with aiv auth login, or create an account with aiv auth register.
--profile NAME selects a saved account (default: $AIVISION_PROFILE or active profile).
Query keys and the last connected Agent are saved separately for each space.
Switching spaces restores that space's context; a new space needs its own query key.

Examples:
  aiv space list
  aiv space use
  aiv space use SPACE_ID --profile work
  aiv space create --name "My team" --description "Shared telemetry"
  aiv key create --name cli
  aiv connect codex
`

const keyUsage = `Manage query SpaceKeys for the selected space.

Usage:
  aiv key create --name NAME [--profile NAME]
  aiv key import [--key-stdin | --space-key KEY] [--profile NAME]
  aiv key list [--profile NAME]
  aiv key revoke KEY_ID [--profile NAME]

Commands:
  create  Create a SpaceKey and immediately save it for this space's queries.
  import  Verify an existing key against the selected space and save it locally.
          With no key option, prompt without echoing the key.
  list    List key IDs, names, masked prefixes and enabled state.
  revoke  Revoke the numeric KEY_ID shown by list; clear the saved key if it
          matches or is rejected after revocation. Other clients using the
          revoked key lose query access too.

Options:
  --name NAME      Required name for a new key.
  --key-stdin      Read one SpaceKey line from stdin (for non-interactive import).
  --space-key KEY  Explicit key to import; prefer --key-stdin to avoid shell history.
  --profile NAME   Saved profile (default: $AIVISION_PROFILE or active profile).

Create/list/revoke require an account session and the server's space permissions.
Import also works with an existing query-only profile. The server does not return
existing key values: import a known key or create a new one. Output masks keys.
These keys authorize queries; connecting an Agent does not require a query key.

Examples:
  aiv key create --name cli
  aiv key import --key-stdin < query-key.txt
  aiv key list
  aiv key revoke 123
  aiv query trace list --since 1h --limit 5
`

func cmdSpace(args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("%s", strings.TrimSpace(spaceUsage))
	}
	if args[0] == "help" || args[0] == "--help" || args[0] == "-h" {
		fmt.Print(spaceUsage)
		return nil
	}
	sub, rest := args[0], args[1:]
	fs := flag.NewFlagSet("space "+sub, flag.ContinueOnError)
	fs.Usage = func() { fmt.Fprint(fs.Output(), spaceUsage) }
	profile := fs.String("profile", os.Getenv(config.EnvProfile), "account profile")
	name := fs.String("name", "", "new space name")
	description := fs.String("description", "", "new space description")
	id := ""
	if sub == "use" && len(rest) > 0 && !strings.HasPrefix(rest[0], "-") {
		id, rest = rest[0], rest[1:]
	}
	if err := fs.Parse(rest); err != nil {
		return err
	}
	if sub == "use" && id == "" && fs.NArg() == 1 {
		id = fs.Arg(0)
	} else if fs.NArg() != 0 {
		return fmt.Errorf("unexpected arguments: %v", fs.Args())
	}
	c, p, err := account.Open(*profile)
	if err != nil {
		return err
	}
	ctx := context.Background()
	if sub == "create" {
		if strings.TrimSpace(*name) == "" {
			return fmt.Errorf("space create: --name is required")
		}
		var space account.Space
		err := c.DoUpdate(ctx, "POST", "/api/v1/spaces", map[string]any{"name": *name, "description": *description, "type": 2}, &space, func(p *config.Profile) error {
			if space.ID == "" {
				return fmt.Errorf("server returned no space id")
			}
			p.SelectSpace(space.ID)
			return nil
		})
		if err != nil {
			return err
		}
		fmt.Printf("Created and selected space %s (%s)\n", space.Name, space.ID)
		return syncEnv()
	}
	if sub != "list" && sub != "use" {
		return fmt.Errorf("unknown space command %q", sub)
	}
	spaces, err := c.Spaces(ctx)
	if err != nil {
		return err
	}
	if sub == "list" || id == "" {
		w := tabwriter.NewWriter(os.Stdout, 0, 4, 2, ' ', 0)
		fmt.Fprintln(w, "CURRENT\tSPACE_ID\tNAME\tTYPE")
		for _, s := range spaces {
			mark := ""
			if s.ID == p.SpaceID {
				mark = "*"
			}
			fmt.Fprintf(w, "%s\t%s\t%s\t%d\n", mark, s.ID, s.Name, s.Type)
		}
		w.Flush()
	}
	if sub == "list" {
		return nil
	}
	if id == "" {
		id, err = auth.PromptLine("Select a space ID")
		if err != nil {
			return fmt.Errorf("space use: pass SPACE_ID in non-interactive mode: %w", err)
		}
	}
	for _, s := range spaces {
		if s.ID == id {
			if err := c.Update(ctx, func(p *config.Profile) error { p.SelectSpace(id); return nil }); err != nil {
				return err
			}
			fmt.Printf("Current space: %s (%s)\n", s.Name, id)
			return syncEnv()
		}
	}
	return fmt.Errorf("space %q is not accessible to this account", id)
}

func cmdKey(args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("%s", strings.TrimSpace(keyUsage))
	}
	if args[0] == "help" || args[0] == "--help" || args[0] == "-h" {
		fmt.Print(keyUsage)
		return nil
	}
	sub, rest := args[0], args[1:]
	fs := flag.NewFlagSet("key "+sub, flag.ContinueOnError)
	fs.Usage = func() { fmt.Fprint(fs.Output(), keyUsage) }
	profile := fs.String("profile", os.Getenv(config.EnvProfile), "profile")
	name := fs.String("name", "", "query key name")
	stdin := fs.Bool("key-stdin", false, "read a SpaceKey line from stdin")
	keyFlag := fs.String("space-key", "", "SpaceKey to import (prefer --key-stdin)")
	id := ""
	if sub == "revoke" && len(rest) > 0 && !strings.HasPrefix(rest[0], "-") {
		id, rest = rest[0], rest[1:]
	}
	if err := fs.Parse(rest); err != nil {
		return err
	}
	if sub == "revoke" && id == "" && fs.NArg() == 1 {
		id = fs.Arg(0)
	} else if fs.NArg() != 0 {
		return fmt.Errorf("unexpected arguments: %v", fs.Args())
	}
	ctx := context.Background()
	// Import also works for existing SpaceKey-only profiles, without upgrading
	// the key into an account session.
	if sub == "import" {
		cfg, err := config.Load()
		if err != nil {
			return err
		}
		profileName := firstNonEmpty(*profile, cfg.Active())
		p, ok := cfg.Get(profileName)
		if !ok || p.SpaceID == "" {
			return fmt.Errorf("select a space first (aiv auth login; aiv space use), or use auth login --space-key … --space …")
		}
		key := *keyFlag
		if *stdin && key != "" {
			return fmt.Errorf("--key-stdin and --space-key cannot be combined")
		}
		if *stdin {
			key, err = auth.ReadSecretStdin()
		} else if key == "" {
			key, err = auth.PromptSecret("SpaceKey")
		}
		if err != nil {
			return err
		}
		key = strings.TrimSpace(key)
		if err := config.ValidateSpaceKey(key); err != nil {
			return err
		}
		if _, err := auth.VerifySpaceKey(ctx, p.Endpoint, key, p.SpaceID); err != nil {
			return err
		}
		err = config.Update(ctx, func(f *config.File) error {
			now, ok := f.Get(profileName)
			if !ok || now.Endpoint != p.Endpoint || accountIdentity(now) != accountIdentity(p) {
				return fmt.Errorf("profile changed; retry import")
			}
			s := now.Spaces[p.SpaceID]
			s.SpaceKey, s.KeyID = key, ""
			now.SetSpaceContext(p.SpaceID, s)
			f.Set(profileName, now)
			return nil
		})
		if err != nil {
			return err
		}
		fmt.Printf("Saved query key %s (space %s)\n", config.MaskKey(key), p.SpaceID)
		return syncEnv()
	}
	c, p, err := account.Open(*profile)
	if err != nil {
		return err
	}
	if p.SpaceID == "" {
		return fmt.Errorf("select a space with aiv space use")
	}
	path := "/api/v1/spaces/" + url.PathEscape(p.SpaceID) + "/space-keys"
	switch sub {
	case "create":
		if strings.TrimSpace(*name) == "" {
			return fmt.Errorf("key create: --name is required")
		}
		var data struct {
			ID  json.Number `json:"id"`
			Key string      `json:"key"`
		}
		err = c.DoUpdate(ctx, "POST", path, map[string]string{"name": *name}, &data, func(now *config.Profile) error {
			if err := config.ValidateSpaceKey(data.Key); err != nil {
				return fmt.Errorf("server returned no usable query key")
			}
			s := now.Spaces[p.SpaceID]
			s.SpaceKey, s.KeyID = data.Key, string(data.ID)
			now.SetSpaceContext(p.SpaceID, s)
			return nil
		})
		if err != nil {
			return err
		}
		fmt.Printf("Created and saved query key %s: %s (space %s)\n", data.ID, config.MaskKey(data.Key), p.SpaceID)
	case "list":
		var data struct {
			Items []struct {
				ID      int    `json:"id"`
				Name    string `json:"name"`
				Prefix  string `json:"key_prefix"`
				Enabled bool   `json:"enabled"`
			} `json:"items"`
		}
		if err := c.Do(ctx, "GET", path+"?pageSize=100", nil, &data); err != nil {
			return err
		}
		w := tabwriter.NewWriter(os.Stdout, 0, 4, 2, ' ', 0)
		fmt.Fprintln(w, "ID\tNAME\tKEY\tENABLED")
		for _, k := range data.Items {
			fmt.Fprintf(w, "%d\t%s\t%s\t%v\n", k.ID, k.Name, config.MaskKey(k.Prefix), k.Enabled)
		}
		w.Flush()
		fmt.Println("The server does not return existing key values. To query data, import an existing key or create a new one.")
		return nil
	case "revoke":
		if n, err := strconv.Atoi(id); err != nil || n <= 0 {
			return fmt.Errorf("key revoke: a numeric KEY_ID is required (see aiv key list)")
		}
		err = c.DoUpdate(ctx, "DELETE", path+"/"+id, nil, nil, func(now *config.Profile) error {
			s := now.Spaces[p.SpaceID]
			clear := s.KeyID == id
			if s.KeyID == "" && s.SpaceKey != "" {
				// Imported keys have no recoverable server ID. Only discard them
				// when the server explicitly rejects the key after revocation.
				_, checkErr := auth.VerifySpaceKey(ctx, now.Endpoint, s.SpaceKey, p.SpaceID)
				clear = checkErr != nil && strings.Contains(checkErr.Error(), "HTTP 401")
			}
			if clear {
				s.SpaceKey, s.KeyID = "", ""
				now.SetSpaceContext(p.SpaceID, s)
			}
			return nil
		})
		if err != nil {
			return err
		}
		fmt.Printf("Revoked query key %s\n", id)
	default:
		return fmt.Errorf("unknown key command %q", sub)
	}
	return syncEnv()
}

func accountIdentity(p config.Profile) string {
	if p.Account == nil {
		return ""
	}
	return p.Account.ID
}
