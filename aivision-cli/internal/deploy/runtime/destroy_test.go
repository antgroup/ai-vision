package runtime

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"testing"
	"time"
)

func newDestroyTestApp(t *testing.T, workdir, sourceRoot string, repos ...Repo) *App {
	t.Helper()
	bp := &Blueprint{
		Metadata: Metadata{Namespace: "aivision"},
		Config:   Config{Workdir: workdir, SourceRoot: sourceRoot},
		Components: []Component{
			{Name: "demo", Kind: "process", Run: "true"},
		},
	}
	for i, repo := range repos {
		bp.Components = append(bp.Components, Component{
			Name: "repo" + strconv.Itoa(i),
			Kind: "process",
			Run:  "true",
			Repo: &repo,
		})
	}
	return &App{
		Log:    NewLogger(false),
		BP:     bp,
		State:  NewState(workdir, "aivision"),
		Vars:   map[string]string{"SOURCE_ROOT": sourceRoot, "WORKDIR": workdir},
		Ctx:    context.Background(),
		Docker: NewDocker(NewLogger(false)),
	}
}

// makeClone creates a checkout with the ownership marker aiv writes after a
// successful clone and records it with the given id.
func makeClone(t *testing.T, app *App, dir, recordedID, markerID string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Join(dir, ".git"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "file"), []byte("cloned"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := writeRepoOwner(dir, markerID); err != nil {
		t.Fatal(err)
	}
	if recordedID != "" {
		if err := app.State.RecordManagedRepo(dir, recordedID); err != nil {
			t.Fatal(err)
		}
	}
}

func writeTestPurgeMarker(t *testing.T, sourceRoot, tombstone string, repo ManagedRepo) {
	t.Helper()
	fs, err := openRepoFS(sourceRoot)
	if err != nil {
		t.Fatal(err)
	}
	defer fs.Close()
	if err := fs.writePurgeMarker(tombstone, repo); err != nil {
		t.Fatal(err)
	}
}

func TestDestroyPurgeValidatesStateBeforeTeardown(t *testing.T) {
	workdir := filepath.Join(t.TempDir(), "work")
	if err := os.MkdirAll(workdir, 0o755); err != nil {
		t.Fatal(err)
	}
	victim := filepath.Join(filepath.Dir(workdir), "victim")
	if err := os.MkdirAll(victim, 0o755); err != nil {
		t.Fatal(err)
	}
	teardownRan := filepath.Join(t.TempDir(), "teardown-ran")
	app := newDestroyTestApp(t, workdir, t.TempDir())
	app.BP.Components[0].Teardown = []Step{{Shell: "touch " + teardownRan}}
	app.State = NewState(workdir, "..")

	if err := app.Destroy(DestroyOpts{Purge: true}); err == nil {
		t.Fatal("destroy --purge accepted a state root outside workdir")
	}
	if _, err := os.Stat(teardownRan); !os.IsNotExist(err) {
		t.Fatalf("teardown ran before the state purge target was validated: %v", err)
	}
	if _, err := os.Stat(victim); err != nil {
		t.Fatalf("directory outside workdir was removed: %v", err)
	}
	if _, err := os.Stat(workdir); err != nil {
		t.Fatalf("workdir itself was removed: %v", err)
	}
}

func TestDestroyPurgeReposKeepsPreExistingCheckout(t *testing.T) {
	sourceRoot := t.TempDir()
	existing := filepath.Join(sourceRoot, "existing")
	if err := os.MkdirAll(existing, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(existing, "important.txt"), []byte("keep"), 0o600); err != nil {
		t.Fatal(err)
	}
	app := newDestroyTestApp(t, t.TempDir(), sourceRoot, Repo{Path: "existing"})

	if err := app.Destroy(DestroyOpts{Purge: true, PurgeRepos: true}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(existing, "important.txt")); err != nil {
		t.Fatalf("pre-existing checkout was removed: %v", err)
	}
}

func TestDestroyPurgeReposRefusesBlueprintReposOutsideSourceRoot(t *testing.T) {
	workdir := t.TempDir()
	base := t.TempDir()
	sourceRoot := filepath.Join(base, "src")
	victim := filepath.Join(base, "victim")
	if err := os.MkdirAll(victim, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(victim, "important.txt"), []byte("keep"), 0o600); err != nil {
		t.Fatal(err)
	}

	for name, repo := range map[string]Repo{
		"relative": {Path: "../victim"},
		"absolute": {Path: victim},
	} {
		t.Run(name, func(t *testing.T) {
			app := newDestroyTestApp(t, workdir, sourceRoot, repo)
			if err := app.Destroy(DestroyOpts{Purge: true, PurgeRepos: true}); err == nil {
				t.Fatal("destroy --purge-repos accepted a repo outside sourceRoot")
			}
			if _, err := os.Stat(filepath.Join(victim, "important.txt")); err != nil {
				t.Fatalf("directory outside sourceRoot was removed: %v", err)
			}
		})
	}
}

func TestDestroyPurgeReposDeletesVerifiedClone(t *testing.T) {
	sourceRoot := t.TempDir()
	cloned := filepath.Join(sourceRoot, "cloned")
	app := newDestroyTestApp(t, t.TempDir(), sourceRoot, Repo{Path: "cloned"})
	makeClone(t, app, cloned, "owner-id", "owner-id")

	if err := app.Destroy(DestroyOpts{Purge: true, PurgeRepos: true}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(cloned); !os.IsNotExist(err) {
		t.Fatalf("verified aiv clone still exists: %v", err)
	}
	repos, err := app.State.ManagedRepos()
	if err != nil || len(repos) != 0 {
		t.Fatalf("records after purge = %v, %v", repos, err)
	}
}

func TestDestroyPurgeReposKeepsCheckoutWithMismatchedOwner(t *testing.T) {
	sourceRoot := t.TempDir()
	replaced := filepath.Join(sourceRoot, "replaced")
	app := newDestroyTestApp(t, t.TempDir(), sourceRoot, Repo{Path: "replaced"})
	makeClone(t, app, replaced, "expected-id", "different-id")

	if err := app.Destroy(DestroyOpts{Purge: true, PurgeRepos: true}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(replaced, "file")); err != nil {
		t.Fatalf("checkout with mismatched ownership was removed: %v", err)
	}
}

func TestDestroyPurgeReposSkipsUnusableRecordsAndCleansOthers(t *testing.T) {
	workdir := t.TempDir()
	sourceRoot := t.TempDir()
	app := newDestroyTestApp(t, workdir, sourceRoot)

	outside := filepath.Join(t.TempDir(), "outside")
	makeClone(t, app, outside, "outside-id", "outside-id")
	// A component of a stale external record is now a regular file.
	if err := app.State.RecordManagedRepo(filepath.Join(outside, "file", "old"), "stale-id"); err != nil {
		t.Fatal(err)
	}
	makeClone(t, app, sourceRoot, "root-id", "root-id")
	valid := filepath.Join(sourceRoot, "valid")
	makeClone(t, app, valid, "valid-id", "valid-id")

	if err := app.Destroy(DestroyOpts{Purge: true, PurgeRepos: true}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(outside); err != nil {
		t.Fatalf("record outside sourceRoot was removed: %v", err)
	}
	if _, err := os.Stat(sourceRoot); err != nil {
		t.Fatalf("sourceRoot itself was removed: %v", err)
	}
	if _, err := os.Stat(valid); !os.IsNotExist(err) {
		t.Fatalf("eligible clone was not removed: %v", err)
	}
}

func TestDestroyPurgeOnlyKeepsRepoRecords(t *testing.T) {
	sourceRoot := t.TempDir()
	cloned := filepath.Join(sourceRoot, "cloned")
	app := newDestroyTestApp(t, t.TempDir(), sourceRoot, Repo{Path: "cloned"})
	makeClone(t, app, cloned, "owner-id", "owner-id")

	if err := app.Destroy(DestroyOpts{Purge: true}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(cloned, "file")); err != nil {
		t.Fatalf("state-only purge removed the clone: %v", err)
	}
	repos, err := app.State.ManagedRepos()
	if err != nil || len(repos) != 1 || repos[0].Path != cloned {
		t.Fatalf("records after state-only purge = %v, %v", repos, err)
	}

	if err := app.Destroy(DestroyOpts{Purge: true, PurgeRepos: true}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(cloned); !os.IsNotExist(err) {
		t.Fatalf("clone was not removed by the retry: %v", err)
	}
}

func TestDestroyPurgeReposKeepsRecordWhenRemovalFails(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("permission-based removal failure does not apply to root")
	}
	sourceRoot := t.TempDir()
	cloned := filepath.Join(sourceRoot, "cloned")
	app := newDestroyTestApp(t, t.TempDir(), sourceRoot, Repo{Path: "cloned"})
	makeClone(t, app, cloned, "owner-id", "owner-id")

	if err := os.Chmod(sourceRoot, 0o555); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chmod(sourceRoot, 0o755) })
	if err := app.Destroy(DestroyOpts{Purge: true, PurgeRepos: true}); err == nil {
		t.Fatal("destroy reported success after the repo removal failed")
	}
	repos, err := app.State.ManagedRepos()
	if err != nil || len(repos) != 1 || repos[0].Path != cloned {
		t.Fatalf("failed cleanup did not retain its record: %v, %v", repos, err)
	}

	if err := os.Chmod(sourceRoot, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := app.Destroy(DestroyOpts{Purge: true, PurgeRepos: true}); err != nil {
		t.Fatalf("retry after restoring permissions failed: %v", err)
	}
	if _, err := os.Stat(cloned); !os.IsNotExist(err) {
		t.Fatalf("checkout survived the retry: %v", err)
	}
	repos, err = app.State.ManagedRepos()
	if err != nil || len(repos) != 0 {
		t.Fatalf("record survived the retry: %v, %v", repos, err)
	}
}

func TestDestroyPurgeReposFinishesInterruptedRemoval(t *testing.T) {
	sourceRoot := t.TempDir()
	cloned := filepath.Join(sourceRoot, "cloned")
	app := newDestroyTestApp(t, t.TempDir(), sourceRoot, Repo{Path: "cloned"})
	makeClone(t, app, cloned, "owner-id", "owner-id")

	tombstone := app.purgeTombstone(ManagedRepo{Path: cloned, ID: "owner-id"})
	if err := app.State.SetRepoPending(cloned, "owner-id", true); err != nil {
		t.Fatal(err)
	}
	writeTestPurgeMarker(t, sourceRoot, tombstone, ManagedRepo{Path: cloned, ID: "owner-id"})
	if err := os.Rename(cloned, tombstone); err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(filepath.Join(tombstone, ".git", "aivision-clone.json")); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(tombstone, "partially-removed"), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}

	preview, err := app.PurgeRepoPreview()
	if err != nil {
		t.Fatal(err)
	}
	if len(preview) != 1 || preview[0] != cloned {
		t.Fatalf("preview = %v, want pending removal %s", preview, cloned)
	}

	if err := app.Destroy(DestroyOpts{Purge: true, PurgeRepos: true}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(tombstone); !os.IsNotExist(err) {
		t.Fatalf("pending removal survived: %v", err)
	}
	repos, err := app.State.ManagedRepos()
	if err != nil || len(repos) != 0 {
		t.Fatalf("record survived: %v, %v", repos, err)
	}
}

func TestDestroyPurgeReposRetriesAfterUnreadableRemovalMarker(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("permission-based marker read failure does not apply to root")
	}
	sourceRoot := t.TempDir()
	cloned := filepath.Join(sourceRoot, "cloned")
	app := newDestroyTestApp(t, t.TempDir(), sourceRoot, Repo{Path: "cloned"})
	makeClone(t, app, cloned, "owner-id", "owner-id")
	if err := app.State.SetRepoPending(cloned, "owner-id", true); err != nil {
		t.Fatal(err)
	}
	repo := ManagedRepo{Path: cloned, ID: "owner-id"}
	tombstone := app.purgeTombstone(repo)
	writeTestPurgeMarker(t, sourceRoot, tombstone, repo)
	if err := os.Rename(cloned, tombstone); err != nil {
		t.Fatal(err)
	}
	marker := purgeMarkerPath(tombstone)
	if err := os.Chmod(marker, 0); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chmod(marker, 0o600) })
	if err := app.Destroy(DestroyOpts{PurgeRepos: true}); !errors.Is(err, os.ErrPermission) {
		t.Fatalf("destroy did not return the marker read error: %v", err)
	}
	if _, err := os.Stat(filepath.Join(tombstone, "file")); err != nil {
		t.Fatalf("checkout did not survive the failed retry: %v", err)
	}
	repos, err := app.State.ManagedRepos()
	if err != nil || len(repos) != 1 || !repos[0].Pending {
		t.Fatalf("pending record was not preserved: %v, %v", repos, err)
	}
	if err := os.Chmod(marker, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := app.Destroy(DestroyOpts{PurgeRepos: true}); err != nil {
		t.Fatalf("retry after restoring marker access: %v", err)
	}
	for _, path := range []string{tombstone, marker} {
		if _, err := os.Stat(path); !os.IsNotExist(err) {
			t.Fatalf("%s survived the retry: %v", path, err)
		}
	}
	repos, err = app.State.ManagedRepos()
	if err != nil || len(repos) != 0 {
		t.Fatalf("record survived the retry: %v, %v", repos, err)
	}
}

