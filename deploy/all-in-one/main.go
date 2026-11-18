// The All-in-One prototype runs the existing native services in one container.
package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"
)

const (
	dataDir = "/data"
	runDir  = "/run/aivision"
	socket  = runDir + "/seekdb.sock"
	backend = "/opt/aivision/backend"
	lens    = "/app"
	signoz  = "/opt/aivision/signoz"
)

type settings struct {
	SecretKey         string
	IngestSecret      string
	AgentLensToken    string
	SignozKey         string
	SignozPassword    string
	TraceHistoryStart string
}

func randomSecret() (string, error) {
	var random [32]byte
	if _, err := rand.Read(random[:]); err != nil {
		return "", err
	}
	return hex.EncodeToString(random[:]), nil
}

func loadSettings(dir string, now time.Time) (settings, error) {
	path := filepath.Join(dir, "settings.json")
	var state settings
	if raw, err := os.ReadFile(path); err == nil {
		err = json.Unmarshal(raw, &state)
		if err == nil && (state.SecretKey == "" || state.IngestSecret == "" || state.AgentLensToken == "" ||
			state.SignozKey == "" || state.SignozPassword == "" || state.TraceHistoryStart == "") {
			err = errors.New("persisted settings are incomplete")
		}
		return state, err
	} else if !errors.Is(err, os.ErrNotExist) {
		return state, err
	}
	for _, field := range []*string{&state.SecretKey, &state.IngestSecret, &state.AgentLensToken,
		&state.SignozKey, &state.SignozPassword} {
		secret, err := randomSecret()
		if err != nil {
			return state, err
		}
		*field = secret
	}
	state.SignozPassword = "Local-9!" + state.SignozPassword
	state.TraceHistoryStart = now.UTC().Truncate(24 * time.Hour).Format(time.RFC3339)
	file, err := os.CreateTemp(dir, ".settings-")
	if err != nil {
		return state, err
	}
	defer os.Remove(file.Name())
	err = json.NewEncoder(file).Encode(state)
	if err == nil {
		err = file.Sync()
	}
	err = errors.Join(err, file.Close())
	if err != nil {
		return state, err
	}
	// Link rather than overwrite a concurrent initialization of the same volume.
	if err := os.Link(file.Name(), path); errors.Is(err, os.ErrExist) {
		return loadSettings(dir, now)
	} else if err != nil {
		return state, err
	}
	return state, nil
}

func envWith(values map[string]string) []string {
	var env []string
	for _, pair := range os.Environ() {
		key, _, _ := strings.Cut(pair, "=")
		if _, replaced := values[key]; !replaced {
			env = append(env, pair)
		}
	}
	for key, value := range values {
		env = append(env, key+"="+value)
	}
	return env
}

func addressEnv(key, fallback string) (string, error) {
	value := os.Getenv(key)
	if value == "" {
		value = fallback
	}
	u, err := url.Parse(value)
	if err != nil || u.Hostname() == "" || u.User != nil || u.RawQuery != "" ||
		u.Fragment != "" || (u.Path != "" && u.Path != "/") || (u.Scheme != "http" && u.Scheme != "https") {
		return "", fmt.Errorf("%s must be an HTTP(S) origin", key)
	}
	return u.Scheme + "://" + u.Host, nil
}

type child struct {
	name string
	cmd  *exec.Cmd
	done chan struct{}
	err  error
}

type stack struct {
	children []*child
	ctx      context.Context
	cancel   context.CancelCauseFunc
}

func (s *stack) start(name, dir string, env map[string]string, args ...string) (*child, error) {
	cmd := exec.Command(args[0], args[1:]...)
	cmd.Dir, cmd.Env = dir, envWith(env)
	cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("start %s: %w", name, err)
	}
	c := &child{name: name, cmd: cmd, done: make(chan struct{})}
	s.children = append(s.children, c)
	go func() {
		c.err = cmd.Wait()
		close(c.done)
	}()
	return c, nil
}

