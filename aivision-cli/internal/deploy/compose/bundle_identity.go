package compose

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"hash"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"syscall"

	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/generate"
)

const (
	bundleIdentityPrefix = "hmac-v1-"
	bundleKeyName        = ".bundle-identity-key"
)

// digest exists only to read the exact content identity of pre-keyed bundles.
func digest(files []generate.File) string { return hashBundle(sha256.New(), files) }

func keyedBundleIdentity(key []byte, files []generate.File) string {
	return bundleIdentityPrefix + hashBundle(hmac.New(sha256.New, key), files)
}

func hashBundle(digest hash.Hash, files []generate.File) string {
	ordered := append([]generate.File(nil), files...)
	sort.Slice(ordered, func(i, j int) bool { return ordered[i].Path < ordered[j].Path })
	for _, file := range ordered {
		_ = binary.Write(digest, binary.BigEndian, uint64(len(file.Path)))
		_, _ = digest.Write([]byte(file.Path))
		_ = binary.Write(digest, binary.BigEndian, uint64(len(file.Content)))
		_, _ = digest.Write(file.Content)
	}
	return hex.EncodeToString(digest.Sum(nil))
}

func ownedByProcess(info os.FileInfo) bool {
	owner, ok := info.Sys().(*syscall.Stat_t)
	return ok && int(owner.Uid) == os.Geteuid()
}

func readBundleKey(composeDir string) ([]byte, error) {
	dir, err := os.Lstat(composeDir)
	if err != nil || !dir.IsDir() || dir.Mode().Perm()&0077 != 0 || !ownedByProcess(dir) {
		return nil, fmt.Errorf("Compose identity key must reside in a private owner-only directory")
	}
	path := filepath.Join(composeDir, bundleKeyName)
	keyFile, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, err
	}
	defer keyFile.Close()
	info, err := keyFile.Stat()
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 || !ownedByProcess(info) || info.Size() != sha256.Size {
		return nil, fmt.Errorf("invalid private Compose identity key; restore the original key")
	}
	key := make([]byte, sha256.Size)
	if _, err := io.ReadFull(keyFile, key); err != nil {
		return nil, err
	}
	return key, nil
}

func loadOrCreateBundleKey(bundlesDir string) ([]byte, error) {
	composeDir := filepath.Dir(bundlesDir)
	key, err := readBundleKey(composeDir)
	if err == nil || !errors.Is(err, os.ErrNotExist) {
		return key, err
	}
	entries, err := os.ReadDir(bundlesDir)
	if err != nil {
		return nil, err
	}
	for _, entry := range entries {
		if strings.HasPrefix(entry.Name(), bundleIdentityPrefix) {
			// A concurrent publisher installs the key before publishing any bundle.
			return readSavedBundleKey(composeDir)
		}
	}
	// The pointer may outlive a bundle directory in a partial state restore.
	pointerPath := filepath.Join(filepath.Dir(composeDir), pointerName)
	if err := privateFile(pointerPath); err == nil {
		data, err := os.ReadFile(pointerPath)
		if err != nil {
			return nil, err
		}
		var saved savedProject
		if err := json.Unmarshal(data, &saved); err != nil {
			return nil, fmt.Errorf("invalid saved Compose project pointer: %w", err)
		}
		if strings.HasPrefix(filepath.Base(saved.Bundle), bundleIdentityPrefix) {
			return readSavedBundleKey(composeDir)
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}

	key = make([]byte, sha256.Size)
	if _, err := rand.Read(key); err != nil {
		return nil, err
	}
	staging, err := os.CreateTemp(composeDir, ".bundle-key-")
	if err != nil {
		return nil, err
	}
	defer os.Remove(staging.Name())
	if _, err = staging.Write(key); err == nil {
		err = staging.Sync()
	}
	closeErr := staging.Close()
	if err != nil {
		return nil, err
	}
	if closeErr != nil {
		return nil, closeErr
	}
	// Link atomically, without replacing an identity key created by another cast.
	if err := os.Link(staging.Name(), filepath.Join(composeDir, bundleKeyName)); err != nil {
		if !errors.Is(err, os.ErrExist) {
			return nil, err
		}
		return readSavedBundleKey(composeDir)
	}
	return key, nil
}

func readSavedBundleKey(composeDir string) ([]byte, error) {
	key, err := readBundleKey(composeDir)
	if err != nil {
		return nil, fmt.Errorf("saved Compose bundle requires its original private identity key: %w", err)
	}
	return key, nil
}