func TestPurgePreviewRefusesIncompletePendingIdentity(t *testing.T) {
	sourceRoot := t.TempDir()
	cloned := filepath.Join(sourceRoot, "cloned")
	app := newDestroyTestApp(t, t.TempDir(), sourceRoot)
	makeClone(t, app, cloned, "owner-id", "owner-id")
	if err := app.State.SetRepoPending(cloned, "owner-id", true); err != nil {
		t.Fatal(err)
	}
	tombstone := app.purgeTombstone(ManagedRepo{Path: cloned, ID: "owner-id"})
	if err := os.Rename(cloned, tombstone); err != nil {
		t.Fatal(err)
	}
	marker, err := json.Marshal(purgeMarkerRecord{Path: cloned, ID: "owner-id"})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(purgeMarkerPath(tombstone), marker, 0o600); err != nil {
		t.Fatal(err)
	}
	if preview, err := app.PurgeRepoPreview(); err == nil || len(preview) != 0 {
		t.Fatalf("preview authorized a pending removal without directory identity: %v, %v", preview, err)
	}
}

func TestDestroyPurgeReposIgnoresMaliciousOwnerID(t *testing.T) {
	sourceRoot := t.TempDir()
	victim := filepath.Join(sourceRoot, "victim")
	if err := os.MkdirAll(victim, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(victim, "keep"), []byte("keep"), 0o600); err != nil {
		t.Fatal(err)
	}
	cloned := filepath.Join(sourceRoot, "cloned")
	app := newDestroyTestApp(t, t.TempDir(), sourceRoot, Repo{Path: "cloned"})
	makeClone(t, app, cloned, "../../victim", "../../victim")

	if err := app.Destroy(DestroyOpts{Purge: true, PurgeRepos: true}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(victim, "keep")); err != nil {
		t.Fatalf("unrelated directory was removed by a crafted owner id: %v", err)
	}
	if _, err := os.Stat(cloned); !os.IsNotExist(err) {
		t.Fatalf("checkout was not removed: %v", err)
	}
}

