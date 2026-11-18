package sandboxbootstrap

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/antgroup/ai-vision/aivision-cli/internal/sandboxplugin"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/antgroup/ai-vision/aivision-cli/internal/installprogress"
)

func TestSetupRefusesWrongHostBeforeCreatingFilesOrPulling(t *testing.T) {
	docker := hostFixture(t)
	docker.backend["Id"] = strings.Repeat("f", 64)
	docker.backend["Config"].(map[string]any)["Hostname"] = "ffffffffffff"
	plan := validPlan(t)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		if strings.HasSuffix(req.URL.Path, "/bootstrap") {
			json.NewEncoder(w).Encode(map[string]any{"code": 0, "data": map[string]any{"plan": plan}})
			return
		}
		if strings.HasSuffix(req.URL.Path, "/enroll") {
			t.Error("wrong host attempted enrollment")
		}
		var batch struct {
			Events []struct {
				Seq int `json:"seq"`
			} `json:"events"`
		}
		json.NewDecoder(req.Body).Decode(&batch)
		json.NewEncoder(w).Encode(map[string]any{"code": 0, "data": map[string]any{"last_seq": batch.Events[len(batch.Events)-1].Seq}})
	}))
	defer server.Close()
	base := server.URL + "/api/public/sandbox-installations/01234567-89ab-cdef-0123-456789abcdef"
	client, err := NewClient(base, "private-bootstrap", "private-enrollment")
	if err != nil {
		t.Fatal(err)
	}
	reporter, err := installprogress.New(base+"/events", "private-progress", "bootstrap", io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	defer reporter.Close()
	root := filepath.Join(t.TempDir(), "untouched")
	if err = Setup(context.Background(), client, reporter, docker, root, io.Discard); err == nil {
		t.Fatal("wrong host setup succeeded")
	}
	if _, err = os.Stat(root); !os.IsNotExist(err) {
		t.Fatal("created private files before target proof")
	}
	for _, cmd := range docker.commands {
		if cmd[0] == "run" || cmd[0] == "pull" || cmd[0] == "start" {
			t.Fatalf("mutated wrong daemon: %v", cmd)
		}
	}
}
func TestGatewayConfigurationPinsImagesTLSAndHostSupervisorCallback(t *testing.T) {
	plan := validPlan(t)
	state := &ownedState{Dir: "/private/owned", GatewayPort: 29999}
	images := plan.Catalog.Images["linux/arm64"]
	config := string(state.gatewayConfig(plan, images))
	for _, expected := range []string{`supervisor_network_mode = "host"`, `grpc_endpoint = "https://127.0.0.1:29999"`, `disable_tls = false`, `enable_bind_mounts = false`, images.Worker, images.Supervisor, images.Runtime} {
		if !strings.Contains(config, expected) {
			t.Fatalf("gateway config missing %q", expected)
		}
	}
	if strings.Contains(config, "allow_unauthenticated_users") || strings.Contains(config, "guest_tls_key") || strings.Contains(config, ":latest") {
		t.Fatal("unsafe gateway defaults")
	}
	args := gatewayArgs(plan, state, images, 1234)
	joined := strings.Join(args, " ")
	for _, expected := range []string{"127.0.0.1:29999:8080", "--cap-drop ALL", "--user ", "--group-add 1234", "--config /private/owned/gateway.toml"} {
		if !strings.Contains(joined, expected) {
			t.Fatalf("gateway argv missing %q: %s", expected, joined)
		}
	}
	if strings.Contains(joined, "--privileged") || strings.Contains(joined, "0.0.0.0:29999") {
		t.Fatal("gateway widened host exposure")
	}
}

