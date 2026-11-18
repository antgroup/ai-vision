package sandboxplugin

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
)

type fakeDocker struct {
	commands               [][]string
	created                bool
	running                bool
	foreign                bool
	badPort                bool
	exitCode               int
	oom                    bool
	exitOnStart            bool
	image                  string
	port                   string
	labels                 map[string]string
	startedAt              string
	restartCount           int
	inspects               int
	restartAtInspect       int
	restartDuringStop      bool
	changeStartedAtOnly    bool
	changeRestartCountOnly bool
}

const fakeID = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"

func (d *fakeDocker) Run(_ context.Context, args ...string) ([]byte, error) {
	d.commands = append(d.commands, append([]string(nil), args...))
	switch args[0] {
	case "image", "pull":
		return []byte("{}"), nil
	case "run":
		d.created, d.running = true, true
		d.startedAt = "2026-11-02T00:00:00Z"
		if d.exitOnStart {
			d.running = false
		}
		d.labels = map[string]string{}
		d.image = args[len(args)-1]
		for i, arg := range args {
			if arg == "--label" {
				key, value, _ := strings.Cut(args[i+1], "=")
				d.labels[key] = value
			}
			if arg == "--publish" {
				d.port = strings.Split(args[i+1], ":")[1]
			}
		}
		return []byte(fakeID + "\n"), nil
	case "container":
		if args[1] == "ls" {
			if d.created {
				return []byte(fakeID[:12]), nil
			}
			return nil, nil
		}
		if !d.created {
			return nil, errors.New("not found")
		}
		d.inspects++
		if d.restartAtInspect == d.inspects {
			d.restart()
		}
		labels := map[string]string{}
		for key, value := range d.labels {
			labels[key] = value
		}
		if d.foreign {
			labels[identityLabel] = "foreign"
		}
		port := d.port
		if d.badPort {
			port = "1"
		}
		data := map[string]any{"Id": fakeID, "RestartCount": d.restartCount, "Config": map[string]any{"Image": d.image, "Labels": labels}, "State": map[string]any{"StartedAt": d.startedAt, "Running": d.running, "ExitCode": d.exitCode, "OOMKilled": d.oom}, "NetworkSettings": map[string]any{"Ports": map[string]any{"8091/tcp": []map[string]string{{"HostIp": "127.0.0.1", "HostPort": port}}}}}
		return json.Marshal(data)
	case "stop":
		if d.restartDuringStop {
			d.restart()
		}
		d.running = false
		return []byte(fakeID), nil
	case "rm":
		d.created = false
		return []byte(fakeID), nil
	}
	return nil, fmt.Errorf("unexpected fake Docker command: %s", args[0])
}

func (d *fakeDocker) restart() {
	if !d.changeRestartCountOnly {
		d.startedAt = "2026-11-02T00:00:01Z"
	}
	if !d.changeStartedAtOnly {
		d.restartCount++
	}
}
func (d *fakeDocker) Stream(_ context.Context, out io.Writer, args ...string) error {
	d.commands = append(d.commands, append([]string(nil), args...))
	_, err := io.WriteString(out, "safe plugin log\n")
	return err
}
func (d *fakeDocker) count(action string) int {
	n := 0
	for _, args := range d.commands {
		if args[0] == action {
			n++
		}
	}
	return n
}

type fixture struct {
	m            *Manager
	docker       *fakeDocker
	options      Options
	mu           sync.Mutex
	active       int
	draining     bool
	invalid      bool
	unauthorized bool
	drains       int
}

