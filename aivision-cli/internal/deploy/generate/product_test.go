package generate

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/blueprint"
	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/runtime"
	"gopkg.in/yaml.v3"
)

func TestProductComposeReadinessGateRejectsLiveButUnavailableStorage(t *testing.T) {
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("requires Python readiness-job execution")
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
	doc, files := productRender(t, productApp(t, "oceanbase", true, nil), Options{})
	endpoint, err := url.Parse(doc.Services["data-backend-ready"].Command[0])
	if err != nil {
		t.Fatal(err)
	}
	script := filepath.Join(t.TempDir(), "wait-http.py")
	for _, file := range files {
		if file.Path == "bootstrap/wait-http.py" {
			if err := os.WriteFile(script, file.Content, 0600); err != nil {
				t.Fatal(err)
			}
		}
	}
	run := func() error {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		// Execute the exported wait script with its actual generated endpoint
		// path, removing retry sleeps while keeping every HTTP status check.
		cmd := exec.CommandContext(ctx, python, "-c", `import runpy,sys,time,urllib.request
time.sleep=lambda _: None
urllib.request.urlopen=urllib.request.build_opener(urllib.request.ProxyHandler({})).open
script=sys.argv.pop(1)
runpy.run_path(script,run_name="__main__")`, script, server.URL+endpoint.Path)
		return cmd.Run()
	}
	if err := run(); err == nil {
		t.Fatal("Compose startup gate accepted HTTP liveness while storage readiness returned 503")
	}
	ready.Store(true)
	if err := run(); err != nil {
		t.Fatalf("Compose startup gate did not recover after storage became ready: %v", err)
	}
}

type productService struct {
	Image         string
	ContainerName string `yaml:"container_name"`
	Environment   map[string]string
	Entrypoint    []string
	Command       []string
	Ports         []string
	Volumes       []string
	Profiles      []string
	Labels        map[string]string
	Networks      any
	DependsOn     map[string]struct{ Condition string } `yaml:"depends_on"`
	Healthcheck   struct {
		Test    []string
		Timeout string
	}
}

type productDocument struct {
	Name     string
	Services map[string]productService
	Volumes  map[string]struct {
		Name   string
		Labels map[string]string
	}
	Networks map[string]struct {
		Name     string
		Internal bool
	}
	Identity struct {
		Backend  string
		Managed  bool
		StateDir string `yaml:"state_dir"`
	} `yaml:"x-aivision"`
}

func productApp(t *testing.T, backend string, managed bool, values map[string]string) *runtime.App {
	t.Helper()
	bp, err := blueprint.Load("../../../blueprints/aivision.yaml")
	if err != nil {
		t.Fatal(err)
	}
	bp.Metadata.Namespace = "product-test"
	bp.Config.Workdir = t.TempDir()
	bp.Config.Vars["OB_BACKEND"] = backend
	bp.Config.Vars["OB_MANAGED"] = fmt.Sprint(managed)
	bp.Config.Vars["AIVISION_NATIVE_TRACE_HISTORY_START"] = "2026-10-01T00:00:00Z"
	if !managed {
		bp.Config.Vars["OB_HOST"] = "external.example.test"
	}
	for key, value := range values {
		bp.Config.Vars[key] = value
	}
	app, err := runtime.NewApp(bp, runtime.NewLogger(false), false, false)
	if err != nil {
		t.Fatal(err)
	}
	return app
}

func productRender(t *testing.T, app *runtime.App, opts Options) (productDocument, []File) {
	t.Helper()
	files, _, err := Render(app, Compose, opts)
	if err != nil {
		t.Fatal(err)
	}
	var doc productDocument
	if len(files) == 0 || files[0].Path != "compose.yaml" {
		t.Fatal("missing product Compose entrypoint")
	}
	if err := yaml.Unmarshal(files[0].Content, &doc); err != nil {
		t.Fatal(err)
	}
	paths := map[string]bool{}
	for _, file := range files {
		paths[file.Path] = true
	}
	required := []string{"bootstrap/seekdb.sh", "bootstrap/oceanbase.sh"}
	if app.Vars["NATIVE_PYTHON_RUNTIME"] != "true" {
		required = append(required, "bootstrap/database-init.py", "bootstrap/backend-bootstrap.py", "bootstrap/wait-http.py")
	}
	for _, name := range required {
		if !paths[name] {
			t.Errorf("export lacks lifecycle dependency %s", name)
		}
	}
	if paths["compose.py"] || paths["bootstrap/compose-run.py"] {
		t.Error("Compose exports must use aiv and native Docker Compose without a host Python controller")
	}
	return doc, files
}

