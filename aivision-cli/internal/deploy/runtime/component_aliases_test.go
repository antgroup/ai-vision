package runtime

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/components"
)

func TestLogsRoleAliasesUseExistingContainerNames(t *testing.T) {
	dir := t.TempDir()
	docker := filepath.Join(dir, "docker")
	calls := filepath.Join(dir, "calls")
	if err := os.WriteFile(docker, []byte("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$AIV_ALIAS_TEST_CALLS\"\n"), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("AIV_ALIAS_TEST_CALLS", calls)
	t.Setenv("DOCKER_BIN", docker)
	bp := &Blueprint{}
	for _, c := range components.All() {
		bp.Components = append(bp.Components, Component{Name: c.LegacyName, Kind: "container", ContainerName: c.DockerName})
	}
	// A custom blueprint's exact component name wins over a builtin alias.
	bp.Components = append(bp.Components, Component{Name: "backend", Kind: "container", ContainerName: "custom-backend"})
	app := &App{BP: bp, Ctx: context.Background(), Docker: NewDocker(NewLogger(false)), Vars: map[string]string{}}
	for _, c := range components.All() {
		for _, selector := range append([]string{c.ID, c.LegacyName}, c.Aliases...) {
			if err := app.Logs(selector, 3, false); err != nil {
				t.Fatalf("logs %s: %v", selector, err)
			}
			data, err := os.ReadFile(calls)
			if err != nil {
				t.Fatal(err)
			}
			lines := strings.Split(strings.TrimSpace(string(data)), "\n")
			want := "logs --tail 3 " + c.DockerName
			if selector == "backend" {
				want = "logs --tail 3 custom-backend"
			}
			if got := lines[len(lines)-1]; got != want {
				t.Errorf("logs %s = %q, want %q", selector, got, want)
			}
		}
	}
}

func TestSecretVariablesRemainLiteralThroughResolution(t *testing.T) {
	t.Setenv("TOKEN", "must-not-use-host-token")
	t.Setenv("OTHER", "must-not-use-host-other")
	secret := "literal-$TOKEN-${OTHER}"
	app, err := datastoreApp(t, "aivision.yaml", map[string]string{
		"SECRET_KEY": secret, "SIGNOZ_USER_ROOT_PASSWORD": secret,
	})
	if err != nil {
		t.Fatal(err)
	}
	backend := app.BP.Component("be")
	env := app.ResolveEnv(*backend, app.ComponentVars(*backend))
	for _, key := range []string{"SECRET_KEY", "SIGNOZ_USER_ROOT_PASSWORD"} {
		if app.Vars[key] != secret || env[key] != secret {
			t.Errorf("%s changed during variable resolution: vars=%q env=%q", key, app.Vars[key], env[key])
		}
	}
}

func TestCustomBlueprintSecretNamedPathsRetainTemplateResolution(t *testing.T) {
	bp := &Blueprint{}
	bp.Config.Workdir = t.TempDir()
	bp.Config.Vars = map[string]string{
		"SSH_KEY_PATH":   "${WORKDIR}/key.pem",
		"API_TOKEN_FILE": "${WORKDIR}/token",
		"SECRET_KEY":     "${SSH_KEY_PATH}",
	}
	app, err := NewApp(bp, NewLogger(false), false, false)
	if err != nil {
		t.Fatal(err)
	}
	for key, suffix := range map[string]string{
		"SSH_KEY_PATH": "key.pem", "API_TOKEN_FILE": "token", "SECRET_KEY": "key.pem",
	} {
		if got, want := app.Vars[key], filepath.Join(bp.Config.Workdir, suffix); got != want {
			t.Errorf("%s = %q, want %q", key, got, want)
		}
	}
}
