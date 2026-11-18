package kubernetes

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"github.com/antgroup/ai-vision/aivision-cli/internal/distribution"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"text/template"
	"time"

	manifests "github.com/antgroup/ai-vision/aivision-cli/deploy/kubernetes"
	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/components"
	"gopkg.in/yaml.v3"
)

type Options struct {
	Context, Namespace, StateRoot string
	Overrides                     map[string]string
	Reset                         bool
	Runner                        Runner
	Out                           io.Writer
	// Called only by cast when private image credentials have not been supplied.
	Credentials func() (username, token string, err error)
}

type App struct {
	Config                 Config
	Context                string
	stateFile              string
	runner                 Runner
	out                    io.Writer
	credentials            func() (string, string, error)
	redactions             []string
	rollout, orgID         string
	pollInterval           time.Duration
	probeClient            *http.Client
	dependencies           []dependency
	ingressClassController string
}

func New(ctx context.Context, opts Options) (*App, error) {
	if opts.Runner == nil {
		opts.Runner = Kubectl{}
	}
	if opts.Out == nil {
		opts.Out = os.Stdout
	}
	if opts.Namespace == "" {
		opts.Namespace = "aivision"
	}
	// Reject unsupported names before contacting the cluster. Validate coupled
	// values (scheme, TLS secret, ports) after merging the saved configuration.
	opts.Overrides = components.NormalizeVars(opts.Overrides)
	if distribution.NativeOnly {
		if mode, ok := opts.Overrides["NATIVE_PYTHON_RUNTIME"]; ok && mode != "true" {
			return nil, fmt.Errorf("native distribution requires NATIVE_PYTHON_RUNTIME=true")
		}
	}
	if err := validateOverrides(opts.Overrides); err != nil {
		return nil, err
	}
	if err := validateNamespace(opts.Namespace); err != nil {
		return nil, err
	}
	a := &App{Context: opts.Context, runner: opts.Runner, out: opts.Out, credentials: opts.Credentials, pollInterval: 2 * time.Second}
	if a.Context == "" {
		b, err := a.runner.Run(ctx, nil, "config", "current-context")
		if err != nil {
			return nil, fmt.Errorf("select Kubernetes context: %w", err)
		}
		a.Context = strings.TrimSpace(string(b))
	}
	if a.Context == "" {
		return nil, fmt.Errorf("no Kubernetes context selected; pass --context")
	}
	// A namespace UID identifies the actual cluster even when kubeconfig aliases
	// change, and changes when a local cluster is recreated.
	cluster, err := a.get(ctx, "", "namespace", "kube-system")
	if err != nil {
		return nil, fmt.Errorf("connect to context %s: %w", a.Context, err)
	}
	uid := str(cluster, "metadata", "uid")
	if uid == "" {
		return nil, fmt.Errorf("cannot identify Kubernetes cluster (kube-system UID is missing)")
	}
	root := opts.StateRoot
	if root == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return nil, err
		}
		root = filepath.Join(home, ".aivision", "deploy", "kubernetes")
	}
	a.stateFile = statePath(root, uid, opts.Namespace)
	values, err := loadConfig(a.stateFile, opts.Reset, opts.Overrides)
	if err != nil {
		return nil, err
	}
	a.Config = Config{Namespace: opts.Namespace, Values: values}
	if err = a.Config.validate(); err != nil {
		return nil, err
	}
	// Local endpoint checks must not be sent through a host HTTP proxy.
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	a.probeClient = &http.Client{Transport: transport, Timeout: 5 * time.Second,
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
	return a, nil
}

