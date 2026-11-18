// Package config stores account sessions and per-space query context in private
// named profiles. It never stores passwords, Agent reporting credentials or
// internal SigNoz keys. Legacy shell exports remain available explicitly.
package config

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// Environment variable names the CLI reads and writes.
const (
	EnvEndpoint     = "AIVISION_ENDPOINT"
	EnvSpaceID      = "AIVISION_SPACE_ID"
	EnvSpaceKey     = "AIVISION_SPACE_KEY"
	EnvAgentProduct = "AIVISION_AGENT_PRODUCT"
	EnvProfile      = "AIVISION_PROFILE"
	EnvConfigDir    = "AIVISION_CONFIG_DIR"

	DefaultName    = "default"
	SpaceKeyPrefix = "sk-aiv-"

	dirPerms  = 0o700
	filePerms = 0o600

	// Markers for the managed block installed into the user's shell rc.
	shellBlockBegin = "# >>> aivision env >>>"
	shellBlockEnd   = "# <<< aivision env <<<"
)

// Profile binds one platform/account, with separate query context per space.
type Profile struct {
	Endpoint     string                  `json:"endpoint"`
	SpaceID      string                  `json:"spaceId,omitempty"`
	SpaceKey     string                  `json:"spaceKey,omitempty"`
	AgentProduct string                  `json:"agentProduct,omitempty"`
	UpdatedAt    string                  `json:"updatedAt,omitempty"`
	QueryKeyID   string                  `json:"queryKeyId,omitempty"`
	Account      *Account                `json:"account,omitempty"`
	Cookies      []Cookie                `json:"cookies,omitempty"`
	Spaces       map[string]SpaceContext `json:"spaces,omitempty"`
}

type Account struct {
	ID    string `json:"user_uuid"`
	Email string `json:"email"`
	Name  string `json:"name"`
}

// Cookies retain their origin, path, secure flag and absolute expiry. In
// particular the refresh cookie must never be sent to ordinary API endpoints.
type Cookie struct {
	URL   string      `json:"url"`
	Value http.Cookie `json:"value"`
}

type SpaceContext struct {
	SpaceKey     string                  `json:"spaceKey,omitempty"`
	KeyID        string                  `json:"keyId,omitempty"`
	AgentProduct string                  `json:"agentProduct,omitempty"`
	Agents       map[string]AgentContext `json:"agents,omitempty"`
}

type AgentContext struct {
	UUID      string `json:"uuid"`
	ConfigDir string `json:"configDir,omitempty"`
	BaseURL   string `json:"baseUrl,omitempty"`
}

// SelectSpace projects only this space's credentials into the legacy fields.
func (p *Profile) SelectSpace(id string) {
	p.SpaceID = id
	s := p.Spaces[id]
	p.SpaceKey, p.QueryKeyID, p.AgentProduct = s.SpaceKey, s.KeyID, s.AgentProduct
}

func (p *Profile) SetSpaceContext(id string, s SpaceContext) {
	if p.Spaces == nil {
		p.Spaces = map[string]SpaceContext{}
	}
	p.Spaces[id] = s
	if p.SpaceID == id {
		p.SelectSpace(id)
	}
}

func (p *Profile) rememberSpace() {
	if p.Spaces == nil {
		p.Spaces = map[string]SpaceContext{}
	}
	if p.SpaceID == "" {
		return
	}
	s := p.Spaces[p.SpaceID]
	s.SpaceKey, s.KeyID, s.AgentProduct = p.SpaceKey, p.QueryKeyID, p.AgentProduct
	p.Spaces[p.SpaceID] = s
}

// File is the on-disk config: an active profile name plus the profile map.
type File struct {
	ActiveProfile string             `json:"activeProfile"`
	Profiles      map[string]Profile `json:"profiles"`

	path string
}

// Dir returns the config directory (~/.aivision, or $AIVISION_CONFIG_DIR).
func Dir() string {
	if d := os.Getenv(EnvConfigDir); d != "" {
		return d
	}
	home, err := os.UserHomeDir()
	if err != nil || home == "" {
		return ".aivision"
	}
	return filepath.Join(home, ".aivision")
}

// Path returns the config.json path.
func Path() string { return filepath.Join(Dir(), "config.json") }

// EnvFilePath returns the sourceable env.sh path.
func EnvFilePath() string { return filepath.Join(Dir(), "env.sh") }

// Load reads config.json, returning an empty file when it does not exist.
func Load() (*File, error) {
	f := &File{Profiles: map[string]Profile{}, ActiveProfile: DefaultName, path: Path()}
	b, err := os.ReadFile(f.path)
	if os.IsNotExist(err) {
		return f, nil
	}
	if err != nil {
		return nil, fmt.Errorf("read config %s: %w", f.path, err)
	}
	if err := json.Unmarshal(b, f); err != nil {
		return nil, fmt.Errorf("parse config %s: %w", f.path, err)
	}
	if f.Profiles == nil {
		f.Profiles = map[string]Profile{}
	}
	if f.ActiveProfile == "" {
		f.ActiveProfile = DefaultName
	}
	f.path = Path()
	for name, p := range f.Profiles {
		// Old flat profiles belong to their original space, never the next space.
		if p.Spaces == nil {
			p.rememberSpace()
		}
		p.SelectSpace(p.SpaceID)
		f.Profiles[name] = p
	}
	return f, nil
}

