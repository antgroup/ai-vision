package cli

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/compose"
	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/generate"
	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/runtime"
	"gopkg.in/yaml.v3"
)

// Explicit Compose must use the guarded lifecycle; no daemon is used here.
func TestComposeCastUsesGuardedLifecycle(t *testing.T) {
	content, err := os.ReadFile("../../blueprints/aivision.yaml")
	if err != nil {
		t.Fatal(err)
	}
	previous := defaultBlueprintYAML
	defaultBlueprintYAML = string(content)
	defer func() { defaultBlueprintYAML = previous }()
	for _, target := range []string{"compose"} {
		t.Run(target, func(t *testing.T) {
			dir, capture := composeCLIExecutables(t)
			args := []string{"--workdir", dir, "--namespace", "compose-test", "--no-gauge", "--no-forge", "--var", "AIVISION_NATIVE_TRACE_HISTORY_START=2026-01-01T00:00:00Z", "--var", "SECRET_KEY=literal-$kept"}
			if target != "" {
				args = append(args, "--target", target)
			}
			if err := cmdCast(args); err != nil {
				t.Fatalf("product cast must use Compose lifecycle: %v", err)
			}
			data, err := os.ReadFile(capture)
			if err != nil {
				t.Fatalf("Python lifecycle was not invoked: %v", err)
			}
			argv := strings.Split(strings.TrimSpace(string(data)), "\n")
			if !strings.Contains(string(data), "\nup\n--detach\n--wait\n") || argv[0] != "compose" {
				t.Fatalf("wrong lifecycle arguments: %q", argv)
			}
			bundle := filepath.Dir(argv[2])
			var sidecar struct {
				Namespace string            `json:"namespace"`
				Vars      map[string]string `json:"vars"`
			}
			data, err = os.ReadFile(filepath.Join(bundle, ".cli-vars.json"))
			if err != nil || json.Unmarshal(data, &sidecar) != nil || sidecar.Namespace != "compose-test" || sidecar.Vars["SECRET_KEY"] != "literal-$kept" || sidecar.Vars["FE_IMAGE"] == "" || sidecar.Vars["OB_BACKEND"] != "oceanbase" {
				t.Fatalf("private effective configuration was not bundled: %v", err)
			}
			for _, marker := range []string{"meta.json", "datastore-backend"} {
				if _, err := os.Stat(filepath.Join(dir, "compose-test", "state", marker)); !os.IsNotExist(err) {
					t.Fatalf("Go wrote lifecycle-owned state %s", marker)
				}
			}
			for _, marker := range []string{"vars.json", ".compose-cli.json"} {
				if _, err := os.Stat(filepath.Join(dir, "compose-test", "state", marker)); err != nil {
					t.Fatalf("Go lifecycle did not save accepted configuration: %v", err)
				}
			}
			if _, err := os.Stat(filepath.Join(bundle, "compose.py")); !os.IsNotExist(err) {
				t.Fatal("private bundle contains a host Python control script")
			}
		})
	}
}

func TestDefaultDockerCastUsesGoRuntimeWithoutPythonOrCompose(t *testing.T) {
	for _, target := range []string{"", "docker"} {
		t.Run(target, func(t *testing.T) {
			dir, capture := composeCLIExecutables(t)
			t.Setenv("AIV_PYTHON_BIN", "/missing/python-must-not-run")
			t.Setenv("AIV_COMPOSE_TEST_EXPECT_DIRECT", "1")
			args := []string{"--file", "../../blueprints/aivision.yaml", "--workdir", dir, "--namespace", "docker-default", "--no-gauge", "--no-forge"}
			if target != "" {
				args = append(args, "--target", target)
			}
			err := cmdCast(args)
			if err == nil || !strings.Contains(err.Error(), "exit status 91") {
				t.Fatalf("expected original Go runtime to reach fixture Docker: %v", err)
			}
			if _, err := os.Stat(filepath.Join(dir, "direct-docker")); err != nil {
				t.Fatal("default did not execute original Docker runtime")
			}
			if _, err := os.Stat(capture); !os.IsNotExist(err) {
				t.Fatal("default invoked Compose/Python")
			}
		})
	}
}

