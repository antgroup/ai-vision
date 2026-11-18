package cli

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/antgroup/ai-vision/aivision-cli/internal/config"
)

// This fixture exercises real CLI commands, the downloaded Python installer,
// native hook edits, and the two separate HTTP authentication planes.
func TestPureCLIOnboarding(t *testing.T) {
	python, err := resolvePython("")
	if err != nil {
		t.Fatal(err)
	}
	root := t.TempDir()
	t.Setenv(config.EnvConfigDir, filepath.Join(root, "client"))
	t.Setenv(config.EnvProfile, "")
	t.Setenv(config.EnvSpaceKey, "sk-aiv-stale-environment")
	host := filepath.Join(root, "bin")
	if err := os.Mkdir(host, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(host, "codex"), []byte("#!/bin/sh\nprintf 'hooks enabled true\\n'\n"), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", host+string(os.PathListSeparator)+os.Getenv("PATH"))
	native := filepath.Join(root, "native")
	scripts := filepath.Join("..", "..", "..", "ai-vision", "agent-plugins", "scripts")
	files := map[string][]byte{"cdxhooks": []byte("#!/bin/sh\nprintf 'cdxhooks test\\n'\n")}
	for _, name := range []string{"connect", "native_config.py", "hook_runtime.py"} {
		data, err := os.ReadFile(filepath.Join(scripts, name))
		if err != nil {
			t.Fatal(err)
		}
		files[name] = data
	}
	const agentUUID = "b825a737-e2bc-4a30-b0d4-821b7e6713e6"
	var mu sync.Mutex
	creates, grants, exchanges, confirmations := 0, 0, 0, 0
	keyRevoked, credentialRevoked, expiredGrant, missingPlatform, failConfirmation := false, false, false, false, false
	privacySupported := true
	legacyInstaller := false
	var canonical, management *httptest.Server
	serve := func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		defer mu.Unlock()
		path := r.URL.Path
		respond := func(data any) { _ = json.NewEncoder(w).Encode(map[string]any{"code": 0, "data": data}) }
		if strings.HasPrefix(path, "/api/v1/") {
			if r.Header.Get("Authorization") != "" {
				t.Error("account request sent SpaceKey")
			}
			if r.Method != "GET" {
				cookie, err := r.Cookie("csrftoken")
				if err != nil || r.Header.Get("X-CSRFToken") != cookie.Value {
					http.Error(w, "CSRF", 403)
					return
				}
			}
			if path != "/api/v1/auth/csrf" && path != "/api/v1/auth/register" {
				if _, err := r.Cookie("aivision_access"); err != nil {
					http.Error(w, "session", 401)
					return
				}
			}
		}
		switch {
		case path == "/api/v1/auth/csrf":
			http.SetCookie(w, &http.Cookie{Name: "csrftoken", Value: "csrf", Path: "/"})
			respond(map[string]any{})
		case path == "/api/v1/auth/register":
			http.SetCookie(w, &http.Cookie{Name: "aivision_access", Value: "account", Path: "/"})
			respond(map[string]any{"user": config.Account{ID: "user", Email: "new@example.test"}, "personal_space": map[string]any{"space_id": "personal", "type": 1}})
		case path == "/api/v1/auth/me":
			respond(map[string]any{"user": config.Account{ID: "user", Email: "new@example.test"}})
		case path == "/api/v1/auth/logout":
			respond(map[string]any{})
		case path == "/api/v1/spaces":
			if r.Method == "POST" {
				respond(map[string]any{"space_id": "team", "name": "Team", "type": 2})
			} else {
				respond(map[string]any{"items": []any{map[string]any{"space_id": "personal", "type": 1}, map[string]any{"space_id": "team", "type": 2}}})
			}
		case path == "/api/v1/spaces/team/space-keys" && r.Method == "POST":
			respond(map[string]any{"id": 7, "key": "sk-aiv-team-query-secret"})
		case path == "/api/v1/spaces/team/space-keys" && r.Method == "GET":
			respond(map[string]any{"items": []any{map[string]any{"id": 7, "name": "cli", "key_prefix": "sk-aiv-team", "enabled": true}}})
		case path == "/api/v1/spaces/team/space-keys/7":
			keyRevoked = true
			respond(map[string]any{"deleted": true})
		case path == "/api/v1/spaces/team/agents/create":
			creates++
			respond(map[string]any{"agent_uuid": agentUUID, "agent_product": "Codex"})
		case strings.HasSuffix(path, "/installation-tokens"):
			grants++
			token := "aiv-install-ticket"
			if expiredGrant {
				token = "aiv-install-expired"
			}
			respond(map[string]any{"agent_uuid": agentUUID, "space_id": "team", "installation_token": token, "base_url": canonical.URL})
		case path == "/api/public/onboarding/scripts/connect":
			if legacyInstaller {
				_, _ = w.Write([]byte(`import json
print(json.dumps({"ready":True,"agent":"codex","agent_product":"Codex","config_dir":"/legacy"}))
`))
			} else {
				_, _ = w.Write(files["connect"])
			}
		case path == "/api/public/onboarding/catalog.json":
			artifact := func(name string) map[string]any {
				sum := sha256.Sum256(files[name])
				return map[string]any{"name": name, "url": canonical.URL + "/api/artifact/" + name, "sha256": hex.EncodeToString(sum[:])}
			}
			item := artifact("cdxhooks")
			item["platform"], item["arch"], item["target_filename"] = "all", "all", "cdxhooks"
			if privacySupported {
				item["collection_modes"] = []string{"full", "privacy"}
			}
			if missingPlatform {
				item["platform"] = "missing"
			}
			respond(map[string]any{"agents": []any{map[string]any{"key": "codex", "version": "test", "plugin": map[string]any{"key": "codex", "host": "codex", "name": "Codex", "binary": "cdxhooks", "product": "Codex"}, "downloads": []any{item}}}, "support_files": []any{artifact("native_config.py"), artifact("hook_runtime.py")}})
		case strings.HasPrefix(path, "/api/artifact/"):
			if r.Host != strings.TrimPrefix(canonical.URL, "http://") {
				t.Error("installer ignored canonical base_url")
			}
			_, _ = w.Write(files[strings.TrimPrefix(path, "/api/artifact/")])
		case path == "/openapi/v1/agent-installations/exchange" || path == "/openapi/v1/ingest-tokens":
			authorization := r.Header.Get("Authorization")
			if strings.Contains(authorization, "expired") || credentialRevoked {
				http.Error(w, "expired/revoked", 401)
				return
			}
			data := map[string]any{"space_id": "team", "agent_uuid": agentUUID, "agent_product": "Codex", "endpoint": canonical.URL + "/v1/traces", "token": "ingest-short-token", "expires_in": 900}
			if strings.HasSuffix(path, "/exchange") {
				exchanges++
				if authorization != "Bearer aiv-install-ticket" {
					t.Error("wrong installation grant")
				}
				data["agent_credential"] = "aiv-agent-long-credential"
			} else if authorization != "Bearer aiv-agent-long-credential" {
				t.Error("ingest token must use Agent credential")
			}
			respond(data)
		case strings.HasSuffix(path, "/installation-complete"):
			confirmations++
			if failConfirmation {
				http.Error(w, "retry", 503)
				return
			}
			respond(map[string]any{"agent_uuid": agentUUID, "space_id": "team", "status": 1})
		case strings.HasSuffix(path, "/traces/list"):
			if r.Header.Get("Cookie") != "" || r.Header.Get("Authorization") != "Bearer sk-aiv-team-query-secret" {
				t.Error("query authentication mixed with account session")
			}
			if keyRevoked {
				http.Error(w, "revoked", 401)
				return
			}
			respond(map[string]any{"list": []any{map[string]any{"traceId": "fixture-trace"}}})
		case path == "/openapi/v1/spaces/team/agents":
			if keyRevoked {
				http.Error(w, "revoked", 401)
				return
			}
			respond([]any{map[string]any{"agent_product": "Codex"}})
		default:
			http.NotFound(w, r)
		}
	}
	canonical = httptest.NewServer(http.HandlerFunc(serve))
	defer canonical.Close()
	management = httptest.NewServer(http.HandlerFunc(serve))
	defer management.Close()
	input, err := os.CreateTemp(root, "password-")
	if err != nil {
		t.Fatal(err)
	}
	defer input.Close()
	_, _ = input.WriteString("test-password\n")
	_, _ = input.Seek(0, 0)
	oldStdin := os.Stdin
	os.Stdin = input
	defer func() { os.Stdin = oldStdin }()
	if err := cmdAuth([]string{"register", "--endpoint", management.URL, "--email", "new@example.test", "--name", "New", "--password-stdin"}); err != nil {
		t.Fatal(err)
	}
	if err := cmdAuth([]string{"status"}); err != nil {
		t.Fatal(err)
	}
	if err := cmdSpace([]string{"create", "--name", "Team"}); err != nil {
		t.Fatal(err)
	}
	connect := []string{"codex", "--config-dir", native, "--python", python}
	if err := cmdConnect(append(append([]string{}, connect...), "--dry-run")); err != nil {
		t.Fatal(err)
	}
	if creates != 0 || grants != 0 || exchanges != 0 {
		t.Fatal("dry-run mutated server state")
	}
	if _, err := os.Stat(native); !os.IsNotExist(err) {
		t.Fatal("dry-run wrote native config")
	}
	mu.Lock()
	legacyInstaller = true
	mu.Unlock()
	if err := cmdConnect(append(append([]string{}, connect...), "--collection-mode", "privacy")); err == nil {
		t.Fatal("legacy installer without mode preflight accepted")
	}
	if creates != 0 || grants != 0 || exchanges != 0 {
		t.Fatal("legacy installer created server state")
	}
	mu.Lock()
	legacyInstaller = false
	mu.Unlock()
	for _, invalid := range []string{"", "private", "full,privacy"} {
		if err := cmdConnect(append(append([]string{}, connect...), "--collection-mode", invalid)); err == nil {
			t.Fatalf("invalid collection mode %q accepted", invalid)
		}
	}
	// Reject a missing platform before creating the Agent or minting a grant.
	mu.Lock()
	privacySupported = false
	mu.Unlock()
	if err := cmdConnect(append(append([]string{}, connect...), "--collection-mode", "privacy")); err == nil {
		t.Fatal("unsupported privacy mode accepted")
	}
	if creates != 0 || grants != 0 || exchanges != 0 {
		t.Fatal("unsupported mode mutated server state")
	}
	mu.Lock()
	privacySupported = true
	missingPlatform = true
	mu.Unlock()
	if err := cmdConnect(connect); err == nil {
		t.Fatal("missing platform accepted")
	}
	if creates != 0 || grants != 0 {
		t.Fatal("preflight failure still mutated server")
	}
	mu.Lock()
	missingPlatform = false
	expiredGrant = true
	mu.Unlock()
	if err := cmdConnect(connect); err == nil {
		t.Fatal("expired grant accepted")
	}
	mu.Lock()
	expiredGrant = false
	failConfirmation = true
	mu.Unlock()
	if err := cmdConnect(connect); err == nil {
		t.Fatal("confirmation failure accepted")
	}
	if _, err := os.Stat(filepath.Join(native, "hooks.json")); !os.IsNotExist(err) {
		t.Fatal("native config not rolled back")
	}
	mu.Lock()
	failConfirmation = false
	mu.Unlock()
	for range 2 {
		if err := cmdConnect(connect); err != nil {
			t.Fatal(err)
		}
	}
	if exchanges != 1 {
		t.Fatalf("retry did not reuse redeemed credential: %d", exchanges)
	}
	readMode := func() string {
		t.Helper()
		data, err := os.ReadFile(filepath.Join(native, "aivision", "codex", "ingest.json"))
		if err != nil {
			t.Fatal(err)
		}
		var saved struct {
			Mode string `json:"collection_mode"`
		}
		if err := json.Unmarshal(data, &saved); err != nil {
			t.Fatal(err)
		}
		return saved.Mode
	}
	if readMode() != "full" {
		t.Fatal("new installation must default to full")
	}
	for _, mode := range []string{" Privacy ", ""} {
		argv := append([]string{}, connect...)
		if mode != "" {
			argv = append(argv, "--collection-mode", mode)
		}
		if err := cmdConnect(argv); err != nil {
			t.Fatal(err)
		}
		if readMode() != "privacy" {
			t.Fatal("privacy selection was not saved/preserved")
		}
	}
	mu.Lock()
	failConfirmation = true
	mu.Unlock()
	if err := cmdConnect(append(append([]string{}, connect...), "--collection-mode", "full")); err == nil {
		t.Fatal("failed reconfiguration accepted")
	}
	if readMode() != "privacy" {
		t.Fatal("failed reconfiguration lost prior privacy mode")
	}
	mu.Lock()
	failConfirmation = false
	mu.Unlock()
	explicitArgs := []string{"codex", "--base-url", canonical.URL, "--space-id", "team", "--agent-uuid", agentUUID, "--installation-token", "aiv-install-ticket", "--config-dir", native, "--python", python, "--collection-mode", "full"}
	if err := cmdConnect(explicitArgs); err != nil {
		t.Fatal(err)
	}
	if readMode() != "full" {
		t.Fatal("explicit browser command did not switch back to full")
	}
	var hooks struct {
		Hooks map[string][]any `json:"hooks"`
	}
	b, _ := os.ReadFile(filepath.Join(native, "hooks.json"))
	_ = json.Unmarshal(b, &hooks)
	for event, entries := range hooks.Hooks {
		if len(entries) != 1 {
			t.Fatalf("duplicate native hook %s", event)
		}
	}
	f, _ := config.Load()
	p, _ := f.Get("")
	if p.AgentProduct != "Codex" || p.Spaces["team"].Agents["codex"].BaseURL != canonical.URL {
		t.Fatal("query context not saved")
	}
	status := []string{"status", "codex", "--python", python, "--format", "json"}
	if err := cmdAgent(status); err != nil {
		t.Fatal(err)
	} // No query key required for installation/status.
	if err := cmdKey([]string{"create", "--name", "cli"}); err != nil {
		t.Fatal(err)
	}
	if err := cmdKey([]string{"list"}); err != nil {
		t.Fatal(err)
	}
	keyInput, err := os.CreateTemp(root, "key-")
	if err != nil {
		t.Fatal(err)
	}
	defer keyInput.Close()
	_, _ = keyInput.WriteString("sk-aiv-team-query-secret\n")
	_, _ = keyInput.Seek(0, 0)
	os.Stdin = keyInput
	if err := cmdKey([]string{"import", "--key-stdin"}); err != nil {
		t.Fatal(err)
	}
	if err := cmdAgent(status); err != nil {
		t.Fatal(err)
	}
	if err := cmdQuery([]string{"trace", "list", "--format", "json"}); err != nil {
		t.Fatal(err)
	}
	if err := cmdSpace([]string{"use", "personal"}); err != nil {
		t.Fatal(err)
	}
	if _, err := (&commonFlags{}).client(); err == nil {
		t.Fatal("key crossed space boundary")
	}
	if err := cmdSpace([]string{"use", "team"}); err != nil {
		t.Fatal(err)
	}
	if err := cmdKey([]string{"revoke", "7"}); err != nil {
		t.Fatal(err)
	}
	f, _ = config.Load()
	p, _ = f.Get("")
	if p.SpaceKey != "" {
		t.Fatal("revoked key remained in config")
	}
	mu.Lock()
	credentialRevoked = true
	mu.Unlock()
	if err := cmdAgent(status); err == nil {
		t.Fatal("revoked Agent credential passed")
	}
	if err := cmdAuth([]string{"logout"}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(native, "aivision", "codex", "ingest.json")); err != nil {
		t.Fatal("account logout removed Agent credentials")
	}
	if confirmations < 3 {
		t.Fatal(fmt.Sprintf("missing confirmation/retry: %d", confirmations))
	}
}
