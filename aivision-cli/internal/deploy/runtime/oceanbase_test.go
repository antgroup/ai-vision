package runtime

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/assets"
)

func TestOceanbaseLifecycleAssetAndContainerCompatibility(t *testing.T) {
	for _, blueprint := range []string{"aivision.yaml", "aivision-source.yaml"} {
		t.Run(blueprint, func(t *testing.T) {
			a, err := datastoreApp(t, blueprint, nil)
			if err != nil {
				t.Fatal(err)
			}
			path := a.Vars["OCEANBASE_SCRIPT"]
			if _, err := os.Stat(path); !os.IsNotExist(err) {
				t.Fatal("NewApp materialized execution files")
			}
			if err := a.prepareDatastoreAssets(); err != nil {
				t.Fatal(err)
			}
			content, _ := os.ReadFile(path)
			embedded, _ := assets.Files.ReadFile("bootstrap/oceanbase.sh")
			if !bytes.Equal(content, embedded) {
				t.Fatal("CLI does not use shared OceanBase lifecycle")
			}
			for name, mode := range map[string]os.FileMode{path: 0600, filepath.Dir(path): 0700} {
				info, err := os.Stat(name)
				if err != nil || info.Mode().Perm() != mode {
					t.Fatalf("incorrect private asset mode: %s", name)
				}
			}
			record := installOceanbaseDockerFixture(t, a)
			c := *a.BP.Component("oceanbase")
			if err := a.startContainer(c, a.ComponentVars(c)); err != nil {
				t.Fatal(err)
			}
			args, _ := os.ReadFile(record)
			for _, want := range []string{
				"type=bind,source=" + path + ",target=/opt/aivision/oceanbase.sh,readonly",
				"aivforge-ob-data-aivision:/root/ob", "aivforge-ob-config-aivision:/root/.obd/cluster",
				oceanbaseLifecycleLabel + "=ce-v1", "--stop-timeout 120",
				"/opt/aivision/oceanbase.sh",
			} {
				if !strings.Contains(string(args), want) {
					t.Fatalf("missing compatibility argument %q", want)
				}
			}
			if blueprint == "aivision.yaml" &&
				(!strings.Contains(string(args), "io.aivision.component=database") ||
					!strings.Contains(string(args), "aivision.forge.component=oceanbase")) {
				t.Fatal("product role labels must coexist with the legacy selector label")
			}
			if len(c.Entrypoint) != 0 || len(c.Mounts) != 0 {
				t.Fatal("runtime changed source/generic blueprint")
			}
		})
	}
}

func TestOceanbaseRemovalConfirmsGracefulShutdownBeforeRemoving(t *testing.T) {
	for _, tc := range []struct {
		name, lifecycle, code, execCode string
		wantError                       bool
	}{
		{"new", "ce-v1", "0", "0", false},
		{"forced", "ce-v1", "137", "0", true},
		{"legacy", "", "137", "0", false},
		{"legacy-failure", "", "137", "7", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			a, err := datastoreApp(t, "aivision.yaml", nil)
			if err != nil {
				t.Fatal(err)
			}
			record := installOceanbaseDockerFixture(t, a)
			t.Setenv("AIV_TEST_RUNNING", "true")
			t.Setenv("AIV_TEST_LIFECYCLE", tc.lifecycle)
			t.Setenv("AIV_TEST_EXIT", tc.code)
			t.Setenv("AIV_TEST_EXEC_EXIT", tc.execCode)
			err = a.removeContainer(*a.BP.Component("oceanbase"))
			if (err != nil) != tc.wantError {
				t.Fatalf("remove result=%v, want error=%t", err, tc.wantError)
			}
			data, _ := os.ReadFile(record)
			calls := string(data)
			removed := strings.Contains(calls, "rm -f ")
			if removed == tc.wantError {
				t.Fatalf("unsafe removal after shutdown result: %s", calls)
			}
			if tc.lifecycle == "ce-v1" && !strings.Contains(calls, "stop --time 120 ") {
				t.Fatal("new lifecycle was not given TERM and its grace period")
			}
			if tc.lifecycle == "" && !strings.Contains(calls, "obd cluster stop obcluster") {
				t.Fatal("legacy database was not stopped before force-removing its idle shell")
			}
		})
	}
}