func (s *stack) service(name, dir string, env map[string]string, args ...string) error {
	c, err := s.start(name, dir, env, args...)
	if err != nil {
		return err
	}
	go func() {
		<-c.done
		s.cancel(fmt.Errorf("%s exited: %v", c.name, c.err))
	}()
	return nil
}

func (s *stack) once(name, dir string, env map[string]string, args ...string) error {
	c, err := s.start(name, dir, env, args...)
	if err != nil {
		return err
	}
	select {
	case <-c.done:
		if c.err != nil {
			return fmt.Errorf("%s: %w", name, c.err)
		}
		return nil
	case <-s.ctx.Done():
		return context.Cause(s.ctx)
	}
}

func (s *stack) stop() error {
	var seekdbErr error
	for i := len(s.children) - 1; i >= 0; i-- {
		c := s.children[i]
		select {
		case <-c.done:
		default:
			log.Printf("stopping %s", c.name)
			if c.name == "seekdb" {
				// The owner closes the database through pylibseekdb before exiting.
				_ = c.cmd.Process.Signal(syscall.SIGTERM)
			} else {
				_ = syscall.Kill(-c.cmd.Process.Pid, syscall.SIGTERM)
			}
			select {
			case <-c.done:
			case <-time.After(35 * time.Second):
				// The child can finish as the timer fires.
				select {
				case <-c.done:
				default:
					log.Printf("forcing %s to stop after timeout", c.name)
					if c.name == "seekdb" {
						seekdbErr = errors.New("seekdb shutdown timed out")
					}
					_ = syscall.Kill(-c.cmd.Process.Pid, syscall.SIGKILL)
					<-c.done
				}
			}
		}
		if c.name == "seekdb" && c.err != nil {
			seekdbErr = errors.Join(seekdbErr, fmt.Errorf("seekdb shutdown: %w", c.err))
		}
	}
	return seekdbErr
}

func waitReady(ctx context.Context, name string, check func() bool) error {
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	deadline := time.NewTimer(4 * time.Minute)
	defer deadline.Stop()
	for {
		if check() {
			log.Printf("%s ready", name)
			return nil
		}
		select {
		case <-ctx.Done():
			return context.Cause(ctx)
		case <-deadline.C:
			return fmt.Errorf("%s did not become ready", name)
		case <-ticker.C:
		}
	}
}

func httpReady(endpoint string) bool {
	client := &http.Client{Timeout: 2 * time.Second, Transport: &http.Transport{DisableKeepAlives: true}}
	response, err := client.Get(endpoint)
	if err != nil {
		return false
	}
	defer response.Body.Close()
	return response.StatusCode == http.StatusOK
}

var readiness = []string{
	"http://127.0.0.1:8080/api/v1/ready",
	"http://127.0.0.1:13133/debug/pprof/",
	"http://127.0.0.1:8001/api/v1/ready",
	"http://127.0.0.1:8081/internal/readyz",
	"http://127.0.0.1:5174/",
}

func nginxConfig() error {
	template, err := os.ReadFile("/etc/nginx/templates/default.conf.template")
	if err != nil {
		return err
	}
	conf := strings.NewReplacer("${FE_PORT}", "5174", "${BE_UPSTREAM}", "http://127.0.0.1:8001",
		"${AIVISION_TRUST_PROXY_HEADERS}", "false").Replace(string(template))
	return os.WriteFile("/etc/nginx/conf.d/aivision.conf", []byte(conf), 0644)
}

