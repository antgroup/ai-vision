package runtime

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

const repoOwnerMarker = ".git/aivision-clone.json"

type repoOwner struct {
	ID    string `json:"id"`
	Scope string `json:"scope,omitempty"`
	Path  string `json:"path"`
	Dev   uint64 `json:"dev"`
	Ino   uint64 `json:"ino"`
}

// newRepoOwnerID returns a random identity for a checkout cloned by aiv.
func newRepoOwnerID() (string, error) {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return hex.EncodeToString(b), nil
}

// writeRepoOwner records ownership inside .git so it never appears as an
// untracked file in the checkout.
func writeRepoOwner(dir, id string, scope ...string) error {
	root, err := os.OpenRoot(dir)
	if err != nil {
		return err
	}
	defer root.Close()
	if info, err := root.Lstat(".git"); err != nil {
		return err
	} else if !info.IsDir() {
		return fmt.Errorf("%s is not a normal git checkout", dir)
	}
	info, err := root.Stat(".")
	if err != nil {
		return err
	}
	dev, ino, ok := fileIdentity(info)
	if !ok {
		return fmt.Errorf("directory identity is unavailable for %s", dir)
	}
	path, err := filepath.Abs(dir)
	if err != nil {
		return err
	}
	owner := repoOwner{ID: id, Path: path, Dev: dev, Ino: ino}
	if len(scope) > 0 {
		owner.Scope = scope[0]
	}
	b, err := json.Marshal(owner)
	if err != nil {
		return err
	}
	return root.WriteFile(repoOwnerMarker, b, 0o644)
}

// A copied marker describes the original checkout. Require its path and
// directory identity so restoring a backup cannot transfer purge eligibility.
func readCheckoutOwner(root *os.Root, dir string, info os.FileInfo) (repoOwner, error) {
	gitDir, err := root.Lstat(".git")
	if os.IsNotExist(err) || (err == nil && !gitDir.IsDir()) {
		return repoOwner{}, nil
	}
	if err != nil {
		return repoOwner{}, err
	}
	b, err := root.ReadFile(repoOwnerMarker)
	if os.IsNotExist(err) {
		return repoOwner{}, nil
	}
	if err != nil {
		return repoOwner{}, err
	}
	var owner repoOwner
	if err := json.Unmarshal(b, &owner); err != nil {
		return repoOwner{}, fmt.Errorf("parse %s: %w", filepath.Join(dir, repoOwnerMarker), err)
	}
	if owner.ID == "" {
		return repoOwner{}, fmt.Errorf("%s has no ownership id", filepath.Join(dir, repoOwnerMarker))
	}
	path, err := filepath.Abs(dir)
	if err != nil {
		return repoOwner{}, err
	}
	dev, ino, ok := fileIdentity(info)
	if !ok || owner.Path != path || owner.Dev != dev || owner.Ino != ino {
		return repoOwner{}, nil
	}
	return owner, nil
}