func setup(t *testing.T) *fixture {
	t.Helper()
	directory, err := os.MkdirTemp("", "aiv-plugin-test-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(directory) })
	f := &fixture{docker: &fakeDocker{}}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		defer f.mu.Unlock()
		if r.Header.Get("Authorization") != "Bearer synthetic-plugin-token" {
			t.Error("plugin request did not use token-file authentication")
			w.WriteHeader(401)
			return
		}
		if f.unauthorized {
			w.WriteHeader(401)
			_, _ = io.WriteString(w, "private-token-in-provider-error")
			return
		}
		if r.Method == http.MethodPost && r.URL.Path == "/v1/admin/drain" {
			f.draining = true
			f.drains++
		}
		if f.invalid {
			_, _ = io.WriteString(w, `{"protocol_version":1,"provider":"openshell","ready":false,"draining":true}`)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"protocol_version": 1, "provider": "openshell", "ready": !f.draining, "draining": f.draining, "active_leases": f.active})
	}))
	t.Cleanup(server.Close)
	_, port, _ := net.SplitHostPort(strings.TrimPrefix(server.URL, "http://"))
	portNumber, _ := strconv.Atoi(port)
	token := filepath.Join(directory, "token")
	policy := filepath.Join(directory, "policy.json")
	if err := os.WriteFile(token, []byte("synthetic-plugin-token\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(policy, []byte(`{"version":1,"process":{"run_as_user":"1000","run_as_group":"1000"},"landlock":{"compatibility":"hard_requirement"},"filesystem_policy":{"include_workdir":true},"network_policies":{}}`), 0600); err != nil {
		t.Fatal(err)
	}
	socket := filepath.Join(directory, "docker.sock")
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = listener.Close() })
	f.options = Options{Image: "explicit/plugin:test", Gateway: "gateway.example:30051", WorkloadImage: "explicit/rules:test", TokenFile: token, PolicyFile: policy, DockerSocket: socket, JournalDir: filepath.Join(directory, "journal"), Workspace: "default", Port: portNumber}
	f.m = New(filepath.Join(directory, "state"))
	f.m.Docker = f.docker
	return f
}
func (f *fixture) install(t *testing.T) {
	t.Helper()
	status, err := f.m.Install(context.Background(), f.options)
	if err != nil || status == nil || !status.Installed || status.Health == nil || !*status.Health.Ready {
		t.Fatalf("install: %v, %#v", err, status)
	}
}

func TestImmutableWorkerIsOfferedForEnhancedRuntimeQualification(t *testing.T) {
	f := setup(t)
	f.options.WorkloadImage = "example/worker@sha256:" + strings.Repeat("a", 64)
	f.install(t)
	commands, _ := json.Marshal(f.docker.commands)
	if !strings.Contains(string(commands), "AIV_SANDBOX_ENHANCED_IMAGE="+f.options.WorkloadImage) {
		t.Fatal("the exact worker digest was not supplied for runtime qualification")
	}
}

func TestInstallUsesPrivatePersistentStateAndNoArgvSecrets(t *testing.T) {
	f := setup(t)
	f.options.GatewayTokenFile = filepath.Join(filepath.Dir(f.options.TokenFile), "gateway-token")
	if err := os.WriteFile(f.options.GatewayTokenFile, []byte("synthetic-gateway-token"), 0600); err != nil {
		t.Fatal(err)
	}
	f.install(t)
	commands, _ := json.Marshal(f.docker.commands)
	uid, gid, err := ownerIDs(f.options.TokenFile)
	if err != nil {
		t.Fatal(err)
	}
	_, socketGID, err := ownerIDs(f.options.DockerSocket)
	if err != nil {
		t.Fatal(err)
	}
	flags := map[string]string{}
	for _, args := range f.docker.commands {
		if args[0] != "run" {
			continue
		}
		for i, arg := range args {
			if (arg == "--user" || arg == "--group-add") && i+1 < len(args) {
				flags[arg] = args[i+1]
			}
		}
	}
	if flags["--user"] != fmt.Sprintf("%d:%d", uid, gid) || flags["--group-add"] != strconv.Itoa(socketGID) {
		t.Fatal("container identity did not follow operator file ownership and socket group")
	}
	for _, secret := range []string{"synthetic-plugin-token", "synthetic-gateway-token"} {
		if bytes.Contains(commands, []byte(secret)) {
			t.Fatal("token bytes entered Docker argv")
		}
	}
	for _, required := range []string{"TOKEN_FILE=/run/secrets/plugin-token", "GATEWAY_TOKEN_FILE=/run/secrets/gateway-token", "readonly", "--cap-drop", "ALL", "IMAGE_ALLOWLIST=[\\\"explicit/rules:test\\\"]"} {
		if !strings.Contains(string(commands), required) {
			t.Errorf("missing fixed Docker contract %q", required)
		}
	}
	data, err := os.ReadFile(f.m.statePath())
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(data, []byte("synthetic-plugin-token")) || bytes.Contains(data, []byte("synthetic-gateway-token")) {
		t.Fatal("token bytes entered saved installation state")
	}
	for _, path := range []string{f.m.statePath(), f.options.JournalDir} {
		info, err := os.Stat(path)
		if err != nil || info.Mode().Perm()&0077 != 0 {
			t.Errorf("nonprivate state/journal: %v", err)
		}
	}
	if err := f.m.Remove(context.Background()); err != nil {
		t.Fatal(err)
	}
	if f.docker.count("stop") != 1 || f.docker.count("rm") != 1 {
		t.Fatal("clean drained installation was not stopped and removed")
	}
	if _, err := os.Stat(f.m.statePath()); !os.IsNotExist(err) {
		t.Fatal("removed installation retained active state")
	}
	if _, err := os.Stat(f.options.JournalDir); err != nil {
		t.Fatal("removal purged persistent journal")
	}
	if _, err := os.Stat(f.options.TokenFile); err != nil {
		t.Fatal("removal changed operator secret file")
	}
}

