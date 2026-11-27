// Package state persists everything the CLI records about a deployment
// (pidfiles, once-markers, deploy metadata) so status, logs and destroy can
// operate even when the blueprint is not at hand.
package state

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/blueprint"
)

// State tracks everything aiv persists about a deployment so that status,
// logs and destroy can operate even when the blueprint is not at hand.
type State struct {
	Namespace   string
	Workdir     string // resolved workdir; Root must stay strictly inside it
	Root        string // <workdir>/<namespace>
	StateDir    string // Root/state
	LogDir      string // Root/logs
	ArtifactDir string // Root/artifacts
}

// DeployMeta is written to state/meta.json on every successful cast.
type DeployMeta struct {
	DatastoreBackend string            `json:"datastoreBackend,omitempty"`
	Namespace        string            `json:"namespace"`
	Blueprint        string            `json:"blueprint"`
	SourceRoot       string            `json:"sourceRoot"`
	DeployedAt       string            `json:"deployedAt"`
	Components       map[string]string `json:"components"` // name -> kind
	Containers       map[string]string `json:"containers"` // component -> container name
	Discovered       map[string]string `json:"discovered"` // var -> value (e.g. ORG_ID)
}

func NewState(workdir, namespace string) *State {
	workdir = blueprint.CleanPath(workdir, nil)
	root := filepath.Join(workdir, namespace)
	return &State{
		Namespace:   namespace,
		Workdir:     workdir,
		Root:        root,
		StateDir:    filepath.Join(root, "state"),
		LogDir:      filepath.Join(root, "logs"),
		ArtifactDir: filepath.Join(root, "artifacts"),
	}
}

func (s *State) EnsureDirs() error {
	for _, d := range []string{s.StateDir, s.LogDir, s.ArtifactDir} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			return fmt.Errorf("create dir %s: %w", d, err)
		}
	}
	return nil
}

// PidFile returns the pidfile path for a process component.
func (s *State) PidFile(comp string) string {
	return filepath.Join(s.StateDir, comp+".pid")
}

// LogFile returns the log path for a component.
func (s *State) LogFile(comp string) string {
	return filepath.Join(s.LogDir, comp+".log")
}

func (s *State) WritePid(comp string, pid int) error {
	return os.WriteFile(s.PidFile(comp), []byte(strconv.Itoa(pid)), 0o644)
}

func (s *State) ReadPid(comp string) int {
	b, err := os.ReadFile(s.PidFile(comp))
	if err != nil {
		return 0
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(b)))
	if err != nil {
		return 0
	}
	return pid
}

func (s *State) RemovePid(comp string) {
	_ = os.Remove(s.PidFile(comp))
}

// PidAlive reports whether a pid exists and is running. It probes with
// signal 0 (syscall.Kill) which never actually delivers a signal.
func PidAlive(pid int) bool {
	if pid <= 0 {
		return false
	}
	return syscall.Kill(pid, 0) == nil
}

// markerFile returns the once-marker path for a component step.
func (s *State) markerFile(comp, step string) string {
	safe := strings.Map(func(r rune) rune {
		if r == '/' || r == ' ' {
			return '_'
		}
		return r
	}, step)
	if len(safe) > 48 {
		safe = safe[:48]
	}
	return filepath.Join(s.StateDir, fmt.Sprintf("%s.%s.done", comp, safe))
}

func (s *State) MarkerDone(comp, step string) bool {
	_, err := os.Stat(s.markerFile(comp, step))
	return err == nil
}

func (s *State) MarkDone(comp, step string) error {
	return os.WriteFile(s.markerFile(comp, step), []byte(time.Now().Format(time.RFC3339)), 0o644)
}

// ClearMarkers removes all once-markers (used by destroy --purge and --force-bootstrap).
func (s *State) ClearMarkers() {
	entries, _ := os.ReadDir(s.StateDir)
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".done") {
			_ = os.Remove(filepath.Join(s.StateDir, e.Name()))
		}
	}
}

func (s *State) metaFile() string { return filepath.Join(s.StateDir, "meta.json") }

func (s *State) SaveMeta(m DeployMeta) error {
	b, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(s.metaFile(), b, 0o644)
}

func (s *State) LoadMeta() (*DeployMeta, error) {
	b, err := os.ReadFile(s.metaFile())
	if err != nil {
		return nil, err
	}
	var m DeployMeta
	if err := json.Unmarshal(b, &m); err != nil {
		return nil, err
	}
	return &m, nil
}

