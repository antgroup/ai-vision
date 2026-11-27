package runtime

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/antgroup/ai-vision/aivision-cli/internal/blueprint"
)

func TestForceSourceFetchRequiresConfiguredOrigin(t *testing.T) {
	isolateGitFixture(t)
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

func TestCloneRecordsManagedRepo(t *testing.T) {
	isolateGitFixture(t)
	root := t.TempDir()
	origin := filepath.Join(root, "origin")
	runGit := func(args ...string) string {
		t.Helper()
		out, err := exec.Command("git", args...).CombinedOutput()
		if err != nil {
			t.Fatalf("git %v: %v\n%s", args, err, out)
		}
		return strings.TrimSpace(string(out))
	}
	runGit("-c", "init.defaultBranch=main", "init", "--quiet", origin)
	runGit("-C", origin, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
		"commit", "--quiet", "--allow-empty", "-m", "initial source")

	sourceRoot := filepath.Join(root, "src")
	workdir := filepath.Join(root, "work")
	app := &App{
		Log:   NewLogger(false),
		Ctx:   context.Background(),
		State: NewState(workdir, "aivision"),
		Vars:  map[string]string{"SOURCE_ROOT": sourceRoot},
	}
	component := Component{Name: "backend", Repo: &blueprint.Repo{URL: origin, Branch: "main", Path: "backend"}}
	if err := app.gitCloneOrFetch(component); err != nil {
		t.Fatal(err)
	}
	repos, err := app.State.ManagedRepos()
	if err != nil {
		t.Fatal(err)
	}
	want := filepath.Join(sourceRoot, "backend")
	if len(repos) != 1 || repos[0].Path != want || repos[0].ID == "" {
		t.Fatalf("managed repos = %v, want [%s]", repos, want)
	}
	if _, err := os.Stat(filepath.Join(want, ".git")); err != nil {
		t.Fatalf("clone was not created: %v", err)
	}
	fs, err := openRepoFS(sourceRoot)
	if err != nil {
		t.Fatal(err)
	}
	defer fs.Close()
	owner, err := fs.checkoutOwner(want)
	if err != nil || owner.ID != repos[0].ID {
		t.Fatalf("checkout ownership = %v, %v; record = %q", owner, err, repos[0].ID)
	}
}

func TestCloneAtSourceRootIsNotRecorded(t *testing.T) {
	isolateGitFixture(t)
	root := t.TempDir()
	origin := filepath.Join(root, "origin")
	runGit := func(args ...string) {
		t.Helper()
		out, err := exec.Command("git", args...).CombinedOutput()
		if err != nil {
			t.Fatalf("git %v: %v\n%s", args, err, out)
		}
	}
	runGit("-c", "init.defaultBranch=main", "init", "--quiet", origin)
	runGit("-C", origin, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
		"commit", "--quiet", "--allow-empty", "-m", "initial source")

	sourceRoot := filepath.Join(root, "src")
	app := &App{
		Log:   NewLogger(false),
		Ctx:   context.Background(),
		State: NewState(filepath.Join(root, "work"), "aivision"),
		Vars:  map[string]string{"SOURCE_ROOT": sourceRoot},
	}
	component := Component{Name: "backend", Repo: &blueprint.Repo{URL: origin, Branch: "main", Path: "."}}
	if err := app.gitCloneOrFetch(component); err != nil {
		t.Fatal(err)
	}
	repos, err := app.State.ManagedRepos()
	if err != nil {
		t.Fatal(err)
	}
	if len(repos) != 0 {
		t.Fatalf("clone at sourceRoot was recorded: %v", repos)
	}
}

// User-installed Git hooks can write into a fixture after the CLI returns.
func isolateGitFixture(t *testing.T) {
	t.Helper()
	t.Setenv("GIT_CONFIG_GLOBAL", os.DevNull)
	t.Setenv("GIT_TEMPLATE_DIR", t.TempDir())
}

func TestReusedCheckoutRecoversMissingRecord(t *testing.T) {
	root := t.TempDir()
	sourceRoot := filepath.Join(root, "src")
	checkout := filepath.Join(sourceRoot, "backend")
	if err := os.MkdirAll(filepath.Join(checkout, ".git"), 0o755); err != nil {
		t.Fatal(err)
	}
	app := &App{
		Log:   NewLogger(false),
		Ctx:   context.Background(),
		State: NewState(filepath.Join(root, "work"), "aivision"),
		Vars:  map[string]string{"SOURCE_ROOT": sourceRoot},
	}
	if err := writeRepoOwner(checkout, "recovered-id", app.repoOwnerScope()); err != nil {
		t.Fatal(err)
	}
	component := Component{Name: "backend", Repo: &blueprint.Repo{
		URL: "https://example.invalid/repo.git", Branch: "main", Path: "backend",
	}}
	if err := app.gitCloneOrFetch(component); err != nil {
		t.Fatal(err)
	}
	repos, err := app.State.ManagedRepos()
	if err != nil {
		t.Fatal(err)
	}
	if len(repos) != 1 || repos[0].Path != checkout || repos[0].ID != "recovered-id" {
		t.Fatalf("recovered records = %v", repos)
	}
}

func TestReusedCheckoutDoesNotRecoverForeignRegistration(t *testing.T) {
	root := t.TempDir()
	sourceRoot := filepath.Join(root, "src")
	checkout := filepath.Join(sourceRoot, "backend")
	if err := os.MkdirAll(filepath.Join(checkout, ".git"), 0o755); err != nil {
		t.Fatal(err)
	}
	app := &App{
		Log:   NewLogger(false),
		Ctx:   context.Background(),
		State: NewState(filepath.Join(root, "work"), "other-namespace"),
		Vars:  map[string]string{"SOURCE_ROOT": sourceRoot},
	}
	if err := writeRepoOwner(checkout, "foreign-id", "another-workdir\x00another-namespace"); err != nil {
		t.Fatal(err)
	}
	component := Component{Name: "backend", Repo: &blueprint.Repo{
		URL: "https://example.invalid/repo.git", Branch: "main", Path: "backend",
	}}
	if err := app.gitCloneOrFetch(component); err != nil {
		t.Fatal(err)
	}
	repos, err := app.State.ManagedRepos()
	if err != nil {
		t.Fatal(err)
	}
	if len(repos) != 0 {
		t.Fatalf("foreign checkout was recorded: %v", repos)
	}
}

func TestReusedCheckoutOnlyRegistersOriginalClone(t *testing.T) {
	source := t.TempDir()
	checkout := filepath.Join(source, "original")
	backup := filepath.Join(source, "backup")
	app := newDestroyTestApp(t, t.TempDir(), source, Repo{URL: "unused", Path: backup}, Repo{URL: "unused", Path: checkout})
	makeClone(t, app, checkout, "id", "id")
	if err := writeRepoOwner(checkout, "id", app.repoOwnerScope()); err != nil {
		t.Fatal(err)
	}
	// Keep a user backup and restore a copy, including .git, at the old path.
	if err := os.Rename(checkout, backup); err != nil {
		t.Fatal(err)
	}
	if err := os.CopyFS(checkout, os.DirFS(backup)); err != nil {
		t.Fatal(err)
	}
	if err := app.Forge(); err != nil {
		t.Fatal(err)
	}
	plan, err := app.PurgeRepoPlan()
	if err != nil || len(plan) != 0 {
		t.Fatalf("restored or relocated checkout eligible for deletion: %v, %v", plan, err)
	}
	records, err := app.State.ManagedRepos()
	if err != nil || len(records) != 1 || records[0].Path != checkout {
		t.Fatalf("user backup acquired a new record: %v, %v", records, err)
	}
}

func TestReusedCheckoutKeepsUnboundMarker(t *testing.T) {
	source := t.TempDir()
	checkout := filepath.Join(source, "checkout")
	app := newDestroyTestApp(t, t.TempDir(), source)
	makeClone(t, app, checkout, "id", "id")
	// Draft builds wrote a random ID without the clone's directory identity.
	if err := os.WriteFile(filepath.Join(checkout, repoOwnerMarker), []byte(`{"id":"id"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	plan, err := app.PurgeRepoPlan()
	if err != nil || len(plan) != 0 {
		t.Fatalf("unbound checkout eligible for deletion: %v, %v", plan, err)
	}
}

func TestDestroyPurgeReposKeepsReplacementGitWorktree(t *testing.T) {
	source := t.TempDir()
	app := newDestroyTestApp(t, t.TempDir(), source)
	replaced := filepath.Join(source, "worktree")
	if err := os.Mkdir(replaced, 0o755); err != nil {
		t.Fatal(err)
	}
	// Linked worktrees and submodules have a .git file, rather than a .git directory.
	gitfile := filepath.Join(replaced, ".git")
	if err := os.WriteFile(gitfile, []byte("gitdir: elsewhere\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := app.State.RecordManagedRepo(replaced, "old"); err != nil {
		t.Fatal(err)
	}
	cloned := filepath.Join(source, "eligible")
	makeClone(t, app, cloned, "current", "current")
	if err := app.Destroy(DestroyOpts{PurgeRepos: true}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(gitfile); err != nil {
		t.Fatalf("replacement worktree was touched: %v", err)
	}
	if _, err := os.Stat(cloned); !os.IsNotExist(err) {
		t.Fatalf("eligible checkout was not removed: %v", err)
	}
}

func TestReusedCheckoutReportsFailedRegistration(t *testing.T) {
	source := t.TempDir()
	checkout := filepath.Join(source, "checkout")
	workdir := t.TempDir()
	app := newDestroyTestApp(t, workdir, source, Repo{URL: "unused", Path: checkout})
	makeClone(t, app, checkout, "", "id")
	if err := writeRepoOwner(checkout, "id", app.repoOwnerScope()); err != nil {
		t.Fatal(err)
	}
	store := filepath.Join(workdir, ".aivision-repos")
	if err := os.WriteFile(store, []byte("unavailable"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := app.Forge(); err == nil {
		t.Fatal("forge reported success while registration was still failing")
	}
	if err := os.Remove(store); err != nil {
		t.Fatal(err)
	}
	if err := app.Forge(); err != nil {
		t.Fatal(err)
	}
	records, err := app.State.ManagedRepos()
	if err != nil || len(records) != 1 || records[0].Path != checkout {
		t.Fatalf("registration did not recover: %v, %v", records, err)
	}
}
