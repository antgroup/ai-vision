package runtime

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// A different/missing workdir removes state-marker evidence; physical Compose
// ownership must still stop every native runtime mutation before its first step.
func TestPhysicalComposeOwnershipPreventsDockerMutationsWithFreshState(t *testing.T) {
	for _, owner := range []string{"compose-container", "compose-volume"} {
		for _, action := range []string{"cast", "deploy", "destroy", "redeploy", "gauge", "forge"} {
			t.Run(owner+"/"+action, func(t *testing.T) {
				app, err := datastoreApp(t, "aivision.yaml", nil)
				if err != nil {
					t.Fatal(err)
				}
				mutation := installOwnershipDockerFixture(t, app, owner)
				switch action {
				case "cast":
					err = app.Cast(true, true)
				case "deploy":
					err = app.Deploy()
				case "destroy":
					err = app.Destroy(DestroyOpts{Purge: true})
				case "redeploy":
					err = app.Redeploy(true, true)
				case "gauge":
					err = app.Gauge()
				case "forge":
					err = app.Forge()
				}
				if err == nil || !strings.Contains(err.Error(), "managed by Compose") {
					t.Fatalf("physical ownership not enforced for fresh workdir: %v", err)
				}
				if _, err := os.Stat(mutation); !os.IsNotExist(err) {
					t.Fatal("runtime reached Docker mutations before physical ownership preflight")
				}
				if _, err := os.Stat(app.State.StateDir); !os.IsNotExist(err) {
					t.Fatal("runtime created deployment state before rejecting another lifecycle owner")
				}
			})
		}
	}
}

func TestPhysicalOwnershipInspectionFailsClosedAndAllowsNativeResources(t *testing.T) {
	for _, mode := range []string{"permission", "ambiguous", "malformed", "absent", "native"} {
		t.Run(mode, func(t *testing.T) {
			app, err := datastoreApp(t, "aivision.yaml", nil)
			if err != nil {
				t.Fatal(err)
			}
			mutation := installOwnershipDockerFixture(t, app, mode)
			err = app.checkDeploymentDriver()
			if mode == "absent" || mode == "native" {
				if err != nil {
					t.Fatalf("valid native resources or exact absence rejected: %v", err)
				}
			} else if err == nil {
				t.Fatal("ambiguous or failed Docker ownership inspection was treated as absence")
			}
			if err != nil && strings.Contains(err.Error(), "private-fixture-detail") {
				t.Fatal("ownership error exposed Docker inspection output")
			}
			if _, err := os.Stat(mutation); !os.IsNotExist(err) {
				t.Fatal("ownership inspection performed a mutation")
			}
		})
	}
}

func TestPhysicalOwnershipChecksImplicitAndCaseInsensitiveLongVolumeMounts(t *testing.T) {
	for _, mount := range []string{
		"source=aivforge-ob-data-aivision,target=/data",
		"type=volume,src=aivforge-ob-data-aivision,dst=/data",
		"TYPE=VoLuMe,SOURCE=aivforge-ob-data-aivision,TARGET=/data",
		"SRC=aivforge-ob-data-aivision,DST=/data",
		`type=volume,"source=aivforge-ob-data-aivision",target=/data`,
		`type=volume,source=aivforge-ob-data-aivision,target=/data,"volume-opt=o=rw,src=other,hard"`,
		"type=bind,source=aivforge-ob-data-aivision,target=/data",
	} {
		t.Run(mount, func(t *testing.T) {
			app, err := datastoreApp(t, "aivision.yaml", nil)
			if err != nil {
				t.Fatal(err)
			}
			database := app.BP.Component("oceanbase")
			database.Volumes = nil
			database.Mounts = []string{mount}
			installOwnershipDockerFixture(t, app, "compose-volume")
			err = app.checkDeploymentDriver()
			if strings.HasPrefix(mount, "type=bind") {
				if err != nil {
					t.Fatalf("explicit bind mount was mistaken for a named Docker volume: %v", err)
				}
			} else if err == nil || !strings.Contains(err.Error(), "managed by Compose") {
				t.Fatalf("Docker long-mount volume ownership was missed: %v", err)
			}
		})
	}
}