func TestSavedComposeCommandsUseExactBundleAndIgnoreAmbientConfiguration(t *testing.T) {
	dir, capture := composeCLIExecutables(t)
	g := &globalOpts{file: "../../blueprints/aivision.yaml", workdir: dir, namespace: "saved", vars: varFlags{"AIVISION_NATIVE_TRACE_HISTORY_START=2026-01-01T00:00:00Z"}}
	app, err := buildApp(g)
	if err != nil {
		t.Fatal(err)
	}
	bundle, err := compose.New(app).Publish()
	if err != nil {
		t.Fatal(err)
	}
	installComposePointer(t, app, bundle)
	t.Setenv("AIV_VAR_OB_BACKEND", "invalid-ambient-backend")
	base := []string{"--target", "compose", "--file", g.file, "--workdir", dir, "--namespace", "saved", "--vars-file", "/missing/ignored-vars.yaml", "--var", "OB_BACKEND=invalid-flag", "--reset-vars"}
	t.Setenv("AIV_PYTHON_BIN", "/missing/python-status-and-logs-must-not-run")
	t.Setenv("COMPOSE_FILE", "/foreign/compose.yaml")
	t.Setenv("COMPOSE_PROFILES", "bootstrap")
	t.Setenv("DOCKER_HOST", "unix:///selected.sock")
	t.Setenv("DOCKER_CONTEXT", "selected")
	if err := cmdSimple("status", base, func(*runtime.App) error { t.Fatal("direct runtime called"); return nil }); err != nil {
		t.Fatal(err)
	}
	assertNativeComposeInvocation(t, capture, bundle, "ps\n--all")
	if err := cmdLogs(append(base, "--follow", "-n", "17", "be")); err != nil {
		t.Fatal(err)
	}
	assertNativeComposeInvocation(t, capture, bundle, "logs\n--tail\n17\n--follow\nbackend")
	environment, err := os.ReadFile(capture + ".env")
	if err != nil || strings.Contains(string(environment), "COMPOSE_FILE=") || strings.Contains(string(environment), "COMPOSE_PROFILES=") || !strings.Contains(string(environment), "DOCKER_HOST=unix:///selected.sock") || !strings.Contains(string(environment), "DOCKER_CONTEXT=selected") {
		t.Fatal("native Compose inherited ambient project settings or lost selected Docker daemon")
	}
	if err := cmdDestroy(append(base, "--purge", "-y")); err != nil {
		t.Fatal(err)
	}
	assertNativeComposeInvocation(t, capture, bundle, "--profile\nbootstrap\ndown\n--volumes")
	if _, err := os.Stat(bundle); err != nil {
		t.Fatal("purge removed the retained bundle")
	}
}

func TestDeploymentTargetsRejectTheOtherOwnerForEveryCommand(t *testing.T) {
	for _, target := range []string{"docker", "compose"} {
		t.Run(target, func(t *testing.T) {
			dir, _ := composeCLIExecutables(t)
			app, err := buildApp(&globalOpts{file: "../../blueprints/aivision.yaml", workdir: dir, namespace: "other-owner"})
			if err != nil {
				t.Fatal(err)
			}
			if err := os.MkdirAll(app.State.StateDir, 0700); err != nil {
				t.Fatal(err)
			}
			marker := ".compose-state.json"
			if target == "compose" {
				marker = "datastore-backend"
			}
			if err := os.WriteFile(filepath.Join(app.State.StateDir, marker), []byte("{}"), 0600); err != nil {
				t.Fatal(err)
			}
			for _, command := range []string{"gauge", "forge", "cast", "redeploy", "status", "logs", "destroy"} {
				if _, err := compose.Select(app, target == "compose", command); err == nil {
					t.Errorf("%s %s inferred another target's lifecycle", target, command)
				}
			}
		})
	}
}

func TestProductGaugeDoesNotCreateDeploymentState(t *testing.T) {
	dir, capture := composeCLIExecutables(t)
	args := []string{"--target", "compose", "--file", "../../blueprints/aivision.yaml", "--workdir", dir, "--namespace", "gauge", "--var", "AIVISION_NATIVE_TRACE_HISTORY_START=2026-01-01T00:00:00Z"}
	if err := cmdSimple("gauge", args, func(*runtime.App) error { t.Fatal("legacy gauge called"); return nil }); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(capture)
	if err != nil || strings.Contains(string(data), "compose.py") {
		t.Fatalf("wrong gauge dispatch: %v", err)
	}
	if _, err := os.Stat(filepath.Join(dir, "gauge")); !os.IsNotExist(err) {
		t.Fatal("gauge reserved deployment state")
	}
}

