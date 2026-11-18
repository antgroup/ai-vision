package kubernetes

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	manifests "github.com/antgroup/ai-vision/aivision-cli/deploy/kubernetes"
	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/components"
)

// A small stateful API simulator checks lifecycle constraints which a canned
// sequence of kubectl responses would miss: credentials before admission,
// immutable Job templates, and bootstrap dependencies between applications.
type fakeCluster struct {
	objects      map[string]object
	calls        []string
	writes       []string
	failJob      string
	blockRollout string
	secretError  bool
}

func canonical(kind string) string {
	kind = strings.ToLower(strings.Split(kind, ".")[0])
	aliases := map[string]string{"namespaces": "namespace", "nodes": "node", "pods": "pod", "services": "service", "storageclasses": "storageclass", "deployments": "deployment", "secrets": "secret", "configmaps": "configmap", "jobs": "job", "persistentvolumeclaims": "persistentvolumeclaim", "crd": "customresourcedefinition", "customresourcedefinitions": "customresourcedefinition", "ingresses": "ingress", "ingressclasses": "ingressclass"}
	if v := aliases[kind]; v != "" {
		return v
	}
	return kind
}

func objectKey(kind, ns, name string) string { return canonical(kind) + "/" + ns + "/" + name }
func (f *fakeCluster) put(obj object) {
	b, _ := json.Marshal(obj)
	var copy object
	json.Unmarshal(b, &copy)
	f.objects[objectKey(str(copy, "kind"), str(copy, "metadata", "namespace"), str(copy, "metadata", "name"))] = copy
}
func (f *fakeCluster) obj(kind, ns, name string) object { return f.objects[objectKey(kind, ns, name)] }
func fixture(kind, ns, name string) object {
	return object{"apiVersion": "v1", "kind": kind, "metadata": map[string]any{"name": name, "namespace": ns}}
}
func newCluster() *fakeCluster {
	f := &fakeCluster{objects: map[string]object{}}
	f.put(object{"kind": "Namespace", "metadata": map[string]any{"name": "kube-system", "uid": "cluster-one"}})
	f.put(object{"kind": "Node", "metadata": map[string]any{"name": "worker", "labels": map[string]string{"kubernetes.io/arch": "arm64", "kubernetes.io/os": "linux"}}, "status": map[string]any{"allocatable": map[string]string{"cpu": "8", "memory": "32Gi"}, "conditions": []any{map[string]string{"type": "Ready", "status": "True"}}}})
	f.put(object{"kind": "StorageClass", "metadata": map[string]any{"name": "local-path", "annotations": map[string]string{"storageclass.kubernetes.io/is-default-class": "true"}}, "provisioner": "rancher.io/local-path"})
	return f
}

