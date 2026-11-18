package cli

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/components"
	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/runtime"
)

func TestRoleConfigAliasesPreserveOverridePrecedence(t *testing.T) {
	workdir := t.TempDir()
	g := &globalOpts{file: "../../blueprints/aivision.yaml", namespace: "alias-test", workdir: workdir}
	state := runtime.NewState(workdir, g.namespace)
	if err := state.SaveVars(map[string]string{"DATA_BACKEND_IMAGE": "example/saved:v1"}); err != nil {
		t.Fatal(err)
	}
	varsFile := filepath.Join(t.TempDir(), "vars.yaml")
	if err := os.WriteFile(varsFile, []byte("SIGNOZ_IMAGE: example/file:v1\n"), 0600); err != nil {
		t.Fatal(err)
	}
	check := func(want, source string) {
		t.Helper()
		app, err := buildApp(g)
		if err != nil {
			t.Fatal(err)
		}
		if app.Vars["SIGNOZ_IMAGE"] != want || app.VarSources["SIGNOZ_IMAGE"] != source {
			t.Fatalf("effective image=%q source=%q, want %q/%q", app.Vars["SIGNOZ_IMAGE"], app.VarSources["SIGNOZ_IMAGE"], want, source)
		}
		if app.Vars["DATA_BACKEND_IMAGE"] != want {
			t.Fatalf("role variable references resolve to %q, want %q", app.Vars["DATA_BACKEND_IMAGE"], want)
		}
		if app.BP.Component("signoz-ob") == nil || app.BP.Component("data-backend") != nil {
			t.Fatal("config alias changed internal component/state identity")
		}
		if err := app.State.SaveVars(app.Overrides); err != nil {
			t.Fatal(err)
		}
	}
	check("example/saved:v1", "saved")
	g.varsFile = varsFile
	check("example/file:v1", "file")
	t.Setenv("AIV_VAR_DATA_BACKEND_IMAGE", "example/env:v1")
	check("example/env:v1", "env")
	g.vars = varFlags{"DATA_BACKEND_IMAGE=example/first:v1", "SIGNOZ_IMAGE=example/last:v1"}
	check("example/last:v1", "flag")
	g.vars = varFlags{"SIGNOZ_IMAGE=example/first:v1", "DATA_BACKEND_IMAGE=example/last-role:v1"}
	check("example/last-role:v1", "flag")
}

func TestSavedProductRegistryRetainsMirrorImageNames(t *testing.T) {
	g := &globalOpts{file: "../../blueprints/aivision.yaml", namespace: "mirror-test", workdir: t.TempDir()}
	const mirror = "registry.example.org/legacy"
	const customFrontend = "registry.example.org/custom/frontend:pinned"
	state := runtime.NewState(g.workdir, g.namespace)
	if err := state.SaveVars(map[string]string{
		"IMAGE_REGISTRY": mirror, "IMAGE_TAG": "dev", "FRONTEND_IMAGE": customFrontend,
	}); err != nil {
		t.Fatal(err)
	}
	check := func(registry string, images map[string]string) {
		t.Helper()
		app, err := buildApp(g)
		if err != nil {
			t.Fatal(err)
		}
		if app.Vars["FE_IMAGE"] != customFrontend || app.Vars["FRONTEND_IMAGE"] != customFrontend {
			t.Fatalf("saved frontend override was lost: %q", app.Vars["FE_IMAGE"])
		}
		for key, image := range images {
			want := registry + "/" + image + ":dev"
			if app.Vars[key] != want {
				t.Errorf("%s = %q, want %q", key, app.Vars[key], want)
			}
		}
	}
	check(mirror, map[string]string{
		"BE_IMAGE": "aivision-be", "GATEWAY_IMAGE": "antcollector-gw", "AGENTLENS_IMAGE": "agentlens", "SIGNOZ_IMAGE": "signoz-ob",
	})
	g.vars = varFlags{"IMAGE_REGISTRY=" + components.ProductImageRegistry}
	check(components.ProductImageRegistry, map[string]string{
		"BE_IMAGE": "backend", "GATEWAY_IMAGE": "ingest-gateway", "AGENTLENS_IMAGE": "diagnosis", "SIGNOZ_IMAGE": "data-backend",
	})
}