func TestRemoveFencesAdmissionAndRefusesActiveOrUnverifiableLeases(t *testing.T) {
	for _, kind := range []string{"active", "missing-count", "auth-error"} {
		t.Run(kind, func(t *testing.T) {
			f := setup(t)
			f.install(t)
			f.mu.Lock()
			switch kind {
			case "active":
				f.active = 1
			case "missing-count":
				f.invalid = true
			case "auth-error":
				f.unauthorized = true
			}
			f.mu.Unlock()
			err := f.m.Remove(context.Background())
			if err == nil {
				t.Fatal("unsafe removal was accepted")
			}
			if strings.Contains(err.Error(), "private-token") {
				t.Fatal("raw provider error escaped")
			}
			if f.docker.count("stop") != 0 || f.docker.count("rm") != 0 {
				t.Fatal("unsafe installation was stopped or removed")
			}
			if _, err := os.Stat(f.m.statePath()); err != nil {
				t.Fatal("failed removal discarded installation identity")
			}
			if kind == "active" {
				f.mu.Lock()
				draining := f.draining
				f.active = 0
				f.mu.Unlock()
				if !draining {
					t.Fatal("busy removal did not fence new leases")
				}
				if err := f.m.Remove(context.Background()); err != nil {
					t.Fatalf("drained retry failed: %v", err)
				}
			}
		})
	}
}

func TestForeignOrChangedEndpointIsRejectedBeforeSendingToken(t *testing.T) {
	for _, kind := range []string{"ownership", "port"} {
		t.Run(kind, func(t *testing.T) {
			f := setup(t)
			f.install(t)
			if kind == "ownership" {
				f.docker.foreign = true
			} else {
				f.docker.badPort = true
			}
			if err := f.m.Remove(context.Background()); err == nil {
				t.Fatal("changed installation accepted")
			}
			f.mu.Lock()
			drains := f.drains
			f.mu.Unlock()
			if drains != 0 || f.docker.count("stop") != 0 || f.docker.count("rm") != 0 {
				t.Fatal("unverified container received privileged action")
			}
		})
	}
}

func TestRemoveRejectsUncleanStopAndPreservesJournal(t *testing.T) {
	f := setup(t)
	f.install(t)
	f.docker.exitCode = 137
	f.docker.oom = true
	if err := f.m.Remove(context.Background()); err == nil {
		t.Fatal("forced/OOM shutdown accepted")
	}
	if f.docker.count("rm") != 0 {
		t.Fatal("unclean container removed")
	}
	if _, err := os.Stat(f.options.JournalDir); err != nil {
		t.Fatal("journal lost")
	}
}

func TestDrainedUvicornSIGTERMExit143IsAccepted(t *testing.T) {
	f := setup(t)
	f.install(t)
	f.docker.exitCode = 143
	if err := f.m.Remove(context.Background()); err != nil {
		t.Fatalf("verified drained SIGTERM shutdown rejected: %v", err)
	}
	if f.docker.count("rm") != 1 {
		t.Fatal("drained Uvicorn container was not removed")
	}
}

func TestExitedStartupRetainsIdentityAndReportsFailure(t *testing.T) {
	f := setup(t)
	f.docker.exitOnStart = true
	status, err := f.m.Install(context.Background(), f.options)
	if err == nil || status == nil || !status.Installed || status.Running {
		t.Fatal("an exited plugin was reported as a successful running installation")
	}
	if _, err := os.Stat(f.m.statePath()); err != nil {
		t.Fatal("startup failure discarded saved identity")
	}
	if f.docker.count("rm") != 0 {
		t.Fatal("startup failure removed an unverifiable plugin")
	}
}

func TestRemoveRetainsStateWhenPluginRestartsAfterEmptyDrain(t *testing.T) {
	for _, phase := range []string{"after-drain-started-at", "after-drain-restart-count", "during-stop"} {
		t.Run(phase, func(t *testing.T) {
			f := setup(t)
			f.install(t)
			if phase == "during-stop" {
				f.docker.restartDuringStop = true
			} else {
				f.docker.restartAtInspect = f.docker.inspects + 2
				f.docker.changeStartedAtOnly = phase == "after-drain-started-at"
				f.docker.changeRestartCountOnly = phase == "after-drain-restart-count"
			}
			if err := f.m.Remove(context.Background()); err == nil {
				t.Fatal("a restarted plugin reused an earlier process's empty drain result")
			}
			if f.docker.count("rm") != 0 {
				t.Fatal("restart race removed the plugin container")
			}
			if phase != "during-stop" && f.docker.count("stop") != 0 {
				t.Fatal("post-drain generation change was not fenced before stop")
			}
			for _, path := range []string{f.m.statePath(), f.options.JournalDir, filepath.Join(f.options.JournalDir, ".aiv-plugin-owner.json")} {
				if _, err := os.Stat(path); err != nil {
					t.Fatal("restart race discarded installation or journal ownership")
				}
			}
		})
	}
}

