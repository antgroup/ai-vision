// Package auth retains SpaceKey verification/import and terminal input helpers.
// Cookie-authenticated account management lives in package account.
package auth

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/config"
)

// LoginParams are the resolved inputs for a SpaceKey login (flags → env → prompt).
type LoginParams struct {
	Endpoint     string
	SpaceKey     string
	SpaceID      string
	AgentProduct string
	Profile      string
	Verify       bool // call the OpenAPI agents endpoint to validate key↔space
	WriteEnvFile bool
	InstallShell bool
	Log          func(format string, args ...any)
}

// LoginResult reports what a successful login persisted.
type LoginResult struct {
	Profile  string
	Saved    config.Profile
	EnvFile  string // path when env.sh was written, else ""
	ShellRC  string // rc path when a managed block was installed, else ""
	Verified bool   // true when the key↔space binding was confirmed by the server
	Agents   []AgentInfo
}

// AgentInfo is one entry of the space's agent list (validation response).
type AgentInfo struct {
	AgentProduct string `json:"agent_product"`
	AgentName    string `json:"agent_name"`
	DisplayName  string `json:"display_name"`
	Description  string `json:"description"`
	Status       int    `json:"status"`
	LastActiveAt string `json:"last_active_at"`
}

// Login validates and persists a SpaceKey profile. It never stores an org id or
// a SigNoz key — those do not exist on the CLI/user plane.
func Login(ctx context.Context, p LoginParams) (*LoginResult, error) {
	logf := p.Log
	if logf == nil {
		logf = func(string, ...any) {}
	}

	if err := config.ValidateSpaceKey(p.SpaceKey); err != nil {
		return nil, err
	}
	ep := strings.TrimRight(strings.TrimSpace(p.Endpoint), "/")
	if ep == "" {
		return nil, fmt.Errorf("auth login: endpoint is required (pass --endpoint or set %s)", config.EnvEndpoint)
	}
	if _, err := url.Parse(ep); err != nil {
		return nil, fmt.Errorf("auth login: invalid endpoint %q: %w", ep, err)
	}
	spaceID := strings.TrimSpace(p.SpaceID)
	product := strings.TrimSpace(p.AgentProduct)

	var agents []AgentInfo
	verified := false
	if p.Verify && spaceID != "" {
		logf("verifying space key against %s (space %s)", ep, spaceID)
		a, err := VerifySpaceKey(ctx, ep, p.SpaceKey, spaceID)
		if err != nil {
			return nil, err
		}
		agents, verified = a, true
		// Adopt the first agent product as the default when the user gave none.
		if product == "" && len(a) > 0 {
			product = a[0].AgentProduct
			logf("default agent product set to %q", product)
		}
	}

	name := p.Profile
	var prof config.Profile
	if err := config.Update(ctx, func(cfg *config.File) error {
		if name == "" {
			name = cfg.Active()
		}
		prof, _ = cfg.Get(name)
		if prof.Endpoint != ep {
			prof = config.Profile{Endpoint: ep}
		}
		prof.SelectSpace(spaceID)
		prof.SpaceKey, prof.QueryKeyID = strings.TrimSpace(p.SpaceKey), ""
		if product != "" {
			prof.AgentProduct = product
		}
		cfg.Set(name, prof)
		cfg.ActiveProfile = name
		return nil
	}); err != nil {
		return nil, err
	}

	res := &LoginResult{Profile: name, Saved: prof, Verified: verified, Agents: agents}
	if p.WriteEnvFile {
		if err := config.WriteEnvFile(name, prof); err != nil {
			return nil, err
		}
		res.EnvFile = config.EnvFilePath()
	}
	if p.InstallShell {
		rc, err := config.InstallShellBlock()
		if err != nil {
			return nil, err
		}
		res.ShellRC = rc
	}
	return res, nil
}

// Logout removes a profile plus the sourceable env.sh and the managed shell rc
// block, returning the profile name and rc path touched.
func Logout(profile string) (name, shellRC string, err error) {
	name = profile
	err = config.Locked(context.Background(), func(cfg *config.File) error {
		if name == "" {
			name = cfg.Active()
		}
		p, _ := cfg.Get(name)
		if p.Account != nil {
			return fmt.Errorf("account sessions must be revoked before removing this profile")
		}
		active := name == cfg.Active()
		cfg.Delete(name)
		if err := cfg.Save(); err != nil {
			return err
		}
		if active {
			if p, ok := cfg.Get(""); ok {
				return config.WriteEnvFile(cfg.Active(), p)
			}
			config.RemoveEnvFile()
			var err error
			shellRC, err = config.RemoveShellBlock()
			return err
		}
		return nil
	})
	return name, shellRC, err
}

// apiEnvelope is the backend's uniform response wrapper.
type apiEnvelope struct {
	Code    int             `json:"code"`
	Message string          `json:"message"`
	Data    json.RawMessage `json:"data"`
}

// VerifySpaceKey calls GET /openapi/v1/spaces/{space_id}/agents with the
// SpaceKey as a Bearer token, confirming the key is valid and bound to the
// space. It translates the backend's auth error codes into actionable messages.
func VerifySpaceKey(ctx context.Context, endpoint, spaceKey, spaceID string) ([]AgentInfo, error) {
	u := strings.TrimRight(endpoint, "/") +
		"/openapi/v1/spaces/" + url.PathEscape(spaceID) + "/agents"
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Authorization", "Bearer "+strings.TrimSpace(spaceKey))

	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("cannot reach %s: %w", u, err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 8<<20))

	var env apiEnvelope
	_ = json.Unmarshal(raw, &env)

	if resp.StatusCode < 200 || resp.StatusCode >= 300 || env.Code != 0 {
		return nil, verifyError(resp.StatusCode, env, snippet(raw))
	}
	var agents []AgentInfo
	if len(env.Data) > 0 {
		if err := json.Unmarshal(env.Data, &agents); err != nil {
			// A 200/code:0 with an unexpected data shape is still a valid key.
			return nil, nil
		}
	}
	return agents, nil
}

// verifyError maps HTTP status + envelope code to a clear, actionable error.
func verifyError(status int, env apiEnvelope, body string) error {
	msg := strings.TrimSpace(env.Message)
	switch {
	case status == 401 || env.Code == 4001:
		if msg == "" {
			msg = "authentication required"
		}
		return fmt.Errorf("space key rejected (HTTP 401): %s — check the key value and that it is an aivision SpaceKey (sk-aiv-…)", msg)
	case status == 403 || env.Code == 4003:
		if msg == "" {
			msg = "space key does not match the requested space"
		}
		return fmt.Errorf("space key does not match this space (HTTP 403): %s — verify --space is the space the key was issued for", msg)
	case status == 404 || env.Code == 3001:
		if msg == "" {
			msg = "space not found"
		}
		return fmt.Errorf("space not found (HTTP 404): %s — verify --space and --endpoint", msg)
	default:
		if msg == "" {
			msg = body
		}
		return fmt.Errorf("space key verification failed (HTTP %d): %s", status, msg)
	}
}

func snippet(b []byte) string {
	s := strings.TrimSpace(string(b))
	if len(s) > 300 {
		s = s[:300] + "…"
	}
	if s == "" {
		return "(empty body)"
	}
	return s
}
