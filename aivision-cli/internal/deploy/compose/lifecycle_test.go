package compose

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/generate"
)

func lifecycleFixture(t *testing.T) (string, map[string]any) {
	t.Helper()
	root, err := canonicalPath(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	state := filepath.Join(root, "state")
	config := map[string]any{
		"name": "sample", "x-aivision": map[string]any{"backend": "seekdb", "managed": true,
			"namespace": "sample", "state_dir": state, "history_start": "2026-10-01T00:00:00Z"},
		"volumes": map[string]any{"database-data-0": map[string]any{"name": "aivforge-seekdb-data-sample",
			"labels": map[string]string{"io.aivision.product": "aivision", "io.aivision.namespace": "sample", "io.aivision.state-dir": state}}},
		"services": map[string]any{
			"database": map[string]any{"container_name": "sample-database", "restart": "unless-stopped",
				"volumes": []map[string]any{{"type": "volume", "source": "database-data-0", "target": "/var/lib/seekdb"}}},
			"database-init": map[string]any{"environment": map[string]string{"OB_HOST": "sample-database", "OB_HOST_PORT": "2881", "OB_USER": "root", "OB_TENANT_PASSWORD": "private_password"}},
			"data-backend": map[string]any{"container_name": "sample-data-backend", "restart": "unless-stopped",
				"volumes": []map[string]any{{"type": "bind", "source": filepath.Join(root, "metadata"), "target": "/var/lib/signoz"}}},
		},
	}
	return filepath.Join(root, "compose.yaml"), config
}

func encodedFixture(t *testing.T, value any) string {
	t.Helper()
	data, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

func fixtureExecutor(t *testing.T, config map[string]any, calls *[][]string) dockerRun {
	t.Helper()
	return func(args []string, capture bool) (dockerResult, error) {
		*calls = append(*calls, append([]string{}, args...))
		joined := strings.Join(args, " ")
		switch {
		case reflect.DeepEqual(args, []string{"compose", "version", "--short"}):
			return dockerResult{stdout: "v2.27.0"}, nil
		case len(args) > 0 && args[0] == "version":
			return dockerResult{stdout: "29.0.0"}, nil
		case strings.Contains(joined, "config --format json"):
			return dockerResult{stdout: encodedFixture(t, config)}, nil
		case len(args) > 1 && args[0] == "container" && args[1] == "inspect":
			return dockerResult{code: 1, stderr: "No such container: " + args[len(args)-1]}, nil
		default:
			return dockerResult{}, nil
		}
	}
}

func TestCheckNeverCreatesStateOrMetadataAndRejectsPrerequisiteFailures(t *testing.T) {
	file, config := lifecycleFixture(t)
	var calls [][]string
	if err := runLifecycle(fixtureExecutor(t, config, &calls), file, os.DevNull, "check", false); err != nil {
		t.Fatal(err)
	}
	state := config["x-aivision"].(map[string]any)["state_dir"].(string)
	for _, path := range []string{state, filepath.Join(filepath.Dir(file), "metadata")} {
		if _, err := os.Stat(path); !os.IsNotExist(err) {
			t.Fatalf("check created %s", path)
		}
	}
	for _, command := range calls {
		for _, arg := range command {
			if arg == "up" || arg == "down" || arg == "stop" || arg == "rm" || arg == "run" {
				t.Fatalf("check mutated Docker: %v", command)
			}
		}
	}
	for _, version := range []string{"2.26.9", "unknown"} {
		t.Run(version, func(t *testing.T) {
			calls = nil
			base := fixtureExecutor(t, config, &calls)
			run := func(args []string, capture bool) (dockerResult, error) {
				if reflect.DeepEqual(args, []string{"compose", "version", "--short"}) {
					return dockerResult{stdout: version}, nil
				}
				return base(args, capture)
			}
			if err := runLifecycle(run, file, os.DevNull, "up", false); err == nil {
				t.Fatal("unsupported Compose accepted")
			}
			if len(calls) != 0 {
				t.Fatal("configuration/Docker mutations reached after unsupported Compose")
			}
		})
	}
}

func TestNativeLifecycleReplaysJobsAndIsolatesOnlyManagedSeekdb(t *testing.T) {
	for _, backend := range []string{"oceanbase", "seekdb"} {
		for _, managed := range []bool{true, false} {
			t.Run(backend+map[bool]string{true: "/managed", false: "/external"}[managed], func(t *testing.T) {
				file, config := lifecycleFixture(t)
				deployment := config["x-aivision"].(map[string]any)
				deployment["backend"], deployment["managed"] = backend, managed
				var calls [][]string
				if err := runLifecycle(fixtureExecutor(t, config, &calls), file, os.DevNull, "up", false); err != nil {
					t.Fatal(err)
				}
				var mutations []string
				for _, command := range calls {
					joined := strings.Join(command, " ")
					if strings.Contains(joined, " stop") || strings.Contains(joined, " rm --force") || strings.Contains(joined, " run --rm") || strings.Contains(joined, " up --detach") {
						mutations = append(mutations, joined)
					}
				}
				wantCount := 3
				if managed && backend == "seekdb" {
					wantCount = 4
				}
				if len(mutations) != wantCount || !strings.HasSuffix(mutations[0], " stop") || !strings.Contains(mutations[1], "rm --force database-init data-backend-ready backend-bootstrap") || !strings.HasSuffix(mutations[len(mutations)-1], "up --detach --wait --wait-timeout 1200") {
					t.Fatalf("wrong lifecycle sequence: %v", mutations)
				}
				if wantCount == 4 && !strings.Contains(mutations[2], "--profile bootstrap run --rm --no-deps database-initialize") {
					t.Fatal("seekdb initialization escaped isolated profile")
				}
			})
		}
	}
}

func TestFailedStopRetainsPendingIDsAndBlocksInitializationAndPurgeAcrossRetries(t *testing.T) {
	for _, action := range []string{"up", "down"} {
		for _, failure := range []map[string]any{{"ExitCode": 42}, {"OOMKilled": true}, {"Running": true}, {"ExitCode": 137}} {
			t.Run(action+encodedFixture(t, failure), func(t *testing.T) {
				file, raw := lifecycleFixture(t)
				config, current, err := parseComposeConfig([]byte(encodedFixture(t, raw)))
				if err != nil {
					t.Fatal(err)
				}
				if err := os.MkdirAll(current.StateDir, 0700); err != nil {
					t.Fatal(err)
				}
				container := map[string]any{"Id": "a1", "Config": map[string]any{"Labels": map[string]string{"com.docker.compose.project": "sample", "com.docker.compose.service": "database"}}, "Mounts": []any{}, "State": map[string]any{"Running": true, "Restarting": false, "Dead": false, "OOMKilled": false, "ExitCode": 0, "Status": "running"}}
				stopped := map[string]any{"Running": false, "Restarting": false, "Dead": false, "OOMKilled": false, "ExitCode": 0, "Status": "exited"}
				for key, value := range failure {
					stopped[key] = value
				}
				var mutations []string
				inspections := 0
				run := func(args []string, capture bool) (dockerResult, error) {
					joined := strings.Join(args, " ")
					if strings.Contains(joined, "ps --all --quiet") {
						return dockerResult{stdout: "a1\n"}, nil
					}
					if len(args) > 1 && args[0] == "container" && args[1] == "inspect" {
						inspections++
						if inspections > 1 {
							container["State"] = stopped
						}
						return dockerResult{stdout: encodedFixture(t, []any{container})}, nil
					}
					mutations = append(mutations, joined)
					return dockerResult{}, nil
				}
				controller := lifecycleController{run: run, config: config, identity: current, command: []string{"compose"}}
				for i := 0; i < 2; i++ {
					if err := controller.stopServices(); err == nil {
						t.Fatal("failed shutdown accepted")
					}
				}
				pending, err := os.ReadFile(filepath.Join(current.StateDir, ".compose-stop-pending.json"))
				if err != nil {
					t.Fatal(err)
				}
				if string(pending) == "" || !strings.Contains(string(pending), "a1") {
					t.Fatal("exact failed ID was not retained")
				}
				if len(mutations) != 2 || mutations[0] != "compose stop" || mutations[1] != "compose stop" {
					t.Fatalf("failed stop performed destructive operation: %v", mutations)
				}
				_ = file
			})
		}
	}
}

func TestSidecarAcceptedFailurePersistsRetryButIdentityRejectionPreservesRecords(t *testing.T) {
	file, config := lifecycleFixture(t)
	state := config["x-aivision"].(map[string]any)["state_dir"].(string)
	vars := map[string]string{"SECRET_KEY": "literal-$TOKEN-${OTHER}", "OB_BACKEND": "seekdb"}
	sidecar := configuration{Schema: 1, Namespace: "sample", StateDir: state, Vars: vars}
	files := []generate.File{{Path: "compose.yaml", Content: []byte("private fixture")}, {Path: ".cli-vars.json", Content: []byte(encodedFixture(t, sidecar))}}
	bundle := filepath.Join(state, "compose", "bundles", digest(files))
	if err := os.MkdirAll(bundle, 0700); err != nil {
		t.Fatal(err)
	}
	if err := writeFiles(bundle, files); err != nil {
		t.Fatal(err)
	}
	file = filepath.Join(bundle, "compose.yaml")
	var calls [][]string
	base := fixtureExecutor(t, config, &calls)
	run := func(args []string, capture bool) (dockerResult, error) {
		if strings.Contains(strings.Join(args, " "), " up --detach") {
			return dockerResult{code: 42}, nil
		}
		if strings.HasSuffix(strings.Join(args, " "), " stop") {
			if _, err := os.Stat(filepath.Join(state, "vars.json")); err != nil {
				t.Fatal("first Docker mutation preceded retry config")
			}
		}
		return base(args, capture)
	}
	if err := runLifecycle(run, file, os.DevNull, "up", false); err == nil {
		t.Fatal("failed up accepted")
	}
	pointer := filepath.Join(state, pointerName)
	before, err := os.ReadFile(pointer)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(before), `"pending"`) {
		t.Fatal("accepted failure did not save pending project")
	}
	for _, name := range []string{"vars.json", pointerName, ".compose-state.json"} {
		info, err := os.Stat(filepath.Join(state, name))
		if err != nil || info.Mode().Perm() != 0600 {
			t.Fatalf("saved secret/state permissions: %s %v", name, err)
		}
	}
	config["x-aivision"].(map[string]any)["history_start"] = "2026-10-02T00:00:00Z"
	if err := runLifecycle(base, file, os.DevNull, "up", false); err == nil {
		t.Fatal("history change accepted")
	}
	after, _ := os.ReadFile(pointer)
	if string(after) != string(before) {
		t.Fatal("rejected update replaced pointer")
	}
}

func TestLifecycleFailureCannotReachInitializationOrPurge(t *testing.T) {
	for _, action := range []string{"up", "down"} {
		t.Run(action, func(t *testing.T) {
			file, raw := lifecycleFixture(t)
			container := map[string]any{"Id": "a1", "Config": map[string]any{"Labels": map[string]string{"com.docker.compose.project": "sample", "com.docker.compose.service": "database"}},
				"Mounts": []map[string]string{{"Destination": "/var/lib/seekdb", "Name": "aivforge-seekdb-data-sample"}},
				"State":  map[string]any{"Running": true, "Restarting": false, "Dead": false, "OOMKilled": false, "ExitCode": 0, "Status": "running"}}
			var calls [][]string
			base := fixtureExecutor(t, raw, &calls)
			var mutations []string
			run := func(args []string, capture bool) (dockerResult, error) {
				joined := strings.Join(args, " ")
				if args[0] == "ps" || strings.Contains(joined, " ps --all --quiet") {
					return dockerResult{stdout: "a1\n"}, nil
				}
				if len(args) > 1 && args[0] == "container" && args[1] == "inspect" && args[len(args)-1] != "sample-data-backend" {
					return dockerResult{stdout: encodedFixture(t, []any{container})}, nil
				}
				for _, arg := range args {
					if arg == "stop" || arg == "rm" || arg == "run" || arg == "up" || arg == "down" {
						mutations = append(mutations, arg)
					}
				}
				if strings.HasSuffix(joined, " stop") {
					container["State"] = map[string]any{"Running": false, "Restarting": false, "Dead": false, "OOMKilled": false, "ExitCode": 42, "Status": "exited"}
				}
				return base(args, capture)
			}
			for attempt := 0; attempt < 2; attempt++ {
				if err := runLifecycle(run, file, os.DevNull, action, action == "down"); err == nil {
					t.Fatal("failed shutdown accepted")
				}
			}
			if !reflect.DeepEqual(mutations, []string{"stop", "stop"}) {
				t.Fatalf("failure reached destructive or bootstrap operation: %v", mutations)
			}
			state := raw["x-aivision"].(map[string]any)["state_dir"].(string)
			if _, err := os.Stat(filepath.Join(state, ".compose-stop-pending.json")); err != nil {
				t.Fatal("failed stop gate lost")
			}
		})
	}
}

func TestCheckHonorsMutationLockAndNeverPersistsRecords(t *testing.T) {
	file, raw := lifecycleFixture(t)
	state := raw["x-aivision"].(map[string]any)["state_dir"].(string)
	unlock, err := lockDeployment(state, true)
	if err != nil {
		t.Fatal(err)
	}
	defer unlock()
	var calls [][]string
	if err := runLifecycle(fixtureExecutor(t, raw, &calls), file, os.DevNull, "check", false); err == nil || !strings.Contains(err.Error(), "another operation") {
		t.Fatalf("check bypassed mutation lock: %v", err)
	}
	for _, name := range []string{"vars.json", pointerName, ".compose-state.json"} {
		if _, err := os.Stat(filepath.Join(state, name)); !os.IsNotExist(err) {
			t.Fatalf("check persisted %s", name)
		}
	}
}

func TestNativeEnvironmentCannotActivateBootstrapOrRetargetProject(t *testing.T) {
	t.Setenv("COMPOSE_PROFILES", "bootstrap")
	t.Setenv("COMPOSE_FILE", "foreign.yaml")
	t.Setenv("COMPOSE_PROJECT_NAME", "foreign")
	t.Setenv("DOCKER_HOST", "unix:///intentional.sock")
	t.Setenv("DOCKER_CONTEXT", "intentional")
	values := map[string]string{}
	for _, entry := range lifecycleEnvironment() {
		parts := strings.SplitN(entry, "=", 2)
		values[parts[0]] = parts[1]
		if strings.HasPrefix(parts[0], "COMPOSE_") {
			t.Fatal("ambient Compose selector retained")
		}
	}
	if values["DOCKER_HOST"] != "unix:///intentional.sock" || values["DOCKER_CONTEXT"] != "intentional" {
		t.Fatal("intentional Docker daemon selection lost")
	}
}

func TestDotenvCannotEnableIsolatedBootstrapProfileDuringServe(t *testing.T) {
	// Native Compose accepts both dotenv separators, with optional export,
	// whitespace and a UTF-8 BOM. Each must be rejected before bootstrap/serve.
	for _, assignment := range []string{
		"COMPOSE_PROFILES=bootstrap", "COMPOSE_PROFILES = bootstrap",
		"export COMPOSE_PROFILES=bootstrap", " export\tCOMPOSE_PROFILES = bootstrap",
		"export\rCOMPOSE_PROFILES=bootstrap", "export\fCOMPOSE_PROFILES=bootstrap",
		"export \r\tCOMPOSE_PROFILES=bootstrap", "export\fCOMPOSE_FILE=foreign.yaml",
		"export\rCOMPOSE_PROJECT_NAME:foreign",
		"COMPOSE_PROFILES: bootstrap", "COMPOSE_PROFILES:bootstrap",
		"export COMPOSE_PROFILES: bootstrap", "export\tCOMPOSE_PROFILES: bootstrap",
		"COMPOSE_PROFILES : bootstrap", "\ufeffCOMPOSE_PROFILES: bootstrap",
		"\tCOMPOSE_FILE : foreign.yaml", "export COMPOSE_PROJECT_NAME:foreign",
		"\u00a0COMPOSE_PROFILES=bootstrap", "\u2003COMPOSE_PROFILES=bootstrap",
		"COMPOSE_PROFILES\u00a0=bootstrap", "COMPOSE_PROFILES\u2003:bootstrap",
	} {
		t.Run(assignment, func(t *testing.T) {
			file, raw := lifecycleFixture(t)
			env := filepath.Join(filepath.Dir(file), "private.env")
			if err := os.WriteFile(env, []byte(assignment+"\n"), 0600); err != nil {
				t.Fatal(err)
			}
			var calls [][]string
			if err := runLifecycle(fixtureExecutor(t, raw, &calls), file, env, "up", false); err == nil || !strings.Contains(err.Error(), "reserved COMPOSE_*") {
				t.Fatalf("dotenv selector was not rejected before lifecycle mutation: %v", err)
			}
			for _, command := range calls {
				for _, arg := range command {
					if arg == "stop" || arg == "up" || arg == "run" {
						t.Fatal("reserved Compose dotenv keys reached lifecycle mutation")
					}
				}
			}
		})
	}
}

func TestDotenvReservedLookingTextInsideValuesDoesNotBecomeASelector(t *testing.T) {
	for _, contents := range []string{
		"SECRET_KEY=literal-COMPOSE_PROFILES:bootstrap\n",
		"# COMPOSE_PROFILES: bootstrap\nSECRET_KEY=private\n",
		"SECRET_KEY='first line\nCOMPOSE_PROFILES: bootstrap\nlast line'\n",
		"SECRET_KEY=\"first line\nCOMPOSE_PROFILES=bootstrap\nlast line\"\n",
		"SECRET_KEY='escaped \\' quote\nCOMPOSE_FILE: literal text\nlast line'\n",
		"export\rSECRET_KEY='first line\nCOMPOSE_PROFILES=literal text\nlast line'\n",
		"export\fSECRET_KEY=\"first line\nCOMPOSE_FILE: literal text\nlast line\"\n",
		"exportCOMPOSE_PROFILES=ordinary-key\n",
	} {
		t.Run(contents, func(t *testing.T) {
			file, raw := lifecycleFixture(t)
			env := filepath.Join(filepath.Dir(file), "private.env")
			if err := os.WriteFile(env, []byte(contents), 0600); err != nil {
				t.Fatal(err)
			}
			var calls [][]string
			if err := runLifecycle(fixtureExecutor(t, raw, &calls), file, env, "check", false); err != nil {
				t.Fatalf("literal value was mistaken for a Compose selector: %v", err)
			}
		})
	}
}

func TestSuccessfulStopClearsPendingAndAllowsExplicitRemovalAcknowledgement(t *testing.T) {
	_, raw := lifecycleFixture(t)
	config, current, err := parseComposeConfig([]byte(encodedFixture(t, raw)))
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(current.StateDir, 0700); err != nil {
		t.Fatal(err)
	}
	pending := filepath.Join(current.StateDir, ".compose-stop-pending.json")
	if err := writeLifecycleJSON(pending, map[string]string{"a1": "database", "removed-id": "database"}); err != nil {
		t.Fatal(err)
	}
	controller := lifecycleController{config: config, identity: current, command: []string{"compose"}, run: func(args []string, capture bool) (dockerResult, error) {
		if strings.Contains(strings.Join(args, " "), "ps --all --quiet") {
			return dockerResult{stdout: "a1\n"}, nil
		}
		if args[0] == "container" {
			return dockerResult{stdout: `[{"Id":"a1","Config":{"Labels":{"com.docker.compose.project":"sample","com.docker.compose.service":"database"}},"State":{"Running":false,"Restarting":false,"Dead":false,"OOMKilled":false,"ExitCode":0,"Status":"exited"}}]`}, nil
		}
		return dockerResult{}, nil
	}}
	if err := controller.stopServices(); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(pending); !os.IsNotExist(err) {
		t.Fatal("successful/explicitly removed stop IDs were not cleared")
	}
}

func TestDownPurgeRetainsMetadataIdentityAndSavedRecords(t *testing.T) {
	file, raw := lifecycleFixture(t)
	var calls [][]string
	run := fixtureExecutor(t, raw, &calls)
	if err := runLifecycle(run, file, os.DevNull, "up", false); err != nil {
		t.Fatal(err)
	}
	state := raw["x-aivision"].(map[string]any)["state_dir"].(string)
	if err := writeLifecycleJSON(filepath.Join(state, "vars.json"), map[string]string{"SECRET_KEY": "private"}); err != nil {
		t.Fatal(err)
	}
	if err := writeLifecycleJSON(filepath.Join(state, pointerName), savedProject{Schema: 1, Bundle: "/original/bundle", Status: "ready"}); err != nil {
		t.Fatal(err)
	}
	before := map[string]string{}
	for _, name := range []string{"vars.json", pointerName, ".compose-state.json"} {
		data, err := os.ReadFile(filepath.Join(state, name))
		if err != nil {
			t.Fatal(err)
		}
		before[name] = string(data)
	}
	if err := runLifecycle(run, file, os.DevNull, "down", true); err != nil {
		t.Fatal(err)
	}
	for name, want := range before {
		data, err := os.ReadFile(filepath.Join(state, name))
		if err != nil || string(data) != want {
			t.Fatalf("purge changed retained record %s", name)
		}
	}
	if !strings.HasSuffix(strings.Join(calls[len(calls)-1], " "), "--profile bootstrap down --volumes") {
		t.Fatalf("named-volume purge not requested: %v", calls[len(calls)-1])
	}
}