func TestSourceAndCustomBlueprintsRetainRuntimeWithoutBypassingComposeOwnership(t *testing.T) {
	for _, blueprint := range []string{"../../blueprints/aivision-source.yaml", "custom"} {
		t.Run(blueprint, func(t *testing.T) {
			dir, _ := composeCLIExecutables(t)
			file := blueprint
			if file == "custom" {
				file = filepath.Join(dir, "custom.yaml")
				if err := os.WriteFile(file, []byte("metadata:\n  name: custom\n  namespace: custom\nconfig:\n  workdir: "+dir+"\n  sourceRoot: "+dir+"\ncomponents:\n  - name: worker\n    kind: process\n    run: sleep 100\n"), 0600); err != nil {
					t.Fatal(err)
				}
			}
			args := []string{"--file", file, "--workdir", dir, "--namespace", "fallback"}
			called := false
			if err := cmdSimple("status", args, func(*runtime.App) error { called = true; return nil }); err != nil || !called {
				t.Fatalf("development runtime not preserved: %v", err)
			}
			if err := cmdSimple("status", append(args, "--target", "compose"), func(*runtime.App) error { t.Fatal("explicit Compose used fallback"); return nil }); err == nil {
				t.Fatal("unsupported Compose blueprint accepted")
			}
			state := runtime.NewState(dir, "fallback")
			if err := os.MkdirAll(state.StateDir, 0700); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(state.StateDir, ".compose-state.json"), []byte("{}"), 0600); err != nil {
				t.Fatal(err)
			}
			if err := cmdDestroy(append(args, "--purge", "-y")); err == nil {
				t.Fatal("custom destroy bypassed Compose ownership")
			}
		})
	}
}

func TestSavedTemplatesReexpandHighLevelChangesAndKeepCredentialsLiteral(t *testing.T) {
	dir, _ := composeCLIExecutables(t)
	g := &globalOpts{file: "../../blueprints/aivision.yaml", workdir: dir, namespace: "templates", vars: varFlags{"AIVISION_NATIVE_TRACE_HISTORY_START=2026-01-01T00:00:00Z", "SECRET_KEY=literal-${PUBLIC_HOST}-$kept"}}
	app, err := buildApp(g)
	if err != nil {
		t.Fatal(err)
	}
	bundle, err := compose.New(app).Publish()
	if err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(filepath.Join(bundle, ".cli-vars.json"))
	if err != nil {
		t.Fatal(err)
	}
	var sidecar struct {
		Vars map[string]string `json:"vars"`
	}
	if err := json.Unmarshal(data, &sidecar); err != nil {
		t.Fatal(err)
	}
	if err := app.State.SaveVars(sidecar.Vars); err != nil {
		t.Fatal(err)
	}
	g.vars = varFlags{"OB_TENANT_PASSWORD=changed-password", "PUBLIC_HOST=vision.example", "BE_PORT=8123"}
	updated, err := buildApp(g)
	if err != nil {
		t.Fatalf("saved template configuration cannot be reused: %v", err)
	}
	if !strings.Contains(updated.Vars["OCEANBASE_DSN"], ":changed-password@") || updated.Vars["PUBLIC_HOST"] != "vision.example" || updated.Vars["BE_PORT"] != "8123" || updated.Vars["SECRET_KEY"] != "literal-${PUBLIC_HOST}-$kept" {
		t.Fatal("derived configuration or literal credentials changed unexpectedly")
	}
	diagnosis := updated.BP.Component("agentlens")
	env := updated.ResolveEnv(*diagnosis, updated.ComponentVars(*diagnosis))
	if !strings.Contains(env["AGENTLENS_OB_URL"], ":changed-password@") {
		t.Fatal("diagnosis URL did not re-expand with changed password")
	}
	backend := updated.BP.Component("be")
	env = updated.ResolveEnv(*backend, updated.ComponentVars(*backend))
	if env["SITE_URL"] != "http://vision.example:5174" {
		t.Fatalf("public endpoint did not re-expand: %q", env["SITE_URL"])
	}
}