func TestDestroyPurgeReposPreservesUnrelatedTombstone(t *testing.T) {
	sourceRoot := t.TempDir()
	cloned := filepath.Join(sourceRoot, "cloned")
	app := newDestroyTestApp(t, t.TempDir(), sourceRoot, Repo{Path: "cloned"})
	makeClone(t, app, cloned, "owner-id", "owner-id")
	tombstone := app.purgeTombstone(ManagedRepo{Path: cloned, ID: "owner-id"})
	if err := os.RemoveAll(cloned); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(tombstone, "keep"), 0o755); err != nil {
		t.Fatal(err)
	}

	if err := app.Destroy(DestroyOpts{PurgeRepos: true}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(tombstone, "keep")); err != nil {
		t.Fatalf("unrelated directory at the tombstone path was removed: %v", err)
	}
}

func TestDestroyPurgeReposPreservesReplacementCheckout(t *testing.T) {
	sourceRoot := t.TempDir()
	cloned := filepath.Join(sourceRoot, "cloned")
	app := newDestroyTestApp(t, t.TempDir(), sourceRoot, Repo{Path: "cloned"})
	makeClone(t, app, cloned, "old-id", "old-id")

	tombstone := app.purgeTombstone(ManagedRepo{Path: cloned, ID: "old-id"})
	if err := app.State.SetRepoPending(cloned, "old-id", true); err != nil {
		t.Fatal(err)
	}
	writeTestPurgeMarker(t, sourceRoot, tombstone, ManagedRepo{Path: cloned, ID: "old-id"})
	if err := os.Rename(cloned, tombstone); err != nil {
		t.Fatal(err)
	}
	makeClone(t, app, cloned, "new-id", "new-id")

	if err := app.Destroy(DestroyOpts{PurgeRepos: true}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(cloned, "file")); err != nil {
		t.Fatalf("replacement checkout was removed: %v", err)
	}
	if _, err := os.Stat(tombstone); !os.IsNotExist(err) {
		t.Fatalf("pending removal survived: %v", err)
	}
	repos, err := app.State.ManagedRepos()
	if err != nil {
		t.Fatal(err)
	}
	if len(repos) != 1 || repos[0].ID != "new-id" || repos[0].Pending {
		t.Fatalf("replacement record = %v", repos)
	}
}

