package runtime

import (
	"context"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/antgroup/ai-vision/aivision-cli/internal/blueprint"
)

func TestForceSourceFetchRequiresConfiguredOrigin(t *testing.T) {
	root := t.TempDir()
	origin := filepath.Join(root, "previous-origin")
	checkout := filepath.Join(root, "checkout")
	git := func(args ...string) string {
		t.Helper()
		out, err := exec.Command("git", args...).CombinedOutput()
		if err != nil {
			t.Fatalf("git %v: %v\n%s", args, err, out)
		}
		return strings.TrimSpace(string(out))
	}
	git("-c", "init.defaultBranch=main", "init", "--quiet", origin)
	git("-C", origin, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
		"commit", "--quiet", "--allow-empty", "-m", "initial source")
	git("clone", "--quiet", origin, checkout)
	selected := filepath.Join(root, "configured-origin")
	component := Component{Name: "backend", Repo: &blueprint.Repo{URL: selected, Branch: "main", Path: checkout}}
	app := &App{Log: NewLogger(false), Ctx: context.Background(), Force: true}

	if err := app.gitCloneOrFetch(component); err == nil {
		t.Fatal("forced source fetch accepted a checkout with a different origin")
	}
	if got := git("-C", checkout, "remote", "get-url", "origin"); got != origin {
		t.Fatalf("mismatched checkout origin changed to %q", got)
	}
	app.Force = false
	if err := app.gitCloneOrFetch(component); err != nil {
		t.Fatalf("a non-forced local checkout should be reusable: %v", err)
	}
	app.Force = true
	component.Repo.URL = origin
	if err := app.gitCloneOrFetch(component); err != nil {
		t.Fatalf("forced source fetch with the configured origin failed: %v", err)
	}
}