func TestComposeLogsPreservesZeroTailAndRejectsNegativeBeforeLifecycle(t *testing.T) {
	dir, capture := composeCLIExecutables(t)
	g := &globalOpts{file: "../../blueprints/aivision.yaml", workdir: dir, namespace: "log-tail", vars: varFlags{"AIVISION_NATIVE_TRACE_HISTORY_START=2026-01-01T00:00:00Z"}}
	app, err := buildApp(g)
	if err != nil {
		t.Fatal(err)
	}
	bundle, err := compose.New(app).Publish()
	if err != nil {
		t.Fatal(err)
	}
	installComposePointer(t, app, bundle)
	base := []string{"--target", "compose", "--file", g.file, "--workdir", dir, "--namespace", "log-tail"}
	if err := cmdLogs(append(base, "-n", "0", "backend")); err != nil {
		t.Fatal(err)
	}
	assertNativeComposeInvocation(t, capture, bundle, "logs\n--tail\n0\nbackend")
	if err := os.Remove(capture); err != nil {
		t.Fatal(err)
	}
	if err := cmdLogs(append(base, "-n", "-1", "backend")); err == nil {
		t.Fatal("negative log tail accepted")
	}
	if _, err := os.Stat(capture); !os.IsNotExist(err) {
		t.Fatal("invalid log tail invoked lifecycle")
	}
}

func TestComposeDestructiveResetFlagsFailBeforeLifecycle(t *testing.T) {
	for _, command := range []string{"redeploy", "destroy"} {
		t.Run(command, func(t *testing.T) {
			dir, capture := composeCLIExecutables(t)
			args := []string{"--target", "compose", "--file", "../../blueprints/aivision.yaml", "--workdir", dir, "--namespace", "no-reset"}
			var err error
			if command == "redeploy" {
				err = cmdRedeploy(append(args, "--purge"))
			} else {
				err = cmdDestroy(append(args, "--purge-repos", "-y"))
			}
			if err == nil {
				t.Fatal("unsupported destructive reset accepted")
			}
			if _, err := os.Stat(capture); !os.IsNotExist(err) {
				t.Fatal("rejected reset invoked lifecycle")
			}
		})
	}
}

func installComposePointer(t *testing.T, app *runtime.App, bundle string) {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(bundle, ".cli-vars.json"))
	if err != nil {
		t.Fatal(err)
	}
	var sidecar struct {
		Vars map[string]string `json:"vars"`
	}
	if err := json.Unmarshal(data, &sidecar); err != nil {
		t.Fatal(err)
	}
	if err := app.State.SaveVars(sidecar.Vars); err != nil {
		t.Fatal(err)
	}
	data, err = json.Marshal(map[string]any{"schema": 1, "bundle": bundle, "status": "pending"})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(app.State.StateDir, ".compose-cli.json"), data, 0600); err != nil {
		t.Fatal(err)
	}
}

func TestProductCastRefusesLegacyMigrationBeforeDocker(t *testing.T) {
	dir, capture := composeCLIExecutables(t)
	state := filepath.Join(dir, "legacy", "state")
	if err := os.MkdirAll(state, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(state, "datastore-backend"), []byte("oceanbase\n"), 0600); err != nil {
		t.Fatal(err)
	}
	err := cmdCast([]string{"--target", "compose", "--file", "../../blueprints/aivision.yaml", "--workdir", dir, "--namespace", "legacy", "--no-gauge", "--no-forge"})
	if err == nil || !strings.Contains(err.Error(), "legacy") {
		t.Fatalf("legacy installation must not be implicitly migrated: %v", err)
	}
	if _, err := os.Stat(capture); !os.IsNotExist(err) {
		t.Fatal("legacy migration invoked Compose")
	}
	if _, err := os.Stat(filepath.Join(dir, "direct-docker")); !os.IsNotExist(err) {
		t.Fatal("legacy migration invoked direct Docker")
	}
}

