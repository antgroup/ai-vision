package runtime

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func installLifecycleDockerFixture(t *testing.T, a *App) (string, string) {
	t.Helper()
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("requires Python fixture")
	}
	dir := t.TempDir()
	docker := filepath.Join(dir, "docker")
	fixture := `import json, os, pathlib, sys
root = pathlib.Path(os.environ["AIV_LIFECYCLE_FIXTURE"])
args = sys.argv[1:]
with (root / "calls").open("a") as f:
    f.write(json.dumps(args) + "\n")
if os.environ.get("AIV_LIFECYCLE_DAEMON_FAIL") == "true": sys.exit(1)
name = args[-1]
statefile = root / (name + ".json")
if args[:2] == ["container", "inspect"]:
    if (root / (name + ".removed")).exists():
        print("Error: No such container: " + name, file=sys.stderr); sys.exit(1)
    print(json.dumps([{"Id": name, "Config": {"Labels": {"aivision.forge.namespace": "aivision"}}}]))
elif args[:2] == ["volume", "inspect"]:
    print(json.dumps([{"Name": name, "Labels": {"aivision.forge.namespace": "aivision"}}]))
elif args[0] == "inspect":
    if (root / (name + ".removed")).exists(): sys.exit(1)
    if "--format" not in args: sys.exit(0)
    template = args[args.index("--format") + 1]
    if template == "{{.Id}}":
        print(name)
    elif template == '{"ID":{{json .Id}},"State":{{json .State}}}':
        state = json.loads(statefile.read_text()) if statefile.exists() else {"Status":"running","Running":True,"ExitCode":0,"OOMKilled":False}
        if (root / "ignored-term").exists(): (root / "ignored-term-observed").touch()
        print(json.dumps({"ID": name, "State": state}))
    else:
        raise AssertionError(template)
elif args[0] == "exec":
    assert args[2:] == ["cat", "/proc/1/cmdline"], args
    if os.environ.get("AIV_LIFECYCLE_CMDLINE_FAIL") == "true": sys.exit(1)
    argv = json.loads(os.environ.get("AIV_LIFECYCLE_PID1", '["python", "manage.py", "serve_with_projector", "--address", "0.0.0.0:8001"]'))
    sys.stdout.write("\0".join(argv) + "\0")
elif args[0] == "kill":
    if args[2] == "SIGTERM" and os.environ.get("AIV_LIFECYCLE_TERM_IGNORED") == "true":
        (root / "ignored-term").touch()
        sys.exit(0)
    if args[2] == "SIGINT" and os.environ.get("AIV_LIFECYCLE_INT_FAIL") == "true":
        os.environ["AIV_LIFECYCLE_EXIT"] = "130"
    statefile.write_text(json.dumps({"Status": "exited", "Running": False, "ExitCode": int(os.environ.get("AIV_LIFECYCLE_EXIT", "0")),
                                    "OOMKilled": os.environ.get("AIV_LIFECYCLE_OOM") == "true"}))
elif args[0] == "rm":
    (root / (name + ".removed")).touch()
elif args[:2] == ["volume", "rm"]:
    pass
elif args[:2] == ["container", "ls"]:
    name = args[-1][len("name="):]
    if not (root / (name + ".removed")).exists(): print(name)
else:
    raise AssertionError(args)
`
	if err := os.WriteFile(docker, []byte("#!"+python+"\n"+fixture), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("AIV_LIFECYCLE_FIXTURE", dir)
	a.Docker.Bin = docker
	return dir, filepath.Join(dir, "calls")
}

func lifecycleCalls(t *testing.T, path string) [][]string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var result [][]string
	for _, line := range strings.Split(strings.TrimSpace(string(data)), "\n") {
		var call []string
		if err := json.Unmarshal([]byte(line), &call); err != nil {
			t.Fatal(err)
		}
		result = append(result, call)
	}
	return result
}

