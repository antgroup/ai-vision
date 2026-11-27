package runtime

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/pathguard"
)

var errSourceRootMissing = errors.New("source root does not exist")

// repoFS binds every repository inspection and removal operation to the
// source root that was validated, so a path component swapped for a symlink
// after the check cannot redirect the operation outside it.
type repoFS struct {
	root *os.Root
	base string
	// Keep planned checkout paths until this purge finishes. Removing an inner
	// record must not let a replacement at the same path disappear with its parent.
	plannedCheckoutPaths []string
}

func openRepoFS(base string) (*repoFS, error) {
	if base == "" {
		return nil, fmt.Errorf("source root is not configured")
	}
	root, err := os.OpenRoot(base)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, errSourceRootMissing
		}
		return nil, fmt.Errorf("open source root %s: %w", base, err)
	}
	return &repoFS{root: root, base: base}, nil
}

func (f *repoFS) Close() error { return f.root.Close() }

func (f *repoFS) rel(path string) (string, error) {
	if err := pathguard.EnsureStrictlyWithin(f.base, path); err != nil {
		return "", err
	}
	rel, err := filepath.Rel(f.base, path)
	if err != nil {
		return "", err
	}
	if rel == "." || filepath.IsAbs(rel) || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("%s is outside %s", path, f.base)
	}
	return rel, nil
}

func (f *repoFS) lstat(path string) (os.FileInfo, error) {
	rel, err := f.rel(path)
	if err != nil {
		return nil, err
	}
	return f.root.Lstat(rel)
}

func (f *repoFS) readFile(path string) ([]byte, error) {
	rel, err := f.rel(path)
	if err != nil {
		return nil, err
	}
	return f.root.ReadFile(rel)
}

func (f *repoFS) renameVerifiedCheckout(repo ManagedRepo, tombstone string) error {
	oldRel, err := f.rel(repo.Path)
	if err != nil {
		return err
	}
	newRel, err := f.rel(tombstone)
	if err != nil {
		return err
	}
	marker, owner, err := f.inspectPurgeMarker(tombstone, repo)
	if err != nil {
		return err
	}
	if owner != repoOwnerMatches {
		return fmt.Errorf("removal marker no longer verifies %s", repo.Path)
	}
	info, err := f.root.Lstat(oldRel)
	if err != nil {
		return err
	}
	matches, err := marker.ownsDirectory(info)
	if err != nil {
		return err
	}
	if !matches {
		return fmt.Errorf("checkout %s no longer matches its recorded identity", repo.Path)
	}
	if _, err := f.root.Lstat(newRel); err == nil {
		return fmt.Errorf("removal path %s already exists", tombstone)
	} else if !os.IsNotExist(err) {
		return err
	}
	return f.root.Rename(oldRel, newRel)
}

func (f *repoFS) remove(path string) error {
	rel, err := f.rel(path)
	if err != nil {
		return err
	}
	if err := f.root.Remove(rel); err != nil && !os.IsNotExist(err) {
		return err
	}
	return nil
}

func (f *repoFS) openDirectory(path string) (*os.Root, os.FileInfo, error) {
	rel, err := f.rel(path)
	if err != nil {
		return nil, nil, err
	}
	before, err := f.root.Lstat(rel)
	if err != nil {
		return nil, nil, err
	}
	if !before.IsDir() {
		return nil, nil, fmt.Errorf("%s: %w", path, syscall.ENOTDIR)
	}
	dir, err := f.root.OpenRoot(rel)
	if err != nil {
		return nil, nil, err
	}
	info, err := dir.Stat(".")
	if err != nil {
		_ = dir.Close()
		return nil, nil, err
	}
	if !os.SameFile(before, info) {
		_ = dir.Close()
		return nil, nil, fmt.Errorf("directory %s changed while opening it", path)
	}
	return dir, info, nil
}

func (f *repoFS) checkoutOwner(path string) (repoOwner, error) {
	dir, info, err := f.openDirectory(path)
	if os.IsNotExist(err) || errors.Is(err, syscall.ENOTDIR) {
		return repoOwner{}, nil
	}
	if err != nil {
		return repoOwner{}, err
	}
	defer dir.Close()
	return readCheckoutOwner(dir, path, info)
}

func (f *repoFS) inspectRepoOwner(dir, want string) (repoOwnerState, error) {
	owner, err := f.checkoutOwner(dir)
	if err != nil {
		return repoOwnerMissing, err
	}
	if owner.ID != want {
		return repoOwnerDifferent, nil
	}
	return repoOwnerMatches, nil
}

