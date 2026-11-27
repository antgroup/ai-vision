package runtime

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"

	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/components"
	"github.com/antgroup/ai-vision/aivision-cli/internal/pathguard"
)

// DestroyOpts controls teardown scope.
type DestroyOpts struct {
	Purge      bool // remove named volumes (datastores) + the whole state dir
	PurgeRepos bool // also delete cloned source repos
	// ConfirmedRepos limits removal to the candidates shown to the user.
	// A nil slice keeps the unattended behaviour (remove every eligible repo).
	ConfirmedRepos []ManagedRepo
}

// Destroy tears the stack down in reverse dependency order. By default it stops
// processes and removes containers but preserves data volumes, cloned repos and
// state so a later cast comes back up fast. --purge additionally removes named
// volumes (wiping the OceanBase datastore) and the state directory.
func (a *App) Destroy(opts DestroyOpts) error {
	volumeNames, err := a.checkedDeploymentVolumes()
	if err != nil {
		return err
	}
	if opts.Purge {
		if err := a.VerifyStatePurge(); err != nil {
			return err
		}
	}
	a.Log.Step("destroy: tearing down namespace %s (purge=%v)", a.BP.Metadata.Namespace, opts.Purge)

	var (
		repoPlan repoPurgePlan
		repos    *repoFS
	)
	if opts.PurgeRepos {
		var err error
		repos, err = openRepoFS(a.SourceRoot())
		if errors.Is(err, errSourceRootMissing) {
			if err := a.validateBlueprintRepos(); err != nil {
				return err
			}
			a.Log.Info("source root %s does not exist; no cloned repos to remove", a.SourceRoot())
			repos = nil
		} else if err != nil {
			return err
		}
		if repos != nil {
			defer repos.Close()
			repoPlan, err = a.repoPurgePlan(repos, opts.ConfirmedRepos)
			if err != nil {
				return err
			}
		}
	}

	comps := a.BP.OrderedComponents()
	// reverse order: dependents first
	rev := make([]Component, 0, len(comps))
	for i := len(comps) - 1; i >= 0; i-- {
		rev = append(rev, comps[i])
	}

	for _, c := range rev {
		vars := a.ComponentVars(c)
		if len(c.Teardown) > 0 {
			a.Log.Info("teardown hooks for %s", components.Canonical(c.Name))
			_ = a.RunSteps(c, c.Teardown, vars, "teardown")
		}
		switch c.Kind {
		case "process":
			pid := a.State.ReadPid(c.Name)
			if pid > 0 && PidAlive(pid) {
				a.Log.Info("stopping process %s (pid %d)", components.Canonical(c.Name), pid)
				if err := terminateTree(pid); err != nil {
					a.Log.Warn("terminate %s: %v", components.Canonical(c.Name), err)
				}
			} else {
				a.Log.Info("process %s not running", components.Canonical(c.Name))
			}
			a.State.RemovePid(c.Name)
		case "container":
			name := a.ContainerName(c)
			exists := a.Docker.ContainerExists(a.Ctx, name)
			if a.BP.Metadata.Product == "aivision" {
				var err error
				exists, err = a.productContainerExists(a.Ctx, name)
				if err != nil {
					return err
				}
			}
			if exists {
				a.Log.Info("removing %s container", components.Canonical(c.Name))
				if err := a.removeContainerWithChecks(c, true); err != nil {
					if a.managedDatastore(c) || a.BP.Metadata.Product == "aivision" {
						return fmt.Errorf("stop %s: %w", components.Canonical(c.Name), err)
					}
					a.Log.Warn("remove container %s: %v", name, err)
				}
			} else {
				a.Log.Info("%s container absent", components.Canonical(c.Name))
			}
		}
	}

	// Shared volumes can still be in use until every component is down.
	// Keep the namespace and checkout records available if a retry is needed.
	if opts.Purge {
		for _, name := range volumeNames {
			exists, err := a.checkPhysicalOwner("volume", name)
			if err != nil {
				return err
			}
			if exists {
				a.Log.Info("removing volume %s", name)
				if _, err := a.Docker.Run(a.Ctx, false, "volume", "rm", "-f", name); err != nil {
					return fmt.Errorf("remove Docker volume %s: %w", name, err)
				}
			}
		}
	}
	if opts.PurgeRepos && repos != nil {
		if err := a.cleanupRepoRecords(repos, repoPlan); err != nil {
			return err
		}
		if err := a.removeManagedRepos(repos, repoPlan.candidates); err != nil {
			return err
		}
	}
	if opts.Purge {
		if err := a.VerifyStatePurge(); err != nil {
			return err
		}
		a.Log.Info("purging state dir %s", a.State.Root)
		if err := a.State.RemoveAll(); err != nil {
			return fmt.Errorf("remove state: %w", err)
		}
	}
	a.Log.OK("destroy complete")
	return nil
}