func (f *fakeCluster) Run(ctx context.Context, input []byte, args ...string) ([]byte, error) {
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}
	f.calls = append(f.calls, strings.Join(args, " "))
	ns := ""
	for len(args) > 0 && strings.HasPrefix(args[0], "--") {
		if args[0] == "--context" || args[0] == "--namespace" {
			if args[0] == "--namespace" {
				ns = args[1]
			}
			args = args[2:]
		} else {
			args = args[1:]
		}
	}
	if len(args) == 0 {
		return nil, errors.New("missing command")
	}
	switch args[0] {
	case "config":
		return []byte("test-context"), nil
	case "auth":
		return []byte("yes\n"), nil
	case "wait":
		return nil, nil
	case "rollout":
		if strings.Contains(strings.Join(args, " "), f.blockRollout) && f.blockRollout != "" {
			<-ctx.Done()
			return nil, ctx.Err()
		}
		return nil, nil
	case "exec":
		if f.obj("Job", ns, "aivision-bootstrap") == nil {
			return nil, errors.New("integration not initialized")
		}
		return []byte("org-123\n"), nil
	case "logs":
		return []byte("image pull denied: unauthorized\n"), nil
	case "delete":
		if canonical(args[1]) != "job" {
			return nil, fmt.Errorf("unexpected destructive action: %v", args)
		}
		delete(f.objects, objectKey(args[1], ns, args[2]))
		f.writes = append(f.writes, "delete/"+args[2])
		return nil, nil
	case "get":
		if args[1] == "--raw=/version" {
			return []byte(`{"gitVersion":"v1.35.1"}`), nil
		}
		kind := canonical(args[1])
		if strings.Contains(kind, ",") || kind == "events" {
			return []byte("No warnings\n"), nil
		}
		if len(args) > 2 && !strings.HasPrefix(args[2], "-") {
			obj := f.obj(kind, ns, args[2])
			if obj == nil {
				return nil, nil
			}
			return json.Marshal(obj)
		}
		all := strings.Contains(strings.Join(args, " "), "--all-namespaces")
		items := []object{}
		for _, obj := range f.objects {
			if canonical(str(obj, "kind")) == kind && (all || str(obj, "metadata", "namespace") == ns) {
				items = append(items, obj)
			}
		}
		return json.Marshal(object{"items": items})
	case "apply", "create":
		docs, err := decodeDocuments(input)
		if err != nil {
			return nil, err
		}
		if len(docs) == 1 && str(docs[0], "kind") == "List" {
			var payload struct{ Items []object }
			if err := json.Unmarshal(input, &payload); err != nil {
				return nil, err
			}
			docs = payload.Items
		}
		dryRun := strings.Contains(strings.Join(args, " "), "--dry-run=server")
		for _, obj := range docs {
			kind, name, ns := str(obj, "kind"), str(obj, "metadata", "name"), str(obj, "metadata", "namespace")
			if kind == "Secret" && f.secretError {
				return nil, fmt.Errorf("invalid Secret: %s", input)
			}
			if kind == "OBCluster" {
				for _, field := range []string{"root", "operator", "monitor", "proxyro"} {
					if f.obj("Secret", ns, str(obj, "spec", "userSecrets", field)) == nil {
						return nil, fmt.Errorf("admission: %s credential missing", field)
					}
				}
				obj["status"] = map[string]any{"status": "running"}
				if !dryRun {
					f.put(object{"kind": "Pod", "metadata": map[string]any{"name": "oceanbase-0", "namespace": ns, "labels": map[string]string{"ref-obcluster": "oceanbase"}}, "status": map[string]any{"phase": "Running", "conditions": []any{map[string]string{"type": "Ready", "status": "True"}}}})
				}
			}
			if dryRun {
				continue
			}
			if kind == "OBTenant" {
				obj["status"] = map[string]any{"status": "running"}
			}
			if kind == "Job" {
				if f.obj("Job", ns, name) != nil {
					return nil, errors.New("Job template is immutable")
				}
				if name == "aivision-database-init" && f.obj("OBTenant", ns, "aivision") == nil {
					return nil, errors.New("tenant missing")
				}
				if name == "aivision-bootstrap" && f.obj("Deployment", ns, "aivision-be") == nil {
					return nil, errors.New("backend missing")
				}
				condition := map[string]string{"type": "Complete", "status": "True"}
				if name == f.failJob {
					condition = map[string]string{"type": "Failed", "status": "True", "reason": "DeadlineExceeded"}
				}
				obj["status"] = map[string]any{"conditions": []any{condition}}
			}
			if kind == "Deployment" {
				if name == "signoz-ob" && f.obj("Job", ns, "aivision-database-init") == nil {
					return nil, errors.New("databases missing")
				}
				if name == "agentlens" && f.obj("ConfigMap", ns, "aivision-runtime") == nil {
					return nil, errors.New("organization missing")
				}
				obj["status"] = map[string]any{"observedGeneration": 1, "updatedReplicas": 1, "availableReplicas": 1}
				obj["metadata"].(map[string]any)["generation"] = 1
			}
			f.put(obj)
			f.writes = append(f.writes, kind+"/"+name)
		}
		return []byte("applied"), nil
	}
	return nil, fmt.Errorf("unhandled kubectl: %v", args)
}

func (f *fakeCluster) Stream(ctx context.Context, w io.Writer, args ...string) error {
	b, err := f.Run(ctx, nil, args...)
	if err == nil {
		_, err = w.Write(b)
	}
	return err
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (fn roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return fn(r) }

func testApp(t *testing.T, f *fakeCluster) (*App, *bytes.Buffer) {
	t.Helper()
	t.Setenv("AIV_GHCR_USERNAME", "")
	t.Setenv("AIV_GHCR_TOKEN", "")
	out := &bytes.Buffer{}
	a, err := New(context.Background(), Options{Runner: f, Out: out, StateRoot: t.TempDir(), Overrides: map[string]string{"IMAGE_AUTH": "anonymous"}})
	if err != nil {
		t.Fatal(err)
	}
	a.pollInterval = time.Millisecond
	a.probeClient = &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
		status := http.StatusOK
		if strings.HasPrefix(r.URL.Path, "/v1/") {
			status = http.StatusUnauthorized
		}
		return &http.Response{StatusCode: status, Body: io.NopCloser(strings.NewReader(`{"status":"ok"}`))}, nil
	})}
	return a, out
}