// Bound a TERM-ignored regression by the fixture's observed running state.
// The timeout is only a hang guard; Python startup time is not the assertion.
func cancelWhenIgnoredTermObserved(t *testing.T, a *App, dir string) {
	t.Helper()
	ctx, cancel := context.WithTimeout(a.Ctx, 10*time.Second)
	a.Ctx = ctx
	watchDone := make(chan struct{})
	go func() {
		defer close(watchDone)
		ticker := time.NewTicker(10 * time.Millisecond)
		defer ticker.Stop()
		for {
			if _, err := os.Stat(filepath.Join(dir, "ignored-term-observed")); err == nil {
				cancel()
				return
			}
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
			}
		}
	}()
	t.Cleanup(func() {
		cancel()
		<-watchDone
	})
}

func TestProductStopsConsumersInReverseDependencyOrder(t *testing.T) {
	a, err := datastoreApp(t, "aivision.yaml", nil)
	if err != nil {
		t.Fatal(err)
	}
	_, record := installLifecycleDockerFixture(t, a)
	if err := a.stopProductConsumers(); err != nil {
		t.Fatal(err)
	}
	var stopped []string
	for _, call := range lifecycleCalls(t, record) {
		if call[0] == "kill" {
			stopped = append(stopped, call[len(call)-1])
			wantSignal := "SIGTERM"
			if call[len(call)-1] == a.ContainerName(*a.BP.Component("fe")) {
				wantSignal = "SIGQUIT"
			}
			if call[2] != wantSignal {
				t.Fatalf("wrong stop signal: %v", call)
			}
		}
	}
	ordered := a.BP.OrderedComponents()
	var want []string
	for i := len(ordered) - 1; i >= 0; i-- {
		if _, _, enabled := a.productContainerStop(ordered[i]); enabled {
			want = append(want, a.ContainerName(ordered[i]))
		}
	}
	if len(stopped) != 5 || strings.Join(stopped, ",") != strings.Join(want, ",") {
		t.Fatalf("stopped=%v want=%v; database must remain until consumers stop", stopped, want)
	}
	for _, c := range ordered {
		if _, _, enabled := a.productContainerStop(c); enabled {
			if _, err := os.Stat(a.stopRecord(c)); !os.IsNotExist(err) {
				t.Fatal("successful stop retained a pending record")
			}
		}
	}
}

func TestProductStopFailureCannotBeBypassedByAnotherCastOrPurge(t *testing.T) {
	for _, failure := range []string{"nonzero", "oom"} {
		t.Run(failure, func(t *testing.T) {
			a, err := datastoreApp(t, "aivision.yaml", nil)
			if err != nil {
				t.Fatal(err)
			}
			dir, record := installLifecycleDockerFixture(t, a)
			if failure == "nonzero" {
				t.Setenv("AIV_LIFECYCLE_EXIT", "1")
			} else {
				t.Setenv("AIV_LIFECYCLE_OOM", "true")
			}
			c := *a.BP.Component("fe")
			if err := a.stopProductContainer(c, false); err == nil {
				t.Fatal("failed stop was accepted")
			}
			info, err := os.Stat(a.stopRecord(c))
			if err != nil || info.Mode().Perm() != 0600 {
				t.Fatal("failed stop lacks a private persistent gate")
			}
			if err := a.stopProductConsumers(); err == nil {
				t.Fatal("another cast bypassed the previous failed stop")
			}
			t.Setenv("AIV_LIFECYCLE_DAEMON_FAIL", "true")
			if err := a.stopProductContainer(c, false); err == nil {
				t.Fatal("unavailable Docker daemon was treated as a removed container")
			}
			if _, err := os.Stat(a.stopRecord(c)); err != nil {
				t.Fatal("daemon failure cleared the stop gate")
			}
			t.Setenv("AIV_LIFECYCLE_DAEMON_FAIL", "false")
			if err := a.Destroy(DestroyOpts{Purge: true}); err == nil {
				t.Fatal("destroy bypassed a failed stop")
			}
			for _, call := range lifecycleCalls(t, record) {
				if call[0] == "rm" || (len(call) > 1 && call[0] == "volume" && call[1] == "rm") {
					t.Fatalf("failed stop removed data or container: %v", call)
				}
			}
			// Explicit removal of that exact failed container clears its gate.
			if err := os.WriteFile(filepath.Join(dir, a.ContainerName(c)+".removed"), nil, 0600); err != nil {
				t.Fatal(err)
			}
			if err := a.stopProductContainer(c, false); err != nil {
				t.Fatal(err)
			}
			if _, err := os.Stat(a.stopRecord(c)); !os.IsNotExist(err) {
				t.Fatal("obsolete stop gate remained after explicit container removal")
			}
		})
	}
}