type repoPurgePlan struct {
	candidates []ManagedRepo
	orphans    []ManagedRepo
	retire     []ManagedRepo
	kept       []string
}

// PurgeRepoPreview lists the checkouts this invocation can actually remove.
// It applies the same eligibility and ownership checks as Destroy; removal
// re-verifies ownership again before deleting.
func (a *App) PurgeRepoPreview() ([]string, error) {
	repos, err := a.PurgeRepoPlan()
	if err != nil {
		return nil, err
	}
	paths := make([]string, 0, len(repos))
	for _, repo := range repos {
		paths = append(paths, repo.Path)
	}
	return paths, nil
}

// PurgeRepoPlan returns the records the user is asked to confirm. An empty
// result still limits execution to the confirmed plan.
func (a *App) PurgeRepoPlan() ([]ManagedRepo, error) {
	repos, err := openRepoFS(a.SourceRoot())
	if err != nil {
		if errors.Is(err, errSourceRootMissing) {
			if err := a.validateBlueprintRepos(); err != nil {
				return nil, err
			}
			return []ManagedRepo{}, nil
		}
		return nil, err
	}
	defer repos.Close()
	plan, err := a.repoPurgePlan(repos, nil)
	if err != nil {
		return nil, err
	}
	out := make([]ManagedRepo, 0, len(plan.candidates))
	out = append(out, plan.candidates...)
	return out, nil
}

func confirmedRepoPlan(plan repoPurgePlan, confirmed []ManagedRepo) repoPurgePlan {
	want := make(map[string]bool, len(confirmed))
	for _, repo := range confirmed {
		want[repoKey(repo)] = true
	}
	out := repoPurgePlan{kept: plan.kept, retire: plan.retire}
	for _, repo := range plan.candidates {
		key := repoKey(repo)
		if want[key] {
			out.candidates = append(out.candidates, repo)
			delete(want, key)
		}
	}
	for _, repo := range plan.orphans {
		key := repoKey(repo)
		if want[key] {
			out.orphans = append(out.orphans, repo)
			delete(want, key)
		}
	}
	for _, repo := range confirmed {
		if want[repoKey(repo)] {
			out.kept = append(out.kept, repo.Path+" (confirmed removal is no longer eligible)")
		}
	}
	return out
}

func repoKey(repo ManagedRepo) string {
	return repo.Path + "\x00" + repo.ID
}

// validateBlueprintRepos rejects blueprint paths that could never be removed
// safely, even when the source root itself is missing.
func (a *App) validateBlueprintRepos() error {
	for _, c := range a.BP.Components {
		if c.Repo == nil || !c.IsEnabled() {
			continue
		}
		dir := a.RepoDir(c)
		if dir == "" {
			continue
		}
		if err := pathguard.EnsureWithin(a.SourceRoot(), dir); err != nil {
			return fmt.Errorf("component %q: refusing --purge-repos: %w", c.Name, err)
		}
	}
	return nil
}

