// Package pathguard checks that a destructive filesystem target stays inside
// an operator-selected root. It resolves symlinks on the longest existing
// prefix so a link inside the root cannot redirect a delete outside it.
package pathguard

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// Resolve makes p absolute and evaluates symlinks on its longest existing
// prefix. Components that do not exist yet are appended lexically, which lets
// callers guard a directory before creating it.
func Resolve(p string) (string, error) {
	abs, err := filepath.Abs(p)
	if err != nil {
		return "", err
	}
	abs = filepath.Clean(abs)
	resolved := abs
	var missing []string
	for {
		real, err := filepath.EvalSymlinks(resolved)
		if err == nil {
			for i := len(missing) - 1; i >= 0; i-- {
				real = filepath.Join(real, missing[i])
			}
			return real, nil
		}
		if !os.IsNotExist(err) {
			return "", err
		}
		parent := filepath.Dir(resolved)
		if parent == resolved {
			return abs, nil
		}
		missing = append(missing, filepath.Base(resolved))
		resolved = parent
	}
}

// EnsureWithin rejects target when it is not root or a descendant of root.
func EnsureWithin(root, target string) error {
	return ensure(root, target, true)
}

// EnsureStrictlyWithin rejects target when it is root itself or outside root.
func EnsureStrictlyWithin(root, target string) error {
	return ensure(root, target, false)
}

// Contains reports whether deleting the existing directory root would also
// remove target. Directory identity handles aliases on case-insensitive volumes.
func Contains(root, target string) (bool, error) {
	_, inside, err := RelativeWithin(root, target)
	return inside, err
}

// RelativeWithin returns the relative path below an existing root, resolving
// symlinks and comparing filesystem identities for case-insensitive aliases.
func RelativeWithin(root, target string) (string, bool, error) {
	rootInfo, err := os.Stat(root)
	if os.IsNotExist(err) {
		return "", false, nil
	}
	if err != nil {
		return "", false, err
	}
	path, err := Resolve(target)
	if err != nil {
		return "", false, err
	}
	resolved := path
	for {
		info, err := os.Stat(path)
		if err != nil && !os.IsNotExist(err) {
			return "", false, err
		}
		if err == nil && os.SameFile(rootInfo, info) {
			rel, err := filepath.Rel(path, resolved)
			return rel, true, err
		}
		parent := filepath.Dir(path)
		if parent == path {
			return "", false, nil
		}
		path = parent
	}
}

func ensure(root, target string, allowRoot bool) error {
	resolvedRoot, err := Resolve(root)
	if err != nil {
		return fmt.Errorf("resolve %s: %w", root, err)
	}
	resolvedTarget, err := Resolve(target)
	if err != nil {
		return fmt.Errorf("resolve %s: %w", target, err)
	}
	rel, err := filepath.Rel(resolvedRoot, resolvedTarget)
	if err != nil {
		return fmt.Errorf("%s is outside %s: %w", target, root, err)
	}
	if rel == "." {
		if allowRoot {
			return nil
		}
		return fmt.Errorf("%s is the root %s; refusing to remove it", target, root)
	}
	if rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) || filepath.IsAbs(rel) {
		return fmt.Errorf("%s is outside %s", target, root)
	}
	return nil
}