func TestDestroyPurgeReposRetiresCompletedPendingForReplacement(t *testing.T) {
	sourceRoot := t.TempDir()
	cloned := filepath.Join(sourceRoot, "cloned")
	app := newDestroyTestApp(t, t.TempDir(), sourceRoot, Repo{Path: "cloned"})
	makeClone(t, app, cloned, "old-id", "old-id")
	if err := app.State.SetRepoPending(cloned, "old-id", true); err != nil {
		t.Fatal(err)
	}
	repo := ManagedRepo{Path: cloned, ID: "old-id"}
	tombstone := app.purgeTombstone(repo)
	writeTestPurgeMarker(t, sourceRoot, tombstone, repo)
	// The old checkout is gone and no tombstone remains: deletion completed.
	if err := os.RemoveAll(cloned); err != nil {
		t.Fatal(err)
	}
	makeClone(t, app, cloned, "new-id", "new-id")

	if err := app.Destroy(DestroyOpts{PurgeRepos: true}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(cloned, "file")); err != nil {
		t.Fatalf("replacement checkout was removed: %v", err)
	}
	repos, err := app.State.ManagedRepos()
	if err != nil {
		t.Fatal(err)
	}
	if len(repos) != 1 || repos[0].ID != "new-id" || repos[0].Pending {
		t.Fatalf("records after retiring the completed removal = %v", repos)
	}
	if _, err := os.Stat(purgeMarkerPath(tombstone)); !os.IsNotExist(err) {
		t.Fatalf("pending-removal marker survived retirement: %v", err)
	}
}