func (a *App) templateData() map[string]string {
	v := make(map[string]string)
	for k, val := range a.Config.Values {
		v[k] = val
	}
	registry := strings.TrimSuffix(v["IMAGE_REGISTRY"], "/")
	for key, component := range serviceImages {
		if v[key] == "" {
			v[key] = registry + "/" + component.ImageName(registry) + ":" + v["IMAGE_TAG"]
		}
	}
	v["Namespace"] = a.Config.Namespace
	v["WebURL"], v["OTLPURL"] = a.Config.webURL(), a.Config.otlpURL()
	v["IngressHost"] = ""
	if net.ParseIP(v["PUBLIC_HOST"]) == nil {
		v["IngressHost"] = v["PUBLIC_HOST"]
	}
	v["SecureCookies"] = "false"
	v["TraefikTLS"] = ""
	if v["PUBLIC_SCHEME"] == "https" {
		v["SecureCookies"] = "true"
		if a.managedIngress() || a.ingressClassController == "traefik.io/ingress-controller" {
			v["TraefikTLS"] = "true"
		}
	}
	host := v["PUBLIC_HOST"]
	if strings.Contains(host, ":") {
		host = "[" + host + "]"
	}
	v["AllowedHosts"] = "localhost,127.0.0.1,aivision-be," + host
	// Kubelet probes use Pod IPs as Host by default; manifests provide the
	// accepted Host explicitly, rather than weakening ALLOWED_HOSTS.
	// Ingress-only settings must not restart application Pods.
	appValues := make(map[string]string)
	for k, val := range a.Config.Values {
		if !strings.HasPrefix(k, "INGRESS_") {
			appValues[k] = val
		}
	}
	b, _ := json.Marshal(appValues)
	v["ConfigHash"] = fmt.Sprintf("%x", sha256.Sum256(b))
	v["Rollout"], v["OrgID"] = a.rollout, a.orgID
	return v
}

func (a *App) render(file string) ([]byte, error) {
	b, err := manifests.Files.ReadFile(file)
	if err != nil {
		return nil, err
	}
	t, err := template.New(file).Option("missingkey=error").Funcs(template.FuncMap{
		"memoryQuantity": func(value string) string {
			value = strings.TrimSuffix(strings.ToUpper(value), "B")
			value = strings.TrimSuffix(value, "I")
			if strings.ContainsAny(value[len(value)-1:], "KMGT") {
				return value + "i"
			}
			return value
		},
		"quote": func(v string) string { b, _ := json.Marshal(v); return string(b) },
	}).Parse(string(b))
	if err != nil {
		return nil, err
	}
	var out bytes.Buffer
	if err := t.Execute(&out, a.templateData()); err != nil {
		return nil, err
	}
	return out.Bytes(), nil
}

func decodeDocuments(b []byte) ([]object, error) {
	d := yaml.NewDecoder(bytes.NewReader(b))
	var result []object
	for {
		var obj map[string]any
		if err := d.Decode(&obj); err == io.EOF {
			break
		} else if err != nil {
			return nil, err
		}
		if len(obj) != 0 {
			result = append(result, object(obj))
		}
	}
	return result, nil
}

func markOwned(obj object) {
	m, _ := obj["metadata"].(map[string]any)
	if m == nil {
		m = map[string]any{}
		obj["metadata"] = m
	}
	labels, _ := m["labels"].(map[string]any)
	if labels == nil {
		labels = map[string]any{}
		m["labels"] = labels
	}
	labels[ownerKey] = ownerValue
}

func owned(obj object) bool { return str(obj, "metadata", "labels", ownerKey) == ownerValue }

func (a *App) applyObject(ctx context.Context, obj object, sensitive bool) error {
	markOwned(obj)
	b, err := json.Marshal(obj)
	if err != nil {
		return err
	}
	_, err = a.run(ctx, "", b, "apply", "--server-side", "--field-manager=aiv-kubernetes", "-f", "-")
	if err != nil && sensitive {
		// API validation errors can echo a Secret's data or stringData.
		return fmt.Errorf("could not apply Secret %s (response hidden to protect credentials)", str(obj, "metadata", "name"))
	}
	return err
}

