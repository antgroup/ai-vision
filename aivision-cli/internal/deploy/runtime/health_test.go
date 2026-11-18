package runtime

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/blueprint"
	"gopkg.in/yaml.v3"
)

func TestProductDataBackendProbeRejectsLiveButUnavailableStorage(t *testing.T) {
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("requires Python native-probe fixture")
	}
	var ready atomic.Bool
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/ready" && !ready.Load() {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	dir := t.TempDir()
	docker := filepath.Join(dir, "docker")
	script := `import os, sys, urllib.request
args = sys.argv[1:]
assert args[:1] == ["exec"] and args[2:4] == ["/app/signoz", "healthcheck"], args
assert args[4:] in ([], ["--ready"]), args
endpoint = "/api/v1/ready" if args[4:] == ["--ready"] else "/api/v1/health"
client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with client.open(os.environ["AIV_DATA_BACKEND_PROBE_URL"] + endpoint, timeout=2) as response:
    assert response.status == 200
`
	if err := os.WriteFile(docker, []byte("#!"+python+"\n"+script), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("DOCKER_BIN", docker)
	t.Setenv("AIV_DATA_BACKEND_PROBE_URL", server.URL)
	bp, err := blueprint.Load("../../../../deploy/blueprints/aivision.yaml")
	if err != nil {
		t.Fatal(err)
	}
	bp.Config.Workdir = filepath.Join(dir, "work")
	a, err := NewApp(bp, NewLogger(false), false, false)
	if err != nil {
		t.Fatal(err)
	}
	c := *bp.Component("signoz-ob")
	h := a.componentHealth(c, a.ComponentVars(c))
	if ok, _ := a.Log.Probe(context.Background(), h, 3*time.Second); ok {
		t.Fatal("product CLI accepted local liveness while data-backend storage readiness returned 503")
	}
	ready.Store(true)
	if ok, detail := a.Log.Probe(context.Background(), h, 3*time.Second); !ok {
		t.Fatalf("product CLI did not recover after storage became ready: %s", detail)
	}
}

// Replace only the external Docker executable: the actual deploy/status paths
// must execute the probe inside the resolved container with intact argv.
const nativeProbeDockerFixture = `#!/bin/sh
case "$1 $2" in
  'container inspect') echo "Error: No such container: $3" >&2; exit 1;;
  'network inspect'|'image inspect') exit 0;;
  'inspect -f') echo running; exit 0;;
  inspect*) exit 1;;
  run*) exit 0;;
  exec*)
    if [ "$AIV_NATIVE_PROBE_FAIL" = true ]; then
      echo 'native probe failed' >&2; exit 7
    fi
    test "$#" = 5 && test "$2" = gateway-custom &&
      test "$3" = /app/collector && test "$4" = healthcheck &&
      test "$5" = 'literal space; exit 9' || exit 42
    printf x >> "$AIV_NATIVE_PROBE_RECEIPT"
    exit 0;;
esac
exit 43
`

func TestCastAndStatusRunNativeProbeInsideResolvedContainer(t *testing.T) {
	dir := t.TempDir()
	docker := filepath.Join(dir, "docker fixture")
	if err := os.WriteFile(docker, []byte(nativeProbeDockerFixture), 0o755); err != nil {
		t.Fatal(err)
	}
	receipt := filepath.Join(dir, "probes")
	t.Setenv("DOCKER_BIN", docker)
	t.Setenv("AIV_NATIVE_PROBE_RECEIPT", receipt)
	var bp Blueprint
	if err := yaml.Unmarshal([]byte(`metadata:
  namespace: native-health
config:
  sourceRoot: /tmp/source
  vars:
    GATEWAY_NAME: gateway-custom
    PROBE_BINARY: /app/collector
components:
  - name: gateway
    kind: container
    containerName: ${GATEWAY_NAME}
    image: fixture
    health:
      execArgv: ["${PROBE_BINARY}", "healthcheck", "literal space; exit 9"]
      retries: 1
      interval: 1ms
`), &bp); err != nil {
		t.Fatal(err)
	}
	bp.Config.Workdir = filepath.Join(dir, "work")
	app, err := NewApp(&bp, NewLogger(false), false, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := app.Cast(false, false); err != nil {
		t.Fatal(err)
	}
	if err := app.Status(); err != nil {
		t.Fatal(err)
	}
	got, err := os.ReadFile(receipt)
	if err != nil || string(got) != "xx" {
		t.Fatalf("cast and status must each execute the native probe: got=%q err=%v", got, err)
	}
	t.Setenv("AIV_NATIVE_PROBE_FAIL", "true")
	if err := app.Cast(false, false); err == nil || !strings.Contains(err.Error(), "native probe failed") {
		t.Fatalf("cast must reject a nonzero native probe: %v", err)
	}
	// Capture the real status table so skipping a failing probe cannot be
	// reported as healthy, even though Status itself is a reporting command.
	statusLog, err := os.CreateTemp(dir, "status")
	if err != nil {
		t.Fatal(err)
	}
	defer statusLog.Close()
	stdout := os.Stdout
	os.Stdout = statusLog
	app.Log = NewLogger(false)
	os.Stdout = stdout
	if err := app.Status(); err != nil {
		t.Fatal(err)
	}
	output, err := os.ReadFile(statusLog.Name())
	if err != nil || !strings.Contains(string(output), "down") {
		t.Fatalf("status must show failed native probe as down: %s err=%v", output, err)
	}
}

func TestStatusRunsProcessNativeProbeOnHost(t *testing.T) {
	dir := t.TempDir()
	receipt := filepath.Join(dir, "process-probe")
	var h Health
	raw, err := yaml.Marshal(map[string]any{"execArgv": []string{"touch", "${PROBE_RECEIPT}"}})
	if err != nil {
		t.Fatal(err)
	}
	if err := yaml.Unmarshal(raw, &h); err != nil {
		t.Fatal(err)
	}
	bp := &Blueprint{
		Metadata: Metadata{Namespace: "process-health"},
		Config: Config{SourceRoot: dir, Workdir: filepath.Join(dir, "work"), Vars: map[string]string{
			"PROBE_RECEIPT": receipt,
		}},
		Components: []Component{{Name: "worker", Kind: "process", Run: "fixture", Health: &h}},
	}
	app, err := NewApp(bp, NewLogger(false), false, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := app.Status(); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(receipt); err != nil {
		t.Fatalf("process probe did not execute on the host: %v", err)
	}
}