// Locked serializes the complete read/refresh/write operation across processes.
// Callers must save while holding this lock, not save a stale snapshot later.
func Locked(ctx context.Context, fn func(*File) error) error {
	unlock, err := lock(ctx)
	if err != nil {
		return err
	}
	defer unlock()
	f, err := Load()
	if err != nil {
		return err
	}
	return fn(f)
}

func Update(ctx context.Context, fn func(*File) error) error {
	return Locked(ctx, func(f *File) error {
		if err := fn(f); err != nil {
			return err
		}
		return f.Save()
	})
}

// Save writes config.json atomically with owner-only permissions.
func (f *File) Save() error {
	if err := os.MkdirAll(filepath.Dir(f.path), dirPerms); err != nil {
		return fmt.Errorf("create config dir: %w", err)
	}
	b, err := json.MarshalIndent(f, "", "  ")
	if err != nil {
		return err
	}
	return AtomicWrite(f.path, b)
}

func AtomicWrite(path string, b []byte) error {
	if err := os.MkdirAll(filepath.Dir(path), dirPerms); err != nil {
		return err
	}
	f, err := os.CreateTemp(filepath.Dir(path), ".aiv-*")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	if err = f.Chmod(filePerms); err == nil {
		_, err = f.Write(b)
	}
	if err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	return os.Rename(f.Name(), path)
}

// Active returns the active profile name.
func (f *File) Active() string {
	if f.ActiveProfile == "" {
		return DefaultName
	}
	return f.ActiveProfile
}

// Get returns a profile by name (empty name resolves to the active profile).
func (f *File) Get(name string) (Profile, bool) {
	if name == "" {
		name = f.Active()
	}
	p, ok := f.Profiles[name]
	return p, ok
}

// Set stores a profile and, when it is the first one, makes it active.
func (f *File) Set(name string, p Profile) {
	if name == "" {
		name = DefaultName
	}
	p.UpdatedAt = time.Now().Format(time.RFC3339)
	p.rememberSpace()
	if f.Profiles == nil {
		f.Profiles = map[string]Profile{}
	}
	f.Profiles[name] = p
	if f.ActiveProfile == "" {
		f.ActiveProfile = name
	}
}

// Delete removes a profile, returning whether it existed.
func (f *File) Delete(name string) bool {
	if name == "" {
		name = f.Active()
	}
	if _, ok := f.Profiles[name]; !ok {
		return false
	}
	delete(f.Profiles, name)
	if f.ActiveProfile == name {
		f.ActiveProfile = DefaultName
	}
	return true
}

// CheckPerms returns a human-readable warning when config.json or env.sh is
// readable by group/others, or "" when permissions are safe (or files absent).
func CheckPerms() string {
	var warns []string
	for _, path := range []string{Path(), EnvFilePath()} {
		fi, err := os.Stat(path)
		if err != nil {
			continue
		}
		if fi.Mode().Perm()&0o077 != 0 {
			warns = append(warns, fmt.Sprintf(
				"%s is readable by other users (mode %o); run: chmod 600 %s",
				path, fi.Mode().Perm(), path))
		}
	}
	return strings.Join(warns, "; ")
}

// ValidateSpaceKey checks the shape of a SpaceKey without contacting the server.
func ValidateSpaceKey(key string) error {
	k := strings.TrimSpace(key)
	if k == "" {
		return fmt.Errorf("space key is empty")
	}
	if !strings.HasPrefix(k, SpaceKeyPrefix) {
		return fmt.Errorf("space key must start with %q (got %q)", SpaceKeyPrefix, MaskKey(k))
	}
	if len(k) <= len(SpaceKeyPrefix) {
		return fmt.Errorf("space key looks truncated (only the %q prefix)", SpaceKeyPrefix)
	}
	return nil
}

// MaskedKey returns a safe-to-print masked form of the profile's SpaceKey.
func (p Profile) MaskedKey() string { return MaskKey(p.SpaceKey) }

// MaskKey renders a key as `sk-aiv-xxxx…` (never the full secret).
func MaskKey(k string) string {
	k = strings.TrimSpace(k)
	if k == "" {
		return "(none)"
	}
	const show = len(SpaceKeyPrefix) + 4
	if len(k) > show {
		return k[:show] + "…"
	}
	return "(redacted)"
}