func TestNativeProductComposeOmitsSourcePrograms(t *testing.T) {
	for _, backend := range []string{"oceanbase", "seekdb"} {
		for _, managed := range []bool{true, false} {
			for _, parameterized := range []bool{true, false} {
				t.Run(fmt.Sprintf("%s/managed=%t/template=%t", backend, managed, parameterized), func(t *testing.T) {
					doc, files := productRender(t, productApp(t, backend, managed, map[string]string{
						"NATIVE_PYTHON_RUNTIME": "true", "BE_PORT": "18001", "AGENTLENS_PORT": "18081",
					}), Options{Parameterized: parameterized})
					for _, file := range files {
						if strings.HasSuffix(file.Path, ".py") || strings.Contains(string(file.Content), "python") || strings.Contains(string(file.Content), "manage.py") {
							t.Errorf("native export carries a Python program: %s", file.Path)
						}
					}
					for name, service := range doc.Services {
						for _, mount := range service.Volumes {
							if strings.Contains(mount, ":/opt/aivision/bootstrap:ro") || strings.Contains(mount, ".py:") {
								t.Errorf("%s mounts a source directory or program: %s", name, mount)
							}
						}
					}
					for name, helper := range map[string]string{"database-init": "database-init", "backend-bootstrap": "backend-bootstrap", "data-backend-ready": "wait-http"} {
						service := doc.Services[name]
						if !reflect.DeepEqual(service.Entrypoint, []string{"/app/" + helper}) {
							t.Errorf("%s did not directly select its native helper: %v", name, service.Entrypoint)
						}
						if strings.Contains(strings.Join(service.Volumes, " "), "bootstrap") {
							t.Errorf("%s mounts source bootstrap material", name)
						}
					}
					for _, name := range []string{"backend", "diagnosis"} {
						probe := doc.Services[name].Healthcheck.Test
						if len(probe) != 6 || probe[0] != "CMD" || probe[1] != "/usr/local/bin/aiv-http-probe" || probe[2] != "127.0.0.1" || probe[5] != "3" {
							t.Errorf("%s lacks the bounded native HTTP probe: %v", name, probe)
						}
						port := map[string]string{"backend": "18001", "diagnosis": "18081"}[name]
						if !strings.Contains(probe[3], port) {
							t.Errorf("%s native probe ignores configured port %s: %v", name, port, probe)
						}
					}
				})
			}
		}
	}
}

func TestProductComposeDatastoreMatrixAndDependencies(t *testing.T) {
	for _, backend := range []string{"oceanbase", "seekdb"} {
		for _, managed := range []bool{true, false} {
			t.Run(fmt.Sprintf("%s/managed=%v", backend, managed), func(t *testing.T) {
				doc, _ := productRender(t, productApp(t, backend, managed, nil), Options{})
				if doc.Identity.Backend != backend || doc.Identity.Managed != managed {
					t.Fatalf("wrong datastore identity: %+v", doc.Identity)
				}
				_, hasDatabase := doc.Services["database"]
				_, hasIsolatedInit := doc.Services["database-initialize"]
				if hasDatabase != managed || hasIsolatedInit != (managed && backend == "seekdb") {
					t.Fatalf("database=%v isolated-init=%v for %s/%v", hasDatabase, hasIsolatedInit, backend, managed)
				}
				for _, name := range []string{"frontend", "backend", "diagnosis", "ingest-gateway", "data-backend", "database-init", "data-backend-ready", "backend-bootstrap"} {
					if _, ok := doc.Services[name]; !ok {
						t.Errorf("missing service %s", name)
					}
				}
				for _, name := range []string{"be", "fe", "agentlens", "antcollector-gw", "signoz-ob", "oceanbase"} {
					if _, ok := doc.Services[name]; ok {
						t.Errorf("product exported legacy service ID %s", name)
					}
				}
				for name, service := range doc.Services {
					for dependency := range service.DependsOn {
						if _, ok := doc.Services[dependency]; !ok {
							t.Errorf("%s references absent dependency %s", name, dependency)
						}
					}
				}
				for _, edge := range [][3]string{
					{"data-backend", "database-init", "service_completed_successfully"},
					{"backend", "database-init", "service_completed_successfully"},
					{"backend", "data-backend-ready", "service_completed_successfully"},
					{"backend-bootstrap", "backend", "service_healthy"},
					{"diagnosis", "backend-bootstrap", "service_completed_successfully"},
					{"diagnosis", "ingest-gateway", "service_healthy"},
					{"frontend", "backend-bootstrap", "service_completed_successfully"},
				} {
					if got := doc.Services[edge[0]].DependsOn[edge[1]].Condition; got != edge[2] {
						t.Errorf("%s -> %s = %q, want %q", edge[0], edge[1], got, edge[2])
					}
				}
				if managed {
					if doc.Services["database-init"].DependsOn["database"].Condition != "service_healthy" {
						t.Error("database-init did not wait for authenticated database health")
					}
					database := doc.Services["database"]
					wantScript := "/opt/aivision/bootstrap/" + backend + ".sh"
					if strings.Join(database.Entrypoint, " ") != "/bin/sh "+wantScript ||
						!strings.Contains(strings.Join(database.Volumes, " "), ":/opt/aivision/bootstrap:ro") {
						t.Error("managed database must use the supplied lifecycle supervisor")
					}
				} else if len(doc.Services["database-init"].DependsOn) != 0 {
					t.Error("external database requires a missing managed service")
				}
				wantUser := "root@aivision"
				if backend == "seekdb" {
					wantUser = "root"
				}
				if got := doc.Services["database-init"].Environment["OB_USER"]; got != wantUser {
					t.Errorf("database user = %q, want %q", got, wantUser)
				}
				if managed && backend == "seekdb" {
					init := doc.Services["database-initialize"]
					if len(init.Ports) != 0 || !doc.Networks["initialization"].Internal || len(init.Profiles) != 1 || init.Profiles[0] != "bootstrap" {
						t.Error("seekdb initialization must stay isolated and explicitly requested")
					}
				}
			})
		}
	}
}