func TestInstalledReleaseMetadataReachesBackendConfig(t *testing.T) {
	for _, metadata := range []string{"", "eyJwcm9kdWN0X3ZlcnNpb24iOiIwLjEuMCJ9"} {
		t.Run(metadata, func(t *testing.T) {
			a, err := New(context.Background(), Options{Runner: newCluster(), StateRoot: t.TempDir(),
				Overrides: map[string]string{"AIVISION_RELEASE_MANIFEST_BASE64": metadata}})
			if err != nil {
				t.Fatal(err)
			}
			data, err := a.render("config.yaml")
			if err != nil {
				t.Fatal(err)
			}
			documents, err := decodeDocuments(data)
			if err != nil {
				t.Fatal(err)
			}
			if got := str(documents[0], "data", "AIVISION_RELEASE_MANIFEST_BASE64"); got != metadata {
				t.Fatalf("release metadata = %q, want %q", got, metadata)
			}
		})
	}
}

func TestCastAndRepeatPreserveCredentialsAndData(t *testing.T) {
	f := newCluster()
	a, out := testApp(t, f)
	if err := a.Cast(context.Background()); err != nil {
		t.Fatalf("cast: %v\n%s", err, out.String())
	}
	ns := a.Config.Namespace
	before := f.obj("Secret", ns, "aivision-secrets")["data"]
	pvc := f.obj("PersistentVolumeClaim", ns, "signoz-metastore")
	rollout := str(f.obj("Deployment", ns, "aivision-fe"), "spec", "template", "metadata", "annotations", "aivision.ob-labs.io/rollout")
	if rollout == "" {
		t.Fatal("mutable image tag did not trigger rollout")
	}
	if err := a.Cast(context.Background()); err != nil {
		t.Fatalf("repeat: %v\n%s", err, out.String())
	}
	if !reflect.DeepEqual(before, f.obj("Secret", ns, "aivision-secrets")["data"]) {
		t.Fatal("credentials rotated")
	}
	if !reflect.DeepEqual(pvc, f.obj("PersistentVolumeClaim", ns, "signoz-metastore")) {
		t.Fatal("persistent volume changed")
	}
	if str(f.obj("Deployment", ns, "aivision-fe"), "spec", "template", "metadata", "annotations", "aivision.ob-labs.io/rollout") == rollout {
		t.Fatal("same-tag redeployment did not restart apps")
	}
	for _, job := range []string{"aivision-database-init", "aivision-bootstrap"} {
		if !strings.Contains(strings.Join(f.writes, " "), "delete/"+job) {
			t.Fatalf("Job %s was not recreated", job)
		}
	}
	if str(f.obj("ConfigMap", ns, "aivision-runtime"), "data", "SIGNOZ_ORG_ID") != "org-123" {
		t.Fatal("organization not discovered")
	}
	if !strings.Contains(out.String(), "Deployment ready.") {
		t.Fatal("missing completion")
	}
	saved, err := os.ReadFile(a.stateFile)
	if err != nil {
		t.Fatal(err)
	}
	for _, value := range a.redactions {
		if strings.Contains(string(saved), value) || strings.Contains(out.String(), value) {
			t.Fatal("credential leaked to state/logs")
		}
	}
	if err := a.Status(context.Background()); err != nil {
		t.Fatal(err)
	}
	// Deleting the master Secret must never mint new database credentials.
	delete(f.objects, objectKey("Secret", ns, "aivision-secrets"))
	if err := a.ensureSecrets(context.Background()); err == nil || !strings.Contains(err.Error(), "restore") {
		t.Fatalf("missing database credentials accepted: %v", err)
	}
}