// repoPurgePlan validates every repository path before anything is deleted.
// Blueprint paths outside sourceRoot are rejected; unusable records are kept
// so they cannot block cleanup of other eligible checkouts.
func (a *App) repoPurgePlan(fs *repoFS, confirmed []ManagedRepo) (repoPurgePlan, error) {
	var plan repoPurgePlan
	if err := a.validateBlueprintRepos(); err != nil {
		return plan, err
	}
	if err := a.State.VerifyRecordTargets(); err != nil {
		return plan, err
	}
	repos, err := a.State.ManagedRepos()
	if err != nil {
		return plan, fmt.Errorf("read recorded repos: %w", err)
	}
	pending := make(map[string]ManagedRepo)
	for _, repo := range confirmed {
		fs.plannedCheckoutPaths = append(fs.plannedCheckoutPaths, repo.Path)
	}
	for _, repo := range repos {
		if repo.Pending {
			pending[repo.Path] = repo
		}
	}
	for _, repo := range repos {
		if err := pathguard.EnsureStrictlyWithin(a.SourceRoot(), repo.Path); err != nil {
			plan.kept = append(plan.kept, repo.Path+" ("+err.Error()+")")
			continue
		}
		fs.plannedCheckoutPaths = append(fs.plannedCheckoutPaths, repo.Path)
		if repo.ID == "" {
			plan.kept = append(plan.kept, repo.Path+" (no verified ownership id)")
			continue
		}
		if err := a.verifyCheckoutPurge(repo.Path); err != nil {
			return plan, err
		}
		tombstone := a.purgeTombstone(repo)
		if err := pathguard.EnsureStrictlyWithin(a.SourceRoot(), tombstone); err != nil {
			return plan, fmt.Errorf("invalid pending removal for %s: %w", repo.Path, err)
		}
		if repo.Pending {
			if err := a.addPendingCandidate(&plan, fs, repo, tombstone); err != nil {
				return plan, err
			}
			continue
		}
		if pendingRepo, ok := pending[repo.Path]; ok {
			plan.kept = append(plan.kept, repo.Path+" (pending removal for "+pendingRepo.ID+" is still in progress)")
			continue
		}
		if _, err := fs.lstat(repo.Path); err != nil {
			if !os.IsNotExist(err) {
				return plan, fmt.Errorf("inspect repo %s: %w", repo.Path, err)
			}
			// A directory at the tombstone path is only owned cleanup when the
			// record says a removal is pending; otherwise leave it alone.
			plan.orphans = append(plan.orphans, repo)
			continue
		}
		owner, err := fs.inspectRepoOwner(repo.Path, repo.ID)
		if err != nil {
			return plan, fmt.Errorf("inspect ownership for %s: %w", repo.Path, err)
		}
		if owner != repoOwnerMatches {
			plan.kept = append(plan.kept, repo.Path+" (ownership id does not match the record)")
			continue
		}
		if _, err := fs.lstat(tombstone); err == nil {
			plan.kept = append(plan.kept, repo.Path+" (removal path already exists)")
			continue
		} else if !os.IsNotExist(err) {
			return plan, fmt.Errorf("inspect pending removal %s: %w", tombstone, err)
		}
		if _, owner, err := fs.inspectPurgeMarker(tombstone, repo); err != nil {
			return plan, fmt.Errorf("inspect removal marker %s: %w", purgeMarkerPath(tombstone), err)
		} else if owner == repoOwnerDifferent {
			plan.kept = append(plan.kept, repo.Path+" (removal marker belongs to another record)")
			continue
		}
		plan.candidates = append(plan.candidates, repo)
	}
	if confirmed != nil {
		plan = confirmedRepoPlan(plan, confirmed)
	}
	// A nested clone must be removed independently before its parent.
	sort.SliceStable(plan.candidates, func(i, j int) bool {
		return len(plan.candidates[i].Path) > len(plan.candidates[j].Path)
	})
	eligible := make(map[string]bool)
	filtered := plan.candidates[:0]
	for _, repo := range plan.candidates {
		tree := repo.Path
		if repo.Pending {
			tomb := a.purgeTombstone(repo)
			if _, err := fs.lstat(tomb); err == nil {
				tree = tomb
			} else if !os.IsNotExist(err) {
				return plan, err
			}
		}
		child, err := a.nestedCheckout(fs, repo.Path, tree, eligible, repos)
		if err != nil {
			return plan, err
		}
		if child != "" {
			plan.kept = append(plan.kept, repo.Path+" (contains separate checkout "+child+")")
			continue
		}
		filtered = append(filtered, repo)
		if !repo.Pending {
			eligible[repo.Path] = true
		}
	}
	plan.candidates = filtered
	return plan, nil
}

