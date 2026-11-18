// Package account talks only to the cookie-authenticated management API.
// SpaceKeys and Agent credentials never enter this client.
package account

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"strings"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/config"
)

type APIError struct {
	Status  int
	Code    int
	Message string
}

func (e *APIError) Error() string {
	return fmt.Sprintf("account API: HTTP %d: %s", e.Status, e.Message)
}
func IsStatus(err error, status int) bool {
	var e *APIError
	return errors.As(err, &e) && e.Status == status
}

type Space struct {
	ID   string `json:"space_id"`
	Name string `json:"name"`
	Type int    `json:"type"`
}

type Client struct{ Profile, Endpoint, UserID string }

func NormalizeEndpoint(raw string) (string, error) {
	u, err := url.Parse(strings.TrimRight(strings.TrimSpace(raw), "/"))
	if err != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return "", fmt.Errorf("--endpoint must be an absolute http(s) URL without credentials, query or fragment")
	}
	return strings.TrimRight(u.String(), "/"), nil
}

func Open(name string) (*Client, config.Profile, error) {
	f, err := config.Load()
	if err != nil {
		return nil, config.Profile{}, err
	}
	if name == "" {
		name = f.Active()
	}
	p, ok := f.Get(name)
	if !ok || p.Account == nil {
		return nil, p, fmt.Errorf("profile %q has no account session; run: aiv auth login --endpoint URL --email EMAIL", name)
	}
	return &Client{name, p.Endpoint, p.Account.ID}, p, nil
}

func (c *Client) matches(p config.Profile) bool {
	return p.Account != nil && p.Account.ID == c.UserID && p.Endpoint == c.Endpoint
}

// Do reloads cookies *inside* the process lock. A second process therefore sees
// the newly rotated refresh token instead of replaying the old one.
func (c *Client) Do(ctx context.Context, method, path string, body, out any) error {
	return c.DoUpdate(ctx, method, path, body, out, nil)
}

// DoUpdate saves one-time response secrets before releasing the session lock.
func (c *Client) DoUpdate(ctx context.Context, method, path string, body, out any, apply func(*config.Profile) error) error {
	return config.Locked(ctx, func(f *config.File) error {
		p, _ := f.Get(c.Profile)
		if !c.matches(p) {
			return fmt.Errorf("account profile changed; retry the command")
		}
		s := newSession(p.Endpoint, p.Cookies)
		err := s.do(ctx, method, path, body, out)
		if err == nil && apply != nil {
			err = apply(&p)
		}
		p.Cookies = s.cookies
		f.Set(c.Profile, p)
		if saveErr := f.Save(); saveErr != nil {
			return saveErr
		}
		return err
	})
}

func (c *Client) Update(ctx context.Context, fn func(*config.Profile) error) error {
	return config.Update(ctx, func(f *config.File) error {
		p, _ := f.Get(c.Profile)
		if !c.matches(p) {
			return fmt.Errorf("account profile changed; retry the command")
		}
		if err := fn(&p); err != nil {
			return err
		}
		f.Set(c.Profile, p)
		return nil
	})
}

func (c *Client) Spaces(ctx context.Context) ([]Space, error) {
	var data struct {
		Items []Space `json:"items"`
	}
	err := c.Do(ctx, "GET", "/api/v1/spaces", nil, &data)
	return data.Items, err
}

func Login(ctx context.Context, profile, endpoint, email, name, password string, register bool) (*Client, config.Profile, error) {
	ep, err := NormalizeEndpoint(endpoint)
	if err != nil {
		return nil, config.Profile{}, err
	}
	var saved config.Profile
	err = config.Locked(ctx, func(f *config.File) error {
		if profile == "" {
			profile = f.Active()
		}
		s := newSession(ep, nil)
		path := "/api/v1/auth/login"
		body := map[string]string{"email": email, "password": password}
		if register {
			path = "/api/v1/auth/register"
			body["name"] = name
			body["password_confirm"] = password
		}
		var data struct {
			User          config.Account `json:"user"`
			PersonalSpace Space          `json:"personal_space"`
		}
		if err := s.request(ctx, "POST", path, body, &data); err != nil {
			return err
		}
		if data.User.ID == "" {
			return fmt.Errorf("account API returned no user identity")
		}
		old, _ := f.Get(profile)
		saved = config.Profile{Endpoint: ep, Account: &data.User, Cookies: s.cookies}
		// Reuse local context only for this exact platform and identity. Old
		// SpaceKey-only profiles are migrated but not assigned to an unknown user.
		if old.Endpoint == ep && old.Account != nil && old.Account.ID == data.User.ID {
			saved.Spaces = old.Spaces
			saved.SelectSpace(old.SpaceID)
		}
		// Persist successful authentication even if listing spaces is unavailable.
		// This avoids stranding an account after a successful registration.
		f.Set(profile, saved)
		f.ActiveProfile = profile
		if err := f.Save(); err != nil {
			return err
		}
		var list struct {
			Items []Space `json:"items"`
		}
		listErr := s.do(ctx, "GET", "/api/v1/spaces", nil, &list)
		// A refresh may have succeeded even when the retried space request
		// failed. Never leave the consumed refresh token on disk.
		saved.Cookies = s.cookies
		f.Set(profile, saved)
		if err := f.Save(); err != nil {
			return err
		}
		if listErr != nil {
			return fmt.Errorf("account login succeeded, but selecting a space failed; retry aiv space list/use: %w", listErr)
		}
		selected, personal := "", data.PersonalSpace.ID
		for _, space := range list.Items {
			if space.ID == saved.SpaceID {
				selected = space.ID
			}
			if space.Type == 1 {
				personal = space.ID
			}
		}
		if selected == "" {
			selected = personal
		}
		if selected == "" {
			return fmt.Errorf("account has no personal space; run: aiv space list")
		}
		saved.SelectSpace(selected)
		saved.Cookies = s.cookies
		f.Set(profile, saved)
		saved, _ = f.Get(profile)
		return f.Save()
	})
	if err != nil {
		return nil, saved, err
	}
	return &Client{profile, ep, saved.Account.ID}, saved, nil
}