func TestKubernetesRoleAliasesNormalizeEachLayer(t *testing.T) {
	varsFile := filepath.Join(t.TempDir(), "vars.yaml")
	if err := os.WriteFile(varsFile, []byte("DATA_BACKEND_IMAGE: example/file:v1\nDIAGNOSIS_IMAGE: example/diagnosis:v1\n"), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("AIV_VAR_SIGNOZ_IMAGE", "example/env:v1")
	g := &globalOpts{varsFile: varsFile}
	values, err := kubernetesOverrides(g)
	if err != nil || values["SIGNOZ_IMAGE"] != "example/env:v1" || values["AGENTLENS_IMAGE"] != "example/diagnosis:v1" {
		t.Fatalf("file/env role aliases: %#v, %v", values, err)
	}
	g.vars = varFlags{"DATA_BACKEND_IMAGE=example/first:v1", "SIGNOZ_IMAGE=example/last:v1"}
	values, err = kubernetesOverrides(g)
	if err != nil || values["SIGNOZ_IMAGE"] != "example/last:v1" {
		t.Fatalf("last flag did not win across spellings: %#v, %v", values, err)
	}
}

func TestQueryRecoveryRoleAliasesPreserveOverridePrecedence(t *testing.T) {
	for _, item := range []struct{ role, legacy, normal, emergency string }{
		{"BACKEND_QUERY_MODE", "AIVISION_QUERY_MODE", "auto", "raw"},
		{"DATA_BACKEND_PROJECTION_MAINTENANCE_ENABLED", "SIGNOZ_AIVISION_PROJECTION_MAINTENANCE_ENABLED", "true", "false"},
	} {
		t.Run(item.role, func(t *testing.T) {
			g := &globalOpts{file: "../../blueprints/aivision.yaml", namespace: "query-alias-test", workdir: t.TempDir()}
			state := runtime.NewState(g.workdir, g.namespace)
			if err := state.SaveVars(map[string]string{item.role: item.emergency}); err != nil {
				t.Fatal(err)
			}
			check := func(want, source string) {
				t.Helper()
				app, err := buildApp(g)
				if err != nil {
					t.Fatal(err)
				}
				if app.Vars[item.legacy] != want || app.VarSources[item.legacy] != source || app.Vars[item.role] != want {
					t.Fatalf("query control legacy=%q role=%q source=%q, want %q/%q", app.Vars[item.legacy], app.Vars[item.role], app.VarSources[item.legacy], want, source)
				}
			}
			check(item.emergency, "saved")
			g.varsFile = filepath.Join(t.TempDir(), "vars.yaml")
			if err := os.WriteFile(g.varsFile, []byte(item.legacy+": \""+item.normal+"\"\n"), 0600); err != nil {
				t.Fatal(err)
			}
			check(item.normal, "file")
			t.Setenv("AIV_VAR_"+item.role, item.emergency)
			check(item.emergency, "env")
			g.vars = varFlags{item.role + "=" + item.emergency, item.legacy + "=" + item.normal}
			check(item.normal, "flag")
			g.vars = varFlags{item.legacy + "=" + item.normal, item.role + "=" + item.emergency}
			check(item.emergency, "flag")
		})
	}
}

func TestVarsTemplateUsesRoleKeysAndPreservesImplementationSecrets(t *testing.T) {
	content, err := os.ReadFile("../../blueprints/aivision.yaml")
	if err != nil {
		t.Fatal(err)
	}
	previous := defaultBlueprintYAML
	defaultBlueprintYAML = string(content)
	defer func() { defaultBlueprintYAML = previous }()
	template, err := varsTemplate()
	if err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{
		"FRONTEND_IMAGE", "BACKEND_IMAGE", "DIAGNOSIS_IMAGE", "INGEST_GATEWAY_IMAGE",
		"DATA_BACKEND_IMAGE", "DATABASE_IMAGE", "DATABASE_BACKEND", "DATABASE_MANAGED",
		"BACKEND_QUERY_MODE", "DATA_BACKEND_PROJECTION_MAINTENANCE_ENABLED",
		"SIGNOZ_INGEST_INTERNAL_KEY", "SIGNOZ_USER_ROOT_PASSWORD", "OB_TENANT_PASSWORD",
	} {
		if !strings.Contains(template, "#"+key+":") {
			t.Errorf("vars template omitted %s", key)
		}
	}
	for _, old := range []string{"SIGNOZ_IMAGE", "SIGNOZ_CONTAINER", "AGENTLENS_IMAGE", "BE_IMAGE", "FE_IMAGE", "OB_BACKEND", "OB_HOST", "AIVISION_QUERY_MODE", "SIGNOZ_AIVISION_PROJECTION_MAINTENANCE_ENABLED"} {
		if strings.Contains(template, "#"+old+":") {
			t.Errorf("vars template still advertises legacy product key %s", old)
		}
	}
	if strings.Contains(template, "${OB_CONTAINER}") || !strings.Contains(template, "${DATABASE_CONTAINER}") {
		t.Error("vars template references a legacy product key")
	}
}