func (a *App) addPendingCandidate(plan *repoPurgePlan, fs *repoFS, repo ManagedRepo, tombstone string) error {
	if _, err := fs.lstat(tombstone); err == nil {
		owned, err := fs.purgeMarkerOwnsTombstone(tombstone, repo)
		if err != nil {
			return fmt.Errorf("verify pending directory %s: %w", tombstone, err)
		}
		if !owned {
			plan.kept = append(plan.kept, repo.Path+" (pending removal path is not owned by this record)")
			return nil
		}
		plan.candidates = append(plan.candidates, repo)
		return nil
	} else if !os.IsNotExist(err) {
		return fmt.Errorf("inspect pending removal %s: %w", tombstone, err)
	}
	if _, owner, err := fs.inspectPurgeMarker(tombstone, repo); err != nil {
		return fmt.Errorf("inspect removal marker %s: %w", purgeMarkerPath(tombstone), err)
	} else if owner == repoOwnerDifferent {
		plan.kept = append(plan.kept, repo.Path+" (removal marker belongs to another record)")
		return nil
	}
	if _, err := fs.lstat(repo.Path); err == nil {
		owner, err := fs.inspectRepoOwner(repo.Path, repo.ID)
		if err != nil {
			return fmt.Errorf("inspect pending ownership for %s: %w", repo.Path, err)
		}
		if owner != repoOwnerMatches {
			// The tombstone is gone but the path belongs to another checkout:
			// the removal completed and the stale pending record can be retired.
			plan.retire = append(plan.retire, repo)
			return nil
		}
	} else if !os.IsNotExist(err) {
		return fmt.Errorf("inspect repo %s: %w", repo.Path, err)
	}
	plan.candidates = append(plan.candidates, repo)
	return nil
}

// purgeTombstone names the sibling used to make removal restartable: once
// ownership is verified the checkout is renamed here before deletion, so an
// interrupted recursive delete can be resumed on the next invocation.
func (a *App) purgeTombstone(repo ManagedRepo) string {
	// Hash the full target so a crafted id cannot add path components and a
	// long checkout basename cannot exceed the filesystem name limit.
	sum := sha256.Sum256([]byte(repo.Path + "\x00" + repo.ID))
	suffix := hex.EncodeToString(sum[:8])
	return filepath.Join(filepath.Dir(repo.Path), ".aiv-purge-"+suffix)
}

func purgeMarkerPath(tombstone string) string { return tombstone + ".owner" }

type purgeMarkerRecord struct {
	Path        string `json:"path"`
	ID          string `json:"id"`
	HasIdentity bool   `json:"hasIdentity,omitempty"`
	Dev         uint64 `json:"dev,omitempty"`
	Ino         uint64 `json:"ino,omitempty"`
}

