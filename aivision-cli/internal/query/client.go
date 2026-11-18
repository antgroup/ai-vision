// Package query is the aivision OpenAPI client used by `aiv query`.
//
// It talks to the public observability OpenAPI with an aivision SpaceKey as a
// Bearer token. Every path is scoped to a space and (for most endpoints) an
// agent product:
//
//	/openapi/v1/spaces/{space_id}/agents/{agent_product}/…
//	/openapi/v1/grafana/spaces/{space_id}/agents/{agent_product}/query
//
// The client never sees the org id or the internal SigNoz key; result visibility
// is enforced by the backend, so the CLI does no second-guessing of authorization.
package query

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// Client is an aivision OpenAPI client bound to one space + agent product.
type Client struct {
	Endpoint     string // base URL, no trailing slash
	SpaceKey     string // Bearer token (sk-aiv-…)
	SpaceID      string
	AgentProduct string
	HTTP         *http.Client
}

// New builds a client. endpoint has no trailing slash; spaceKey is sent as an
// Authorization: Bearer header.
func New(endpoint, spaceKey, spaceID, agentProduct string) *Client {
	return &Client{
		Endpoint:     strings.TrimRight(strings.TrimSpace(endpoint), "/"),
		SpaceKey:     strings.TrimSpace(spaceKey),
		SpaceID:      strings.TrimSpace(spaceID),
		AgentProduct: strings.TrimSpace(agentProduct),
		HTTP:         &http.Client{Timeout: 60 * time.Second},
	}
}

// envelope is the backend's uniform response wrapper: {code,message,data}.
type envelope struct {
	Code    int             `json:"code"`
	Message string          `json:"message"`
	Data    json.RawMessage `json:"data"`
}

// --- URL builders ------------------------------------------------------------

// AgentsURL is the space's agent list (also used to validate a SpaceKey).
func (c *Client) AgentsURL() string {
	return c.Endpoint + "/openapi/v1/spaces/" + url.PathEscape(c.SpaceID) + "/agents"
}

// AgentURL builds an agent-scoped path: …/agents/{product}/{suffix}.
func (c *Client) AgentURL(suffix string) string {
	return c.Endpoint + "/openapi/v1/spaces/" + url.PathEscape(c.SpaceID) +
		"/agents/" + url.PathEscape(c.AgentProduct) + "/" + strings.TrimPrefix(suffix, "/")
}

// GrafanaURL is the time-series/rawSql query endpoint.
func (c *Client) GrafanaURL() string {
	return c.Endpoint + "/openapi/v1/grafana/spaces/" + url.PathEscape(c.SpaceID) +
		"/agents/" + url.PathEscape(c.AgentProduct) + "/query"
}

// --- transport ---------------------------------------------------------------

// Get issues a GET and returns the envelope's `data` payload.
func (c *Client) Get(ctx context.Context, fullURL string, q url.Values) (json.RawMessage, error) {
	if len(q) > 0 {
		if strings.Contains(fullURL, "?") {
			fullURL += "&" + q.Encode()
		} else {
			fullURL += "?" + q.Encode()
		}
	}
	return c.do(ctx, http.MethodGet, fullURL, nil)
}

// Post issues a POST with a JSON body and returns the envelope's `data` payload.
func (c *Client) Post(ctx context.Context, fullURL string, body any) (json.RawMessage, error) {
	return c.do(ctx, http.MethodPost, fullURL, body)
}

func (c *Client) do(ctx context.Context, method, fullURL string, body any) (json.RawMessage, error) {
	if err := c.validate(); err != nil {
		return nil, err
	}
	var reader io.Reader
	if body != nil {
		buf, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		reader = bytes.NewReader(buf)
	}
	req, err := http.NewRequestWithContext(ctx, method, fullURL, reader)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Authorization", "Bearer "+c.SpaceKey)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, fmt.Errorf("cannot reach %s: %w", fullURL, err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 64<<20))

	var env envelope
	_ = json.Unmarshal(raw, &env)
	if resp.StatusCode < 200 || resp.StatusCode >= 300 || env.Code != 0 {
		return nil, apiError(resp.StatusCode, env, snippet(raw))
	}
	return env.Data, nil
}

func (c *Client) validate() error {
	if c.Endpoint == "" {
		return fmt.Errorf("no endpoint: run `aiv auth login` or set AIVISION_ENDPOINT")
	}
	if c.SpaceKey == "" {
		return fmt.Errorf("no space key: run `aiv auth login` or set AIVISION_SPACE_KEY")
	}
	if c.SpaceID == "" {
		return fmt.Errorf("no space id: pass --space or set AIVISION_SPACE_ID")
	}
	return nil
}

// Ping validates the credential by listing the space's agents (cheap, read-only).
func (c *Client) Ping(ctx context.Context) error {
	_, err := c.Get(ctx, c.AgentsURL(), nil)
	return err
}

// apiError maps HTTP status + envelope code to a clear, actionable message.
func apiError(status int, env envelope, body string) error {
	msg := strings.TrimSpace(env.Message)
	switch {
	case status == 401 || env.Code == 4001:
		if msg == "" {
			msg = "authentication required"
		}
		return fmt.Errorf("unauthorized (HTTP 401): %s — check your SpaceKey (aiv auth status)", msg)
	case status == 403 || env.Code == 4003:
		if msg == "" {
			msg = "space key does not match the requested space"
		}
		return fmt.Errorf("forbidden (HTTP 403): %s — verify --space matches the key's space", msg)
	case status == 404 || env.Code == 3001:
		if msg == "" {
			msg = "not found"
		}
		return fmt.Errorf("not found (HTTP 404): %s — verify --space / --agent-product / id", msg)
	default:
		if msg == "" {
			msg = body
		}
		return fmt.Errorf("query failed (HTTP %d): %s", status, msg)
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
