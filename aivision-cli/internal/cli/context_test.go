package cli

import (
	"context"
	"os"
	"strings"
	"testing"

	"github.com/antgroup/ai-vision/aivision-cli/internal/config"
)

func TestLegacyMigrationAndQueryIsolation(t *testing.T) {
	t.Setenv(config.EnvConfigDir, t.TempDir())
	t.Setenv(config.EnvProfile, "")
	raw := `{"activeProfile":"old","profiles":{"old":{"endpoint":"https://one.test","spaceId":"one","spaceKey":"sk-aiv-original-secret","agentProduct":"codex"}}}`
	if err := os.WriteFile(config.Path(), []byte(raw), 0600); err != nil {
		t.Fatal(err)
	}
	f, err := config.Load()
	if err != nil {
		t.Fatal(err)
	}
	p, _ := f.Get("")
	if p.Spaces["one"].SpaceKey != p.SpaceKey {
		t.Fatal("legacy key not migrated to original space")
	}
	t.Setenv(config.EnvSpaceKey, "sk-aiv-stale-shell")
	t.Setenv(config.EnvAgentProduct, "codex")
	if _, err := (&commonFlags{}).client(); err != nil {
		t.Fatal(err)
	}
	if _, err := (&commonFlags{space: "two"}).client(); err == nil {
		t.Fatal("explicit space inherited old key")
	}
	if _, err := (&commonFlags{endpoint: "https://two.test"}).client(); err == nil {
		t.Fatal("explicit endpoint inherited old key")
	}
	if err := config.Update(context.Background(), func(f *config.File) error { p, _ := f.Get(""); p.SelectSpace("two"); f.Set("old", p); return nil }); err != nil {
		t.Fatal(err)
	}
	if _, err := (&commonFlags{}).client(); err == nil {
		t.Fatal("new space inherited shell key")
	}
	f, _ = config.Load()
	p, _ = f.Get("")
	p.SelectSpace("one")
	if p.SpaceKey != "sk-aiv-original-secret" {
		t.Fatal("switch lost original key")
	}
	if err := config.Update(context.Background(), func(f *config.File) error { f.Set("old", p); return nil }); err != nil {
		t.Fatal(err)
	}
	if _, err := (&commonFlags{}).client(); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(strings.Join((config.Profile{}).EnvExports("old"), "\n"), "AIVISION_SPACE_KEY=''") {
		t.Fatal("empty exports must clear stale values")
	}
}

func TestAuthModesDoNotMix(t *testing.T) {
	if err := authLogin([]string{"--email", "one@example.test", "--space-key", "sk-aiv-secret"}); err == nil {
		t.Fatal("mixed authentication modes accepted")
	}
}

func TestTraceStatusRejectsUnknownResponses(t *testing.T) {
	for _, raw := range []string{`{"error":"not rows"}`, `{"columns":[{"name":"traceId"}]}`} {
		if _, err := traceCount([]byte(raw)); err == nil {
			t.Fatal("unknown response mistaken for trace data")
		}
	}
	for _, raw := range []string{`[]`, `{"data":[]}`, `{"data":{"list":[]}}`} {
		if n, err := traceCount([]byte(raw)); err != nil || n != 0 {
			t.Fatalf("empty trace list: %d %v", n, err)
		}
	}
	if n, err := traceCount([]byte(`{"list":[{"traceId":"abc"}]}`)); err != nil || n != 1 {
		t.Fatalf("trace count: %d %v", n, err)
	}
}