func run(ctx context.Context) (result error) {
	for _, path := range []string{dataDir + "/seekdb", dataDir + "/signoz", dataDir + "/logs/backend",
		dataDir + "/logs/agentlens", runDir} {
		if err := os.MkdirAll(path, 0700); err != nil {
			return err
		}
	}
	// Docker start of the same stopped container may retain the old writable layer.
	if info, err := os.Lstat(socket); err == nil {
		if info.Mode()&os.ModeSymlink == 0 {
			return errors.New("database socket alias path already exists and is not a symlink")
		}
		if err := os.Remove(socket); err != nil {
			return err
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	state, err := loadSettings(dataDir, time.Now())
	if err != nil {
		return err
	}
	public, err := addressEnv("AIVISION_PUBLIC_BASE_URL", "http://127.0.0.1:5174")
	if err != nil {
		return err
	}
	ingest, err := addressEnv("AIVISION_OTLP_INGEST_ENDPOINT", "http://127.0.0.1:4318")
	if err != nil {
		return err
	}
	publicURL, _ := url.Parse(public)

	stackCtx, cancel := context.WithCancelCause(ctx)
	s := stack{ctx: stackCtx, cancel: cancel}
	defer cancel(nil)
	defer func() {
		if stopErr := s.stop(); stopErr != nil {
			result = errors.Join(result, stopErr)
		}
	}()
	if err := s.service("seekdb", "/", nil, "/usr/local/bin/aivision-seekdb", dataDir+"/seekdb", socket); err != nil {
		return err
	}
	if err := waitReady(stackCtx, "seekdb and application schemas", func() bool {
		info, err := os.Stat(socket)
		return err == nil && info.Mode()&os.ModeSocket != 0
	}); err != nil {
		return err
	}

	signozEnv := map[string]string{
		"SIGNOZ_TELEMETRYSTORE_PROVIDER":      "oceanbase",
		"SIGNOZ_TELEMETRYSTORE_OCEANBASE_DSN": "root@unix(" + socket + ")/signoz?charset=utf8mb4",
		"SIGNOZ_WEB_ENABLED":                  "false", "SIGNOZ_USER_ROOT_ENABLED": "false",
		"SIGNOZ_ANALYTICS_ENABLED": "false", "SIGNOZ_STATSREPORTER_ENABLED": "false",
		"SIGNOZ_INSTRUMENTATION_METRICS_ENABLED":         "false",
		"SIGNOZ_AIVISION_PROJECTION_MAINTENANCE_ENABLED": "true",
		"SIGNOZ_INGEST_INTERNAL_KEY":                     state.SignozKey,
	}
	if err := s.service("SigNoz", signoz, signozEnv, signoz+"/signoz", "server"); err != nil {
		return err
	}
	if err := waitReady(stackCtx, "SigNoz", func() bool { return httpReady(readiness[0]) }); err != nil {
		return err
	}
	collectorEnv := map[string]string{
		"AIVISION_INGEST_SECRET": state.IngestSecret, "GOMEMLIMIT": "576MiB",
		"SIGNOZ_INGEST_ENDPOINT": "http://127.0.0.1:8080", "SIGNOZ_INGEST_INTERNAL_KEY": state.SignozKey,
	}
	if err := s.service("Collector", "/", collectorEnv, "/opt/aivision/collector/collector", "--config", "/etc/otelcol/config.yaml"); err != nil {
		return err
	}
	if err := waitReady(stackCtx, "Collector", func() bool { return httpReady(readiness[1]) }); err != nil {
		return err
	}

	backendEnv := map[string]string{
		"PATH":                    backend + "/.venv/bin:" + os.Getenv("PATH"),
		"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1", "AIVISION_ENV": "container",
		"AIVISION_AGENT_PLUGINS_DIR": "/agent-plugins", "AIVISION_PLUGIN_ARTIFACTS_DIR": "/agent-plugins/dist",
		"AIVISION_LOG_DIR":                    dataDir + "/logs/backend",
		"AIVISION_NATIVE_TRACE_HISTORY_START": state.TraceHistoryStart,
		"AIVISION_INGEST_SECRET":              state.IngestSecret, "SECRET_KEY": state.SecretKey,
		"DB_ENGINE": "aivision_backend.db_backends.mysql_connector_oceanbase",
		"DB_HOST":   socket, "DB_NAME": "aivision", "DB_USER": "root", "DB_PASSWORD": "", "DB_PORT": "2881",
		"BE_PORT": "8001", "AGENTLENS_BASE_URL": "http://127.0.0.1:8081", "AGENTLENS_TOKEN": state.AgentLensToken,
		"ALLOWED_HOSTS":        "localhost,127.0.0.1," + publicURL.Hostname(),
		"CSRF_TRUSTED_ORIGINS": public, "CORS_ALLOWED_ORIGINS": public,
		"AIVISION_PUBLIC_BASE_URL": public, "SITE_URL": public,
		"AIVISION_OTLP_INGEST_ENDPOINT": ingest, "AIVISION_OTLP_UPSTREAM_ENDPOINT": "http://127.0.0.1:4318",
		"SIGNOZ_INTERNAL_URL": "http://127.0.0.1:8080", "SIGNOZ_ROOT_EMAIL": "aivision@example.invalid",
		"SIGNOZ_ROOT_ORG_NAME": "AI Vision", "SIGNOZ_USER_ROOT_PASSWORD": state.SignozPassword,
	}
	if err := s.service("Django", backend, backendEnv, backend+"/docker-entrypoint.sh"); err != nil {
		return err
	}
	if err := waitReady(stackCtx, "Django", func() bool { return httpReady(readiness[2]) }); err != nil {
		return err
	}
	bootstrap := []string{backend + "/backend-bootstrap"}
	if _, err := os.Stat(bootstrap[0]); errors.Is(err, os.ErrNotExist) {
		bootstrap = []string{backend + "/.venv/bin/python", backend + "/backend-bootstrap.py"}
	}
	if err := s.once("integration bootstrap", backend, backendEnv, bootstrap...); err != nil {
		return err
	}

	mysql := func(db string) string {
		return "mysql+pymysql://root@localhost/" + db + "?charset=utf8mb4&unix_socket=" + url.QueryEscape(socket)
	}
	lensEnv := map[string]string{
		"PATH": lens + "/.venv/bin:" + os.Getenv("PATH"), "PYTHONPATH": "/app/src",
		"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1", "AIVISION_ENV": "dev",
		"AGENTLENS_OB_URL": mysql("agentlens"), "SIGNOZ_OB_URL": mysql("signoz"),
		"AGENTLENS_LOG_DIR": dataDir + "/logs/agentlens", "AGENTLENS_LOG_STDOUT": "true",
		"AGENTLENS_PORT": "8081", "AGENTLENS_TOKEN": state.AgentLensToken,
		"SIGNOZ_ORG_ID_FILE":        runDir + "/org-id",
		"AIVISION_INGEST_TOKEN_URL": "http://127.0.0.1:8001/api/internal/agentlens/ingest-tokens",
		"ANTCOLLECTOR_ENDPOINT":     "http://127.0.0.1:4318/v1/traces",
	}
	if err := s.service("AgentLens", lens, lensEnv, lens+"/agentlens-entrypoint"); err != nil {
		return err
	}
	if err := waitReady(stackCtx, "AgentLens", func() bool { return httpReady(readiness[3]) }); err != nil {
		return err
	}
	if err := nginxConfig(); err != nil {
		return err
	}
	if err := s.service("Web", "/", nil, "/usr/sbin/nginx", "-g", "daemon off;"); err != nil {
		return err
	}
	if err := waitReady(stackCtx, "Web", func() bool { return httpReady(readiness[4]) }); err != nil {
		return err
	}
	<-stackCtx.Done()
	return context.Cause(stackCtx)
}

func main() {
	if len(os.Args) == 2 && os.Args[1] == "healthcheck" {
		for _, endpoint := range readiness {
			if !httpReady(endpoint) {
				log.Fatalf("not ready: %s", endpoint)
			}
		}
		return
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	// A bare cancellation is normal. A shutdown failure may also carry cancellation.
	if err := run(ctx); err != nil && err != context.Canceled {
		log.Fatal(err)
	}
}