func TestBootstrapFailureRetainsResourcesAndCanResume(t *testing.T) {
	f := newCluster()
	f.failJob = "aivision-bootstrap"
	a, out := testApp(t, f)
	err := a.Cast(context.Background())
	if err == nil || !strings.Contains(err.Error(), "Agent market") {
		t.Fatalf("expected bootstrap failure: %v", err)
	}
	if f.obj("Deployment", a.Config.Namespace, "agentlens") != nil || strings.Contains(out.String(), "Deployment ready.") {
		t.Fatal("failed deployment reported ready")
	}
	if f.obj("OBCluster", a.Config.Namespace, "oceanbase") == nil {
		t.Fatal("database was removed")
	}
	secrets := f.obj("Secret", a.Config.Namespace, "aivision-secrets")["data"]
	f.failJob = ""
	if err := a.Cast(context.Background()); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(secrets, f.obj("Secret", a.Config.Namespace, "aivision-secrets")["data"]) {
		t.Fatal("retry replaced credentials")
	}
}

func TestStatusRequiresReadyDatabasePodAndTenant(t *testing.T) {
	f := newCluster()
	a, out := testApp(t, f)
	if err := a.Cast(context.Background()); err != nil {
		t.Fatal(err)
	}
	pod := f.obj("Pod", a.Config.Namespace, "oceanbase-0")
	for _, scenario := range []string{"not ready", "terminating", "missing", "tenant not ready"} {
		t.Run(scenario, func(t *testing.T) {
			f.put(pod)
			current := f.obj("Pod", a.Config.Namespace, "oceanbase-0")
			switch scenario {
			case "not ready":
				current["status"].(map[string]any)["conditions"] = []any{}
			case "terminating":
				current["metadata"].(map[string]any)["deletionTimestamp"] = "2026-09-22T00:00:00Z"
			case "missing":
				delete(f.objects, objectKey("Pod", a.Config.Namespace, "oceanbase-0"))
			case "tenant not ready":
				f.obj("OBTenant", a.Config.Namespace, "aivision")["status"] = map[string]any{"status": "creating"}
			}
			out.Reset()
			if err := a.Status(context.Background()); err == nil || !strings.Contains(out.String(), "database\tnot ready") {
				t.Fatalf("unavailable database reported healthy: %v\n%s", err, out.String())
			}
		})
	}
}

func TestWaitFailureAndImagePullDiagnostics(t *testing.T) {
	for _, mode := range []string{"timeout", "pull"} {
		t.Run(mode, func(t *testing.T) {
			f := newCluster()
			a, out := testApp(t, f)
			if mode == "timeout" {
				f.blockRollout = "deployment/signoz-ob"
				a.Config.Values["TIMEOUT"] = "1s"
			} else {
				f.failJob = "aivision-database-init"
			}
			pod := fixture("Pod", a.Config.Namespace, "failed-init")
			pod["metadata"].(map[string]any)["labels"] = map[string]string{"app.kubernetes.io/part-of": "aivision"}
			f.put(pod)
			err := a.Cast(context.Background())
			if err == nil || strings.Contains(out.String(), "Deployment ready.") {
				t.Fatal("failed cast succeeded")
			}
			if !strings.Contains(out.String(), "Diagnostics") || !strings.Contains(out.String(), "unauthorized") {
				t.Fatalf("missing diagnostics: %s", out.String())
			}
			if mode == "timeout" && !strings.Contains(err.Error(), "deadline exceeded") {
				t.Fatalf("missing timeout: %v", err)
			}
		})
	}
}

func TestPullCredentialsAndSecretErrorsAreNotLogged(t *testing.T) {
	f := newCluster()
	a, out := testApp(t, f)
	a.Config.Values["IMAGE_AUTH"] = "auto"
	t.Setenv("AIV_GHCR_USERNAME", "tester")
	t.Setenv("AIV_GHCR_TOKEN", "very-private-token")
	if err := a.ensurePullSecret(context.Background()); err != nil {
		t.Fatal(err)
	}
	if a.Config.Values["IMAGE_PULL_SECRET"] != "aivision-registry" {
		t.Fatal("pull secret not selected")
	}
	t.Setenv("AIV_GHCR_USERNAME", "")
	t.Setenv("AIV_GHCR_TOKEN", "")
	a.credentials = func() (string, string, error) { t.Fatal("credentials should be reused"); return "", "", nil }
	if err := a.ensurePullSecret(context.Background()); err != nil {
		t.Fatal(err)
	}
	a.Config.Values["IMAGE_AUTH"] = "anonymous"
	if err := a.ensurePullSecret(context.Background()); err != nil || a.Config.Values["IMAGE_PULL_SECRET"] != "" {
		t.Fatalf("switching to public images kept a saved pull secret: %v", err)
	}
	f.secretError = true
	err := a.applyObject(context.Background(), secretObject(a.Config.Namespace, "example", "Opaque", map[string]string{"password": "do-not-echo-this"}), true)
	if err == nil || strings.Contains(err.Error(), "do-not-echo-this") || strings.Contains(out.String(), "very-private-token") {
		t.Fatalf("secret error not protected: %v", err)
	}
}