func TestDestroyRejectsInvalidNamedVolumeTarget(t *testing.T) {
	for _, spec := range []string{
		"archive:/",
		"archive:relative",
		"type=volume,src=archive",
		"source=archive,dst=relative",
		"source=archive,destination=/data/..",
	} {
		t.Run(spec, func(t *testing.T) {
			app, err := datastoreApp(t, "aivision.yaml", nil)
			if err != nil {
				t.Fatal(err)
			}
			database := app.BP.Component("oceanbase")
			database.Volumes, database.Mounts = nil, nil
			if strings.Contains(spec, "=") {
				database.Mounts = []string{spec}
			} else {
				database.Volumes = []string{spec}
			}
			mutation := installOwnershipDockerFixture(t, app, "absent")
			if err := app.Destroy(DestroyOpts{Purge: true}); err == nil || !strings.Contains(err.Error(), "target") {
				t.Fatalf("expected invalid volume target error for %q, got %v", spec, err)
			}
			if _, err := os.Stat(mutation); !os.IsNotExist(err) {
				t.Fatalf("Docker mutation before rejecting %q: %v", spec, err)
			}
		})
	}
}

func TestDeploymentVolumeOptions(t *testing.T) {
	for _, tc := range []struct {
		spec    string
		name    string
		long    bool
		invalid bool
	}{
		{spec: "archive:/data:ro,Z,nocopy,cached", name: "archive"},
		{spec: "/tmp/archive:/data:ro"},
		{spec: "/data"},
		{spec: "archive:/data:rw,ro", invalid: true},
		{spec: "archive:/data:unexpected", invalid: true},
		{spec: `  "type=volume",src=archive,dst=/data,"volume-opt=o=rw,src=other,hard"  `, name: "archive", long: true},
		{spec: "type=bind,source=./archive,target=/data,bind-create-src", long: true},
		{spec: "type=volume,target=/data", long: true},
		{spec: "src=archive, target=/data", long: true, invalid: true},
		{spec: "src=archive,target=/data,unexpected=1", long: true, invalid: true},
		{spec: "src=archive,target=/data,ro=invalid", long: true, invalid: true},
	} {
		t.Run(tc.spec, func(t *testing.T) {
			parse := volumeName
			if tc.long {
				parse = mountVolumeName
			}
			name, err := parse(tc.spec)
			if name != tc.name || (err != nil) != tc.invalid {
				t.Fatalf("volume name = %q, error = %v", name, err)
			}
		})
	}
}