func TestProductComposePreservesStorageAndOrgBoundary(t *testing.T) {
	for _, backend := range []string{"oceanbase", "seekdb"} {
		doc, _ := productRender(t, productApp(t, backend, true, nil), Options{})
		physical := map[string]bool{}
		for _, volume := range doc.Volumes {
			if volume.Name == "" || strings.Contains(volume.Name, "${") {
				t.Errorf("volume does not have a resolved explicit physical identity: %+v", volume)
			}
			physical[volume.Name] = true
			if volume.Labels["io.aivision.product"] != "aivision" ||
				volume.Labels["io.aivision.namespace"] != doc.Name ||
				volume.Labels["io.aivision.state-dir"] != doc.Identity.StateDir {
				t.Error("persistent volume must identify its namespace and original state directory after down")
			}
		}
		want := "aivforge-ob-data-product-test"
		if backend == "seekdb" {
			want = "aivforge-seekdb-data-product-test"
		}
		if !physical[want] || !physical["aivforge-runtime-product-test"] {
			t.Errorf("%s lost existing volume identity: %v", backend, physical)
		}
		diagnosis := doc.Services["diagnosis"]
		if diagnosis.Environment["SIGNOZ_ORG_ID"] != "" || diagnosis.Environment["SIGNOZ_ORG_ID_FILE"] != "/run/aivision/org-id" {
			t.Error("diagnosis must read discovered identity, not a preconfigured org")
		}
		if !containsString(diagnosis.Volumes, "runtime:/run/aivision:ro") ||
			!containsString(doc.Services["backend-bootstrap"].Volumes, "runtime:/run/aivision") {
			t.Error("organization identity must flow from bootstrap to a read-only consumer")
		}
		for name, service := range doc.Services {
			for _, mount := range service.Volumes {
				if strings.HasPrefix(mount, "runtime:") && name != "diagnosis" && name != "backend-bootstrap" {
					t.Errorf("unexpected runtime identity volume consumer: %s", name)
				}
			}
		}
		gateway := doc.Services["ingest-gateway"].Healthcheck.Test
		if !reflect.DeepEqual(gateway, []string{"CMD", "/app/collector", "healthcheck", "--ready"}) {
			t.Errorf("gateway healthcheck assumes a shell in its minimal image: %v", gateway)
		}
		if doc.Services["ingest-gateway"].Healthcheck.Timeout != "8s" {
			t.Error("gateway probe budget must cover all three bounded readiness requests")
		}
		if !reflect.DeepEqual(doc.Services["data-backend"].Healthcheck.Test, []string{"CMD", "/app/signoz", "healthcheck", "--ready"}) ||
			doc.Services["data-backend-ready"].Entrypoint[0] != "/bin/sh" ||
			!strings.Contains(doc.Services["data-backend-ready"].Entrypoint[2], "/app/wait-http") {
			t.Error("data-backend must keep the initialization readiness Job and continuous native health probe")
		}
		if !reflect.DeepEqual(doc.Services["frontend"].Healthcheck.Test, []string{"CMD", "curl", "-fsS", "-o", "/dev/null", "http://127.0.0.1:5174/"}) {
			t.Errorf("frontend healthcheck must use the image curl, got %v", doc.Services["frontend"].Healthcheck.Test)
		}
	}
}