func TestPublicServiceImagesNeedNoImplicitRegistryCredentials(t *testing.T) {
	a, _ := testApp(t, newCluster())
	a.Config.Values["IMAGE_AUTH"] = "auto"
	t.Setenv("AIV_GHCR_USERNAME", "")
	t.Setenv("AIV_GHCR_TOKEN", "")
	a.credentials = func() (string, string, error) {
		t.Fatal("the default public images must not invoke the GHCR credential helper")
		return "", "", nil
	}
	if err := a.ensurePullSecret(context.Background()); err != nil || a.Config.Values["IMAGE_PULL_SECRET"] != "" {
		t.Fatalf("public service images need anonymous pulls, got %v", err)
	}
	a.Config.Values["FE_IMAGE"] = "ghcr.io/example/private-frontend:dev"
	a.credentials = nil
	if err := a.ensurePullSecret(context.Background()); err == nil {
		t.Fatal("a private service override must still require pull credentials")
	}
	a.Config.Values["FE_IMAGE"] = ""
	a.Config.Values["INGRESS_IMAGE"] = "ghcr.io/example/private-ingress:dev"
	if err := a.ensurePullSecret(context.Background()); err == nil {
		t.Fatal("a custom managed ingress image must still require pull credentials")
	}
}

func TestSavedKubernetesRegistryKeepsMirrorImageNames(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.json")
	const mirror = "registry.example.org/legacy"
	const override = "registry.example.org/custom/diagnosis:pinned"
	saved := defaults()
	saved["IMAGE_REGISTRY"], saved["IMAGE_TAG"], saved["AGENTLENS_IMAGE"] = mirror, "dev", override
	data, err := json.Marshal(saved)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}

	check := func(registry string, overrides, images map[string]string) {
		t.Helper()
		values, err := loadConfig(path, false, overrides)
		if err != nil {
			t.Fatal(err)
		}
		a := App{Config: Config{Namespace: "aivision", Values: values}}
		resolved := a.templateData()
		if resolved["AGENTLENS_IMAGE"] != override {
			t.Fatalf("saved diagnosis override was lost: %q", resolved["AGENTLENS_IMAGE"])
		}
		for key, image := range images {
			want := registry + "/" + image + ":dev"
			if resolved[key] != want {
				t.Errorf("%s = %q, want %q", key, resolved[key], want)
			}
		}
	}
	check(mirror, nil, map[string]string{
		"FE_IMAGE": "aivision-fe", "BE_IMAGE": "aivision-be", "GATEWAY_IMAGE": "antcollector-gw", "SIGNOZ_IMAGE": "signoz-ob",
	})
	check(components.ProductImageRegistry, map[string]string{"IMAGE_REGISTRY": components.ProductImageRegistry}, map[string]string{
		"FE_IMAGE": "frontend", "BE_IMAGE": "backend", "GATEWAY_IMAGE": "ingest-gateway", "SIGNOZ_IMAGE": "data-backend",
	})
}