func installOwnershipDockerFixture(t *testing.T, app *App, mode string) string {
	t.Helper()
	dir := t.TempDir()
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	docker := filepath.Join(dir, "docker")
	if err := os.WriteFile(docker, []byte("#!/bin/sh\nexec \"$AIV_OWNER_TEST_EXECUTABLE\" -test.run '^TestOwnershipDockerProcess$' -- \"$@\"\n"), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("AIV_OWNER_TEST_EXECUTABLE", executable)
	t.Setenv("AIV_OWNER_TEST_PROCESS", "1")
	t.Setenv("AIV_OWNER_TEST_MODE", mode)
	t.Setenv("AIV_OWNER_TEST_MUTATION", filepath.Join(dir, "mutation"))
	app.Docker.Bin = docker
	return filepath.Join(dir, "mutation")
}

func TestOwnershipDockerProcess(t *testing.T) {
	if os.Getenv("AIV_OWNER_TEST_PROCESS") != "1" {
		return
	}
	var args []string
	for i, value := range os.Args {
		if value == "--" {
			args = os.Args[i+1:]
			break
		}
	}
	if len(args) != 3 || args[1] != "inspect" || (args[0] != "container" && args[0] != "volume") {
		_ = os.WriteFile(os.Getenv("AIV_OWNER_TEST_MUTATION"), []byte(strings.Join(args, " ")), 0600)
		os.Exit(93)
	}
	kind, name, mode := args[0], args[2], os.Getenv("AIV_OWNER_TEST_MODE")
	switch mode {
	case "permission":
		fmt.Fprintln(os.Stderr, "permission denied: private-fixture-detail")
		os.Exit(1)
	case "ambiguous":
		os.Exit(1)
	case "malformed":
		fmt.Print("{}")
		os.Exit(0)
	}
	labels := map[string]string{"aivision.forge.namespace": "aivision"}
	if (mode == "compose-container" && kind == "container" && name == "aivision-fe") || (mode == "compose-volume" && kind == "volume" && name == "aivforge-ob-data-aivision") {
		labels = map[string]string{"com.docker.compose.project": "unrelated-state", "com.docker.compose.service": "frontend"}
	} else if mode != "native" {
		if kind == "container" {
			fmt.Fprintln(os.Stderr, "Error: No such container: "+name)
		} else {
			fmt.Fprintln(os.Stderr, "Error response from daemon: get "+name+": no such volume")
		}
		fmt.Print("[]\n")
		os.Exit(1)
	}
	var data []byte
	if kind == "container" {
		data, _ = json.Marshal([]any{map[string]any{"Id": name + "-id", "Name": "/" + name, "Config": map[string]any{"Labels": labels}, "State": map[string]any{"Running": false}}})
	} else {
		data, _ = json.Marshal([]any{map[string]any{"Name": name, "Labels": labels}})
	}
	_, _ = os.Stdout.Write(data)
	os.Exit(0)
}

func TestComposeStatePreventsDockerActionsFromOtherDriver(t *testing.T) {
	dir := t.TempDir()
	calls := filepath.Join(dir, "docker-was-called")
	docker := filepath.Join(dir, "docker")
	if err := os.WriteFile(docker, []byte("#!/bin/sh\ntouch \"$AIV_DRIVER_TEST_CALLS\"\nexit 99\n"), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("DOCKER_BIN", docker)
	t.Setenv("AIV_DRIVER_TEST_CALLS", calls)
	app, err := datastoreApp(t, "aivision.yaml", nil)
	if err != nil {
		t.Fatal(err)
	}
	if err := app.State.EnsureDirs(); err != nil {
		t.Fatal(err)
	}
	marker := filepath.Join(app.State.StateDir, ".compose-state.json")
	if err := os.WriteFile(marker, []byte(`{"namespace":"aivision"}`), 0600); err != nil {
		t.Fatal(err)
	}
	for name, action := range map[string]func() error{
		"cast": func() error { return app.Cast(true, true) }, "deploy": app.Deploy,
		"destroy":  func() error { return app.Destroy(DestroyOpts{Purge: true}) },
		"redeploy": func() error { return app.Redeploy(true, true) },
	} {
		t.Run(name, func(t *testing.T) {
			if err := action(); err == nil || !strings.Contains(err.Error(), "managed by Compose") {
				t.Fatalf("driver change was not rejected before Docker operations: %v", err)
			}
		})
	}
	if _, err := os.Stat(calls); !os.IsNotExist(err) {
		t.Fatal("Docker was invoked before deployment owner validation")
	}
	if _, err := os.Stat(marker); err != nil {
		t.Fatal("other driver's identity was removed")
	}
}

func TestVerboseContainerStartupDoesNotLogCredentials(t *testing.T) {
	dir := t.TempDir()
	docker := filepath.Join(dir, "docker")
	if err := os.WriteFile(docker, []byte("#!/bin/sh\ncase \"$1\" in inspect) exit 1;; run) echo fixture-container;; *) exit 99;; esac\n"), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("DOCKER_BIN", docker)
	output, err := os.Create(filepath.Join(dir, "stdout"))
	if err != nil {
		t.Fatal(err)
	}
	defer output.Close()
	original := os.Stdout
	os.Stdout = output
	defer func() { os.Stdout = original }()
	bp := &Blueprint{Metadata: Metadata{Namespace: "verbose-test"}, Config: Config{Workdir: dir, SourceRoot: dir}}
	app, err := NewApp(bp, NewLogger(true), true, false)
	if err != nil {
		t.Fatal(err)
	}
	component := Component{Name: "be", Kind: "container", Image: "fixture",
		ContainerName: "existing-backend", Env: map[string]string{
			"SECRET_KEY": "private-django-key", "DB_PASSWORD": "private-database-password",
		}}
	if err := app.startContainer(component, app.ComponentVars(component)); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(output.Name())
	if err != nil {
		t.Fatal(err)
	}
	for _, value := range component.Env {
		if strings.Contains(string(data), value) {
			t.Fatal("verbose startup output exposed an application credential")
		}
	}
	if !strings.Contains(string(data), "container=existing-backend image=fixture") {
		t.Fatal("verbose startup lost non-sensitive diagnostic identity")
	}
}