func TestSeekdbRemovalRejectsForcedDatabaseShutdown(t *testing.T) {
	for _, tc := range []struct {
		name, code, entrypoint string
		wantError              bool
	}{
		{"normal", "0", `["/bin/sh","/opt/aivision/seekdb.sh"]`, false},
		{"new-forced", "137", `["/bin/sh","/opt/aivision/seekdb.sh"]`, true},
		{"legacy-pid1", "0", `["/usr/bin/seekdb"]`, false},
		{"legacy-pid1-failure", "137", `["/usr/bin/seekdb"]`, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			a, err := datastoreApp(t, "aivision.yaml", map[string]string{"OB_BACKEND": "seekdb"})
			if err != nil {
				t.Fatal(err)
			}
			record := installOceanbaseDockerFixture(t, a)
			t.Setenv("AIV_TEST_RUNNING", "true")
			t.Setenv("AIV_TEST_EXIT", tc.code)
			t.Setenv("AIV_TEST_ENTRYPOINT", tc.entrypoint)
			err = a.removeContainer(*a.BP.Component("oceanbase"))
			if (err != nil) != tc.wantError {
				t.Fatalf("stop seekdb returned %v for exit %s", err, tc.code)
			}
			data, _ := os.ReadFile(record)
			calls := string(data)
			expected := "stop --time 120 "
			if tc.entrypoint == `["/usr/bin/seekdb"]` {
				expected = "kill --signal SIGUSR1 "
			}
			if !strings.Contains(calls, expected) {
				t.Fatal("seekdb was not given its supported shutdown signal")
			}
			if strings.Contains(calls, "rm -f ") == tc.wantError {
				t.Fatal("failed seekdb shutdown was removed instead of retained")
			}
		})
	}
}

func TestFailedDatabaseStopCannotBeBypassedByAnotherCast(t *testing.T) {
	for _, backend := range []string{"oceanbase", "seekdb"} {
		t.Run(backend, func(t *testing.T) {
			a, err := datastoreApp(t, "aivision.yaml", map[string]string{"OB_BACKEND": backend})
			if err != nil {
				t.Fatal(err)
			}
			dir, calls := installDatabaseRetryFixture(t, a)
			c := *a.BP.Component("oceanbase")
			for attempt := 0; attempt < 2; attempt++ {
				if err := a.removeContainer(c); err == nil {
					t.Fatalf("cast attempt %d bypassed the failed database shutdown", attempt+1)
				}
			}
			if info, err := os.Stat(a.databaseStopRecord()); err != nil || info.Mode().Perm() != 0600 {
				t.Fatal("failed database stop lacks a private persistent gate")
			}
			data, _ := os.ReadFile(calls)
			if strings.Contains(string(data), "rm -f") {
				t.Fatal("retry removed the database after its failed shutdown")
			}
			// Restarting the same database and stopping it cleanly acknowledges
			// the failure without deleting its retained data or identity.
			if err := os.WriteFile(filepath.Join(dir, "state"), []byte("running"), 0600); err != nil {
				t.Fatal(err)
			}
			t.Setenv("AIV_DATABASE_RETRY_EXIT", "0")
			if err := a.removeContainer(c); err != nil {
				t.Fatalf("clean recovery could not replace the database: %v", err)
			}
			if _, err := os.Stat(a.databaseStopRecord()); !os.IsNotExist(err) {
				t.Fatal("clean recovery retained a stale database stop gate")
			}
		})
	}
}