func containsString(values []string, want string) bool {
	for _, value := range values {
		if value == want {
			return true
		}
	}
	return false
}

func TestProductComposeCustomPortsAndLiteralDollarCredentials(t *testing.T) {
	secret := "literal-$TOKEN-${OTHER}"
	doc, _ := productRender(t, productApp(t, "oceanbase", true, map[string]string{
		"BE_PORT": "18001", "FE_PORT": "15174", "AGENTLENS_PORT": "18081",
		"GATEWAY_HTTP_PORT": "14318", "SECRET_KEY": secret,
	}), Options{})
	if got := doc.Services["backend"].Environment["SECRET_KEY"]; got != "literal-$$TOKEN-$${OTHER}" {
		t.Errorf("Compose would interpolate credential %q; want escaped literal dollars", got)
	}
	for service, port := range map[string]string{"backend": "18001", "frontend": "15174", "diagnosis": "18081"} {
		if !strings.Contains(strings.Join(doc.Services[service].Healthcheck.Test, " "), ":"+port+"/") {
			t.Errorf("%s healthcheck ignores custom port %s", service, port)
		}
	}
	if !containsString(doc.Services["ingest-gateway"].Ports, "127.0.0.1:14318:4318") {
		t.Error("gateway host override changed its fixed internal OTLP port")
	}
}

func TestProductTemplatesParseAndRequirePrivateConfiguration(t *testing.T) {
	for _, backend := range []string{"oceanbase", "seekdb"} {
		for _, managed := range []bool{true, false} {
			doc, files := productRender(t, productApp(t, backend, managed, nil), Options{Parameterized: true, BootstrapPath: "../../bootstrap"})
			if doc.Name != "${NAMESPACE:-aivision}" {
				t.Errorf("template baked in namespace: %s", doc.Name)
			}
			content := string(files[0].Content)
			for _, marker := range []string{"${AIV_STATE_DIR:?", "${OB_TENANT_PASSWORD:?", "${SECRET_KEY:?"} {
				if !strings.Contains(content, marker) {
					t.Errorf("%s/%v template does not require private %s", backend, managed, marker)
				}
			}
			if strings.Contains(content, "aivision-local-ob-change-me") || strings.Contains(content, "aivision-local-django-secret-change-me") {
				t.Error("repository template contains developer credentials")
			}
			if !managed && !strings.Contains(content, "${OB_HOST:?") {
				t.Error("external database template must require a reachable host")
			}
		}
	}
}

func TestProductComposeImageFallbacksKeepMirrorCompatibility(t *testing.T) {
	doc, _ := productRender(t, productApp(t, "oceanbase", true, nil), Options{Parameterized: true})
	const frontend = "${FRONTEND_IMAGE:-${FE_IMAGE:-${IMAGE_REGISTRY:-ghcr.io/antgroup/ai-vision/frontend}${IMAGE_REGISTRY:+/aivision-fe}:${IMAGE_TAG:-latest}}}"
	if got := doc.Services["frontend"].Image; got != frontend {
		t.Fatalf("frontend image template = %q, want %q", got, frontend)
	}
	const mirror = "registry.example.org/legacy"
	const pinned = "registry.example.org/custom/diagnosis:pinned"
	doc, _ = productRender(t, productApp(t, "oceanbase", true, map[string]string{
		"IMAGE_REGISTRY": mirror, "IMAGE_TAG": "dev", "AGENTLENS_IMAGE": pinned,
	}), Options{})
	if got := doc.Services["frontend"].Image; got != mirror+"/aivision-fe:dev" {
		t.Errorf("resolved frontend = %q, want historical mirror basename", got)
	}
	if got := doc.Services["diagnosis"].Image; got != pinned {
		t.Errorf("resolved diagnosis = %q, want pinned override %q", got, pinned)
	}
}

func TestSourceComposeKeepsGenericRendering(t *testing.T) {
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
	var doc struct {
		Services map[string]any
	}
	if err := yaml.Unmarshal(files[0].Content, &doc); err != nil {
		t.Fatal(err)
	}
	if _, ok := doc.Services["be"]; !ok {
		t.Fatal("source-development container IDs changed")
	}
	if _, ok := doc.Services["backend-bootstrap"]; ok || len(files) != 1 {
		t.Fatal("source-development hooks were replaced by product lifecycle")
	}
}
