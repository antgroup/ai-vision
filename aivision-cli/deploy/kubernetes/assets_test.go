package manifests

import (
	"crypto/sha256"
	"fmt"
	"testing"
)

// Upstream distributions are deliberately vendored verbatim. A version bump
// updates the manifest, this pin, provenance and dependency compatibility tests.
func TestDependencyChecksums(t *testing.T) {
	for name, expected := range map[string]string{
		"cert-manager.yaml": "e03b668ec8675214af6b0a671699d088f2601fa3878e0dbe1b41d3feafd1879f",
		"ob-operator.yaml":  "71d3f64fa7a6585472fa7ae3d2b9fe655342fa2e07c2c212c82d60a8f1d34d95",
	} {
		data, err := Files.ReadFile("dependencies/" + name)
		if err != nil {
			t.Fatal(err)
		}
		if got := fmt.Sprintf("%x", sha256.Sum256(data)); got != expected {
			t.Fatalf("%s differs from pinned upstream: %s", name, got)
		}
	}
}