func TestProductCastCanReplacePriorStartupFailure(t *testing.T) {
	a, err := datastoreApp(t, "aivision.yaml", nil)
	if err != nil {
		t.Fatal(err)
	}
	dir, _ := installLifecycleDockerFixture(t, a)
	c := *a.BP.Component("be")
	if err := os.WriteFile(filepath.Join(dir, a.ContainerName(c)+".json"),
		[]byte(`{"Status":"exited","Running":false,"ExitCode":1,"OOMKilled":false}`), 0600); err != nil {
		t.Fatal(err)
	}
	if err := a.stopProductContainer(c, false); err != nil {
		t.Fatalf("a prior startup failure must be retryable after a configuration fix: %v", err)
	}
	if err := a.stopProductContainer(c, true); err == nil {
		t.Fatal("strict destroy should retain an unsuccessful container before purge")
	}
}

func TestProductStopRejectsUnstableDockerStates(t *testing.T) {
	for _, state := range []string{
		`{"Status":"restarting","Running":false,"Restarting":true,"ExitCode":0}`,
		`{"Status":"dead","Running":false,"Dead":true,"ExitCode":0}`,
		`{"Status":"created","Running":false,"ExitCode":0}`,
	} {
		a, err := datastoreApp(t, "aivision.yaml", nil)
		if err != nil {
			t.Fatal(err)
		}
		dir, _ := installLifecycleDockerFixture(t, a)
		c := *a.BP.Component("be")
		if err := os.WriteFile(filepath.Join(dir, a.ContainerName(c)+".json"), []byte(state), 0600); err != nil {
			t.Fatal(err)
		}
		if err := a.stopProductContainer(c, true); err == nil {
			t.Fatalf("accepted non-exited state: %s", state)
		}
	}
}

func TestProductShutdownBudgetsDoNotChangeSourceOrCustomBlueprints(t *testing.T) {
	a, err := datastoreApp(t, "aivision.yaml", nil)
	if err != nil {
		t.Fatal(err)
	}
	for role, want := range map[string]time.Duration{"fe": 30 * time.Second, "agentlens": 300 * time.Second, "be": 60 * time.Second} {
		budget, _, ok := a.productContainerStop(*a.BP.Component(role))
		if !ok || budget != want {
			t.Fatalf("%s budget=%s enabled=%t", role, budget, ok)
		}
	}
	a.BP.Metadata.Product = ""
	for _, c := range a.BP.Components {
		if _, _, enabled := a.productContainerStop(c); enabled {
			t.Fatal("a custom blueprint opted into product lifecycle")
		}
	}
	a, err = datastoreApp(t, "aivision-source.yaml", nil)
	if err != nil {
		t.Fatal(err)
	}
	for _, c := range a.BP.Components {
		if _, _, enabled := a.productContainerStop(c); enabled {
			t.Fatal("source development opted into product lifecycle")
		}
	}
}

