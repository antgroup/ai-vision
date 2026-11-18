package generate

import (
	"bytes"
	"io"
	"reflect"
	"testing"

	"github.com/antgroup/ai-vision/aivision-cli/internal/blueprint"
	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/runtime"
	"gopkg.in/yaml.v3"
)

func nativeHealthApp(t *testing.T) *runtime.App {
	t.Helper()
	var bp runtime.Blueprint
	if err := yaml.Unmarshal([]byte(`metadata:
  namespace: native-health
config:
  sourceRoot: /tmp/source
  vars:
    PROBE_BINARY: /app/collector
components:
  - name: gateway
    kind: container
    image: fixture
    health:
      execArgv: ["${PROBE_BINARY}", "healthcheck", "literal space; exit 9", "$$AIV_NATIVE_REVIEW_LITERAL"]
      interval: 2s
      timeout: 3s
      retries: 7
`), &bp); err != nil {
		t.Fatal(err)
	}
	bp.Config.Workdir = t.TempDir()
	app, err := runtime.NewApp(&bp, runtime.NewLogger(false), false, false)
	if err != nil {
		t.Fatal(err)
	}
	return app
}

// A scratch container cannot execute CMD-SHELL, and joining argv would change
// the meaning of arguments containing spaces or shell metacharacters.
func TestComposeNativeHealthcheckRunsLiteralArgvWithoutShell(t *testing.T) {
	files, _, err := Render(nativeHealthApp(t), Compose, Options{})
	if err != nil {
		t.Fatal(err)
	}
	var compose struct {
		Services map[string]struct {
			Healthcheck struct {
				Test     []string
				Interval string
				Timeout  string
				Retries  int
			}
		}
	}
	if err := yaml.Unmarshal(files[0].Content, &compose); err != nil {
		t.Fatal(err)
	}
	h := compose.Services["gateway"].Healthcheck
	want := []string{"CMD", "/app/collector", "healthcheck", "literal space; exit 9", "$$AIV_NATIVE_REVIEW_LITERAL"}
	if !reflect.DeepEqual(h.Test, want) {
		t.Fatalf("generated Compose healthcheck = %q, want %q", h.Test, want)
	}
	if h.Interval != "2s" || h.Timeout != "3s" || h.Retries != 7 {
		t.Fatalf("probe timing lost: %+v", h)
	}
}

// Exec probes require no declared network port and must stay shell-free in the
// generated workload just as they do in Compose.
func TestKubernetesNativeHealthcheckNeedsNoPublishedPort(t *testing.T) {
	files, _, err := Render(nativeHealthApp(t), Kubernetes, Options{})
	if err != nil {
		t.Fatal(err)
	}
	for _, file := range files {
		if file.Path != "gateway.yaml" {
			continue
		}
		decoder := yaml.NewDecoder(bytes.NewReader(file.Content))
		for {
			var doc struct {
				Kind string
				Spec struct {
					Template struct {
						Spec struct {
							Containers []struct {
								ReadinessProbe struct {
									Exec           struct{ Command []string }
									TimeoutSeconds int `yaml:"timeoutSeconds"`
								} `yaml:"readinessProbe"`
							}
						}
					}
				}
			}
			if err := decoder.Decode(&doc); err == io.EOF {
				break
			} else if err != nil {
				t.Fatal(err)
			}
			if doc.Kind == "Deployment" {
				got := doc.Spec.Template.Spec.Containers[0].ReadinessProbe.Exec.Command
				want := []string{"/app/collector", "healthcheck", "literal space; exit 9", "$AIV_NATIVE_REVIEW_LITERAL"}
				if !reflect.DeepEqual(got, want) {
					t.Fatalf("generated readiness exec = %q, want %q", got, want)
				}
				if got := doc.Spec.Template.Spec.Containers[0].ReadinessProbe.TimeoutSeconds; got != 3 {
					t.Fatalf("generated readiness timeoutSeconds = %d, want 3", got)
				}
				return
			}
		}
	}
	t.Fatal("gateway Deployment not generated")
}

