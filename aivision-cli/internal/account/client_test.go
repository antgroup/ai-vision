package account

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/config"
)

type authServer struct {
	t                  *testing.T
	server             *httptest.Server
	mu                 sync.Mutex
	expired            bool
	rotations, logouts int
	allowedTeam        bool
	expireOnLogin      bool
	failSpaces         bool
}

func fixture(t *testing.T) *authServer {
	t.Helper()
	t.Setenv(config.EnvConfigDir, t.TempDir())
	f := &authServer{t: t, allowedTeam: true}
	f.server = httptest.NewServer(http.HandlerFunc(f.serve))
	t.Cleanup(f.server.Close)
	return f
}

func (f *authServer) serve(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if r.Header.Get("Authorization") != "" {
		f.t.Error("account request leaked Bearer credential")
	}
	if r.URL.Path != "/api/v1/auth/refresh" {
		if _, err := r.Cookie("aivision_refresh"); err == nil {
			f.t.Errorf("refresh cookie leaked to %s", r.URL.Path)
		}
	}
	if r.Method != "GET" {
		cookie, err := r.Cookie("csrftoken")
		if err != nil || cookie.Value != r.Header.Get("X-CSRFToken") || r.Header.Get("Origin") != f.server.URL || r.Header.Get("Referer") != f.server.URL+"/" {
			f.t.Errorf("missing CSRF cookie/header/origin on %s", r.URL.Path)
			http.Error(w, "CSRF", 403)
			return
		}
	}
	respond := func(status int, data any) {
		w.WriteHeader(status)
		_ = json.NewEncoder(w).Encode(map[string]any{"code": 0, "data": data})
	}
	issue := func(user string) {
		http.SetCookie(w, &http.Cookie{Name: "aivision_access", Value: user, Path: "/", HttpOnly: true, MaxAge: 900})
		http.SetCookie(w, &http.Cookie{Name: "aivision_refresh", Value: fmt.Sprintf("refresh-%d", f.rotations), Path: "/api/v1/auth/refresh", HttpOnly: true, MaxAge: 86400})
	}
	switch r.URL.Path {
	case "/api/v1/auth/csrf":
		http.SetCookie(w, &http.Cookie{Name: "csrftoken", Value: "csrf-secret", Path: "/"})
		respond(200, map[string]any{})
		return
	case "/api/v1/auth/register", "/api/v1/auth/login":
		var b map[string]string
		_ = json.NewDecoder(r.Body).Decode(&b)
		if b["password"] != " secret with spaces " {
			f.t.Error("password was not preserved")
		}
		if strings.HasSuffix(r.URL.Path, "register") && (b["password_confirm"] != b["password"] || b["name"] == "") {
			f.t.Error("invalid register body")
		}
		issue(b["email"])
		f.expired = f.expireOnLogin
		respond(200, map[string]any{"user": config.Account{ID: b["email"], Email: b["email"]}, "personal_space": Space{ID: "personal", Type: 1}})
		return
	case "/api/v1/auth/refresh":
		cookie, err := r.Cookie("aivision_refresh")
		if err != nil || cookie.Value != fmt.Sprintf("refresh-%d", f.rotations) {
			http.Error(w, "refresh replay", 409)
			return
		}
		f.rotations++
		f.expired = false
		issue("one@example.test")
		respond(200, map[string]any{})
		return
	}
	access, err := r.Cookie("aivision_access")
	if err != nil || f.expired {
		http.Error(w, "expired", 401)
		return
	}
	switch r.URL.Path {
	case "/api/v1/auth/me":
		respond(200, map[string]any{"user": config.Account{ID: access.Value, Email: access.Value}})
	case "/api/v1/auth/logout":
		f.logouts++
		respond(200, map[string]any{})
	case "/api/v1/spaces":
		if f.failSpaces {
			http.Error(w, "spaces unavailable", 503)
			return
		}
		spaces := []Space{{ID: "personal", Type: 1, Name: "Personal"}}
		if f.allowedTeam {
			spaces = append(spaces, Space{ID: "team", Type: 2, Name: "Team"})
		}
		respond(200, map[string]any{"items": spaces})
	case "/api/v1/forbidden":
		http.Error(w, "forbidden", 403)
	default:
		http.NotFound(w, r)
	}
}

func login(t *testing.T, f *authServer, profile, email string, register bool) *Client {
	t.Helper()
	c, p, err := Login(context.Background(), profile, f.server.URL, email, "Tester", " secret with spaces ", register)
	if err != nil {
		t.Fatal(err)
	}
	if p.SpaceID != "personal" {
		t.Fatalf("personal space not selected: %#v", p.SpaceID)
	}
	return c
}

func TestRegisterRefreshLogoutAndPermissions(t *testing.T) {
	f := fixture(t)
	c := login(t, f, "one", "one@example.test", true)
	f.mu.Lock()
	f.expired = true
	f.mu.Unlock()
	if err := c.Do(context.Background(), "GET", "/api/v1/auth/me", nil, nil); err != nil {
		t.Fatal(err)
	}
	if f.rotations != 1 {
		t.Fatalf("refresh count %d", f.rotations)
	}
	if err := c.Do(context.Background(), "POST", "/api/v1/forbidden", map[string]any{}, nil); !IsStatus(err, 403) {
		t.Fatalf("want permission failure, got %v", err)
	}
	if f.rotations != 1 {
		t.Fatal("permission denial must not refresh")
	}
	raw, _ := os.ReadFile(config.Path())
	if strings.Contains(string(raw), "secret with spaces") {
		t.Fatal("password written to disk")
	}
	stat, _ := os.Stat(config.Path())
	if stat.Mode().Perm() != 0600 {
		t.Fatal("config not private")
	}
	if err := c.Logout(context.Background()); err != nil {
		t.Fatal(err)
	}
	if f.logouts != 1 {
		t.Fatal("server logout not called")
	}
	if _, _, err := Open("one"); err == nil {
		t.Fatal("logged out session survived")
	}
}

