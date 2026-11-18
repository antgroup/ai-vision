package sandboxbootstrap

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestPrivateStateRetainsTokenAndRefusesChangedPlanOrSymlink(t *testing.T) {
	root := filepath.Join(t.TempDir(), "private")
	plan := validPlan(t)
	host := Host{DaemonID: "daemon", BackendID: strings.Repeat("a", 64), DiagnosisID: strings.Repeat("b", 64), NetworkID: strings.Repeat("c", 64), NetworkName: "stack", Socket: "/run/docker.sock", Platform: "linux/arm64"}
	owned, unlock, err := openOwned(context.Background(), root, plan, host)
	if err != nil {
		t.Fatal(err)
	}
	if err = owned.configure(); err != nil {
		t.Fatal(err)
	}
	token, err := os.ReadFile(filepath.Join(owned.Dir, "plugin-token"))
	if err != nil {
		t.Fatal(err)
	}
	unlock()
	reused, unlock, err := openOwned(context.Background(), root, plan, host)
	if err != nil {
		t.Fatal(err)
	}
	if err = reused.configure(); err != nil {
		t.Fatal(err)
	}
	again, _ := os.ReadFile(filepath.Join(reused.Dir, "plugin-token"))
	if string(token) != string(again) {
		t.Fatal("retry rotated the active service key")
	}
	unlock()
	changed := plan
	changed.PlanID = strings.Repeat("f", 64)
	if _, _, err = openOwned(context.Background(), root, changed, host); err == nil {
		t.Fatal("changed plan adopted saved gateway/journal")
	}
	if err = os.Remove(filepath.Join(reused.Dir, "plugin-token")); err != nil {
		t.Fatal(err)
	}
	if err = os.Symlink(filepath.Join(reused.Dir, "policy.json"), filepath.Join(reused.Dir, "plugin-token")); err != nil {
		t.Fatal(err)
	}
	bad, unlock, err := openOwned(context.Background(), root, plan, host)
	if err != nil {
		t.Fatal(err)
	}
	defer unlock()
	if err = bad.configure(); err == nil {
		t.Fatal("secret symlink accepted")
	}
}