func TestKubernetesNativeHealthcheckTimeoutRoundsUpToWholeSeconds(t *testing.T) {
	for _, tc := range []struct {
		name    string
		timeout string
		want    int
	}{
		{"default", "", 3},
		{"fraction", "2100ms", 3},
		{"subsecond", "500ms", 1},
		{"zero", "0s", 1},
		{"negative", "-1s", 1},
		{"invalid", "invalid", 3},
	} {
		t.Run(tc.name, func(t *testing.T) {
			app := nativeHealthApp(t)
			app.BP.Components[0].Health.Timeout = tc.timeout
			files, _, err := Render(app, Kubernetes, Options{})
			if err != nil {
				t.Fatal(err)
			}
			for _, file := range files {
				if file.Path != "gateway.yaml" {
					continue
				}
				decoder := yaml.NewDecoder(bytes.NewReader(file.Content))
				for {
					var doc map[string]any
					if err := decoder.Decode(&doc); err == io.EOF {
						break
					} else if err != nil {
						t.Fatal(err)
					}
					if doc["kind"] != "Deployment" {
						continue
					}
					spec := doc["spec"].(map[string]any)["template"].(map[string]any)["spec"].(map[string]any)
					probe := spec["containers"].([]any)[0].(map[string]any)["readinessProbe"].(map[string]any)
					if got := probe["timeoutSeconds"]; got != tc.want {
						t.Fatalf("timeout %q generated timeoutSeconds = %v, want %d", tc.timeout, got, tc.want)
					}
					return
				}
			}
			t.Fatal("gateway Deployment not generated")
		})
	}
}

func TestComposeLegacyHealthchecksKeepShellSemantics(t *testing.T) {
	for _, tc := range []struct {
		name string
		yaml string
		want []string
	}{
		{"exec", "exec: printf ok | grep ok", []string{"CMD-SHELL", "printf ok | grep ok"}},
		{"http", "http: http://127.0.0.1:8080/health", []string{"CMD-SHELL", "curl -fsS http://127.0.0.1:8080/health >/dev/null 2>&1 || exit 1"}},
		{"tcp", "tcp: 127.0.0.1:18080", []string{"CMD-SHELL", "bash -c 'exec 3<>/dev/tcp/127.0.0.1/8080' 2>/dev/null || exit 1"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var health runtime.Health
			if err := yaml.Unmarshal([]byte(tc.yaml), &health); err != nil {
				t.Fatal(err)
			}
			var doc struct{ Healthcheck struct{ Test []string } }
			if err := yaml.Unmarshal([]byte(composeHealthcheck(Resolved{Health: &health, Ports: []string{"18080:8080"}})), &doc); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(doc.Healthcheck.Test, tc.want) {
				t.Fatalf("legacy probe = %q, want %q", doc.Healthcheck.Test, tc.want)
			}
		})
	}
}

func TestSourceBlueprintKeepsGatewayTCPProbeForOlderBranches(t *testing.T) {
	bp, err := blueprint.Load("../../../blueprints/aivision-source.yaml")
	if err != nil {
		t.Fatal(err)
	}
	bp.Config.Workdir = t.TempDir()
	app, err := runtime.NewApp(bp, runtime.NewLogger(false), false, false)
	if err != nil {
		t.Fatal(err)
	}
	files, _, err := Render(app, Compose, Options{})
	if err != nil {
		t.Fatal(err)
	}
	var compose struct {
		Services map[string]struct{ Healthcheck struct{ Test []string } }
	}
	if err := yaml.Unmarshal(files[0].Content, &compose); err != nil {
		t.Fatal(err)
	}
	got := compose.Services["antcollector-gw"].Healthcheck.Test
	want := []string{"CMD-SHELL", "bash -c 'exec 3<>/dev/tcp/127.0.0.1/4318' 2>/dev/null || exit 1"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("source gateway must retain legacy TCP probe: got %q, want %q", got, want)
	}
}