func (c *Client) Logout(ctx context.Context) error {
	return config.Locked(ctx, func(f *config.File) error {
		p, _ := f.Get(c.Profile)
		if !c.matches(p) {
			return fmt.Errorf("account profile changed; retry logout")
		}
		s := newSession(p.Endpoint, p.Cookies)
		err := s.do(ctx, "POST", "/api/v1/auth/logout", map[string]any{}, nil)
		p.Cookies = s.cookies
		f.Set(c.Profile, p)
		if err != nil && !IsStatus(err, 401) {
			if saveErr := f.Save(); saveErr != nil {
				return saveErr
			}
			return err
		}
		f.Delete(c.Profile)
		return f.Save()
	})
}

type session struct {
	endpoint string
	http     *http.Client
	cookies  []config.Cookie
}

func newSession(endpoint string, cookies []config.Cookie) *session {
	jar, _ := cookiejar.New(nil)
	s := &session{endpoint: endpoint, http: &http.Client{Jar: jar, Timeout: 30 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}
	for _, c := range cookies {
		if !c.Value.Expires.IsZero() && !c.Value.Expires.After(time.Now()) {
			continue
		}
		u, err := url.Parse(c.URL)
		if err != nil {
			continue
		}
		base, _ := url.Parse(endpoint)
		if u.Scheme != base.Scheme || u.Host != base.Host {
			continue
		}
		jar.SetCookies(u, []*http.Cookie{&c.Value})
		s.cookies = append(s.cookies, c)
	}
	return s
}

func (s *session) do(ctx context.Context, method, path string, body, out any) error {
	err := s.request(ctx, method, path, body, out)
	if !IsStatus(err, 401) {
		return err
	}
	if err := s.request(ctx, "POST", "/api/v1/auth/refresh", map[string]any{}, nil); err != nil {
		return fmt.Errorf("session renewal failed; run aiv auth login again: %w", err)
	}
	return s.request(ctx, method, path, body, out)
}

func (s *session) request(ctx context.Context, method, path string, body, out any) error {
	if !strings.HasPrefix(path, "/api/v1/") {
		return fmt.Errorf("invalid account API path")
	}
	u, _ := url.Parse(s.endpoint + path)
	csrf := ""
	unsafe := method != "GET" && method != "HEAD"
	if unsafe {
		for _, c := range s.http.Jar.Cookies(u) {
			if c.Name == "csrftoken" {
				csrf = c.Value
			}
		}
		if csrf == "" {
			if err := s.request(ctx, "GET", "/api/v1/auth/csrf", nil, nil); err != nil {
				return err
			}
			for _, c := range s.http.Jar.Cookies(u) {
				if c.Name == "csrftoken" {
					csrf = c.Value
				}
			}
			if csrf == "" {
				return fmt.Errorf("server did not provide a CSRF cookie; check endpoint and AUTH_COOKIE_SECURE/HTTPS configuration")
			}
		}
	}
	var payload []byte
	var err error
	if body != nil {
		payload, err = json.Marshal(body)
		if err != nil {
			return err
		}
	}
	req, err := http.NewRequestWithContext(ctx, method, u.String(), bytes.NewReader(payload))
	if err != nil {
		return err
	}
	req.Header.Set("Accept", "application/json")
	if unsafe {
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("X-CSRFToken", csrf)
		req.Header.Set("Origin", u.Scheme+"://"+u.Host)
		req.Header.Set("Referer", s.endpoint+"/")
	}
	resp, err := s.http.Do(req)
	if err != nil {
		return fmt.Errorf("account API unavailable: %w", err)
	}
	defer resp.Body.Close()
	s.capture(u, resp.Cookies())
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if err != nil {
		return err
	}
	var envelope struct {
		Code    int             `json:"code"`
		Message string          `json:"message"`
		Data    json.RawMessage `json:"data"`
	}
	decodeErr := json.Unmarshal(raw, &envelope)
	if resp.StatusCode < 200 || resp.StatusCode >= 300 || envelope.Code != 0 {
		msg := envelope.Message
		if msg == "" {
			msg = http.StatusText(resp.StatusCode)
		}
		return &APIError{resp.StatusCode, envelope.Code, msg}
	}
	if decodeErr != nil {
		return fmt.Errorf("account API returned invalid JSON (HTTP %d)", resp.StatusCode)
	}
	if out != nil {
		return json.Unmarshal(envelope.Data, out)
	}
	return nil
}

func (s *session) capture(u *url.URL, cookies []*http.Cookie) {
	for _, incoming := range cookies {
		c := *incoming
		if c.Path == "" {
			c.Path = u.Path[:strings.LastIndex(u.Path, "/")+1]
			if c.Path != "/" {
				c.Path = strings.TrimSuffix(c.Path, "/")
			}
		}
		if c.MaxAge > 0 {
			c.Expires = time.Now().Add(time.Duration(c.MaxAge) * time.Second)
			c.MaxAge = 0
		}
		kept := s.cookies[:0]
		for _, old := range s.cookies {
			if old.Value.Name != c.Name || old.Value.Path != c.Path || old.Value.Domain != c.Domain {
				kept = append(kept, old)
			}
		}
		s.cookies = kept
		if c.MaxAge < 0 || (!c.Expires.IsZero() && !c.Expires.After(time.Now())) {
			continue
		}
		s.cookies = append(s.cookies, config.Cookie{URL: u.String(), Value: c})
	}
}