func TestDatabaseStopGateTracksContainerIdentityAndInspectionFailures(t *testing.T) {
	for _, recovery := range []string{"removed", "replaced", "restarted"} {
		t.Run(recovery, func(t *testing.T) {
			a, err := datastoreApp(t, "aivision.yaml", nil)
			if err != nil {
				t.Fatal(err)
			}
			dir, _ := installDatabaseRetryFixture(t, a)
			c := *a.BP.Component("oceanbase")
			t.Setenv("AIV_DATABASE_RETRY_EXIT", "0")
			t.Setenv("AIV_DATABASE_RETRY_OOM", "true")
			if err := a.removeContainer(c); err == nil {
				t.Fatal("an OOM during stop was accepted")
			}
			t.Setenv("AIV_DATABASE_RETRY_INSPECT_FAILURE", "true")
			if err := a.removeContainer(c); err == nil {
				t.Fatal("a Docker inspection error cleared the failed stop gate")
			}
			if _, err := os.Stat(a.databaseStopRecord()); err != nil {
				t.Fatal("Docker inspection failure discarded the database stop gate")
			}
			t.Setenv("AIV_DATABASE_RETRY_INSPECT_FAILURE", "false")
			switch recovery {
			case "removed":
				t.Setenv("AIV_DATABASE_RETRY_ABSENT", "true")
			case "replaced":
				t.Setenv("AIV_DATABASE_RETRY_ID", "replacement-id")
			case "restarted":
				if err := os.WriteFile(filepath.Join(dir, "state"), []byte("running"), 0600); err != nil {
					t.Fatal(err)
				}
				t.Setenv("AIV_DATABASE_RETRY_OOM", "false")
			}
			if err := a.removeContainer(c); err != nil {
				t.Fatalf("explicit %s recovery could not clear the obsolete stop gate: %v", recovery, err)
			}
			if _, err := os.Stat(a.databaseStopRecord()); !os.IsNotExist(err) {
				t.Fatal("successful recovery retained a stale database stop gate")
			}
		})
	}
}

func TestFailedSeekdbPreStartStopBlocksOfflineInitializationOnRetry(t *testing.T) {
	a, err := datastoreApp(t, "aivision.yaml", map[string]string{"OB_BACKEND": "seekdb"})
	if err != nil {
		t.Fatal(err)
	}
	dir, calls := installDatabaseRetryFixture(t, a)
	if err := a.prepareDatastoreAssets(); err != nil {
		t.Fatal(err)
	}
	c := *a.BP.Component("oceanbase")
	var step Step
	for _, candidate := range c.PreStart {
		if candidate.Name == "initialize or verify seekdb credentials offline" {
			step = expandStep(candidate, a.ComponentVars(c))
		}
	}
	for attempt := 0; attempt < 2; attempt++ {
		cmd := exec.Command("/bin/sh", "-c", step.Shell)
		cmd.Env = os.Environ()
		for key, value := range step.Env {
			cmd.Env = append(cmd.Env, key+"="+value)
		}
		if output, err := cmd.CombinedOutput(); err == nil {
			t.Fatalf("prestart attempt %d bypassed the failed database shutdown: %s", attempt+1, output)
		}
	}
	data, _ := os.ReadFile(calls)
	if strings.Contains(string(data), "create --name") || strings.Contains(string(data), "start -a") {
		t.Fatal("retry initialized storage after the database failed to stop")
	}
	// The shell and Go removal must recognize the same exact-ID gate.
	if err := a.removeContainer(c); err == nil {
		t.Fatal("Go container replacement bypassed the preStart stop gate")
	}
	if err := os.WriteFile(filepath.Join(dir, "state"), []byte("running"), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("AIV_DATABASE_RETRY_EXIT", "0")
	cmd := exec.Command("/bin/sh", "-c", step.Shell)
	cmd.Env = os.Environ()
	for key, value := range step.Env {
		cmd.Env = append(cmd.Env, key+"="+value)
	}
	if output, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("a clean same-container stop did not permit offline initialization: %v\n%s", err, output)
	}
	if _, err := os.Stat(a.databaseStopRecord()); !os.IsNotExist(err) {
		t.Fatal("clean preStart recovery retained a stale database stop gate")
	}
	// Conversely, a Go-created gate must be readable by the preStart shell.
	if err := os.WriteFile(filepath.Join(dir, "state"), []byte("running"), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("AIV_DATABASE_RETRY_EXIT", "137")
	if err := a.removeContainer(c); err == nil {
		t.Fatal("the repeated forced database stop was accepted")
	}
	t.Setenv("AIV_DATABASE_RETRY_EXIT", "0")
	cmd = exec.Command("/bin/sh", "-c", step.Shell)
	cmd.Env = os.Environ()
	for key, value := range step.Env {
		cmd.Env = append(cmd.Env, key+"="+value)
	}
	if output, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("preStart could not read Go's gate after a clean same-ID recovery: %v\n%s", err, output)
	}
	if _, err := os.Stat(a.databaseStopRecord()); !os.IsNotExist(err) {
		t.Fatal("preStart retained the clean Go-created stop gate")
	}
}