func TestDestroyPurgeReposKeepsPendingOnInconclusiveOwnership(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("permission-based ownership failure does not apply to root")
	}
	sourceRoot := t.TempDir()
	cloned := filepath.Join(sourceRoot, "cloned")
	app := newDestroyTestApp(t, t.TempDir(), sourceRoot, Repo{Path: "cloned"})
	makeClone(t, app, cloned, "owner-id", "owner-id")
	if err := app.State.SetRepoPending(cloned, "owner-id", true); err != nil {
		t.Fatal(err)
	}
	gitDir := filepath.Join(cloned, ".git")
	if err := os.Chmod(gitDir, 0o000); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chmod(gitDir, 0o755) })

	if err := app.Destroy(DestroyOpts{PurgeRepos: true}); err == nil {
		t.Fatal("destroy retired a pending record without readable ownership evidence")
	}
	repos, err := app.State.ManagedRepos()
	if err != nil || len(repos) != 1 || !repos[0].Pending {
		t.Fatalf("pending record was not preserved: %v, %v", repos, err)
	}
}

func TestConcurrentDestroyKeepsPendingRemoval(t *testing.T) {
	workdir := t.TempDir()
	sourceRoot := t.TempDir()
	cloned := filepath.Join(sourceRoot, "cloned")
	app := newDestroyTestApp(t, workdir, sourceRoot, Repo{Path: "cloned"})
	makeClone(t, app, cloned, "owner-id", "owner-id")
	repo := ManagedRepo{Path: cloned, ID: "owner-id"}

	// Another invocation claimed the removal and was interrupted after the
	// rename, before the tombstone was deleted.
	if err := app.State.SetRepoPending(cloned, "owner-id", true); err != nil {
		t.Fatal(err)
	}
	tombstone := app.purgeTombstone(repo)
	writeTestPurgeMarker(t, sourceRoot, tombstone, repo)
	if err := os.Rename(cloned, tombstone); err != nil {
		t.Fatal(err)
	}

	// This invocation still holds a plan that saw the checkout as active.
	fs, err := openRepoFS(sourceRoot)
	if err != nil {
		t.Fatal(err)
	}
	defer fs.Close()
	if err := app.cleanupRepoRecords(fs, repoPurgePlan{orphans: []ManagedRepo{repo}}); err != nil {
		t.Fatal(err)
	}
	if err := app.removeManagedRepos(fs, []ManagedRepo{repo}); err != nil {
		t.Fatal(err)
	}
	repos, err := app.State.ManagedRepos()
	if err != nil {
		t.Fatal(err)
	}
	if len(repos) != 0 {
		t.Fatalf("stale plan left the pending record behind: %v", repos)
	}
	if _, err := os.Stat(tombstone); !os.IsNotExist(err) {
		t.Fatalf("pending record was retired while its tombstone survived: %v", err)
	}

	// The follow-up is idempotent.
	if err := app.Destroy(DestroyOpts{PurgeRepos: true}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(tombstone); !os.IsNotExist(err) {
		t.Fatalf("tombstone survived the retry: %v", err)
	}
	repos, err = app.State.ManagedRepos()
	if err != nil || len(repos) != 0 {
		t.Fatalf("record survived the retry: %v, %v", repos, err)
	}
}

func TestDestroySubprocessHelper(t *testing.T) {
	if os.Getenv("AIV_DESTROY_HELPER") != "1" {
		return
	}
	workdir := os.Getenv("AIV_HELPER_WORKDIR")
	sourceRoot := os.Getenv("AIV_HELPER_SOURCE")
	cloned := os.Getenv("AIV_HELPER_CLONE")
	repo := ManagedRepo{Path: cloned, ID: "owner-id"}
	app := newDestroyTestApp(t, workdir, sourceRoot)
	release, err := app.State.AcquireRepoRemovalLock(repo.Path, repo.ID)
	if err != nil {
		t.Fatal(err)
	}
	defer release()
	claimed, err := app.State.ClaimRepoPending(repo.Path, repo.ID)
	if err != nil || !claimed {
		t.Fatalf("claim pending = %v, %v", claimed, err)
	}
	fs, err := openRepoFS(sourceRoot)
	if err != nil {
		t.Fatal(err)
	}
	defer fs.Close()
	tombstone := app.purgeTombstone(repo)
	if err := fs.writePurgeMarker(tombstone, repo); err != nil {
		t.Fatal(err)
	}
	if err := fs.renameVerifiedCheckout(repo, tombstone); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(os.Getenv("AIV_HELPER_READY"), []byte("ready"), 0o600); err != nil {
		t.Fatal(err)
	}
	// The parent kills this process before deferred cleanup can release the lock.
	time.Sleep(10 * time.Second)
	t.Fatal("parent did not terminate the lock holder")
}

func TestDestroyConcurrentProcessFinishesPendingRemoval(t *testing.T) {
	if runtime.GOOS != "linux" && runtime.GOOS != "darwin" {
		t.Skip("deployment crash recovery runs on Linux and macOS")
	}
	workdir := t.TempDir()
	sourceRoot := t.TempDir()
	cloned := filepath.Join(sourceRoot, "cloned")
	app := newDestroyTestApp(t, workdir, sourceRoot, Repo{Path: "cloned"})
	makeClone(t, app, cloned, "owner-id", "owner-id")
	tombstone := app.purgeTombstone(ManagedRepo{Path: cloned, ID: "owner-id"})

	signals := t.TempDir()
	ready := filepath.Join(signals, "ready")
	cmd := exec.Command(os.Args[0], "-test.run=^TestDestroySubprocessHelper$")
	cmd.Env = append(os.Environ(),
		"AIV_DESTROY_HELPER=1",
		"AIV_HELPER_WORKDIR="+workdir,
		"AIV_HELPER_SOURCE="+sourceRoot,
		"AIV_HELPER_CLONE="+cloned,
		"AIV_HELPER_READY="+ready,
	)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	waited := false
	defer func() {
		if !waited {
			_ = cmd.Process.Kill()
			_ = cmd.Wait()
		}
	}()
	deadline := time.Now().Add(10 * time.Second)
	for {
		if _, err := os.Stat(ready); err == nil {
			break
		}
		if time.Now().After(deadline) {
			_ = cmd.Process.Kill()
			_ = cmd.Wait()
			waited = true
			t.Fatalf("helper did not reach the interrupted state: %s", stderr.String())
		}
		time.Sleep(10 * time.Millisecond)
	}

	errCh := make(chan error, 1)
	go func() { errCh <- app.Destroy(DestroyOpts{PurgeRepos: true}) }()
	select {
	case err := <-errCh:
		t.Fatalf("destroy finished while another process held the removal lock: %v", err)
	case <-time.After(250 * time.Millisecond):
	}
	if err := cmd.Process.Kill(); err != nil {
		t.Fatal(err)
	}
	err := cmd.Wait()
	waited = true
	var interrupted *exec.ExitError
	if !errors.As(err, &interrupted) || interrupted.ExitCode() != -1 {
		t.Fatalf("lock holder did not exit by signal: %v\n%s", err, stderr.String())
	}
	select {
	case err := <-errCh:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("destroy did not resume after the helper released the lock")
	}
	if _, err := os.Stat(cloned); !os.IsNotExist(err) {
		t.Fatalf("checkout survived: %v", err)
	}
	if _, err := os.Stat(tombstone); !os.IsNotExist(err) {
		t.Fatalf("pending removal survived: %v", err)
	}
	repos, err := app.State.ManagedRepos()
	if err != nil || len(repos) != 0 {
		t.Fatalf("record survived: %v, %v", repos, err)
	}
}

func TestRepoFSRefusesSwappedSymlink(t *testing.T) {
	sourceRoot := t.TempDir()
	victim := t.TempDir()
	if err := os.WriteFile(filepath.Join(victim, "keep"), []byte("keep"), 0o600); err != nil {
		t.Fatal(err)
	}
	child := filepath.Join(sourceRoot, "child")
	if err := os.MkdirAll(child, 0o755); err != nil {
		t.Fatal(err)
	}
	fs, err := openRepoFS(sourceRoot)
	if err != nil {
		t.Fatal(err)
	}
	defer fs.Close()
	if err := os.Remove(child); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(victim, child); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	if dir, _, err := fs.openDirectory(child); err == nil {
		_ = dir.Close()
		t.Fatal("directory access followed a symlink outside the source root")
	}
	if _, err := os.Stat(filepath.Join(victim, "keep")); err != nil {
		t.Fatalf("directory outside the source root was removed: %v", err)
	}
}

func TestDestroyPurgeReposToleratesMissingSourceRoot(t *testing.T) {
	sourceRoot := filepath.Join(t.TempDir(), "missing")
	app := newDestroyTestApp(t, t.TempDir(), sourceRoot)
	if err := app.Destroy(DestroyOpts{Purge: true, PurgeRepos: true}); err != nil {
		t.Fatal(err)
	}
}

func TestPurgePreviewExcludesRemovalPathConflict(t *testing.T) {
	sourceRoot := t.TempDir()
	cloned := filepath.Join(sourceRoot, "cloned")
	app := newDestroyTestApp(t, t.TempDir(), sourceRoot, Repo{Path: "cloned"})
	makeClone(t, app, cloned, "owner-id", "owner-id")
	tombstone := app.purgeTombstone(ManagedRepo{Path: cloned, ID: "owner-id"})
	if err := os.MkdirAll(filepath.Join(tombstone, "keep"), 0o755); err != nil {
		t.Fatal(err)
	}

	preview, err := app.PurgeRepoPreview()
	if err != nil {
		t.Fatal(err)
	}
	if len(preview) != 0 {
		t.Fatalf("preview listed a candidate with a removal-path conflict: %v", preview)
	}
	if err := app.Destroy(DestroyOpts{PurgeRepos: true}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(cloned, "file")); err != nil {
		t.Fatalf("checkout was removed despite the conflict: %v", err)
	}
	if _, err := os.Stat(filepath.Join(tombstone, "keep")); err != nil {
		t.Fatalf("conflicting directory was removed: %v", err)
	}
}

func TestDestroyPurgeReposPreservesUnmarkedPendingTombstone(t *testing.T) {
	sourceRoot := t.TempDir()
	cloned := filepath.Join(sourceRoot, "cloned")
	app := newDestroyTestApp(t, t.TempDir(), sourceRoot, Repo{Path: "cloned"})
	makeClone(t, app, cloned, "owner-id", "owner-id")
	tombstone := app.purgeTombstone(ManagedRepo{Path: cloned, ID: "owner-id"})
	if err := app.State.SetRepoPending(cloned, "owner-id", true); err != nil {
		t.Fatal(err)
	}
	if err := os.RemoveAll(cloned); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(tombstone, "keep"), 0o755); err != nil {
		t.Fatal(err)
	}

	if err := app.Destroy(DestroyOpts{PurgeRepos: true}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(tombstone, "keep")); err != nil {
		t.Fatalf("unmarked pending-removal directory was removed: %v", err)
	}
}