func TestStatusAndLogsUseExactSavedContainerAndInstallNeverReplaces(t *testing.T) {
	f := setup(t)
	f.install(t)
	runCount := f.docker.count("run")
	if _, err := f.m.Install(context.Background(), f.options); err == nil {
		t.Fatal("existing installation replaced")
	}
	if f.docker.count("run") != runCount {
		t.Fatal("reinstall started another container")
	}
	status, err := f.m.Status(context.Background())
	if err != nil || status.ContainerID != fakeID {
		t.Fatal("status did not use saved identity")
	}
	var logs bytes.Buffer
	if err := f.m.Logs(context.Background(), &logs, 20, false); err != nil {
		t.Fatal(err)
	}
	last := f.docker.commands[len(f.docker.commands)-1]
	if strings.Join(last, " ") != "logs --tail 20 "+fakeID {
		t.Fatalf("wrong logs selector %q", last)
	}
	if logs.String() != "safe plugin log\n" {
		t.Fatal("logs were not streamed")
	}
}

func TestRequiredOperatorInputsAndOwnerOnlyTokenFile(t *testing.T) {
	for _, kind := range []string{"image", "gateway", "workload", "token", "policy", "socket", "journal", "world-readable"} {
		t.Run(kind, func(t *testing.T) {
			f := setup(t)
			o := f.options
			switch kind {
			case "image":
				o.Image = ""
			case "gateway":
				o.Gateway = "https://user:secret@gateway"
			case "workload":
				o.WorkloadImage = ""
			case "token":
				o.TokenFile = ""
			case "policy":
				o.PolicyFile = ""
			case "socket":
				o.DockerSocket = ""
			case "journal":
				o.JournalDir = ""
			case "world-readable":
				if err := os.Chmod(o.TokenFile, 0644); err != nil {
					t.Fatal(err)
				}
			}
			if _, err := f.m.Install(context.Background(), o); err == nil {
				t.Fatal("invalid operator input accepted")
			}
			if len(f.docker.commands) != 0 {
				t.Fatal("invalid input reached Docker")
			}
		})
	}
}

func TestHealthRedirectDoesNotSendTokenToAnotherEndpoint(t *testing.T) {
	f := setup(t)
	f.install(t)
	requests := 0
	receiver := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { requests++; w.WriteHeader(200) }))
	defer receiver.Close()
	redirect := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, receiver.URL, http.StatusTemporaryRedirect)
	}))
	defer redirect.Close()
	_, port, _ := net.SplitHostPort(strings.TrimPrefix(redirect.URL, "http://"))
	number, _ := strconv.Atoi(port)
	state, err := f.m.load()
	if err != nil {
		t.Fatal(err)
	}
	state.Port = number
	if _, err := f.m.health(context.Background(), state, false); err == nil {
		t.Fatal("redirect accepted as verified health")
	}
	if requests != 0 {
		t.Fatal("credential request followed redirect")
	}
}

func TestTwoInstallationsCannotShareOnePersistentJournal(t *testing.T) {
	f := setup(t)
	f.install(t)
	other := New(filepath.Join(filepath.Dir(f.m.StateDir), "second-state"))
	other.Docker = &fakeDocker{}
	if _, err := other.Install(context.Background(), f.options); err == nil {
		t.Fatal("a second installation shared an active journal")
	}
	if other.Docker.(*fakeDocker).count("run") != 0 {
		t.Fatal("second plugin process started before journal ownership was verified")
	}
}

func TestNativeDockerDoesNotReturnUntrustedOutputInFailure(t *testing.T) {
	path := filepath.Join(t.TempDir(), "docker-fixture")
	if err := os.WriteFile(path, []byte("#!/bin/sh\nprintf 'synthetic-private-token'\nprintf 'synthetic-private-token' >&2\nexit 1\n"), 0700); err != nil {
		t.Fatal(err)
	}
	data, err := (NativeDocker{Bin: path}).Run(context.Background(), "run")
	if err == nil || bytes.Contains(data, []byte("synthetic-private-token")) || strings.Contains(err.Error(), "synthetic-private-token") {
		t.Fatal("Docker failure exposed untrusted output")
	}
}