// EnvExports explicitly clears absent values to avoid stale shell credentials.
func (p Profile) EnvExports(profileName string) []string {
	q := shellQuote
	lines := []string{"export " + EnvEndpoint + "=" + q(p.Endpoint)}
	lines = append(lines, "export "+EnvSpaceID+"="+q(p.SpaceID))
	lines = append(lines, "export "+EnvSpaceKey+"="+q(p.SpaceKey))
	lines = append(lines, "export "+EnvAgentProduct+"="+q(p.AgentProduct))
	if profileName != "" {
		lines = append(lines, "export "+EnvProfile+"="+q(profileName))
	}
	return lines
}

// WriteEnvFile writes ~/.aivision/env.sh (0600) for the given profile so a shell
// can `source` it. It is regenerated on every login.
func WriteEnvFile(name string, p Profile) error {
	if err := os.MkdirAll(Dir(), dirPerms); err != nil {
		return err
	}
	var sb strings.Builder
	sb.WriteString("# Generated by aiv auth login. Safe to source; owner-only.\n")
	sb.WriteString("# Usage: source " + EnvFilePath() + "\n")
	for _, l := range p.EnvExports(name) {
		sb.WriteString(l + "\n")
	}
	path := EnvFilePath()
	return AtomicWrite(path, []byte(sb.String()))
}

// RemoveEnvFile deletes ~/.aivision/env.sh (used by logout).
func RemoveEnvFile() { _ = os.Remove(EnvFilePath()) }

// --- managed shell rc block --------------------------------------------------

// ShellRCPath returns the rc file to install the source block into, based on
// $SHELL (zsh -> ~/.zshrc, bash -> ~/.bashrc, fish -> config.fish, else
// ~/.profile). Returns "" when the home directory is unknown.
func ShellRCPath() string {
	home, err := os.UserHomeDir()
	if err != nil || home == "" {
		return ""
	}
	switch shellFlavour() {
	case "fish":
		return filepath.Join(home, ".config", "fish", "config.fish")
	case "zsh":
		return filepath.Join(home, ".zshrc")
	case "bash":
		return filepath.Join(home, ".bashrc")
	default:
		return filepath.Join(home, ".profile")
	}
}

func shellFlavour() string {
	base := filepath.Base(os.Getenv("SHELL"))
	switch base {
	case "zsh", "bash", "fish":
		return base
	default:
		return "sh"
	}
}

// shellSourceLine renders the rc line that sources env.sh for the active shell.
func shellSourceLine() string {
	env := shellQuote(EnvFilePath())
	if shellFlavour() == "fish" {
		return "test -f " + env + "; and source " + env
	}
	return "[ -f " + env + " ] && source " + env
}

// InstallShellBlock writes a marked block that sources env.sh into the user's rc,
// idempotently replacing any prior block. Returns the rc path ("" when unknown).
func InstallShellBlock() (string, error) {
	rc := ShellRCPath()
	if rc == "" {
		return "", nil
	}
	block := shellBlockBegin + "\n" +
		"# Added by `aiv auth login`; removed by `aiv auth logout`.\n" +
		shellSourceLine() + "\n" +
		shellBlockEnd + "\n"
	existing, err := os.ReadFile(rc)
	if err != nil && !os.IsNotExist(err) {
		return rc, err
	}
	if err := os.MkdirAll(filepath.Dir(rc), dirPerms); err != nil {
		return rc, err
	}
	updated := replaceBlock(string(existing), block)
	if err := os.WriteFile(rc, []byte(updated), 0o644); err != nil {
		return rc, err
	}
	return rc, nil
}

// RemoveShellBlock strips the managed block from the rc (used by logout). It is
// a no-op when the rc or the block is absent.
func RemoveShellBlock() (string, error) {
	rc := ShellRCPath()
	if rc == "" {
		return "", nil
	}
	existing, err := os.ReadFile(rc)
	if os.IsNotExist(err) {
		return rc, nil
	}
	if err != nil {
		return rc, err
	}
	updated := replaceBlock(string(existing), "")
	if updated == string(existing) {
		return rc, nil
	}
	if err := os.WriteFile(rc, []byte(updated), 0o644); err != nil {
		return rc, err
	}
	return rc, nil
}

// replaceBlock swaps the region between the markers (inclusive) with block, or
// appends block when no markers exist. An empty block removes the region.
func replaceBlock(content, block string) string {
	i := strings.Index(content, shellBlockBegin)
	j := strings.Index(content, shellBlockEnd)
	if i >= 0 && j > i {
		rest := strings.TrimPrefix(content[j+len(shellBlockEnd):], "\n")
		before := strings.TrimRight(content[:i], "\n")
		if block == "" {
			if before == "" {
				return rest
			}
			return before + "\n" + rest
		}
		if before == "" {
			return block + rest
		}
		return before + "\n\n" + block + rest
	}
	if block == "" {
		return content
	}
	if strings.TrimSpace(content) == "" {
		return block
	}
	return strings.TrimRight(content, "\n") + "\n\n" + block
}

// shellQuote wraps s in single quotes, escaping embedded single quotes.
func shellQuote(s string) string {
	return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'"
}
