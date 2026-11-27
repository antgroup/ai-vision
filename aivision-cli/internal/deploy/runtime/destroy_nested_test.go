package runtime

import (
	"os"
	"path/filepath"
	"testing"
)

func TestDestroyPurgeReposHonorsDeclaredNestedCheckouts(t *testing.T) {
	for _, mode := range []string{"user worktree", "both managed", "inner not confirmed", "appeared after preview", "shared monorepo"} {
		t.Run(mode, func(t *testing.T) {
			src := t.TempDir()
			outer := filepath.Join(src, "outer")
			inner := filepath.Join(outer, "inner")
			nested := Repo{Path: "outer/inner"}
			if mode == "shared monorepo" {
				nested = Repo{Path: "outer", Subdir: "inner"}
			}
			app := newDestroyTestApp(t, t.TempDir(), src, Repo{Path: "outer"}, nested)
			makeClone(t, app, outer, "outer-id", "outer-id")
			addWorktree := func() {
				t.Helper()
				if err := os.MkdirAll(inner, 0o755); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(filepath.Join(inner, ".git"), []byte("gitdir: elsewhere\n"), 0o600); err != nil {
					t.Fatal(err)
				}
			}
			switch mode {
			case "user worktree":
				addWorktree()
			case "both managed", "inner not confirmed":
				makeClone(t, app, inner, "inner-id", "inner-id")
			case "shared monorepo":
				if err := os.Mkdir(inner, 0o755); err != nil {
					t.Fatal(err)
				}
			}
			plan, err := app.PurgeRepoPlan()
			if err != nil {
				t.Fatal(err)
			}
			switch mode {
			case "user worktree":
				if len(plan) != 0 {
					t.Fatalf("preview included parent of user checkout: %v", plan)
				}
			case "both managed", "inner not confirmed":
				if len(plan) != 2 {
					t.Fatalf("expected both managed checkouts in preview: %v", plan)
				}
				if mode == "inner not confirmed" {
					plan = []ManagedRepo{{Path: outer, ID: "outer-id"}}
				}
			default:
				if len(plan) != 1 || plan[0].Path != outer {
					t.Fatalf("expected outer checkout in preview: %v", plan)
				}
			}
			if mode == "appeared after preview" {
				addWorktree()
			}
			if err := app.Destroy(DestroyOpts{PurgeRepos: true, ConfirmedRepos: plan}); err != nil {
				t.Fatal(err)
			}
			if mode == "both managed" || mode == "shared monorepo" {
				if _, err := os.Stat(outer); !os.IsNotExist(err) {
					t.Fatalf("outer clone survived: %v", err)
				}
				if records, err := app.State.ManagedRepos(); err != nil || len(records) != 0 {
					t.Fatalf("checkout records survived: %v, %v", records, err)
				}
			} else if _, err := os.Stat(filepath.Join(inner, ".git")); err != nil {
				t.Fatalf("nested checkout was removed: %v", err)
			}
		})
	}
}