func (f *repoFS) writePurgeMarker(tombstone string, repo ManagedRepo) error {
	dir, info, err := f.openDirectory(repo.Path)
	if err != nil {
		return err
	}
	defer dir.Close()
	checkout, err := readCheckoutOwner(dir, repo.Path, info)
	if err != nil {
		return err
	}
	if checkout.ID != repo.ID {
		return fmt.Errorf("checkout ownership changed for %s", repo.Path)
	}
	dev, ino, ok := fileIdentity(info)
	if !ok {
		return fmt.Errorf("directory identity is unavailable for %s", repo.Path)
	}
	marker := purgeMarkerRecord{Path: repo.Path, ID: repo.ID, HasIdentity: true, Dev: dev, Ino: ino}
	existing, owner, err := f.inspectPurgeMarker(tombstone, repo)
	if err != nil {
		return err
	}
	if owner != repoOwnerMissing {
		if owner == repoOwnerMatches && existing == marker {
			return nil
		}
		return fmt.Errorf("conflicting removal marker %s", purgeMarkerPath(tombstone))
	}
	b, err := json.Marshal(marker)
	if err != nil {
		return err
	}
	path := purgeMarkerPath(tombstone)
	rel, err := f.rel(path)
	if err != nil {
		return err
	}
	dirRel := filepath.Dir(rel)
	base := filepath.Base(rel)
	var file *os.File
	var tmpRel string
	for i := 0; i < 10; i++ {
		tmpRel = filepath.Join(dirRel, fmt.Sprintf(".%s.tmp-%d-%d", base, os.Getpid(), time.Now().UnixNano()))
		file, err = f.root.OpenFile(tmpRel, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
		if err == nil {
			break
		}
		if !os.IsExist(err) {
			return err
		}
	}
	if file == nil {
		return fmt.Errorf("create temporary marker for %s", path)
	}
	defer func() {
		_ = file.Close()
		_ = f.root.Remove(tmpRel)
	}()
	if _, err := file.Write(b); err != nil {
		return err
	}
	if err := file.Sync(); err != nil {
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	// Publish the complete marker without replacing any file at the destination.
	if err := f.root.Link(tmpRel, rel); err != nil {
		return err
	}
	if d, err := f.root.Open(dirRel); err == nil {
		_ = d.Sync()
		_ = d.Close()
	}
	return nil
}

func (f *repoFS) inspectPurgeMarker(tombstone string, repo ManagedRepo) (purgeMarkerRecord, repoOwnerState, error) {
	path := purgeMarkerPath(tombstone)
	info, err := f.lstat(path)
	if os.IsNotExist(err) {
		return purgeMarkerRecord{}, repoOwnerMissing, nil
	}
	if err != nil {
		return purgeMarkerRecord{}, repoOwnerMissing, err
	}
	if !info.Mode().IsRegular() {
		return purgeMarkerRecord{}, repoOwnerMissing, fmt.Errorf("removal marker %s is not a regular file", path)
	}
	b, err := f.readFile(path)
	if err != nil {
		return purgeMarkerRecord{}, repoOwnerMissing, err
	}
	var marker purgeMarkerRecord
	if err := json.Unmarshal(b, &marker); err != nil {
		return purgeMarkerRecord{}, repoOwnerMissing, fmt.Errorf("parse removal marker %s: %w", path, err)
	}
	if marker.Path == "" || marker.ID == "" {
		return purgeMarkerRecord{}, repoOwnerMissing, fmt.Errorf("removal marker %s has no checkout path or ownership id", path)
	}
	if marker.Path != repo.Path || marker.ID != repo.ID {
		return marker, repoOwnerDifferent, nil
	}
	if !marker.HasIdentity {
		return marker, repoOwnerMatches, fmt.Errorf("removal marker %s has no directory identity", path)
	}
	return marker, repoOwnerMatches, nil
}

// purgeMarkerOwnsTombstone verifies that the directory at the tombstone path
// is the object that was renamed, not merely that a removal was once planned.
func (f *repoFS) purgeMarkerOwnsTombstone(tombstone string, repo ManagedRepo) (bool, error) {
	marker, owner, err := f.inspectPurgeMarker(tombstone, repo)
	if err != nil || owner != repoOwnerMatches {
		return false, err
	}
	info, err := f.lstat(tombstone)
	if err != nil {
		return false, err
	}
	return marker.ownsDirectory(info)
}

func (marker purgeMarkerRecord) ownsDirectory(info os.FileInfo) (bool, error) {
	if !info.IsDir() {
		return false, nil
	}
	dev, ino, ok := fileIdentity(info)
	if !marker.HasIdentity || !ok {
		return false, fmt.Errorf("directory identity is unavailable for %s", info.Name())
	}
	return dev == marker.Dev && ino == marker.Ino, nil
}

func (f *repoFS) removeVerifiedTombstone(tombstone string, repo ManagedRepo) error {
	marker, owner, err := f.inspectPurgeMarker(tombstone, repo)
	if err != nil {
		return err
	}
	if owner != repoOwnerMatches {
		return fmt.Errorf("removal marker no longer verifies %s", tombstone)
	}
	dir, info, err := f.openDirectory(tombstone)
	if err != nil {
		return err
	}
	defer dir.Close()
	matches, err := marker.ownsDirectory(info)
	if err != nil {
		return err
	}
	if !matches {
		return fmt.Errorf("pending directory %s no longer matches its recorded identity", tombstone)
	}
	entries, err := fs.ReadDir(dir.FS(), ".")
	if err != nil {
		return err
	}
	// Recursion stays on the verified directory handle. Only the final removal
	// of the empty directory uses its name.
	for _, entry := range entries {
		if err := dir.RemoveAll(entry.Name()); err != nil {
			return err
		}
	}
	current, err := f.lstat(tombstone)
	if err != nil {
		return err
	}
	if !os.SameFile(info, current) {
		return fmt.Errorf("pending directory %s changed during removal", tombstone)
	}
	return f.remove(tombstone)
}
