package sandboxbootstrap

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/antgroup/ai-vision/aivision-cli/internal/sandboxplugin"
)

type hostDocker struct {
	info      map[string]any
	socket    string
	backend   map[string]any
	diagnoses []map[string]any
	commands  [][]string
}

func (d *hostDocker) Run(_ context.Context, args ...string) ([]byte, error) {
	d.commands = append(d.commands, append([]string{}, args...))
	switch strings.Join(args[:min(2, len(args))], " ") {
	case "context inspect":
		return json.Marshal("unix://" + d.socket)
	case "info --format":
		return json.Marshal(d.info)
	case "container ls":
		ids := []string{}
		for _, c := range d.diagnoses {
			ids = append(ids, c["Id"].(string))
		}
		return []byte(strings.Join(ids, "\n")), nil
	case "container inspect":
		id := args[len(args)-1]
		if id == "0123456789ab" {
			return json.Marshal(d.backend)
		}
		for _, c := range d.diagnoses {
			if c["Id"] == id {
				return json.Marshal(c)
			}
		}
	}
	return nil, fmt.Errorf("unexpected read-only command")
}
func (*hostDocker) Stream(context.Context, io.Writer, ...string) error                    { return nil }
func (*hostDocker) Pull(context.Context, string, func(sandboxplugin.ImageProgress)) error { return nil }
func hostFixture(t *testing.T) *hostDocker {
	t.Helper()
	t.Setenv("DOCKER_HOST", "")
	t.Setenv("DOCKER_CONTEXT", "")
	dir, err := os.MkdirTemp("", "aiv-host-test-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	dir, err = filepath.EvalSymlinks(dir)
	if err != nil {
		t.Fatal(err)
	}
	socket := filepath.Join(dir, "docker.sock")
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	docker := &hostDocker{info: map[string]any{"ID": "daemon-identity", "OSType": "linux", "Architecture": "aarch64", "ServerVersion": "28.0.1", "KernelVersion": "fixture-kernel"}}
	server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(docker.info)
	})}
	go server.Serve(listener)
	t.Cleanup(func() { server.Close() })
	network := map[string]any{"stack": map[string]any{"NetworkID": strings.Repeat("d", 64), "Aliases": []string{"agentlens"}}}
	backend := map[string]any{"Id": "0123456789ab" + strings.Repeat("0", 52), "Name": "/backend", "Config": map[string]any{"Hostname": "0123456789ab", "Labels": map[string]string{"io.aivision.product": "aivision", "io.aivision.component": "backend"}, "Env": []string{"AGENTLENS_BASE_URL=http://agentlens:8081", "SECRET_KEY=never-print-this"}}, "State": map[string]any{"Running": true}, "NetworkSettings": map[string]any{"Networks": network}}
	diagnosis := map[string]any{"Id": strings.Repeat("a", 64), "Name": "/agentlens", "Config": map[string]any{"Labels": map[string]string{"io.aivision.product": "aivision", "io.aivision.component": "diagnosis"}, "Env": []string{"AGENTLENS_PORT=8081"}}, "State": map[string]any{"Running": true}, "NetworkSettings": map[string]any{"Networks": network}}
	docker.socket, docker.backend, docker.diagnoses = socket, backend, []map[string]any{diagnosis}
	return docker
}
func TestDiscoverPinsActualDaemonTargetAndSharedNetwork(t *testing.T) {
	docker := hostFixture(t)
	host, err := Discover(context.Background(), docker, validPlan(t).Target)
	if err != nil || host.Platform != "linux/arm64" || host.NetworkName != "stack" || host.DaemonID != "daemon-identity" || host.Socket != docker.socket {
		t.Fatalf("actual host not selected: %#v %v", host, err)
	}
}
func TestDiscoverRefusesWrongHostRemoteContextAndAmbiguityBeforeMutation(t *testing.T) {
	for _, mode := range []string{"wrong-backend", "remote", "ambiguous", "wrong-diagnosis-url"} {
		t.Run(mode, func(t *testing.T) {
			docker := hostFixture(t)
			target := validPlan(t).Target
			switch mode {
			case "wrong-backend":
				docker.backend["Id"] = strings.Repeat("f", 64)
				docker.backend["Config"].(map[string]any)["Hostname"] = "ffffffffffff"
			case "remote":
				t.Setenv("DOCKER_HOST", "tcp://secret@remote:2375")
			case "ambiguous":
				second := map[string]any{}
				for k, v := range docker.diagnoses[0] {
					second[k] = v
				}
				second["Id"] = strings.Repeat("b", 64)
				docker.diagnoses = append(docker.diagnoses, second)
			case "wrong-diagnosis-url":
				target.DiagnosisURL = "http://unrelated:8081"
			}
			_, err := Discover(context.Background(), docker, target)
			if err == nil || strings.Contains(err.Error(), "secret") || strings.Contains(err.Error(), "never-print") {
				t.Fatalf("unsafe host accepted/leaked: %v", err)
			}
			for _, cmd := range docker.commands {
				if cmd[0] != "context" && cmd[0] != "info" && cmd[0] != "container" {
					t.Fatal("mutated before ownership proof")
				}
			}
		})
	}
}