func TestDestroyKeepsRecordedNestedCheckoutAfterBlueprintChange(t *testing.T) {
	src := t.TempDir()
	outer := filepath.Join(src, "outer")
	inner := filepath.Join(outer, "inner")
	app := newDestroyTestApp(t, t.TempDir(), src, Repo{Path: "outer"}, Repo{Path: "outer/inner"})
	makeClone(t, app, outer, "outer-id", "outer-id")
	makeClone(t, app, inner, "inner-id", "inner-id")
	app.BP.Components = app.BP.Components[:len(app.BP.Components)-1]

	plan, err := app.PurgeRepoPlan()
	if err != nil || len(plan) != 2 || plan[0].Path != inner || plan[1].Path != outer {
		t.Fatalf("expected both owned checkouts, child first: %v, %v", plan, err)
	}
	// A user checkout now occupies the path formerly managed by the removed component.
	if err := os.Rename(inner, filepath.Join(src, "saved-inner")); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(inner, 0o755); err != nil {
		t.Fatal(err)
	}
	worktree := filepath.Join(inner, ".git")
	if err := os.WriteFile(worktree, []byte("gitdir: elsewhere\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	preview, err := app.PurgeRepoPlan()
	if err != nil || len(preview) != 0 {
		t.Fatalf("preview includes a parent containing a replacement checkout: %v, %v", preview, err)
	}
	if err := app.Destroy(DestroyOpts{PurgeRepos: true, ConfirmedRepos: plan}); err != nil {
		t.Fatal(err)
	}
	if data, err := os.ReadFile(worktree); err != nil || string(data) != "gitdir: elsewhere\n" {
		t.Fatalf("replacement checkout changed: %q, %v", data, err)
	}

	// Resume an earlier interrupted parent removal using the revised blueprint.
	// The inner checkout and its record must survive successive unattended retries.
	parent := ManagedRepo{Path: outer, ID: "outer-id"}
	tomb := app.purgeTombstone(parent)
	if err := app.State.SetRepoPending(outer, parent.ID, true); err != nil {
		t.Fatal(err)
	}
	writeTestPurgeMarker(t, src, tomb, parent)
	if err := os.Rename(outer, tomb); err != nil {
		t.Fatal(err)
	}
	for range 2 {
		if err := app.Destroy(DestroyOpts{PurgeRepos: true}); err != nil {
			t.Fatal(err)
		}
		if records, err := app.State.ManagedRepos(); err != nil || len(records) != 2 {
			t.Fatalf("interrupted cleanup lost a checkout record: %v, %v", records, err)
		}
		if data, err := os.ReadFile(filepath.Join(tomb, "inner", ".git")); err != nil || string(data) != "gitdir: elsewhere\n" {
			t.Fatalf("replacement checkout in moved tree changed: %q, %v", data, err)
		}
	}
}

func TestDestroyRechecksNestedRegistrationsBeforeRemoval(t *testing.T) {
	src := t.TempDir()
	outer := filepath.Join(src, "outer")
	app := newDestroyTestApp(t, t.TempDir(), src, Repo{Path: "outer"})
	makeClone(t, app, outer, "outer-id", "outer-id")
	fs, err := openRepoFS(src)
	if err != nil {
		t.Fatal(err)
	}
	defer fs.Close()
	plan, err := app.repoPurgePlan(fs, nil)
	if err != nil || len(plan.candidates) != 1 {
		t.Fatalf("expected one planned removal: %v, %v", plan, err)
	}
	inner := filepath.Join(outer, "later")
	makeClone(t, app, inner, "child-id", "child-id")
	if err := app.removeManagedRepos(fs, plan.candidates); err == nil {
		t.Fatal("expected the late child registration to stop removal of the parent")
	}
	if data, err := os.ReadFile(filepath.Join(inner, "file")); err != nil || string(data) != "cloned" {
		t.Fatalf("unconfirmed child changed: %q, %v", data, err)
	}
}

func TestDestroyPurgeReposResumesNestedClonesIndependently(t *testing.T) {
	for _, pending := range []string{"inner", "outer"} {
		t.Run(pending, func(t *testing.T) {
			src := t.TempDir()
			outer := filepath.Join(src, "outer")
			inner := filepath.Join(outer, "inner")
			app := newDestroyTestApp(t, t.TempDir(), src, Repo{Path: "outer"}, Repo{Path: "outer/inner"})
			makeClone(t, app, outer, "outer", "outer")
			record := ManagedRepo{Path: outer, ID: "outer"}
			if pending == "inner" {
				makeClone(t, app, inner, "inner", "inner")
				record = ManagedRepo{Path: inner, ID: "inner"}
			} else if err := os.MkdirAll(filepath.Join(inner, ".git"), 0o755); err != nil {
				t.Fatal(err)
			}
			tomb := app.purgeTombstone(record)
			if err := app.State.SetRepoPending(record.Path, record.ID, true); err != nil {
				t.Fatal(err)
			}
			writeTestPurgeMarker(t, src, tomb, record)
			if err := os.Rename(record.Path, tomb); err != nil {
				t.Fatal(err)
			}
			plan, err := app.PurgeRepoPlan()
			if err != nil {
				t.Fatal(err)
			}
			if pending == "inner" && (len(plan) != 1 || plan[0].Path != inner) {
				t.Fatalf("expected only the interrupted inner removal: %v", plan)
			}
			if pending == "outer" && len(plan) != 0 {
				t.Fatalf("preview included a moved tree containing a user checkout: %v", plan)
			}
			if err := app.Destroy(DestroyOpts{PurgeRepos: true, ConfirmedRepos: plan}); err != nil {
				t.Fatal(err)
			}
			if pending == "inner" {
				if _, err := os.Stat(tomb); !os.IsNotExist(err) {
					t.Fatalf("inner removal did not finish: %v", err)
				}
				if _, err := os.Stat(outer); err != nil {
					t.Fatalf("unconfirmed outer removal: %v", err)
				}
			} else if _, err := os.Stat(filepath.Join(tomb, "inner", ".git")); err != nil {
				t.Fatalf("user checkout in moved tree was removed: %v", err)
			}
		})
	}
}