// beginPendingRemoval records the intent and ownership of a tombstone before
// the checkout is renamed, so a resumed cleanup never deletes an unrelated
// directory that happens to use the same name.
func (a *App) beginPendingRemoval(fs *repoFS, repo ManagedRepo, tombstone string, alreadyPending bool) error {
	if err := a.verifyNestedCheckouts(fs, repo.Path, repo.Path); err != nil {
		return err
	}
	if err := a.verifyCheckoutPurge(repo.Path); err != nil {
		return err
	}
	if !alreadyPending {
		claimed, err := a.State.ClaimRepoPending(repo.Path, repo.ID)
		if err != nil {
			return fmt.Errorf("claim repo %s for removal: %w", repo.Path, err)
		}
		if !claimed {
			return errRepoRemovalBusy
		}
	}
	if err := fs.writePurgeMarker(tombstone, repo); err != nil {
		return fmt.Errorf("mark pending removal %s: %w", tombstone, err)
	}
	if err := fs.renameVerifiedCheckout(repo, tombstone); err != nil {
		return fmt.Errorf("remove repo %s: %w", repo.Path, err)
	}
	return nil
}

var errRepoRemovalBusy = errors.New("repository removal is already in progress")

type repoOwnerState int

const (
	repoOwnerMissing repoOwnerState = iota
	repoOwnerMatches
	repoOwnerDifferent
)

func (a *App) withRepoRemovalLock(repo ManagedRepo, fn func() error) error {
	release, err := a.State.AcquireRepoRemovalLock(repo.Path, repo.ID)
	if err != nil {
		return fmt.Errorf("lock repo %s for removal: %w", repo.Path, err)
	}
	defer release()
	return fn()
}

func (a *App) currentRepoRecord(repo ManagedRepo) (ManagedRepo, bool, error) {
	records, err := a.State.ManagedRepos()
	if err != nil {
		return ManagedRepo{}, false, fmt.Errorf("read recorded repos: %w", err)
	}
	abs, err := filepath.Abs(repo.Path)
	if err != nil {
		return ManagedRepo{}, false, err
	}
	abs = filepath.Clean(abs)
	for _, current := range records {
		if current.Path == abs && current.ID == repo.ID {
			return current, true, nil
		}
	}
	return ManagedRepo{}, false, nil
}

// cleanupRepoRecords retires records whose checkout is already gone. Each
// decision is made again under the per-repo lock so a concurrent destroy
// cannot turn a pending removal back into an active record.
func (a *App) cleanupRepoRecords(fs *repoFS, plan repoPurgePlan) error {
	for _, kept := range plan.kept {
		a.Log.Warn("keeping recorded repo %s", kept)
	}
	for _, repo := range plan.orphans {
		repo := repo
		if err := a.withRepoRemovalLock(repo, func() error {
			return a.cleanupMissingRepo(fs, repo)
		}); err != nil {
			return err
		}
	}
	for _, repo := range plan.retire {
		repo := repo
		if err := a.withRepoRemovalLock(repo, func() error {
			return a.retirePendingRepo(fs, repo)
		}); err != nil {
			return err
		}
	}
	return nil
}

func (a *App) cleanupMissingRepo(fs *repoFS, repo ManagedRepo) error {
	current, ok, err := a.currentRepoRecord(repo)
	if err != nil || !ok {
		return err
	}
	if current.Pending {
		// A concurrent invocation claimed this removal after our plan was
		// built. It has released the lock, so finish the operation instead of
		// leaving a record and tombstone behind.
		return a.finishPendingRemoval(fs, current, a.purgeTombstone(current))
	}
	if _, err := fs.lstat(current.Path); err == nil {
		return nil
	} else if !os.IsNotExist(err) {
		return fmt.Errorf("inspect orphan %s: %w", current.Path, err)
	}
	// A pending parent may have moved this checkout with it. Keep its record
	// so the next invocation can still protect the child in the renamed tree.
	records, err := a.State.ManagedRepos()
	if err != nil {
		return err
	}
	for _, parent := range records {
		if !parent.Pending {
			continue
		}
		rel, err := nestedCheckoutRel(parent.Path, a.purgeTombstone(parent), current.Path)
		if err != nil {
			return err
		}
		if rel != "" {
			return nil
		}
	}
	if _, err := a.State.ForgetManagedRepoIDIfActive(current.Path, current.ID); err != nil {
		return fmt.Errorf("forget removed repo %s: %w", current.Path, err)
	}
	return nil
}