func (a *App) applyFile(ctx context.Context, file string) error {
	b, err := a.render(file)
	if err != nil {
		return err
	}
	docs, err := decodeDocuments(b)
	if err != nil {
		return err
	}
	for _, obj := range docs {
		ns, name := str(obj, "metadata", "namespace"), str(obj, "metadata", "name")
		existing, err := a.get(ctx, ns, str(obj, "kind"), name)
		if err != nil {
			return err
		}
		if existing != nil && !owned(existing) && str(obj, "kind") != "Namespace" {
			return fmt.Errorf("%s/%s already exists and is not managed by aiv; use a separate namespace", str(obj, "kind"), name)
		}
		if err := a.applyObject(ctx, obj, false); err != nil {
			return err
		}
	}
	return nil
}

func (a *App) stage(ctx context.Context, name, ns string, fn func(context.Context) error) error {
	fmt.Fprintf(a.out, "==> %s\n", name)
	stageCtx, cancel := context.WithTimeout(ctx, a.Config.timeout())
	defer cancel()
	if err := fn(stageCtx); err != nil {
		if ctx.Err() == nil {
			a.diagnostics(ctx, ns)
		}
		return fmt.Errorf("%s: %w (resources retained; fix the cause and run cast again)", name, err)
	}
	return nil
}

func (a *App) Cast(ctx context.Context) error {
	if err := a.Gauge(ctx); err != nil {
		return err
	}
	ns := a.Config.Namespace
	if err := a.stage(ctx, "namespace and image credentials", ns, func(ctx context.Context) error {
		// Remember parameters even if a later stage fails. No credentials are saved.
		if err := a.saveConfig(); err != nil {
			return err
		}
		if err := a.ensureNamespace(ctx, ns); err != nil {
			return err
		}
		if err := a.ensurePullSecret(ctx); err != nil {
			return err
		}
		if err := a.saveConfig(); err != nil {
			return err
		}
		return a.applyFile(ctx, "namespace.yaml")
	}); err != nil {
		return err
	}
	if err := a.stage(ctx, "Ingress controller and routes", ns, a.ensureIngress); err != nil {
		return err
	}
	for _, dep := range a.dependencies {
		if err := a.stage(ctx, dep.name, dep.namespace, func(ctx context.Context) error { return a.ensureDependency(ctx, dep) }); err != nil {
			return err
		}
	}
	if err := a.stage(ctx, "database and credentials", ns, func(ctx context.Context) error {
		if err := a.ensureSecrets(ctx); err != nil {
			return err
		}
		if err := a.waitDatabaseAdmission(ctx); err != nil {
			return err
		}
		if err := a.applyFile(ctx, "database.yaml"); err != nil {
			return err
		}
		if err := a.waitStatus(ctx, "obcluster", "oceanbase", "running"); err != nil {
			return err
		}
		if err := a.applyFile(ctx, "tenant.yaml"); err != nil {
			return err
		}
		if err := a.waitStatus(ctx, "obtenant", "aivision", "running"); err != nil {
			return err
		}
		return a.runJob(ctx, "aivision-database-init", "database-init.yaml")
	}); err != nil {
		return err
	}
	if a.Config.Values["IMAGE_PULL"] == "Always" {
		a.rollout = time.Now().UTC().Format(time.RFC3339Nano)
	}
	if err := a.stage(ctx, "application configuration", ns, func(ctx context.Context) error {
		return a.applyFile(ctx, "config.yaml")
	}); err != nil {
		return err
	}
	// Each workload gets its own deadline; a slow data-backend startup must
	// not consume the backend's migration and startup allowance.
	for _, app := range []struct{ file, name string }{{"signoz.yaml", "signoz-ob"}, {"gateway.yaml", "antcollector-gw"}, {"backend.yaml", "aivision-be"}} {
		if err := a.stage(ctx, components.Canonical(app.name), ns, func(ctx context.Context) error {
			return a.deploy(ctx, app.file, app.name)
		}); err != nil {
			return err
		}
	}
	if err := a.stage(ctx, "Agent market and data-backend integration", ns, func(ctx context.Context) error {
		if err := a.runJob(ctx, "aivision-bootstrap", "bootstrap.yaml"); err != nil {
			return err
		}
		b, err := a.run(ctx, ns, nil, a.backendManageArgs("signoz_integration_status", "--print-org", "--require")...)
		if err != nil {
			return err
		}
		a.orgID = strings.TrimSpace(string(b))
		if len(a.orgID) > 128 || !regexpOrg.MatchString(a.orgID) {
			return fmt.Errorf("backend did not return a valid data-backend organization ID")
		}
		existing, err := a.get(ctx, ns, "configmap", "aivision-runtime")
		if err != nil {
			return err
		}
		if existing != nil && !owned(existing) {
			return fmt.Errorf("ConfigMap aivision-runtime exists and is not managed by aiv")
		}
		return a.applyObject(ctx, object{"apiVersion": "v1", "kind": "ConfigMap", "metadata": map[string]any{"name": "aivision-runtime", "namespace": ns}, "data": map[string]string{"SIGNOZ_ORG_ID": a.orgID}}, false)
	}); err != nil {
		return err
	}
	for _, app := range []struct{ file, name string }{{"agentlens.yaml", "agentlens"}, {"frontend.yaml", "aivision-fe"}} {
		if err := a.stage(ctx, components.Canonical(app.name), ns, func(ctx context.Context) error {
			return a.deploy(ctx, app.file, app.name)
		}); err != nil {
			return err
		}
	}
	if err := a.stage(ctx, "host access checks", ns, a.checkAccess); err != nil {
		return err
	}
	fmt.Fprintf(a.out, "Deployment ready.\nWeb: %s\nOTLP HTTP: %s\n", a.Config.webURL(), a.Config.otlpURL())
	return nil
}