func TestMalformedDatabaseStopRecordsFailClosedInGoAndShell(t *testing.T) {
	a, err := datastoreApp(t, "aivision.yaml", map[string]string{"OB_BACKEND": "seekdb"})
	if err != nil {
		t.Fatal(err)
	}
	_, calls := installDatabaseRetryFixture(t, a)
	if err := a.prepareDatastoreAssets(); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Dir(a.databaseStopRecord()), 0700); err != nil {
		t.Fatal(err)
	}
	c := *a.BP.Component("oceanbase")
	for _, record := range []string{"", "database-id\n", "database-id extra", "invalid!id"} {
		if err := os.WriteFile(a.databaseStopRecord(), []byte(record), 0600); err != nil {
			t.Fatal(err)
		}
		if err := a.removeContainer(c); err == nil {
			t.Fatalf("Go accepted malformed database stop record %q", record)
		}
		for _, candidate := range c.PreStart {
			if candidate.Name != "initialize or verify seekdb credentials offline" {
				continue
			}
			step := expandStep(candidate, a.ComponentVars(c))
			cmd := exec.Command("/bin/sh", "-c", step.Shell)
			cmd.Env = os.Environ()
			for key, value := range step.Env {
				cmd.Env = append(cmd.Env, key+"="+value)
			}
			if output, err := cmd.CombinedOutput(); err == nil {
				t.Fatalf("shell accepted malformed database stop record %q: %s", record, output)
			}
		}
	}
	data, _ := os.ReadFile(calls)
	if strings.Contains(string(data), "stop --time") || strings.Contains(string(data), "rm -f") || strings.Contains(string(data), "create --name") {
		t.Fatal("a malformed stop record permitted database mutation")
	}
}

func TestSeekdbPreStartDoesNotInitializeAfterRunningStateInspectionFails(t *testing.T) {
	a, err := datastoreApp(t, "aivision.yaml", map[string]string{"OB_BACKEND": "seekdb"})
	if err != nil {
		t.Fatal(err)
	}
	_, calls := installDatabaseRetryFixture(t, a)
	if err := a.prepareDatastoreAssets(); err != nil {
		t.Fatal(err)
	}
	t.Setenv("AIV_DATABASE_RETRY_RUNNING_INSPECT_FAILURE", "true")
	c := *a.BP.Component("oceanbase")
	for _, candidate := range c.PreStart {
		if candidate.Name != "initialize or verify seekdb credentials offline" {
			continue
		}
		step := expandStep(candidate, a.ComponentVars(c))
		cmd := exec.Command("/bin/sh", "-c", step.Shell)
		cmd.Env = os.Environ()
		for key, value := range step.Env {
			cmd.Env = append(cmd.Env, key+"="+value)
		}
		if output, err := cmd.CombinedOutput(); err == nil {
			t.Fatalf("failed running-state inspection was accepted: %s", output)
		}
	}
	data, _ := os.ReadFile(calls)
	if strings.Contains(string(data), "create --name") || strings.Contains(string(data), "start -a") {
		t.Fatal("state inspection failure permitted offline initialization")
	}
}