func composeCLIExecutables(t *testing.T) (string, string) {
	t.Helper()
	dir := t.TempDir()
	capture := filepath.Join(dir, "python-args")
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	docker := filepath.Join(dir, "docker")
	if err := os.WriteFile(docker, []byte("#!/bin/sh\nexec \"$AIV_COMPOSE_TEST_EXECUTABLE\" -test.run '^TestComposeDockerProcess$' -- \"$@\"\n"), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("AIV_PYTHON_BIN", "/missing/python-must-not-run")
	t.Setenv("DOCKER_BIN", docker)
	t.Setenv("AIV_COMPOSE_TEST_ARGS", capture)
	t.Setenv("AIV_COMPOSE_TEST_DOCKER", filepath.Join(dir, "direct-docker"))
	t.Setenv("AIV_COMPOSE_TEST_EXECUTABLE", executable)
	t.Setenv("AIV_COMPOSE_TEST_PROCESS", "1")
	return dir, capture
}

// The only external dependency is a Docker daemon. This fixture consumes the
// actual generated YAML and implements its config/absence/start command boundary.
func TestComposeDockerProcess(t *testing.T) {
	if os.Getenv("AIV_COMPOSE_TEST_PROCESS") != "1" {
		return
	}
	var args []string
	for i, arg := range os.Args {
		if arg == "--" {
			args = os.Args[i+1:]
			break
		}
	}
	if len(args) == 0 {
		os.Exit(92)
	}
	// Direct Docker runtime is intentionally stopped at its first inspection.
	if os.Getenv("AIV_COMPOSE_TEST_EXPECT_DIRECT") == "1" {
		_ = os.WriteFile(os.Getenv("AIV_COMPOSE_TEST_DOCKER"), []byte("direct"), 0600)
		os.Exit(91)
	}
	capture := os.Getenv("AIV_COMPOSE_TEST_ARGS")
	if args[0] == "compose" {
		_ = os.WriteFile(capture, []byte(strings.Join(args, "\n")+"\n"), 0600)
		_ = os.WriteFile(capture+".env", []byte(strings.Join(os.Environ(), "\n")), 0600)
		if len(args) >= 2 && args[1] == "version" {
			fmt.Print("2.30.0\n")
			os.Exit(0)
		}
		for _, arg := range args {
			if arg == "config" {
				for i, value := range args {
					if value == "--file" && i+1 < len(args) {
						data, err := os.ReadFile(args[i+1])
						if err != nil {
							os.Exit(94)
						}
						var config map[string]any
						if yaml.Unmarshal(data, &config) != nil {
							os.Exit(95)
						}
						config = composeFixtureValues(config).(map[string]any)
						services := config["services"].(map[string]any)
						for _, raw := range services {
							service := raw.(map[string]any)
							if mounts, ok := service["volumes"].([]any); ok {
								for j, rawMount := range mounts {
									if mount, ok := rawMount.(string); ok {
										parts := strings.Split(mount, ":")
										kind, source := "volume", parts[0]
										if strings.Contains(source, "/") {
											kind = "bind"
											if !filepath.IsAbs(source) {
												source = filepath.Join(filepath.Dir(args[i+1]), source)
											}
										}
										mounts[j] = map[string]any{"type": kind, "source": source, "target": parts[1], "read_only": len(parts) > 2 && parts[2] == "ro"}
									}
								}
							}
						}
						data, _ = json.Marshal(config)
						_, _ = os.Stdout.Write(data)
						os.Exit(0)
					}
				}
			}
		}
		os.Exit(0)
	}
	if args[0] == "version" {
		fmt.Print("29.0.0\n")
		os.Exit(0)
	}
	if args[0] == "container" {
		if len(args) > 1 && args[1] == "inspect" {
			if os.Getenv("AIV_COMPOSE_TEST_EXPECT_DIRECT") == "1" {
				_ = os.WriteFile(os.Getenv("AIV_COMPOSE_TEST_DOCKER"), []byte("direct"), 0600)
				os.Exit(91)
			}
			if len(args) > 2 {
				fmt.Fprintln(os.Stderr, "Error: No such object: "+args[2])
			}
			os.Exit(1)
		}
	}
	os.Exit(0)
}

func TestExternalComposeFileUsesNativeLifecycleWithoutBlueprintOrPython(t *testing.T) {
	dir, capture := composeCLIExecutables(t)
	app, err := buildApp(&globalOpts{file: "../../blueprints/aivision.yaml", workdir: dir, namespace: "external-file", vars: varFlags{"AIVISION_NATIVE_TRACE_HISTORY_START=2026-01-01T00:00:00Z"}})
	if err != nil {
		t.Fatal(err)
	}
	files, _, err := generate.Render(app, generate.Compose, generate.Options{})
	if err != nil {
		t.Fatal(err)
	}
	exported := filepath.Join(dir, "exported")
	for _, file := range files {
		path := filepath.Join(exported, file.Path)
		if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, file.Content, 0600); err != nil {
			t.Fatal(err)
		}
	}
	base := []string{"--target", "compose", "--compose-file", filepath.Join(exported, "compose.yaml"), "--compose-env-file", os.DevNull}
	for _, action := range []string{"gauge", "cast", "status", "logs", "redeploy", "destroy"} {
		var err error
		switch action {
		case "gauge", "status":
			err = cmdSimple(action, base, func(*runtime.App) error { t.Fatal("external project invoked blueprint runtime"); return nil })
		case "cast":
			err = cmdCast(base)
		case "logs":
			err = cmdLogs(append(base, "be", "-n", "0"))
		case "redeploy":
			err = cmdRedeploy(base)
		case "destroy":
			err = cmdDestroy(append(base, "-y"))
		}
		if err != nil {
			t.Fatalf("external %s: %v", action, err)
		}
	}
	data, err := os.ReadFile(capture)
	if err != nil || strings.Contains(string(data), "compose.py") {
		t.Fatal("external file used a host Python controller")
	}
	for _, name := range []string{"vars.json", ".compose-cli.json"} {
		if _, err := os.Stat(filepath.Join(app.State.StateDir, name)); !os.IsNotExist(err) {
			t.Fatal("standalone project was silently adopted as a blueprint deployment")
		}
	}
}