// A TERM-only regression leaves the real legacy Django PID 1 alive. Cancel
// when the fixture observes that ignored signal, rather than imposing a short
// deadline on every Python subprocess in the successful shutdown path.
func TestProductLegacyBackendStopsWithoutForcedExit(t *testing.T) {
	a, err := datastoreApp(t, "aivision.yaml", nil)
	if err != nil {
		t.Fatal(err)
	}
	dir, record := installLifecycleDockerFixture(t, a)
	t.Setenv("AIV_LIFECYCLE_PID1", `["python","manage.py","runserver","0.0.0.0:8001","--noreload"]`)
	t.Setenv("AIV_LIFECYCLE_TERM_IGNORED", "true")
	cancelWhenIgnoredTermObserved(t, a, dir)
	c := *a.BP.Component("be")
	if err := a.removeContainerWithChecks(c, true); err != nil {
		t.Fatalf("legacy Django backend must exit cleanly before replacement: %v", err)
	}
	if _, err := os.Stat(filepath.Join(dir, a.ContainerName(c)+".removed")); err != nil {
		t.Fatal("cleanly stopped legacy backend was not replaced")
	}
	if _, err := os.Stat(a.stopRecord(c)); !os.IsNotExist(err) {
		t.Fatal("clean legacy shutdown retained its pending stop gate")
	}
	for _, call := range lifecycleCalls(t, record) {
		if call[0] == "kill" && call[2] != "SIGINT" {
			t.Fatalf("legacy backend received %v", call)
		}
	}
}

func TestProductBackendCommandDetectionPreservesGracefulTerm(t *testing.T) {
	for _, command := range []string{
		`["python","manage.py","serve_with_projector","--address","0.0.0.0:8001"]`,
		`["python","manage.py","runserver_wrapper","0.0.0.0:8001","--noreload"]`,
		`["python","manage.py","runserver","0.0.0.0:8001","--noreload","--insecure"]`,
		`["python","other-manage.py","runserver","0.0.0.0:8001","--noreload"]`,
		`["shell","-c","python manage.py runserver 0.0.0.0:8001 --noreload"]`,
		`["python","manage.py","runserver","invalid-address","--noreload"]`,
		`[]`,
	} {
		t.Run(command, func(t *testing.T) {
			a, err := datastoreApp(t, "aivision.yaml", nil)
			if err != nil {
				t.Fatal(err)
			}
			dir, record := installLifecycleDockerFixture(t, a)
			t.Setenv("AIV_LIFECYCLE_PID1", command)
			t.Setenv("AIV_LIFECYCLE_INT_FAIL", "true")
			c := *a.BP.Component("be")
			if err := a.removeContainerWithChecks(c, true); err != nil {
				t.Fatalf("nonlegacy backend lost its TERM shutdown: %v", err)
			}
			if _, err := os.Stat(filepath.Join(dir, a.ContainerName(c)+".removed")); err != nil {
				t.Fatal("clean backend was not removed")
			}
			for _, call := range lifecycleCalls(t, record) {
				if call[0] == "kill" && call[2] != "SIGTERM" {
					t.Fatalf("nonlegacy backend received %v", call)
				}
			}
		})
	}
}

func TestProductBackendCommandInspectionFailureRetainsFailedStop(t *testing.T) {
	a, err := datastoreApp(t, "aivision.yaml", nil)
	if err != nil {
		t.Fatal(err)
	}
	dir, record := installLifecycleDockerFixture(t, a)
	t.Setenv("AIV_LIFECYCLE_CMDLINE_FAIL", "true")
	t.Setenv("AIV_LIFECYCLE_TERM_IGNORED", "true")
	cancelWhenIgnoredTermObserved(t, a, dir)
	c := *a.BP.Component("be")
	if err := a.removeContainerWithChecks(c, true); err == nil {
		t.Fatal("unverifiable legacy backend was removed")
	}
	if _, err := os.Stat(filepath.Join(dir, "ignored-term-observed")); err != nil {
		t.Fatal("failure did not reach the ignored TERM state readback")
	}
	if info, err := os.Stat(a.stopRecord(c)); err != nil || info.Mode().Perm() != 0600 {
		t.Fatal("failed stop lost its private gate")
	}
	if err := a.Destroy(DestroyOpts{Purge: true}); err == nil {
		t.Fatal("purge bypassed a failed backend stop")
	}
	for _, call := range lifecycleCalls(t, record) {
		if call[0] == "kill" && call[2] != "SIGTERM" {
			t.Fatalf("inspection failure guessed a shutdown signal: %v", call)
		}
		if call[0] == "rm" || (len(call) > 1 && call[0] == "volume" && call[1] == "rm") {
			t.Fatalf("unverified stop removed resources: %v", call)
		}
	}
}