func TestConfigurationIsolationAndValidation(t *testing.T) {
	if statePath("/state", "cluster-a", "one") == statePath("/state", "cluster-b", "one") || statePath("/state", "cluster-a", "one") == statePath("/state", "cluster-a", "two") {
		t.Fatal("state collision")
	}
	for key, value := range map[string]string{"UNKNOWN": "value", "SECRET_KEY": "secret", "PUBLIC_PORT": "0", "INGRESS_HTTP_NODE_PORT": "5174", "INGRESS_HTTPS_NODE_PORT": "32768", "PUBLIC_SCHEME": "ftp", "INGRESS_CONTROLLER": "invalid", "INGRESS_CLASS": "", "PUBLIC_HOST": "http://localhost:5000", "IMAGE_TAG": "tag with space", "TIMEOUT": "0"} {
		values := defaults()
		values[key] = value
		if err := (Config{Namespace: "aivision", Values: values}).validate(); err == nil {
			t.Errorf("accepted invalid %s", key)
		}
	}
	if err := checkVersion("v1.32.0"); err == nil {
		t.Fatal("unsupported version accepted")
	}
	for _, version := range []string{"v1.33.1+k3s1", "v1.36.0"} {
		if err := checkVersion(version); err != nil {
			t.Fatal(err)
		}
	}
	f := newCluster()
	a, _ := testApp(t, f)
	a.Config.Values["IMAGE_TAG"] = "test-saved"
	if err := a.saveConfig(); err != nil {
		t.Fatal(err)
	}
	values, err := loadConfig(a.stateFile, false, map[string]string{"IMAGE_TAG": "flag"})
	if err != nil || values["IMAGE_TAG"] != "flag" {
		t.Fatal("precedence failed")
	}
	values, err = loadConfig(a.stateFile, true, nil)
	if err != nil || values["IMAGE_TAG"] != "latest" {
		t.Fatal("reset failed")
	}
	values = defaults()
	values["INGRESS_HTTP_NODE_PORT"] = "30443"
	if err := (Config{Namespace: "aivision", Values: values}).validate(); err == nil {
		t.Fatal("duplicate controller NodePorts accepted")
	}
}

func TestPreflightIsReadOnlyAndDetectsConflicts(t *testing.T) {
	f := newCluster()
	a, _ := testApp(t, f)
	if err := a.Gauge(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(f.writes) != 0 {
		t.Fatal("gauge mutated cluster")
	}
	if _, err := os.Stat(a.stateFile); !os.IsNotExist(err) {
		t.Fatal("gauge persisted state")
	}
	svc := fixture("Service", "unrelated", "web")
	svc["spec"] = map[string]any{"ports": []any{map[string]any{"nodePort": 30080}}}
	f.put(svc)
	if err := a.checkPorts(context.Background()); err == nil {
		t.Fatal("NodePort conflict accepted")
	}
	delete(f.objects, objectKey("StorageClass", "", "local-path"))
	a.Config.Values["STORAGE_CLASS"] = ""
	if err := a.checkStorage(context.Background()); err == nil {
		t.Fatal("missing StorageClass accepted")
	}
	node := f.obj("Node", "", "worker")
	node["status"].(map[string]any)["allocatable"] = map[string]string{"cpu": "1", "memory": "4Gi"}
	if err := a.checkNodes(context.Background()); err == nil {
		t.Fatal("undersized node accepted")
	}
}

func TestHostPortMayAlreadyBePublishedByLocalCluster(t *testing.T) {
	// Local cluster runtimes reserve host ports before Kubernetes Services
	// exist. That listener is the route into the cluster, not a conflict.
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	_, port, _ := net.SplitHostPort(listener.Addr().String())
	f := newCluster()
	a, _ := testApp(t, f)
	a.Config.Values["PUBLIC_PORT"] = port
	if err := a.checkPorts(context.Background()); err != nil {
		t.Fatalf("existing host forwarding must not block deployment: %v", err)
	}
}

func TestReuseCompatibleExternalDependencies(t *testing.T) {
	if !compatibleImage("quay.io/jetstack/cert-manager-controller:v1.21.2@sha256:abcd", "cert-manager-controller") || compatibleImage("quay.io/jetstack/cert-manager-controller:v1.21.2-rc1", "cert-manager-controller") {
		t.Fatal("dependency image version detection failed")
	}
	f := newCluster()
	a, _ := testApp(t, f)
	for _, dep := range dependencyDefaults() {
		b, _ := manifests.Files.ReadFile(dep.file)
		docs, err := decodeDocuments(b)
		if err != nil {
			t.Fatal(err)
		}
		for _, doc := range docs {
			if str(doc, "kind") != "CustomResourceDefinition" && str(doc, "kind") != "Deployment" {
				continue
			}
			if str(doc, "kind") == "Deployment" {
				doc["metadata"].(map[string]any)["namespace"] = "external-" + dep.name
			}
			f.put(doc)
		}
	}
	if err := a.inspectDependencies(context.Background()); err != nil {
		t.Fatal(err)
	}
	for _, dep := range a.dependencies {
		if dep.install {
			t.Fatalf("would overwrite external %s", dep.name)
		}
	}
	dep := f.obj("Deployment", "external-ob-operator", "oceanbase-controller-manager")
	list(dep, "spec", "template", "spec", "containers")[0].(map[string]any)["image"] = "quay.io/oceanbase/ob-operator:2.2.0"
	if err := a.inspectDependencies(context.Background()); err == nil {
		t.Fatal("incompatible operator accepted")
	}
}

func TestHostHealthRequiresBackendJSON(t *testing.T) {
	a, _ := testApp(t, newCluster())
	a.probeClient = &http.Client{Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader("<html>SPA fallback</html>"))}, nil
	})}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	if err := a.checkAccess(ctx); err == nil {
		t.Fatal("SPA fallback was accepted as backend health")
	}
}