func (a *App) backendManageArgs(command ...string) []string {
	args := []string{"exec", "deployment/aivision-be", "--"}
	if a.Config.Values["NATIVE_PYTHON_RUNTIME"] == "true" {
		args = append(args, "/app/manage")
	} else {
		args = append(args, "python", "manage.py")
	}
	return append(args, command...)
}

func (a *App) ensureNamespace(ctx context.Context, ns string) error {
	obj, err := a.get(ctx, "", "namespace", ns)
	if err != nil {
		return err
	}
	if obj != nil {
		return nil
	}
	return a.applyObject(ctx, object{"apiVersion": "v1", "kind": "Namespace", "metadata": map[string]any{"name": ns}}, false)
}

func (a *App) checkAccess(ctx context.Context) error {
	// A ready Pod alone does not prove that the user's browser can reach it.
	checkCtx, cancel := context.WithTimeout(ctx, time.Minute)
	defer cancel()
	for _, path := range []string{"/", "/api/v1/health", "/v1/traces", "/v1/logs", "/v1/metrics"} {
		url := a.Config.webURL() + path
		if err := a.poll(checkCtx, func(ctx context.Context) (bool, error) {
			req, _ := http.NewRequestWithContext(ctx, "GET", url, nil)
			otlp := strings.HasPrefix(path, "/v1/")
			if otlp {
				req, _ = http.NewRequestWithContext(ctx, "POST", url, strings.NewReader("{}"))
				req.Header.Set("Content-Type", "application/json")
			}
			res, err := a.probeClient.Do(req)
			if err != nil {
				return false, nil
			}
			body, readErr := io.ReadAll(io.LimitReader(res.Body, 4096))
			res.Body.Close()
			if readErr != nil {
				return false, nil
			}
			if otlp {
				// An unauthenticated export must reach the gateway authenticator,
				// not the frontend's successful SPA fallback or a TCP listener.
				return res.StatusCode == http.StatusUnauthorized, nil
			}
			if res.StatusCode != http.StatusOK {
				return false, nil
			}
			if path != "/" {
				var health struct {
					Status string `json:"status"`
				}
				return json.Unmarshal(body, &health) == nil && health.Status == "ok", nil
			}
			return true, nil
		}); err != nil {
			return fmt.Errorf("Ingress route check failed for %s; check PUBLIC_HOST/PUBLIC_PORT/PUBLIC_SCHEME, Ingress class, controller exposure, TLS trust and firewall: %w", url, err)
		}
	}
	return nil
}
