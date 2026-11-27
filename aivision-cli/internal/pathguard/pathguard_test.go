package pathguard

import (
	"os"
	"path/filepath"
	"testing"
)

func TestEnsureWithin(t *testing.T) {
	root := t.TempDir()
	outside := t.TempDir()

	cases := []struct {
		name   string
		target string
		ok     bool
	}{
		{"root", root, true},
		{"child", filepath.Join(root, "state"), true},
		{"missing child", filepath.Join(root, "a", "b"), true},
		{"sibling", filepath.Join(filepath.Dir(root), "other"), false},
		{"parent", filepath.Dir(root), false},
		{"prefix sibling", root + "-other", false},
		{"outside", filepath.Join(outside, "x"), false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			err := EnsureWithin(root, tc.target)
			if tc.ok && err != nil {
				t.Fatalf("EnsureWithin(%q, %q) = %v, want nil", root, tc.target, err)
			}
			if !tc.ok && err == nil {
				t.Fatalf("EnsureWithin(%q, %q) = nil, want error", root, tc.target)
			}
		})
	}
}

func TestEnsureStrictlyWithinRejectsRoot(t *testing.T) {
	root := t.TempDir()
	if err := EnsureStrictlyWithin(root, root); err == nil {
		t.Fatal("expected root itself to be rejected")
	}
	if err := EnsureStrictlyWithin(root, filepath.Join(root, "ns")); err != nil {
		t.Fatalf("expected child to be allowed: %v", err)
	}
}

func TestEnsureWithinResolvesSymlinks(t *testing.T) {
	root := t.TempDir()
	outside := t.TempDir()
	if err := os.WriteFile(filepath.Join(outside, "important.txt"), []byte("keep"), 0o600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(root, "escape")
	if err := os.Symlink(outside, link); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	if err := EnsureWithin(root, filepath.Join(link, "important.txt")); err == nil {
		t.Fatal("expected a symlink inside the root to resolve outside it")
	}
	if err := EnsureWithin(root, link); err == nil {
		t.Fatal("expected a symlink target outside the root to be rejected")
	}
}

func TestResolveKeepsMissingSuffix(t *testing.T) {
	root := t.TempDir()
	realRoot, err := filepath.EvalSymlinks(root)
	if err != nil {
		t.Fatal(err)
	}
	want := filepath.Join(realRoot, "a", "b")
	got, err := Resolve(want)
	if err != nil {
		t.Fatal(err)
	}
	if got != want {
		t.Fatalf("Resolve(%q) = %q, want %q", want, got, want)
	}
}