func (a *App) retirePendingRepo(fs *repoFS, repo ManagedRepo) error {
	current, ok, err := a.currentRepoRecord(repo)
	if err != nil || !ok || !current.Pending {
		return err
	}
	tombstone := a.purgeTombstone(current)
	if _, err := fs.lstat(tombstone); err == nil {
		return nil
	} else if !os.IsNotExist(err) {
		return fmt.Errorf("inspect pending removal %s: %w", tombstone, err)
	}
	owner, err := fs.inspectRepoOwner(current.Path, current.ID)
	if err != nil {
		return fmt.Errorf("verify pending ownership for %s: %w", current.Path, err)
	}
	if owner == repoOwnerMatches {
		return nil
	}
	a.Log.Info("retiring completed pending removal %s", current.Path)
	return a.retirePendingRemoval(fs, current, tombstone)
}

// removeManagedRepos deletes only checkouts aiv cloned. Pre-existing local
// workspaces are never removed, even when they match a blueprint repo.path,
// because the ownership marker must still match the recorded id.
func (a *App) removeManagedRepos(fs *repoFS, repos []ManagedRepo) error {
	if len(repos) == 0 {
		a.Log.Info("no aiv-cloned repos recorded; pre-existing checkouts are kept")
		return nil
	}
	for _, repo := range repos {
		repo := repo
		if err := a.withRepoRemovalLock(repo, func() error {
			return a.removeManagedRepo(fs, repo)
		}); err != nil {
			return err
		}
	}
	return nil
}

func (a *App) removeManagedRepo(fs *repoFS, repo ManagedRepo) error {
	dir := repo.Path
	if _, err := fs.rel(dir); err != nil {
		return fmt.Errorf("refusing to delete repo: %w", err)
	}
	tombstone := a.purgeTombstone(repo)
	if _, err := fs.rel(tombstone); err != nil {
		return fmt.Errorf("refusing to delete repo %s: %w", dir, err)
	}
	current, ok, err := a.currentRepoRecord(repo)
	if err != nil {
		return err
	}
	if !ok {
		a.Log.Info("repo %s is no longer recorded; skipping removal", dir)
		return nil
	}
	if repo.Pending {
		if !current.Pending {
			a.Log.Info("pending removal for %s was already resolved", dir)
			return nil
		}
		return a.finishPendingRemoval(fs, current, tombstone)
	}
	if current.Pending {
		return a.finishPendingRemoval(fs, current, tombstone)
	}
	return a.removeActiveRepo(fs, current, tombstone)
}

func (a *App) removeActiveRepo(fs *repoFS, repo ManagedRepo, tombstone string) error {
	dir := repo.Path
	if _, err := fs.lstat(dir); err != nil {
		if os.IsNotExist(err) {
			if err := a.State.ForgetManagedRepoID(dir, repo.ID); err != nil {
				return fmt.Errorf("forget removed repo %s: %w", dir, err)
			}
			return nil
		}
		return fmt.Errorf("inspect repo %s: %w", dir, err)
	}
	owner, err := fs.inspectRepoOwner(dir, repo.ID)
	if err != nil {
		return fmt.Errorf("verify ownership for %s: %w", dir, err)
	}
	if owner != repoOwnerMatches {
		a.Log.Warn("keeping repo %s: ownership id does not match the record", dir)
		return nil
	}
	if _, err := fs.lstat(tombstone); err == nil {
		a.Log.Warn("keeping repo %s: removal path %s already exists but the record is not pending", dir, tombstone)
		return nil
	} else if !os.IsNotExist(err) {
		return fmt.Errorf("inspect pending removal %s: %w", tombstone, err)
	}
	if _, owner, err := fs.inspectPurgeMarker(tombstone, repo); err != nil {
		return fmt.Errorf("inspect pending-removal marker %s: %w", purgeMarkerPath(tombstone), err)
	} else if owner == repoOwnerDifferent {
		a.Log.Warn("keeping repo %s: pending-removal marker %s belongs to another record", dir, purgeMarkerPath(tombstone))
		return nil
	}
	a.Log.Info("removing aiv-cloned repo %s", dir)
	if err := a.beginPendingRemoval(fs, repo, tombstone, false); err != nil {
		if errors.Is(err, errRepoRemovalBusy) {
			a.Log.Warn("keeping repo %s: removal was claimed by another process", dir)
			return nil
		}
		return err
	}
	return a.deleteTombstone(fs, repo, tombstone)
}