// The published host port is not the API port inside the scratch image.
// The native executable owns the loopback endpoint without curl or a shell.
func TestImageBlueprintNativeHealthUsesCanonicalProductServices(t *testing.T) {
	bp, err := blueprint.Load("../../../blueprints/aivision.yaml")
	if err != nil {
		t.Fatal(err)
	}
	bp.Config.Workdir = t.TempDir()
	bp.Config.Vars["SIGNOZ_PORT"] = "28091"
	app, err := runtime.NewApp(bp, runtime.NewLogger(false), false, false)
	if err != nil {
		t.Fatal(err)
	}
	files, _, err := Render(app, Compose, Options{})
	if err != nil {
		t.Fatal(err)
	}
	var compose struct {
		Services map[string]struct{ Healthcheck struct{ Test []string } }
	}
	if err := yaml.Unmarshal(files[0].Content, &compose); err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		legacy, role string
		argv         []string
	}{
		{"signoz-ob", "data-backend", []string{"/app/signoz", "healthcheck", "--ready"}},
		{"antcollector-gw", "ingest-gateway", []string{"/app/collector", "healthcheck", "--ready"}},
	} {
		t.Run(tc.role, func(t *testing.T) {
			got := compose.Services[tc.role].Healthcheck.Test
			want := append([]string{"CMD"}, tc.argv...)
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("%s scratch healthcheck = %q, want %q", tc.role, got, want)
			}
			for _, component := range app.BP.Components {
				if component.Name != tc.legacy {
					continue
				}
				if component.Health == nil || !reflect.DeepEqual(component.Health.ExecArgv, tc.argv) {
					t.Fatalf("%s CLI blueprint must use the same container-local argv as product Compose", tc.role)
				}
				if tc.role == "ingest-gateway" && component.Health.Timeout != "8s" {
					t.Fatal("CLI cast and status must allow the complete readiness probe budget")
				}
				return
			}
			t.Fatalf("blueprint component %s missing", tc.legacy)
		})
	}
}

func TestSourceBlueprintKeepsSigNozTCPProbeForOlderBranches(t *testing.T) {
	bp, err := blueprint.Load("../../../blueprints/aivision-source.yaml")
	if err != nil {
		t.Fatal(err)
	}
	bp.Config.Workdir = t.TempDir()
	app, err := runtime.NewApp(bp, runtime.NewLogger(false), false, false)
	if err != nil {
		t.Fatal(err)
	}
	files, _, err := Render(app, Compose, Options{})
	if err != nil {
		t.Fatal(err)
	}
	var compose struct {
		Services map[string]struct{ Healthcheck struct{ Test []string } }
	}
	if err := yaml.Unmarshal(files[0].Content, &compose); err != nil {
		t.Fatal(err)
	}
	got := compose.Services["signoz-ob"].Healthcheck.Test
	want := []string{"CMD-SHELL", "bash -c 'exec 3<>/dev/tcp/127.0.0.1/8080' 2>/dev/null || exit 1"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("source SigNoz must retain legacy TCP probe: got %q, want %q", got, want)
	}
}

// The CE blueprint reuses its host-side docker exec command for health.
// Compose already executes this command inside the named service container.
func TestComposeExecHealthRemovesOnlyOwnContainerDockerExecPrefix(t *testing.T) {
	for _, tc := range []struct{ name, container, input, want string }{
		{"bare", "ce-task", "docker exec ce-task obclient -e 'SELECT 1'", "obclient -e 'SELECT 1'"},
		{"quoted", "ce-task", `docker exec 'ce-task' /bin/sh -ec 'MYSQL_PWD="$ROOT_PASSWORD" exec mysql "$@"' sh -e 'SELECT 1'`, `/bin/sh -ec 'MYSQL_PWD="$ROOT_PASSWORD" exec mysql "$@"' sh -e 'SELECT 1'`},
		{"foreign", "ce-task", "docker exec other-task obclient -e 'SELECT 1'", "docker exec other-task obclient -e 'SELECT 1'"},
		{"name-prefix", "ce-task", "docker exec ce-task-other obclient -e 'SELECT 1'", "docker exec ce-task-other obclient -e 'SELECT 1'"},
		{"host-command", "ce-task", "printf 'SELECT 1' | custom-client", "printf 'SELECT 1' | custom-client"},
		{"no-container-name", "", "docker exec ce-task obclient -e 'SELECT 1'", "docker exec ce-task obclient -e 'SELECT 1'"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var doc struct{ Healthcheck struct{ Test []string } }
			rendered := composeHealthcheck(Resolved{ContainerName: tc.container, Health: &runtime.Health{Exec: tc.input}})
			if err := yaml.Unmarshal([]byte(rendered), &doc); err != nil {
				t.Fatal(err)
			}
			want := []string{"CMD-SHELL", tc.want}
			if !reflect.DeepEqual(doc.Healthcheck.Test, want) {
				t.Fatalf("probe = %q, want %q", doc.Healthcheck.Test, want)
			}
		})
	}
}
