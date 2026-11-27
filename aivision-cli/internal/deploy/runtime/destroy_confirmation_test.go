package runtime

import (
	"path/filepath"
	"testing"
)

func TestConfirmedRepoPlanKeepsUnconfirmedOrphanRecord(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "checkout")
	first := ManagedRepo{Path: dir, ID: "previewed"}
	replacement := ManagedRepo{Path: dir, ID: "replacement"}

	plan := confirmedRepoPlan(repoPurgePlan{orphans: []ManagedRepo{replacement}}, []ManagedRepo{first})
	if len(plan.orphans) != 0 {
		t.Fatalf("cleanup includes an unconfirmed ownership record: %v", plan.orphans)
	}
}