// varsFile holds the user-supplied variable overrides that produced the last
// successful cast, so status/destroy/redeploy reuse them without the caller
// re-passing every --var. It may contain secrets, hence 0600.
func (s *State) varsFile() string { return filepath.Join(s.StateDir, "vars.json") }

// SaveVars persists the override set (not the blueprint defaults).
func (s *State) SaveVars(vars map[string]string) error {
	if err := os.MkdirAll(s.StateDir, 0o755); err != nil {
		return err
	}
	b, err := json.MarshalIndent(vars, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(s.varsFile(), b, 0o600)
}

// LoadVars returns the persisted overrides, or an empty map when none exist.
func (s *State) LoadVars() (map[string]string, error) {
	b, err := os.ReadFile(s.varsFile())
	if err != nil {
		if os.IsNotExist(err) {
			return map[string]string{}, nil
		}
		return nil, err
	}
	out := map[string]string{}
	if err := json.Unmarshal(b, &out); err != nil {
		return nil, fmt.Errorf("parse %s: %w", s.varsFile(), err)
	}
	return out, nil
}

// VerifyPurgeTarget checks the namespace directory before teardown begins.
func (s *State) VerifyPurgeTarget() error {
	work, err := s.openPurgeParent()
	if work != nil {
		return work.Close()
	}
	return err
}

// RemoveAll deletes the entire namespace state root (destroy --purge).
func (s *State) RemoveAll() error {
	work, err := s.openPurgeParent()
	if err != nil || work == nil {
		return err
	}
	defer work.Close()
	return work.RemoveAll(s.Namespace)
}

// openPurgeParent returns nil if there is no namespace directory. All lookups
// and the final removal use the selected workdir handle and one direct child.
func (s *State) openPurgeParent() (*os.Root, error) {
	if err := blueprint.ValidateNamespace(s.Namespace); err != nil {
		return nil, err
	}
	if s.Workdir == "" || s.Root != filepath.Join(s.Workdir, s.Namespace) {
		return nil, fmt.Errorf("state root %s does not match namespace %s under %s", s.Root, s.Namespace, s.Workdir)
	}
	work, err := os.OpenRoot(s.Workdir)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	info, err := work.Lstat(s.Namespace)
	if err != nil {
		_ = work.Close()
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	if !info.IsDir() {
		_ = work.Close()
		return nil, fmt.Errorf("state root %s is not a namespace directory", s.Root)
	}
	// Lstat also succeeds with another namespace's spelling on a
	// case-insensitive filesystem. Registries and resource names retain case.
	alternate := strings.ToLower(s.Namespace)
	if alternate == s.Namespace {
		alternate = strings.ToUpper(s.Namespace)
	}
	if alternate == s.Namespace {
		return work, nil // no letters to fold
	}
	alias, err := work.Lstat(alternate)
	if os.IsNotExist(err) || (err == nil && !os.SameFile(info, alias)) {
		return work, nil // distinct names on this filesystem
	}
	// Only a shared entry needs its original spelling from the directory.
	entries, err := fs.ReadDir(work.FS(), ".")
	if err == nil {
		for _, entry := range entries {
			if entry.Name() == s.Namespace && entry.IsDir() {
				return work, nil
			}
		}
		err = fmt.Errorf("namespace %q does not exactly match a directory name in %s", s.Namespace, s.Workdir)
	}
	_ = work.Close()
	return nil, err
}

// ManagedRepo records a checkout aiv cloned. ID matches the ownership marker
// in the checkout's .git directory and is verified again before deletion.
type ManagedRepo struct {
	Path string `json:"path"`
	ID   string `json:"id,omitempty"`
	// Pending marks a removal that was started but not completed. The record
	// stays until the tombstone is gone, so a retry can finish the cleanup.
	Pending bool `json:"pending,omitempty"`
}

// reposFile lives outside the namespace root so a state-only --purge keeps
// the ownership records needed by a later --purge-repos.
func (s *State) reposFile() string {
	// The leading dot is invalid in a namespace. Keep these records distinct
	// from the unprefixed names used in unreleased builds.
	return filepath.Join(s.recordDir(), "."+namespaceKey(s.Namespace)+".json")
}

func namespaceKey(namespace string) string {
	sum := sha256.Sum256([]byte(namespace))
	return hex.EncodeToString(sum[:20])
}

// legacyReposFile is the record location used before records moved out of the
// purgeable state directory.
func (s *State) legacyReposFile() string { return filepath.Join(s.StateDir, "repos.json") }

// VerifyRecordTargets checks both record locations before teardown.
func (s *State) VerifyRecordTargets() error {
	store, err := s.openRecordStore(false)
	if err != nil || store == nil {
		return err
	}
	return store.Close()
}

type recordStore struct {
	root       *os.Root // <workdir>/.aivision-repos
	work       *os.Root // workdir
	deployment *os.Root // <workdir>/<namespace>
	legacy     *os.Root // <workdir>/<namespace>/state
	base       string
	namespace  string
}

func (s *State) recordDir() string { return filepath.Join(s.Workdir, ".aivision-repos") }

func (s *State) openRecordStore(create bool) (*recordStore, error) {
	if err := s.VerifyPurgeTarget(); err != nil {
		return nil, err
	}
	info, err := os.Stat(s.Workdir)
	if os.IsNotExist(err) {
		if !create {
			return nil, nil
		}
		if err := os.MkdirAll(s.Workdir, 0o755); err != nil {
			return nil, fmt.Errorf("create workdir %s: %w", s.Workdir, err)
		}
		info, err = os.Stat(s.Workdir)
	}
	if err != nil {
		return nil, fmt.Errorf("inspect workdir %s: %w", s.Workdir, err)
	}
	work, err := os.OpenRoot(s.Workdir)
	if err != nil {
		return nil, fmt.Errorf("open workdir %s: %w", s.Workdir, err)
	}
	store := &recordStore{work: work, base: s.recordDir(), namespace: s.Namespace}
	if err := verifyOpenedDirectory(work, info, nil); err != nil {
		_ = store.Close()
		return nil, err
	}
	if store.root, err = openRecordDirectory(work, filepath.Base(store.base), create); err == nil {
		store.deployment, err = openRecordDirectory(work, s.Namespace, false)
	}
	if err == nil && store.deployment != nil {
		store.legacy, err = openRecordDirectory(store.deployment, "state", false)
	}
	if err == nil {
		err = store.verify()
	}
	if err != nil {
		_ = store.Close()
		return nil, err
	}
	return store, nil
}

// Open a direct child and compare the resulting directory with the inspected
// entry. The comparison rejects a replacement between inspection and open.
func openRecordDirectory(parent *os.Root, name string, create bool) (*os.Root, error) {
	info, err := parent.Lstat(name)
	if os.IsNotExist(err) && create {
		if err := parent.Mkdir(name, 0o755); err != nil && !os.IsExist(err) {
			return nil, err
		}
		info, err = parent.Lstat(name)
	}
	if os.IsNotExist(err) && !create {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	if !info.IsDir() {
		return nil, fmt.Errorf("ownership storage %s is not a directory", filepath.Join(parent.Name(), name))
	}
	dir, err := parent.OpenRoot(name)
	if err != nil {
		return nil, err
	}
	if err := verifyOpenedDirectory(dir, info, nil); err != nil {
		_ = dir.Close()
		return nil, err
	}
	return dir, nil
}

func verifyOpenedDirectory(dir *os.Root, want os.FileInfo, err error) error {
	if dir == nil {
		return nil
	}
	if err != nil {
		return err
	}
	actual, err := dir.Stat(".")
	if err != nil {
		return err
	}
	if !want.IsDir() || !os.SameFile(want, actual) {
		return fmt.Errorf("ownership storage %s changed during operation", dir.Name())
	}
	return nil
}

func (r *recordStore) verify() error {
	info, err := os.Stat(r.work.Name())
	if err := verifyOpenedDirectory(r.work, info, err); err != nil {
		return err
	}
	for _, dir := range []struct {
		parent, opened *os.Root
		name           string
	}{
		{r.work, r.root, filepath.Base(r.base)},
		{r.work, r.deployment, r.namespace},
		{r.deployment, r.legacy, "state"},
	} {
		if dir.opened == nil {
			continue
		}
		info, err := dir.parent.Lstat(dir.name)
		if err := verifyOpenedDirectory(dir.opened, info, err); err != nil {
			return err
		}
	}
	return nil
}

func (r *recordStore) Close() error {
	for _, dir := range []*os.Root{r.legacy, r.deployment, r.root} {
		if dir != nil {
			_ = dir.Close()
		}
	}
	return r.work.Close()
}

func relWithin(root, path string) (string, error) {
	rel, err := filepath.Rel(root, path)
	if err != nil {
		return "", err
	}
	if rel == "." || filepath.IsAbs(rel) || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("%s is outside %s", path, root)
	}
	return rel, nil
}

func (r *recordStore) rel(path string) (string, error) { return relWithin(r.base, path) }

func readRecordFile(root *os.Root, name string) ([]byte, bool, error) {
	if root == nil {
		return nil, false, nil
	}
	// A namespace writer may replace the file between inspection and open.
	// Retry that case, and read only from the inspected regular file.
	for range 3 {
		info, err := root.Lstat(name)
		if os.IsNotExist(err) {
			return nil, false, nil
		}
		if err != nil {
			return nil, false, err
		}
		if !info.Mode().IsRegular() {
			return nil, false, fmt.Errorf("ownership record %s is not a regular file", name)
		}
		f, err := root.Open(name)
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			return nil, false, err
		}
		opened, err := f.Stat()
		if err == nil && os.SameFile(info, opened) {
			b, err := io.ReadAll(f)
			_ = f.Close()
			return b, err == nil, err
		}
		_ = f.Close()
		if err != nil {
			return nil, false, err
		}
	}
	return nil, false, fmt.Errorf("ownership record %s changed while opening it", name)
}

func verifyRecordFile(root *os.Root, name string, file *os.File) error {
	info, err := root.Lstat(name)
	if err != nil {
		return err
	}
	opened, err := file.Stat()
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() || !os.SameFile(info, opened) {
		return fmt.Errorf("ownership file %s changed while opening it", name)
	}
	return nil
}

func removeRecordFile(root *os.Root, name string) error {
	if root == nil {
		return nil
	}
	if err := root.Remove(name); err != nil && !os.IsNotExist(err) {
		return err
	}
	return nil
}

func (r *recordStore) writeFileAtomic(path string, data []byte, perm os.FileMode) error {
	if err := r.verify(); err != nil {
		return err
	}
	rel, err := r.rel(path)
	if err != nil {
		return err
	}
	dirRel := filepath.Dir(rel)
	if dirRel != "." {
		if err := r.root.MkdirAll(dirRel, 0o755); err != nil {
			return err
		}
	}
	base := filepath.Base(rel)
	var f *os.File
	var tmpRel string
	for i := 0; i < 10; i++ {
		tmpRel = filepath.Join(dirRel, fmt.Sprintf(".%s.tmp-%d-%d", base, os.Getpid(), time.Now().UnixNano()))
		f, err = r.root.OpenFile(tmpRel, os.O_WRONLY|os.O_CREATE|os.O_EXCL, perm)
		if err == nil {
			break
		}
		if !os.IsExist(err) {
			return err
		}
	}
	if f == nil {
		return fmt.Errorf("create temporary record for %s", path)
	}
	defer func() {
		_ = f.Close()
		_ = r.root.Remove(tmpRel)
	}()
	if _, err := f.Write(data); err != nil {
		return err
	}
	if err := f.Sync(); err != nil {
		return err
	}
	if err := f.Close(); err != nil {
		return err
	}
	if err := r.verify(); err != nil {
		return err
	}
	if err := r.root.Rename(tmpRel, rel); err != nil {
		return err
	}
	if d, err := r.root.Open(dirRel); err == nil {
		_ = d.Sync()
		_ = d.Close()
	}
	return nil
}

func parseManagedRepos(path string, b []byte) ([]ManagedRepo, error) {
	var records []ManagedRepo
	if err := json.Unmarshal(b, &records); err == nil {
		return records, nil
	}
	var legacy []string
	if err := json.Unmarshal(b, &legacy); err != nil {
		return nil, fmt.Errorf("parse %s: %w", path, err)
	}
	records = make([]ManagedRepo, 0, len(legacy))
	for _, dir := range legacy {
		records = append(records, ManagedRepo{Path: dir})
	}
	return records, nil
}

// ManagedRepos returns the recorded aiv-cloned repositories, merging the
// current location with the legacy in-state file so a pre-upgrade record is
// not lost while it still exists.
func (s *State) ManagedRepos() ([]ManagedRepo, error) {
	store, err := s.openRecordStore(false)
	if err != nil {
		return nil, err
	}
	if store == nil {
		return nil, nil
	}
	defer store.Close()
	return s.managedReposFrom(store)
}

func (s *State) managedReposFrom(store *recordStore) ([]ManagedRepo, error) {
	if err := store.verify(); err != nil {
		return nil, err
	}
	var out []ManagedRepo
	seen := map[string]bool{}
	seenPath := map[string]bool{}
	add := func(path string, b []byte) error {
		records, err := parseManagedRepos(path, b)
		if err != nil {
			return err
		}
		for _, record := range records {
			dir, err := filepath.Abs(record.Path)
			if err != nil {
				return err
			}
			record.Path = filepath.Clean(dir)
			key := record.Path + "\x00" + record.ID
			if record.Pending {
				key += "\x00pending"
			}
			if seen[key] {
				continue
			}
			if record.ID == "" && seenPath[record.Path] {
				continue
			}
			seen[key] = true
			seenPath[record.Path] = true
			out = append(out, record)
		}
		return nil
	}
	sources := []struct {
		path string
		root *os.Root
	}{
		{s.reposFile(), store.root},
		{s.legacyReposFile(), store.legacy},
	}
	for _, source := range sources {
		b, exists, err := readRecordFile(source.root, filepath.Base(source.path))
		if err != nil {
			return nil, err
		}
		if !exists {
			continue
		}
		if err := add(source.path, b); err != nil {
			return nil, err
		}
	}
	return out, store.verify()
}

func (s *State) writeManagedReposLocked(store *recordStore, repos []ManagedRepo) error {
	if err := store.verify(); err != nil {
		return err
	}
	if len(repos) == 0 {
		if err := removeRecordFile(store.root, filepath.Base(s.reposFile())); err != nil {
			return err
		}
		return removeRecordFile(store.legacy, filepath.Base(s.legacyReposFile()))
	}
	b, err := json.MarshalIndent(repos, "", "  ")
	if err != nil {
		return err
	}
	if err := store.writeFileAtomic(s.reposFile(), b, 0o644); err != nil {
		return err
	}
	if err := store.verify(); err != nil {
		return err
	}
	return removeRecordFile(store.legacy, filepath.Base(s.legacyReposFile()))
}

var recordMutexes sync.Map

func (s *State) lockFile() string {
	return filepath.Join(s.recordDir(), ".namespace-"+namespaceKey(s.Namespace)+".lock")
}

// withRecordLock serialises ownership-record updates across CLI processes.
// The lock and the record file both live under workdir, outside the state
// directory removed by --purge.
func (s *State) withRecordLock(fn func(*recordStore) error) error {
	store, err := s.openRecordStore(true)
	if err != nil {
		return err
	}
	defer store.Close()
	lockPath := s.lockFile()
	muAny, _ := recordMutexes.LoadOrStore(lockPath, &sync.Mutex{})
	mu := muAny.(*sync.Mutex)
	mu.Lock()
	defer mu.Unlock()
	release, err := acquireRecordLock(store, filepath.Base(lockPath))
	if err != nil {
		return err
	}
	defer release()
	if err := store.verify(); err != nil {
		return err
	}
	return fn(store)
}

// AcquireRepoRemovalLock serialises removal of one recorded checkout across
// CLI processes. The lock lives with the records and survives state purge.
func (s *State) AcquireRepoRemovalLock(dir, id string) (func(), error) {
	store, err := s.openRecordStore(true)
	if err != nil {
		return nil, err
	}
	abs, err := filepath.Abs(dir)
	if err != nil {
		_ = store.Close()
		return nil, err
	}
	sum := sha256.Sum256([]byte(filepath.Clean(abs) + "\x00" + id))
	release, err := acquireRecordLock(store, ".removal-"+hex.EncodeToString(sum[:8])+".lock")
	if err != nil {
		_ = store.Close()
		return nil, err
	}
	if err := store.verify(); err != nil {
		release()
		_ = store.Close()
		return nil, err
	}
	return func() {
		release()
		_ = store.Close()
	}, nil
}

func (s *State) updateManagedRepos(mutate func([]ManagedRepo) []ManagedRepo) error {
	return s.withRecordLock(func(store *recordStore) error {
		repos, err := s.managedReposFrom(store)
		if err != nil {
			return err
		}
		return s.writeManagedReposLocked(store, mutate(repos))
	})
}

// RecordManagedRepo marks dir as cloned by aiv so a later --purge-repos can
// delete it. Paths outside sourceRoot are not recorded; destroy keeps them
// even if a stale record exists.
func (s *State) RecordManagedRepo(dir, id string) error {
	if id == "" {
		return fmt.Errorf("record cloned repo %s: empty ownership id", dir)
	}
	abs, err := filepath.Abs(dir)
	if err != nil {
		return err
	}
	abs = filepath.Clean(abs)
	return s.updateManagedRepos(func(repos []ManagedRepo) []ManagedRepo {
		out := make([]ManagedRepo, 0, len(repos)+1)
		recorded := false
		for _, repo := range repos {
			if repo.Path != abs {
				out = append(out, repo)
				continue
			}
			if repo.ID == id {
				// Keep Pending so an interrupted removal is not cancelled by
				// a checkout that reappears at the same path.
				out = append(out, repo)
				recorded = true
				continue
			}
			if repo.Pending {
				out = append(out, repo)
			}
		}
		if !recorded {
			out = append(out, ManagedRepo{Path: abs, ID: id})
		}
		return out
	})
}

// ForgetManagedRepo removes dir from the ownership records after its checkout
// has been deleted.
func (s *State) ForgetManagedRepo(dir string) error {
	abs, err := filepath.Abs(dir)
	if err != nil {
		return err
	}
	abs = filepath.Clean(abs)
	return s.updateManagedRepos(func(repos []ManagedRepo) []ManagedRepo {
		kept := make([]ManagedRepo, 0, len(repos))
		for _, repo := range repos {
			if repo.Path != abs {
				kept = append(kept, repo)
			}
		}
		return kept
	})
}

// ForgetManagedRepoID removes only the record for one ownership id, keeping a
// newer checkout recorded at the same path.
func (s *State) ForgetManagedRepoID(dir, id string) error {
	abs, err := filepath.Abs(dir)
	if err != nil {
		return err
	}
	abs = filepath.Clean(abs)
	return s.updateManagedRepos(func(repos []ManagedRepo) []ManagedRepo {
		kept := make([]ManagedRepo, 0, len(repos))
		for _, repo := range repos {
			if repo.Path != abs || repo.ID != id {
				kept = append(kept, repo)
			}
		}
		return kept
	})
}

// SetRepoPending records that removal of dir has started. It requires a
// matching record so an unrecorded checkout is never treated as pending.
func (s *State) SetRepoPending(dir, id string, pending bool) error {
	abs, err := filepath.Abs(dir)
	if err != nil {
		return err
	}
	abs = filepath.Clean(abs)
	found := false
	err = s.updateManagedRepos(func(repos []ManagedRepo) []ManagedRepo {
		for i := range repos {
			if repos[i].Path == abs && repos[i].ID == id {
				repos[i].Pending = pending
				found = true
			}
		}
		return repos
	})
	if err != nil {
		return err
	}
	if !found {
		return fmt.Errorf("ownership record for %s is missing", dir)
	}
	return nil
}

// ClaimRepoPending transitions an active record to pending. It returns false
// when another invocation already claimed it or the record disappeared.
func (s *State) ClaimRepoPending(dir, id string) (bool, error) {
	abs, err := filepath.Abs(dir)
	if err != nil {
		return false, err
	}
	abs = filepath.Clean(abs)
	claimed := false
	err = s.updateManagedRepos(func(repos []ManagedRepo) []ManagedRepo {
		for i := range repos {
			if repos[i].Path != abs || repos[i].ID != id {
				continue
			}
			if !repos[i].Pending {
				repos[i].Pending = true
				claimed = true
			}
			return repos
		}
		return repos
	})
	return claimed, err
}

// ForgetManagedRepoIDIfActive removes one record only if no removal has been
// claimed since the caller read it.
func (s *State) ForgetManagedRepoIDIfActive(dir, id string) (bool, error) {
	abs, err := filepath.Abs(dir)
	if err != nil {
		return false, err
	}
	abs = filepath.Clean(abs)
	removed := false
	err = s.updateManagedRepos(func(repos []ManagedRepo) []ManagedRepo {
		kept := make([]ManagedRepo, 0, len(repos))
		for _, repo := range repos {
			if repo.Path == abs && repo.ID == id && !repo.Pending {
				removed = true
				continue
			}
			kept = append(kept, repo)
		}
		return kept
	})
	return removed, err
}