func installDatabaseRetryFixture(t *testing.T, a *App) (string, string) {
	t.Helper()
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("requires Python Docker fixture")
	}
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "state"), []byte("running"), 0600); err != nil {
		t.Fatal(err)
	}
	script := `import json, os, pathlib, sys
root = pathlib.Path(os.environ["AIV_DATABASE_RETRY_DIR"])
args = sys.argv[1:]
with (root / "calls").open("a") as stream: stream.write(" ".join(args) + "\n")
running = (root / "state").read_text() == "running"
code = int(os.environ.get("AIV_DATABASE_RETRY_EXIT", "137"))
state = {"Status": "running" if running else "exited", "Running": running,
         "Restarting": False, "Dead": False, "ExitCode": 0 if running else code,
         "OOMKilled": os.environ.get("AIV_DATABASE_RETRY_OOM") == "true"}
if args[0] == "inspect" or args[:2] == ["container", "inspect"]:
    if os.environ.get("AIV_DATABASE_RETRY_INSPECT_FAILURE") == "true" or os.environ.get("AIV_DATABASE_RETRY_ABSENT") == "true": sys.exit(1)
    if "--format" not in args: sys.exit(0)
    template = args[args.index("--format") + 1]
    if template == "{{.State.Running}}":
        if os.environ.get("AIV_DATABASE_RETRY_RUNNING_INSPECT_FAILURE") == "true": sys.exit(1)
        print(str(running).lower())
    elif template == "{{.State.ExitCode}}": print(state["ExitCode"])
    elif template == "{{.State.OOMKilled}}": print(str(state["OOMKilled"]).lower())
    elif template == "{{.Id}}": print(os.environ.get("AIV_DATABASE_RETRY_ID", "database-id"))
    elif template == '{{json .Config.Entrypoint}}': print('["/bin/sh","/opt/aivision/seekdb.sh"]')
    elif "database-lifecycle" in template: print("ce-v1")
    elif template == '{"ID":{{json .Id}},"State":{{json .State}}}': print(json.dumps({"ID": os.environ.get("AIV_DATABASE_RETRY_ID", "database-id"), "State": state}))
    elif template == '{{.State.Status}} {{.State.Running}} {{.State.Restarting}} {{.State.Dead}} {{.State.ExitCode}} {{.State.OOMKilled}}':
        print(" ".join(str(state[key]).lower() for key in ("Status", "Running", "Restarting", "Dead", "ExitCode", "OOMKilled")))
    else: raise AssertionError(template)
elif args[0] in ("stop", "kill"): (root / "state").write_text("stopped")
elif args[:2] == ["container", "ls"]:
    if os.environ.get("AIV_DATABASE_RETRY_ABSENT") != "true": print("oceanbase")
elif args[:2] == ["network", "inspect"]: sys.exit(1)
elif args[:2] == ["network", "create"]: print("isolated-network")
elif args[:2] in (["image", "inspect"], ["volume", "create"], ["network", "rm"]): pass
elif args[0] in ("rm", "create", "start"): pass
else: raise AssertionError(args)
`
	docker := filepath.Join(dir, "docker")
	if err := os.WriteFile(docker, []byte("#!"+python+"\n"+script), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("AIV_DATABASE_RETRY_DIR", dir)
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
	a.Docker.Bin = docker
	return dir, filepath.Join(dir, "calls")
}

func installOceanbaseDockerFixture(t *testing.T, a *App) string {
	t.Helper()
	dir := t.TempDir()
	record := filepath.Join(dir, "calls")
	docker := filepath.Join(dir, "docker")
	script := `#!/bin/sh
printf '%s\n' "$*" >> "$AIV_TEST_DOCKER_CALLS"
case "$*" in
  *State.Running*) if [ -f "$AIV_TEST_DOCKER_CALLS.stopped" ]; then echo false; else echo "${AIV_TEST_RUNNING:-false}"; fi;;
  *database-lifecycle*) echo "${AIV_TEST_LIFECYCLE:-}";;
  *State.ExitCode*) echo "${AIV_TEST_EXIT:-0}";;
  *Config.Entrypoint*) echo "${AIV_TEST_ENTRYPOINT:-}";;
  *'"ID":'*) running=${AIV_TEST_RUNNING:-false}; status=exited; [ "$running" != true ] || status=running; if [ -f "$AIV_TEST_DOCKER_CALLS.stopped" ]; then running=false; status=exited; fi; printf '{"ID":"fixture-id","State":{"Status":"%s","Running":%s,"ExitCode":%s,"OOMKilled":false}}\n' "$status" "$running" "${AIV_TEST_EXIT:-0}";;
  *'{{.Id}}'*) echo fixture-id;;
  kill*|stop*) touch "$AIV_TEST_DOCKER_CALLS.stopped";;
  exec*) exit "${AIV_TEST_EXEC_EXIT:-0}";;
esac
`
	if err := os.WriteFile(docker, []byte(script), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("AIV_TEST_DOCKER_CALLS", record)
	a.Docker.Bin = docker
	return record
}