func TestGatewayReadbackRejectsChangedUserCommandAndSocketGroup(t *testing.T) {
	plan := validPlan(t)
	state := &ownedState{Dir: "/private/owned", GatewayPort: 29999, SocketGID: 1234, Host: Host{Socket: "/run/host-docker.sock", NetworkID: strings.Repeat("d", 64)}}
	images := plan.Catalog.Images["linux/arm64"]
	data := "/private/owned/gateway-state"
	configPath := "/private/owned/gateway.toml"
	object := map[string]any{"Id": strings.Repeat("a", 64), "Config": map[string]any{"Image": images.Gateway, "User": fmt.Sprintf("%d:%d", os.Getuid(), os.Getgid()), "Cmd": []string{"--config", configPath}, "Labels": map[string]string{gatewayOwnerLabel: plan.Target.DeploymentKey, gatewayPlanLabel: plan.PlanID, "io.aivision.sandbox.role": "gateway"}, "Env": []string{"HOME=" + data, "XDG_DATA_HOME=" + data, "OPENSHELL_LOCAL_TLS_DIR=" + data + "/tls", "OPENSHELL_ENABLE_MTLS_AUTH=true", "OPENSHELL_DB_URL=sqlite:" + data + "/gateway.db?mode=rwc"}}, "HostConfig": map[string]any{"GroupAdd": []string{"1234"}, "CapDrop": []string{"ALL"}, "SecurityOpt": []string{"no-new-privileges=true"}, "PortBindings": map[string]any{"8080/tcp": []map[string]string{{"HostIp": "127.0.0.1", "HostPort": "29999"}}}}, "NetworkSettings": map[string]any{"Networks": map[string]any{"stack": map[string]any{"NetworkID": state.Host.NetworkID, "Aliases": []string{plan.Target.GatewayName}}}}, "Mounts": []map[string]any{{"Source": data, "Destination": data, "RW": true}, {"Source": state.Host.Socket, "Destination": "/run/docker.sock", "RW": true}, {"Source": configPath, "Destination": configPath, "RW": false}}}
	decode := func() container {
		raw, _ := json.Marshal(object)
		var resource container
		json.Unmarshal(raw, &resource)
		return resource
	}
	if err := verifyGateway(decode(), plan, state, images); err != nil {
		t.Fatal(err)
	}
	config := object["Config"].(map[string]any)
	for _, change := range []string{"user", "command", "group", "tls"} {
		original := decode()
		switch change {
		case "user":
			original.Config.User = "0:0"
			if os.Getuid() == 0 {
				original.Config.User = "999:999"
			}
		case "command":
			original.Config.Cmd = []string{"--disable-tls"}
		case "group":
			original.HostConfig.GroupAdd = []string{"666"}
		case "tls":
			original.Config.Env = append(original.Config.Env, "OPENSHELL_DISABLE_TLS=true")
		}
		if err := verifyGateway(original, plan, state, images); err == nil {
			t.Fatalf("changed gateway %s accepted; config=%v", change, config)
		}
	}
}

func TestSetupBlocksUnsupportedDockerBeforePrivateStateAndImageAcquisition(t *testing.T) {
	docker := hostFixture(t)
	docker.info["ServerVersion"] = "26.1.3"
	plan := validPlan(t)
	var mu sync.Mutex
	var captured *sandboxplugin.InstallationDiagnostic
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		if strings.HasSuffix(req.URL.Path, "/bootstrap") {
			json.NewEncoder(w).Encode(map[string]any{"code": 0, "data": map[string]any{"plan": plan}})
			return
		}
		var batch struct {
			Events []struct {
				Seq        int                                   `json:"seq"`
				Diagnostic *sandboxplugin.InstallationDiagnostic `json:"diagnostic"`
			} `json:"events"`
		}
		json.NewDecoder(req.Body).Decode(&batch)
		for _, event := range batch.Events {
			if event.Diagnostic != nil {
				mu.Lock()
				captured = event.Diagnostic
				mu.Unlock()
			}
		}
		json.NewEncoder(w).Encode(map[string]any{"code": 0, "data": map[string]any{"last_seq": batch.Events[len(batch.Events)-1].Seq}})
	}))
	defer server.Close()
	base := server.URL + "/api/public/sandbox-installations/01234567-89ab-cdef-0123-456789abcdef"
	client, _ := NewClient(base, "bootstrap-token", "enrollment-token")
	reporter, _ := installprogress.New(base+"/events", "progress-token", "bootstrap", io.Discard)
	root := filepath.Join(t.TempDir(), "untouched")
	err := Setup(context.Background(), client, reporter, docker, root, io.Discard)
	reporter.Close()
	var typed *EnvironmentError
	if !errors.As(err, &typed) || typed.Diagnostic.Code != "docker_version_unsupported" {
		t.Fatalf("unexpected environment failure: %v", err)
	}
	if _, err := os.Stat(root); !os.IsNotExist(err) {
		t.Fatal("created private setup state on unsupported host")
	}
	for _, cmd := range docker.commands {
		if cmd[0] != "context" && cmd[0] != "info" && cmd[0] != "container" {
			t.Fatalf("mutated unsupported daemon: %v", cmd)
		}
	}
	mu.Lock()
	defer mu.Unlock()
	if captured == nil || captured.Code != "docker_version_unsupported" || captured.Actual != "26.1.3" || captured.Required != "28.0" {
		t.Fatalf("missing actionable progress: %#v", captured)
	}
}