func TestEmbeddedManifests(t *testing.T) {
	a, _ := testApp(t, newCluster())
	a.Config.Values["STORAGE_CLASS"] = "local-path"
	a.orgID = "test-org"
	files, err := fs.Glob(manifests.Files, "*.yaml")
	if err != nil {
		t.Fatal(err)
	}
	resources := map[string]object{}
	for _, file := range files {
		b, err := a.render(file)
		if err != nil {
			t.Fatal(err)
		}
		if bytes.Contains(b, []byte("hostPath:")) || bytes.Contains(b, []byte("{{")) {
			t.Fatalf("non-portable/unresolved manifest %s", file)
		}
		docs, err := decodeDocuments(b)
		if err != nil {
			t.Fatal(err)
		}
		for _, doc := range docs {
			if str(doc, "apiVersion") == "" || str(doc, "metadata", "name") == "" {
				t.Fatalf("invalid resource in %s", file)
			}
			resources[str(doc, "kind")+"/"+str(doc, "metadata", "name")] = doc
		}
		// Optional artifact used by the independent schema validation check.
		if dir := os.Getenv("AIV_K8S_RENDER_DIR"); dir != "" {
			if err := os.WriteFile(filepath.Join(dir, file), b, 0600); err != nil {
				t.Fatal(err)
			}
		}
	}
	if str(resources["OBCluster/oceanbase"], "metadata", "annotations", "oceanbase.oceanbase.com/mode") != "service" {
		t.Fatal("unstable database addressing")
	}
	account := str(resources["OBCluster/oceanbase"], "spec", "serviceAccount")
	if at(resources["ServiceAccount/"+account], "automountServiceAccountToken") != true {
		t.Fatal("OceanBase entrypoint requires in-cluster Kubernetes credentials")
	}
	if str(resources["Deployment/signoz-ob"], "spec", "strategy", "type") != "Recreate" {
		t.Fatal("SQLite metastore must have one writer")
	}
	backend := resources["ConfigMap/aivision-backend"]
	if str(backend, "data", "AIVISION_PUBLIC_BASE_URL") != "http://127.0.0.1:5174" || str(backend, "data", "CSRF_TRUSTED_ORIGINS") != "http://127.0.0.1:5174" || str(backend, "data", "AIVISION_OTLP_INGEST_ENDPOINT") != "http://127.0.0.1:5174" {
		t.Fatal("web and OTLP must share the Ingress origin")
	}
	for name, expected := range map[string][2]int{"aivision-fe": {5174, 0}, "antcollector-gw": {4318, 0}} {
		svc := resources["Service/"+name]
		if str(svc, "spec", "type") != "ClusterIP" || integer(list(svc, "spec", "ports")[0], "nodePort") != expected[1] {
			t.Fatal("application Services must not expose NodePorts")
		}
		podSpec := at(resources["Deployment/"+name], "spec", "template", "spec")
		port := list(list(podSpec, "containers")[0], "ports")[0]
		if integer(port, "hostPort") != 0 || integer(port, "containerPort") != expected[0] || at(podSpec, "affinity") != nil {
			t.Fatal("public endpoints must not use host ports or node pinning")
		}
	}
	for _, component := range []string{"signoz-ob", "antcollector-gw", "aivision-be", "agentlens", "aivision-fe"} {
		deployment := resources["Deployment/"+component]
		if deployment == nil {
			t.Fatalf("missing %s", component)
		}
		if str(deployment, "spec", "template", "spec", "serviceAccountName") != "aivision" {
			t.Fatal("image pull credentials not inherited")
		}
		if at(deployment, "spec", "template", "spec", "automountServiceAccountToken") != false {
			t.Fatal("applications do not need the database's Kubernetes token")
		}
	}
}