func TestDestroyPurgeReposKeepsTombstoneWhenRenameNeverHappened(t *testing.T) {
	sourceRoot := t.TempDir()
	cloned := filepath.Join(sourceRoot, "cloned")
	app := newDestroyTestApp(t, t.TempDir(), sourceRoot, Repo{Path: "cloned"})
	makeClone(t, app, cloned, "owner-id", "owner-id")
	repo := ManagedRepo{Path: cloned, ID: "owner-id"}
	tombstone := app.purgeTombstone(repo)
	if err := app.State.SetRepoPending(cloned, "owner-id", true); err != nil {
		t.Fatal(err)
	}
	// Crash window: the marker is durable but the rename has not run yet.
	writeTestPurgeMarker(t, sourceRoot, tombstone, repo)
	if err := os.MkdirAll(filepath.Join(tombstone, "keep"), 0o755); err != nil {
		t.Fatal(err)
	}

	if err := app.Destroy(DestroyOpts{PurgeRepos: true}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(cloned, "file")); err != nil {
		t.Fatalf("original checkout was removed: %v", err)
	}
	if _, err := os.Stat(filepath.Join(tombstone, "keep")); err != nil {
		t.Fatalf("unrelated directory at the tombstone path was removed: %v", err)
	}
	repos, err := app.State.ManagedRepos()
	if err != nil || len(repos) != 1 || !repos[0].Pending {
		t.Fatalf("pending record = %v, %v", repos, err)
	}
}

