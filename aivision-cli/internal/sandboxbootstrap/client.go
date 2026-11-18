package sandboxbootstrap

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"
)

var sessionPath = regexp.MustCompile(`^/api/public/sandbox-installations/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

type Client struct {
	base, bootstrapToken, enrollmentToken string
	http                                  *http.Client
}

func validToken(token string) bool {
	if len(token) < 1 || len(token) > 4096 {
		return false
	}
	for _, char := range token {
		if char < 33 || char > 126 {
			return false
		}
	}
	return true
}
func NewClient(base, bootstrapToken, enrollmentToken string) (*Client, error) {
	parsed, err := url.Parse(base)
	if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Hostname() == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.ForceQuery || strings.Contains(base, "#") || !sessionPath.MatchString(parsed.Path) {
		return nil, errors.New("--session-url must be the explicit AI Vision bootstrap session URL without credentials, query or fragment")
	}
	if !validToken(bootstrapToken) || !validToken(enrollmentToken) {
		return nil, errors.New("bootstrap and enrollment credentials must be bounded visible ASCII tokens")
	}
	return &Client{base: parsed.String(), bootstrapToken: bootstrapToken, enrollmentToken: enrollmentToken, http: &http.Client{Timeout: 15 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}, nil
}
func (c *Client) post(ctx context.Context, path, token string, payload any) (json.RawMessage, error) {
	raw, err := json.Marshal(payload)
	if err != nil || len(raw) > maxPlanBytes {
		return nil, errors.New("bootstrap request is invalid")
	}
	safe := errors.New("AI Vision bootstrap authorization or registration could not be confirmed; retry with the saved owned state")
	for attempt := 0; attempt < 5; attempt++ {
		request, err := http.NewRequestWithContext(ctx, http.MethodPost, c.base+path, bytes.NewReader(raw))
		if err != nil {
			return nil, safe
		}
		request.Header.Set("Authorization", "Bearer "+token)
		request.Header.Set("Content-Type", "application/json")
		response, callErr := c.http.Do(request)
		retry := callErr != nil
		if callErr == nil {
			body, readErr := io.ReadAll(io.LimitReader(response.Body, maxPlanBytes+1))
			response.Body.Close()
			if response.StatusCode == http.StatusOK && readErr == nil && len(body) <= maxPlanBytes {
				var envelope struct {
					Code *int            `json:"code"`
					Data json.RawMessage `json:"data"`
				}
				if json.Unmarshal(body, &envelope) == nil && envelope.Code != nil && *envelope.Code == 0 && len(envelope.Data) > 0 {
					return envelope.Data, nil
				}
				return nil, safe
			}
			retry = readErr != nil || response.StatusCode == 408 || response.StatusCode == 429 || response.StatusCode >= 500
		}
		if !retry {
			return nil, safe
		}
		timer := time.NewTimer(time.Duration(attempt+1) * 100 * time.Millisecond)
		select {
		case <-ctx.Done():
			timer.Stop()
			return nil, safe
		case <-timer.C:
		}
	}
	return nil, safe
}
func (c *Client) Claim(ctx context.Context, runID string) (Plan, error) {
	raw, err := c.post(ctx, "/bootstrap", c.bootstrapToken, map[string]string{"run_id": runID})
	if err != nil {
		return Plan{}, err
	}
	var data struct {
		Plan json.RawMessage `json:"plan"`
	}
	if decodeStrict(raw, &data) != nil {
		return Plan{}, errors.New("invalid bootstrap plan response")
	}
	return DecodePlan(data.Plan)
}
func (c *Client) Enroll(ctx context.Context, runID string, plan Plan, platform, key string) error {
	raw, err := c.post(ctx, "/enroll", c.enrollmentToken, struct {
		RunID    string `json:"run_id"`
		PlanID   string `json:"plan_id"`
		Platform string `json:"platform"`
		APIKey   string `json:"api_key"`
	}{runID, plan.PlanID, platform, key})
	if err != nil {
		return err
	}
	var result struct {
		ConnectionUUID    string `json:"connection_uuid"`
		RegistrationState string `json:"registration_state"`
	}
	if decodeStrict(raw, &result) != nil || result.RegistrationState != "saved" || !regexp.MustCompile(`^[0-9a-f]{32}$`).MatchString(result.ConnectionUUID) {
		return errors.New("Space connection registration was not confirmed")
	}
	return nil
}
