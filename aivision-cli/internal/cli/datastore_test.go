package cli

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// Backend selection must survive the same saved-vars path used by status,
// destroy and redeploy; the external flags must not reintroduce a tenant login.
func TestSeekdbSelectionPersistsAndExternalFlagsUseRoot(t *testing.T) {
	workdir := t.TempDir()
	varsFile := filepath.Join(t.TempDir(), "vars.yaml")
	if err := os.WriteFile(varsFile, []byte("OB_BACKEND: seekdb\n"), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("AIV_SEEKDB_TEST_PASSWORD", "deployment-test-password")
	g := &globalOpts{
		file: "../../blueprints/aivision.yaml", namespace: "seekdb-cli-test",
		workdir: workdir, varsFile: varsFile, obHost: "db.example.test",
		obPort: "3881", obPasswordEnv: "AIV_SEEKDB_TEST_PASSWORD",
	}
	app, err := buildApp(g)
	if err != nil {
		t.Fatal(err)
	}
	if app.Vars["OB_USER"] != "root" || app.OBManaged() {
		t.Fatalf("external seekdb selection: user=%q managed=%v", app.Vars["OB_USER"], app.OBManaged())
	}
	if err := app.State.SaveVars(app.Overrides); err != nil {
		t.Fatal(err)
	}
	reloaded, err := buildApp(&globalOpts{file: g.file, namespace: g.namespace, workdir: workdir})
	if err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"OB_BACKEND", "OB_USER", "OB_HOST", "OB_HOST_PORT", "OB_TENANT_PASSWORD", "OCEANBASE_DSN"} {
		if reloaded.Vars[key] != app.Vars[key] {
			t.Errorf("saved selection changed %s", key)
		}
	}
	if len(reloaded.BP.OrderedComponents()) != 5 {
		t.Fatal("saved external seekdb configuration enabled the managed datastore")
	}
}

func TestKubernetesRejectsSeekdbBeforeInvokingKubectl(t *testing.T) {
	if err := cmdCast([]string{"--target", "kubernetes", "--var", "OB_BACKEND=seekdb"}); err == nil || !strings.Contains(err.Error(), "unsupported Kubernetes variable OB_BACKEND") {
		t.Fatalf("seekdb must be rejected before invoking kubectl: %v", err)
	}
}