func TestDestroyPurgeReposUsesConfirmedCandidates(t *testing.T) {
	sourceRoot := t.TempDir()
	first := filepath.Join(sourceRoot, "first")
	second := filepath.Join(sourceRoot, "second")
	app := newDestroyTestApp(t, t.TempDir(), sourceRoot, Repo{Path: "first"}, Repo{Path: "second"})
	makeClone(t, app, first, "first-id", "first-id")
	makeClone(t, app, second, "second-id", "second-id")

	planned, err := app.PurgeRepoPlan()
	if err != nil {
		t.Fatal(err)
	}
	confirmed := make([]ManagedRepo, 0, len(planned))
	for _, repo := range planned {
		if repo.Path == first {
			confirmed = append(confirmed, repo)
		}
	}
	if err := app.Destroy(DestroyOpts{PurgeRepos: true, ConfirmedRepos: confirmed}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(first); !os.IsNotExist(err) {
		t.Fatalf("confirmed checkout survived: %v", err)
	}
	if _, err := os.Stat(filepath.Join(second, "file")); err != nil {
		t.Fatalf("unconfirmed checkout was removed: %v", err)
	}
}

func TestDestroyPurgeReposSkipsDisabledComponentPath(t *testing.T) {
	sourceRoot := t.TempDir()
	cloned := filepath.Join(sourceRoot, "cloned")
	app := newDestroyTestApp(t, t.TempDir(), sourceRoot, Repo{Path: "../outside"}, Repo{Path: "cloned"})
	app.BP.Components[1].Enabled = "false"
	makeClone(t, app, cloned, "owner-id", "owner-id")

	if err := app.Destroy(DestroyOpts{PurgeRepos: true}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(cloned); !os.IsNotExist(err) {
		t.Fatalf("eligible checkout survived: %v", err)
	}
}

func TestPurgeTombstoneNameIsBounded(t *testing.T) {
	sourceRoot := t.TempDir()
	longName := strings.Repeat("a", 240)
	cloned := filepath.Join(sourceRoot, longName)
	app := newDestroyTestApp(t, t.TempDir(), sourceRoot, Repo{Path: longName})
	makeClone(t, app, cloned, "owner-id", "owner-id")
	tombstone := app.purgeTombstone(ManagedRepo{Path: cloned, ID: "owner-id"})
	if name := filepath.Base(tombstone); len(name) > 64 {
		t.Fatalf("tombstone name is too long: %d bytes", len(name))
	}

	if err := app.Destroy(DestroyOpts{PurgeRepos: true}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(cloned); !os.IsNotExist(err) {
		t.Fatalf("long-named checkout survived: %v", err)
	}
}

func TestPurgeRepoPreviewMatchesRemovalCandidates(t *testing.T) {
	sourceRoot := t.TempDir()
	app := newDestroyTestApp(t, t.TempDir(), sourceRoot)
	verified := filepath.Join(sourceRoot, "verified")
	replaced := filepath.Join(sourceRoot, "replaced")
	makeClone(t, app, verified, "verified-id", "verified-id")
	makeClone(t, app, replaced, "expected-id", "different-id")

	preview, err := app.PurgeRepoPreview()
	if err != nil {
		t.Fatal(err)
	}
	if len(preview) != 1 || preview[0] != verified {
		t.Fatalf("preview = %v, want [%s]", preview, verified)
	}
}

func TestDestroyPurgeReposRefusesRecordPathOutsideWorkdir(t *testing.T) {
	workdir := filepath.Join(t.TempDir(), "work")
	if err := os.MkdirAll(workdir, 0o755); err != nil {
		t.Fatal(err)
	}
	app := newDestroyTestApp(t, workdir, t.TempDir())
	app.State = NewState(workdir, "../../escape")
	teardownRan := filepath.Join(t.TempDir(), "teardown-ran")
	app.BP.Components[0].Teardown = []Step{{Shell: "touch " + teardownRan}}

	recordPath := filepath.Join(filepath.Dir(workdir), "escape.json")
	if err := os.MkdirAll(filepath.Dir(recordPath), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(recordPath, []byte("pending"), 0o600); err != nil {
		t.Fatal(err)
	}

	if err := app.Destroy(DestroyOpts{PurgeRepos: true}); err == nil {
		t.Fatal("destroy accepted ownership records outside workdir")
	}
	if _, err := os.Stat(teardownRan); !os.IsNotExist(err) {
		t.Fatalf("teardown ran before record targets were validated: %v", err)
	}
	data, err := os.ReadFile(recordPath)
	if err != nil || string(data) != "pending" {
		t.Fatalf("pending record changed on refusal: %q, %v", data, err)
	}
}