func (a *App) finishPendingRemoval(fs *repoFS, repo ManagedRepo, tombstone string) error {
	dir := repo.Path
	if _, err := fs.lstat(tombstone); err == nil {
		owned, err := fs.purgeMarkerOwnsTombstone(tombstone, repo)
		if err != nil {
			return fmt.Errorf("verify pending directory %s: %w", tombstone, err)
		}
		if !owned {
			a.Log.Warn("keeping repo %s: pending removal %s is not the renamed checkout", dir, tombstone)
			return nil
		}
		a.Log.Info("finishing interrupted removal %s", dir)
		return a.deleteTombstone(fs, repo, tombstone)
	} else if !os.IsNotExist(err) {
		return fmt.Errorf("inspect pending removal %s: %w", tombstone, err)
	}
	if _, err := fs.lstat(dir); err == nil {
		owner, err := fs.inspectRepoOwner(dir, repo.ID)
		if err != nil {
			return fmt.Errorf("verify pending ownership for %s: %w", dir, err)
		}
		if owner != repoOwnerMatches {
			a.Log.Info("retiring completed pending removal %s", dir)
			return a.retirePendingRemoval(fs, repo, tombstone)
		}
		a.Log.Info("resuming removal of %s", dir)
		if err := a.beginPendingRemoval(fs, repo, tombstone, true); err != nil {
			return err
		}
		return a.deleteTombstone(fs, repo, tombstone)
	} else if !os.IsNotExist(err) {
		return fmt.Errorf("inspect repo %s: %w", dir, err)
	}
	return a.retirePendingRemoval(fs, repo, tombstone)
}

func (a *App) retirePendingRemoval(fs *repoFS, repo ManagedRepo, tombstone string) error {
	_, owner, err := fs.inspectPurgeMarker(tombstone, repo)
	if err != nil {
		return fmt.Errorf("verify removal marker %s: %w", purgeMarkerPath(tombstone), err)
	}
	if owner == repoOwnerDifferent {
		return fmt.Errorf("conflicting removal marker %s", purgeMarkerPath(tombstone))
	}
	if owner == repoOwnerMatches {
		if err := fs.remove(purgeMarkerPath(tombstone)); err != nil {
			return fmt.Errorf("clear removal marker %s: %w", purgeMarkerPath(tombstone), err)
		}
	}
	if err := a.State.ForgetManagedRepoID(repo.Path, repo.ID); err != nil {
		return fmt.Errorf("retire pending removal %s: %w", repo.Path, err)
	}
	return nil
}

func (a *App) deleteTombstone(fs *repoFS, repo ManagedRepo, tombstone string) error {
	if err := a.verifyNestedCheckouts(fs, repo.Path, tombstone); err != nil {
		return err
	}
	if err := fs.removeVerifiedTombstone(tombstone, repo); err != nil {
		return fmt.Errorf("remove repo %s: %w", repo.Path, err)
	}
	return a.retirePendingRemoval(fs, repo, tombstone)
}

// Redeploy is destroy (non-purge) followed by a full cast.
func (a *App) Redeploy(runGauge, runForge bool) error {
	if err := a.Destroy(DestroyOpts{}); err != nil {
		return fmt.Errorf("redeploy: %w", err)
	}
	return a.Cast(runGauge, runForge)
}