func TestEnvironmentFailureWinsOverUnreadyAndCannotRegister(t *testing.T) {
	for _, tt := range []struct {
		name        string
		ready       bool
		environment string
		code        string
		enrolls     int
		notReady    bool
	}{
		{"both unready and failed environment", false, "failed", "landlock_unavailable", 0, false},
		{"ready but failed environment", true, "failed", "landlock_unavailable", 0, false},
		{"legacy unready", false, "", "provider_not_ready", 0, true},
		{"qualified but unready", false, "passed", "provider_not_ready", 0, true},
		{"legacy ready lacks qualification", true, "", "environment_probe_failed", 0, false},
		{"qualified ready", true, "passed", "", 1, false},
	} {
		t.Run(tt.name, func(t *testing.T) {
			var mu sync.Mutex
			enrolls := 0
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
				if strings.HasSuffix(req.URL.Path, "/enroll") {
					mu.Lock()
					enrolls++
					mu.Unlock()
					io.WriteString(w, `{"code":0,"data":{"registration_state":"saved","connection_uuid":"0123456789abcdef0123456789abcdef"}}`)
					return
				}
				var batch struct {
					Events []struct {
						Seq int `json:"seq"`
					} `json:"events"`
				}
				json.NewDecoder(req.Body).Decode(&batch)
				json.NewEncoder(w).Encode(map[string]any{"code": 0, "data": map[string]any{"last_seq": batch.Events[len(batch.Events)-1].Seq}})
			}))
			defer server.Close()
			base := server.URL + "/api/public/sandbox-installations/01234567-89ab-cdef-0123-456789abcdef"
			client, err := NewClient(base, "bootstrap-token", "enrollment-token")
			if err != nil {
				t.Fatal(err)
			}
			reporter, err := installprogress.New(base+"/events", "progress-token", "bootstrap", io.Discard)
			if err != nil {
				t.Fatal(err)
			}
			p := newProgress(reporter, io.Discard)
			defer p.close()
			p.start("readiness")
			state := &ownedState{Dir: t.TempDir()}
			if err := os.WriteFile(filepath.Join(state.Dir, "plugin-token"), []byte("private-plugin-token"), 0600); err != nil {
				t.Fatal(err)
			}
			health := &sandboxplugin.Health{Ready: &tt.ready}
			if tt.environment != "" {
				abi := 4
				health.Environment = &sandboxplugin.EnvironmentHealth{Status: tt.environment, DockerVersion: "29.9.3", LandlockABI: &abi}
				if tt.environment == "failed" {
					health.Environment.Diagnostic = &sandboxplugin.InstallationDiagnostic{Code: "landlock_unavailable", Actual: "0", Required: "3"}
				}
			}
			err = finishRegistration(context.Background(), client, reporter, p, validPlan(t), Host{Platform: "linux/arm64"}, state, &sandboxplugin.Status{Installed: true, Health: health}, io.Discard)
			if tt.code == "" {
				if err != nil {
					t.Fatal(err)
				}
			} else if tt.notReady {
				if err == nil || p.active {
					t.Fatal("legacy unready did not finish not_ready")
				}
				var typed *EnvironmentError
				if errors.As(err, &typed) {
					t.Fatalf("legacy unready changed to environment failure: %v", err)
				}
			} else {
				var typed *EnvironmentError
				if !errors.As(err, &typed) || typed.Diagnostic.Code != tt.code {
					t.Fatalf("actionable environment error lost: %v", err)
				}
				if !p.active {
					t.Fatal("environment failure was downgraded to not_ready")
				}
			}
			reporter.Close()
			mu.Lock()
			defer mu.Unlock()
			if enrolls != tt.enrolls {
				t.Fatalf("enroll calls=%d want %d", enrolls, tt.enrolls)
			}
		})
	}
}