func TestAccountAndSpaceIsolation(t *testing.T) {
	f := fixture(t)
	c := login(t, f, "one", "one@example.test", false)
	err := c.Update(context.Background(), func(p *config.Profile) error {
		p.SetSpaceContext("team", config.SpaceContext{SpaceKey: "sk-aiv-team-secret", AgentProduct: "codex"})
		p.SelectSpace("team")
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	_, p, err := Login(context.Background(), "one", f.server.URL, "one@example.test", "", " secret with spaces ", false)
	if err != nil || p.SpaceID != "team" || p.SpaceKey != "sk-aiv-team-secret" {
		t.Fatalf("accessible context not restored: %v", err)
	}
	f.mu.Lock()
	f.allowedTeam = false
	f.mu.Unlock()
	_, p, err = Login(context.Background(), "one", f.server.URL, "one@example.test", "", " secret with spaces ", false)
	if err != nil || p.SpaceID != "personal" || p.SpaceKey != "" {
		t.Fatalf("inaccessible context leaked: %v", err)
	}
	login(t, f, "two", "two@example.test", false)
	c2, _, _ := Open("two")
	if c2.UserID == c.UserID {
		t.Fatal("profiles mixed")
	}
	_, p, err = Login(context.Background(), "one", f.server.URL, "two@example.test", "", " secret with spaces ", false)
	if err != nil || len(p.Spaces) != 1 || p.SpaceKey != "" {
		t.Fatalf("previous account's key leaked: %v", err)
	}
	if err := c.Do(context.Background(), "GET", "/api/v1/auth/me", nil, nil); err == nil {
		t.Fatal("old client accepted replaced profile")
	}
	other := httptest.NewServer(http.HandlerFunc(f.serve))
	defer other.Close()
	// Endpoint identity is also part of the binding, independently of user ID.
	if err := config.Update(context.Background(), func(file *config.File) error {
		p, _ := file.Get("two")
		p.Endpoint = other.URL
		file.Set("two", p)
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if err := c2.Do(context.Background(), "GET", "/api/v1/auth/me", nil, nil); err == nil {
		t.Fatal("client crossed platform boundary")
	}
}

func TestRefreshAcrossProcesses(t *testing.T) {
	f := fixture(t)
	login(t, f, "one", "one@example.test", false)
	f.mu.Lock()
	f.expired = true
	f.mu.Unlock()
	var wg sync.WaitGroup
	for range 6 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			cmd := exec.Command(os.Args[0], "-test.run=^TestRefreshChild$")
			cmd.Env = append(os.Environ(), "AIV_REFRESH_CHILD=1")
			if out, err := cmd.CombinedOutput(); err != nil {
				t.Errorf("child: %v %s", err, out)
			}
		}()
	}
	wg.Wait()
	if f.rotations != 1 {
		t.Fatalf("concurrent clients rotated %d times", f.rotations)
	}
}

func TestRefreshChild(t *testing.T) {
	if os.Getenv("AIV_REFRESH_CHILD") == "" {
		return
	}
	c, _, err := Open("one")
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := c.Do(ctx, "GET", "/api/v1/auth/me", nil, nil); err != nil {
		t.Fatal(err)
	}
}

func TestSecureAndExpiredCookies(t *testing.T) {
	s := newSession("http://localhost", []config.Cookie{
		{URL: "http://localhost/", Value: http.Cookie{Name: "secure", Value: "secret", Path: "/", Secure: true}},
		{URL: "http://localhost/", Value: http.Cookie{Name: "expired", Value: "secret", Path: "/", Expires: time.Now().Add(-time.Second)}},
		{URL: "http://other.test/", Value: http.Cookie{Name: "other", Value: "secret", Path: "/"}},
	})
	req, _ := http.NewRequest("GET", "http://localhost/api/v1/auth/me", nil)
	if len(s.http.Jar.Cookies(req.URL)) != 0 {
		t.Fatal("secure/expired/cross-origin cookies leaked")
	}
}

func TestLoginPersistsRotatedCookieWhenSpaceSelectionFails(t *testing.T) {
	f := fixture(t)
	f.expireOnLogin, f.failSpaces = true, true
	_, _, err := Login(context.Background(), "one", f.server.URL, "one@example.test", "", " secret with spaces ", false)
	if !IsStatus(err, 503) {
		t.Fatalf("expected space selection failure, got %v", err)
	}
	c, _, err := Open("one")
	if err != nil {
		t.Fatal("successful login was not saved:", err)
	}
	f.mu.Lock()
	f.expired = true
	f.failSpaces = false
	f.mu.Unlock()
	if err := c.Do(context.Background(), "GET", "/api/v1/auth/me", nil, nil); err != nil {
		t.Fatal("rotated refresh cookie was lost:", err)
	}
	if f.rotations != 2 {
		t.Fatalf("unexpected rotation count: %d", f.rotations)
	}
}