func TestComposeFileRejectsAmbiguousSelectorsBeforeAnyEngine(t *testing.T) {
	for _, extra := range [][]string{
		{"--compose-file", "/missing/file.yaml"},
		{"--target", "compose", "--compose-env-file", "/missing/private.env"},
		{"--target", "compose", "--compose-file", "/missing/file.yaml", "--file", "/missing/blueprint.yaml"},
		{"--target", "compose", "--compose-file", "/missing/file.yaml", "--namespace", "foreign"},
		{"--target", "compose", "--compose-file", "/missing/file.yaml", "--workdir", "/foreign"},
		{"--target", "compose", "--compose-file", "/missing/file.yaml", "--var", "SECRET_KEY=wrong"},
		{"--target", "compose", "--compose-file", "/missing/file.yaml", "--vars-file", "/missing/vars"},
		{"--target", "compose", "--compose-file", "/missing/file.yaml", "--reset-vars"},
		{"--target", "compose", "--compose-file", "/missing/file.yaml", "--ob-host", "foreign"},
	} {
		_, capture := composeCLIExecutables(t)
		if err := cmdCast(extra); err == nil || strings.Contains(err.Error(), "no such file") {
			t.Fatalf("selectors reached engine instead of validation: %v", err)
		}
		if _, err := os.Stat(capture); !os.IsNotExist(err) {
			t.Fatal("invalid selectors invoked Docker")
		}
	}
}

func composeFixtureValues(value any) any {
	switch typed := value.(type) {
	case string:
		return strings.ReplaceAll(typed, "$$", "$")
	case map[string]any:
		for key, item := range typed {
			typed[key] = composeFixtureValues(item)
		}
		return typed
	case []any:
		for i, item := range typed {
			typed[i] = composeFixtureValues(item)
		}
		return typed
	default:
		return value
	}
}

func assertNativeComposeInvocation(t *testing.T, capture, bundle, suffix string) {
	t.Helper()
	data, err := os.ReadFile(capture)
	if err != nil || !strings.HasPrefix(string(data), "compose\n--file\n"+filepath.Join(bundle, "compose.yaml")+"\n") || !strings.HasSuffix(string(data), suffix+"\n") {
		t.Fatalf("wrong native Compose invocation: %v %s", err, data)
	}
}
