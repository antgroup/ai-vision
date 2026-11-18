// Package sandboxplugin manages an explicitly selected optional provider image.
// It never installs an OpenShell gateway or joins the default product image set.
package sandboxplugin

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/config"
)

const (
	providerLabel = "io.aivision.sandbox.provider"
	identityLabel = "io.aivision.sandbox.installation"
	envPrefix     = "AGENTLENS_SANDBOX_PLUGIN_"
)

var imagePattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._/@:-]{0,511}$`)
var containerIDPattern = regexp.MustCompile(`^[a-f0-9]{64}$`)

// Options are operator-owned inputs. Secrets are read from files, never flags.
type Options struct {
	Image             string `json:"image"`
	Network           string `json:"network,omitempty"`
	NetworkAlias      string `json:"network_alias,omitempty"`
	Gateway           string `json:"gateway"`
	WorkloadImage     string `json:"workload_image"`
	TokenFile         string `json:"token_file"`
	PolicyFile        string `json:"policy_file"`
	DockerSocket      string `json:"docker_socket"`
	JournalDir        string `json:"journal_dir"`
	GatewayTokenFile  string `json:"gateway_token_file,omitempty"`
	GatewayCAFile     string `json:"gateway_ca_file,omitempty"`
	GatewayCertFile   string `json:"gateway_cert_file,omitempty"`
	GatewayKeyFile    string `json:"gateway_key_file,omitempty"`
	Workspace         string `json:"workspace"`
	Port              int    `json:"port"`
	RunUID            int    `json:"run_uid"`
	RunGID            int    `json:"run_gid"`
	SocketGID         int    `json:"socket_gid"`
	SocketGIDOverride *int   `json:"socket_gid_override,omitempty"`
}

type installation struct {
	Options
	Identity    string `json:"identity"`
	ContainerID string `json:"container_id,omitempty"`
	Name        string `json:"name"`
}

type Health struct {
	Environment     *EnvironmentHealth `json:"environment,omitempty"`
	ProtocolVersion int                `json:"protocol_version"`
	Provider        string             `json:"provider"`
	Ready           *bool              `json:"ready"`
	Draining        *bool              `json:"draining"`
	ActiveLeases    *int               `json:"active_leases"`
}

type Status struct {
	Installed   bool    `json:"installed"`
	ContainerID string  `json:"container_id,omitempty"`
	Endpoint    string  `json:"endpoint,omitempty"`
	Running     bool    `json:"running"`
	Health      *Health `json:"health,omitempty"`
}

type Docker interface {
	Run(context.Context, ...string) ([]byte, error)
	Stream(context.Context, io.Writer, ...string) error
}

type NativeDocker struct{ Bin string }

func (d NativeDocker) binary() string {
	if d.Bin != "" {
		return d.Bin
	}
	return "docker"
}
func (d NativeDocker) Run(ctx context.Context, args ...string) ([]byte, error) {
	// Do not include Docker output in errors; an image can print credentials.
	data, err := exec.CommandContext(ctx, d.binary(), args...).Output()
	if err != nil {
		return nil, errors.New("Docker operation failed; inspect the managed installation separately")
	}
	return data, nil
}
func (d NativeDocker) Stream(ctx context.Context, out io.Writer, args ...string) error {
	cmd := exec.CommandContext(ctx, d.binary(), args...)
	cmd.Stdout, cmd.Stderr = out, out
	if cmd.Run() != nil {
		return errors.New("Docker logs failed")
	}
	return nil
}

type Manager struct {
	StateDir string
	Docker   Docker
	HTTP     *http.Client
	Progress func(ProgressEvent)
}

func New(stateDir string) *Manager {
	return &Manager{StateDir: stateDir, Docker: NativeDocker{Bin: os.Getenv("DOCKER_BIN")}, HTTP: &http.Client{Timeout: 10 * time.Second, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}}
}

func privateDirectory(path string) error {
	if err := os.MkdirAll(path, 0700); err != nil {
		return err
	}
	info, err := os.Lstat(path)
	if err != nil || !info.IsDir() || info.Mode().Perm()&0077 != 0 {
		return errors.New("plugin directories must be real directories accessible only to their owner (0700)")
	}
	return nil
}

func regularFile(path string, secret bool) (string, error) {
	absolute, err := filepath.Abs(path)
	if err != nil {
		return "", errors.New("invalid operator file path")
	}
	resolved, err := filepath.EvalSymlinks(absolute)
	if err != nil {
		return "", errors.New("required operator file is unavailable")
	}
	info, err := os.Stat(resolved)
	if err != nil || !info.Mode().IsRegular() || (secret && info.Mode().Perm()&0077 != 0) {
		return "", errors.New("operator secret files must be regular owner-only files (0600)")
	}
	if strings.ContainsAny(resolved, ",\n\r") {
		return "", errors.New("operator mount paths cannot contain commas or line breaks")
	}
	return resolved, nil
}

func readToken(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", errors.New("plugin token file is unavailable")
	}
	defer file.Close()
	data, err := io.ReadAll(io.LimitReader(file, 8194))
	token := strings.TrimSpace(string(data))
	if err != nil || len(data) > 8193 || len(token) == 0 || len(token) > 8192 {
		return "", errors.New("plugin token file must contain one bounded access token")
	}
	for _, char := range token {
		if char < 33 || char > 126 {
			return "", errors.New("plugin token must contain visible ASCII characters")
		}
	}
	return token, nil
}

func (o Options) validate() (Options, error) {
	if (o.Network == "") != (o.NetworkAlias == "") || (o.Network != "" && (!regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`).MatchString(o.Network) || !regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$`).MatchString(o.NetworkAlias))) {
		return o, errors.New("invalid internal plugin network or alias")
	}
	if o.SocketGIDOverride != nil && (*o.SocketGIDOverride < 0 || *o.SocketGIDOverride > 2147483647) {
		return o, errors.New("--docker-socket-gid must be between 0 and 2147483647")
	}
	if !imagePattern.MatchString(o.Image) || !imagePattern.MatchString(o.WorkloadImage) {
		return o, errors.New("explicit --image and --workload-image references are required")
	}
	if strings.ContainsAny(o.Gateway, "/@?#\n\r \t") {
		return o, errors.New("--gateway-endpoint must be a credential-free host:port")
	}
	host, port, err := net.SplitHostPort(o.Gateway)
	if err != nil || host == "" || port == "" {
		return o, errors.New("--gateway-endpoint must be an existing OpenShell host:port")
	}
	portNumber, err := strconv.Atoi(port)
	if err != nil || portNumber < 1 || portNumber > 65535 {
		return o, errors.New("invalid gateway port")
	}
	if o.Port < 1 || o.Port > 65535 {
		return o, errors.New("--port must be between 1 and 65535")
	}
	if o.Workspace == "" || len(o.Workspace) > 128 || strings.ContainsAny(o.Workspace, "\n\r") {
		return o, errors.New("invalid gateway workspace")
	}
	if o.TokenFile == "" || o.PolicyFile == "" || o.DockerSocket == "" || o.JournalDir == "" {
		return o, errors.New("--token-file, --policy-file, --docker-socket and --journal-dir are required")
	}
	for _, field := range []struct {
		path   *string
		secret bool
	}{{&o.TokenFile, true}, {&o.PolicyFile, false}, {&o.GatewayTokenFile, true}, {&o.GatewayCAFile, false}, {&o.GatewayCertFile, false}, {&o.GatewayKeyFile, true}} {
		if *field.path == "" {
			continue
		}
		resolved, err := regularFile(*field.path, field.secret)
		if err != nil {
			return o, err
		}
		*field.path = resolved
	}
	if _, err := readToken(o.TokenFile); err != nil {
		return o, err
	}
	o.RunUID, o.RunGID, err = ownerIDs(o.TokenFile)
	if err != nil {
		return o, err
	}
	if o.GatewayTokenFile != "" {
		if _, err := readToken(o.GatewayTokenFile); err != nil {
			return o, err
		}
	}
	if (o.GatewayCertFile == "") != (o.GatewayKeyFile == "") {
		return o, errors.New("--gateway-cert-file and --gateway-key-file must be configured together")
	}
	o.DockerSocket, err = filepath.Abs(o.DockerSocket)
	if err != nil {
		return o, err
	}
	socketInfo, err := os.Stat(o.DockerSocket)
	if err != nil || socketInfo.Mode()&os.ModeSocket == 0 {
		return o, errors.New("--docker-socket must be the gateway Docker daemon's Unix socket")
	}
	if o.SocketGIDOverride != nil {
		o.SocketGID = *o.SocketGIDOverride
	} else {
		_, o.SocketGID, err = ownerIDs(o.DockerSocket)
		if err != nil {
			return o, err
		}
	}
	for _, file := range []string{o.PolicyFile, o.GatewayTokenFile, o.GatewayCAFile, o.GatewayCertFile, o.GatewayKeyFile} {
		if file == "" {
			continue
		}
		info, statErr := os.Stat(file)
		uid, _, ownerErr := ownerIDs(file)
		if statErr != nil || ownerErr != nil || (uid != o.RunUID && info.Mode().Perm()&0004 == 0) {
			return o, errors.New("operator files must be readable by the plugin token-file owner")
		}
	}
	o.JournalDir, err = filepath.Abs(o.JournalDir)
	if err != nil {
		return o, err
	}
	if strings.ContainsAny(o.DockerSocket+o.JournalDir, ",\n\r") {
		return o, errors.New("operator mount paths cannot contain commas or line breaks")
	}
	return o, nil
}

func (m *Manager) statePath() string { return filepath.Join(m.StateDir, "installation.json") }
func (m *Manager) name() string {
	sum := sha256.Sum256([]byte(m.StateDir))
	return "aiv-sandbox-openshell-" + hex.EncodeToString(sum[:6])
}
func (m *Manager) load() (*installation, error) {
	info, err := os.Lstat(m.statePath())
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 {
		return nil, errors.New("plugin installation state must be an owner-only regular file (0600)")
	}
	data, err := os.ReadFile(m.statePath())
	if err != nil {
		return nil, err
	}
	var state installation
	if json.Unmarshal(data, &state) != nil || len(state.Identity) != 32 || state.Name != m.name() || (state.ContainerID != "" && !containerIDPattern.MatchString(state.ContainerID)) || state.Port < 1 || state.Port > 65535 || !filepath.IsAbs(state.TokenFile) {
		return nil, errors.New("invalid plugin installation state; refusing resource changes")
	}
	return &state, nil
}
func (m *Manager) save(state *installation) error {
	data, err := json.MarshalIndent(state, "", "  ")
	if err != nil {
		return err
	}
	return config.AtomicWrite(m.statePath(), data)
}

type journalOwner struct {
	Identity string `json:"identity"`
	StateDir string `json:"state_dir"`
}

func (m *Manager) verifyJournalOwner(state *installation) error {
	path := filepath.Join(state.JournalDir, ".aiv-plugin-owner.json")
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 {
		return errors.New("persistent journal ownership cannot be verified")
	}
	data, err := os.ReadFile(path)
	var owner journalOwner
	if err != nil || json.Unmarshal(data, &owner) != nil || owner.Identity != state.Identity || owner.StateDir != m.StateDir {
		return errors.New("persistent journal belongs to another installation; refusing concurrent plugin processes")
	}
	return nil
}
func (m *Manager) claimJournal(state *installation) error {
	path := filepath.Join(state.JournalDir, ".aiv-plugin-owner.json")
	file, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if os.IsExist(err) {
		return m.verifyJournalOwner(state)
	}
	if err != nil {
		return err
	}
	data, _ := json.Marshal(journalOwner{Identity: state.Identity, StateDir: m.StateDir})
	_, err = file.Write(data)
	if err == nil {
		err = file.Sync()
	}
	closeErr := file.Close()
	if err != nil {
		return err
	}
	return closeErr
}

type inspected struct {
	ID           string `json:"Id"`
	RestartCount *int
	Config       struct {
		Image  string
		Labels map[string]string
	}
	State struct {
		Running   bool
		ExitCode  int
		OOMKilled bool
		StartedAt string
	}
	NetworkSettings struct {
		Networks map[string]struct {
			NetworkID string
			Aliases   []string
		}
		Ports map[string][]struct {
			HostIP   string `json:"HostIp"`
			HostPort string
		}
	}
}

func sameGeneration(before, after *inspected) bool {
	return before.RestartCount != nil && after.RestartCount != nil &&
		*before.RestartCount >= 0 && *after.RestartCount >= 0 &&
		*before.RestartCount == *after.RestartCount && before.State.StartedAt == after.State.StartedAt
}

func (m *Manager) inspect(ctx context.Context, state *installation) (*inspected, error) {
	id := state.ContainerID
	if id == "" {
		id = state.Name
	}
	data, err := m.Docker.Run(ctx, "container", "inspect", "--format", "{{json .}}", id)
	if err != nil {
		return nil, errors.New("cannot verify managed plugin container; refusing resource changes")
	}
	var result inspected
	if json.Unmarshal(data, &result) != nil || !containerIDPattern.MatchString(result.ID) || (state.ContainerID != "" && result.ID != state.ContainerID) || result.Config.Labels[providerLabel] != "openshell" || result.Config.Labels[identityLabel] != state.Identity || result.Config.Image != state.Image {
		return nil, errors.New("plugin container ownership or image does not match saved installation")
	}
	if state.Network != "" {
		matched := false
		for name, network := range result.NetworkSettings.Networks {
			if network.NetworkID != state.Network && name != state.Network {
				continue
			}
			for _, alias := range network.Aliases {
				matched = matched || alias == state.NetworkAlias
			}
		}
		if !matched {
			return nil, errors.New("plugin network identity or alias does not match saved installation")
		}
	}
	ports := result.NetworkSettings.Ports["8091/tcp"]
	if result.State.Running && (len(ports) != 1 || ports[0].HostIP != "127.0.0.1" || ports[0].HostPort != strconv.Itoa(state.Port)) {
		return nil, errors.New("plugin localhost port does not match saved installation")
	}
	if state.ContainerID == "" {
		state.ContainerID = result.ID
		if err := m.save(state); err != nil {
			return nil, err
		}
	}
	return &result, nil
}

func (m *Manager) health(ctx context.Context, state *installation, drain bool) (*Health, error) {
	token, err := readToken(state.TokenFile)
	if err != nil {
		return nil, err
	}
	method, path := http.MethodGet, "/v1/health"
	if drain {
		method, path = http.MethodPost, "/v1/admin/drain"
	}
	request, err := http.NewRequestWithContext(ctx, method, fmt.Sprintf("http://127.0.0.1:%d%s", state.Port, path), nil)
	if err != nil {
		return nil, errors.New("invalid plugin health endpoint")
	}
	request.Header.Set("Authorization", "Bearer "+token)
	response, err := m.HTTP.Do(request)
	if err != nil {
		return nil, errors.New("plugin health cannot be verified; no container was removed")
	}
	defer response.Body.Close()
	data, err := io.ReadAll(io.LimitReader(response.Body, 65537))
	var health Health
	if err != nil || len(data) > 65536 || response.StatusCode != 200 || json.Unmarshal(data, &health) != nil || health.ProtocolVersion != 1 || health.Provider != "openshell" || health.Ready == nil || health.Draining == nil || health.ActiveLeases == nil || *health.ActiveLeases < 0 {
		return nil, errors.New("plugin returned unverifiable health; no container was removed")
	}
	return &health, nil
}

func (m *Manager) Install(ctx context.Context, options Options) (result *Status, err error) {
	progress := &installProgress{notify: m.Progress}
	progress.start("configuration")
	defer func() {
		if err == nil {
			return
		}
		detail := ""
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			detail = "timeout"
			err = fmt.Errorf("plugin installation timed out during %s; saved state and journal (if any) were retained", progress.stage)
		} else if errors.Is(ctx.Err(), context.Canceled) {
			detail = "canceled"
			err = fmt.Errorf("plugin installation canceled during %s; saved state and journal (if any) were retained", progress.stage)
		}
		progress.finish("failed", detail)
	}()
	validated, err := options.validate()
	if err != nil {
		return nil, err
	}
	progress.finish("done", "")
	progress.start("docker")
	m.StateDir, err = filepath.Abs(m.StateDir)
	if err != nil {
		return nil, err
	}
	unlock, err := lock(ctx, m.StateDir)
	if err != nil {
		return nil, err
	}
	defer unlock()
	existing, err := m.load()
	if err != nil {
		return nil, err
	}
	state := &installation{Options: validated, Name: m.name()}
	if existing != nil {
		if existing.ContainerID != "" {
			return nil, errors.New("plugin installation already exists; inspect it with status/logs and remove it safely before reinstalling")
		}
		left, _ := json.Marshal(existing.Options)
		right, _ := json.Marshal(validated)
		if string(left) != string(right) {
			return nil, errors.New("pending installation must resume with its original operator options")
		}
		state = existing
	} else {
		identity := make([]byte, 16)
		if _, err = rand.Read(identity); err != nil {
			return nil, err
		}
		state.Identity = hex.EncodeToString(identity)
	}
	listed, err := m.Docker.Run(ctx, "container", "ls", "--all", "--filter", "name=^/"+state.Name+"$", "--format", "{{.ID}}")
	if err != nil {
		return nil, errors.New("cannot inspect Docker container names")
	}
	if strings.TrimSpace(string(listed)) != "" {
		if existing != nil {
			_, _ = m.inspect(ctx, state)
		}
		return nil, errors.New("plugin container name already exists; refusing to replace or adopt it")
	}
	if err := privateDirectory(state.JournalDir); err != nil {
		return nil, err
	}
	journalUID, _, err := ownerIDs(state.JournalDir)
	if err != nil {
		return nil, err
	}
	if journalUID != state.RunUID {
		return nil, errors.New("persistent journal directory must be owned by the plugin token-file owner")
	}
	if _, markerErr := os.Lstat(filepath.Join(state.JournalDir, ".aiv-plugin-owner.json")); markerErr == nil {
		if err := m.verifyJournalOwner(state); err != nil {
			return nil, err
		}
	} else if !os.IsNotExist(markerErr) {
		return nil, markerErr
	}
	progress.finish("done", "")
	progress.start("image")
	imageDetail := "cached"
	if _, err = m.Docker.Run(ctx, "image", "inspect", state.Image); err != nil {
		imageDetail = "pulled"
		progress.update("pulling", nil)
		if puller, ok := m.Docker.(interface {
			Pull(context.Context, string, func(ImageProgress)) error
		}); ok {
			err = puller.Pull(ctx, state.Image, func(image ImageProgress) { progress.update("pulling", &image) })
		} else {
			_, err = m.Docker.Run(ctx, "pull", state.Image)
		}
		if err != nil {
			return nil, errors.New("could not pull the explicitly selected plugin image")
		}
	}
	progress.finish("done", imageDetail)
	progress.start("state")
	if err = m.save(state); err != nil {
		return nil, err
	}
	if err = m.claimJournal(state); err != nil {
		return nil, err
	}
	progress.finish("done", "")
	progress.start("container")
	args := []string{"run", "--detach", "--name", state.Name, "--label", providerLabel + "=openshell", "--label", identityLabel + "=" + state.Identity, "--publish", fmt.Sprintf("127.0.0.1:%d:8091", state.Port), "--restart", "unless-stopped", "--stop-signal", "SIGTERM", "--user", fmt.Sprintf("%d:%d", state.RunUID, state.RunGID), "--group-add", strconv.Itoa(state.SocketGID), "--security-opt", "no-new-privileges=true", "--cap-drop", "ALL", "--env", "HOME=/tmp"}
	if state.Network != "" {
		args = append(args, "--network", state.Network, "--network-alias", state.NetworkAlias)
	}
	mount := func(source, target string, readonly bool) {
		value := "type=bind,source=" + source + ",target=" + target
		if readonly {
			value += ",readonly"
		}
		args = append(args, "--mount", value)
	}
	env := func(key, value string) { args = append(args, "--env", envPrefix+key+"="+value) }
	mount(state.TokenFile, "/run/secrets/plugin-token", true)
	env("TOKEN_FILE", "/run/secrets/plugin-token")
	mount(state.PolicyFile, "/etc/aivision/sandbox-policy.yaml", true)
	env("POLICY_PATH", "/etc/aivision/sandbox-policy.yaml")
	mount(state.DockerSocket, "/run/docker-readback.sock", true)
	env("DOCKER_SOCKET", "/run/docker-readback.sock")
	mount(state.JournalDir, "/var/lib/agentlens-sandbox", false)
	env("JOURNAL_PATH", "/var/lib/agentlens-sandbox/leases.sqlite3")
	for _, file := range []struct{ source, target, key string }{{state.GatewayTokenFile, "/run/secrets/gateway-token", "GATEWAY_TOKEN_FILE"}, {state.GatewayCAFile, "/etc/aivision/gateway-ca.crt", "TLS_CA"}, {state.GatewayCertFile, "/etc/aivision/gateway-client.crt", "TLS_CERT"}, {state.GatewayKeyFile, "/run/secrets/gateway-client.key", "TLS_KEY"}} {
		if file.source != "" {
			mount(file.source, file.target, true)
			env(file.key, file.target)
		}
	}
	allowlist, _ := json.Marshal([]string{state.WorkloadImage})
	env("IMAGE_ALLOWLIST", string(allowlist))
	// The plugin advertises enhancement only after inspecting this immutable
	// image and matching its label to the shipped runtime inventory.
	if regexp.MustCompile(`^[^\s@]+@sha256:[a-f0-9]{64}$`).MatchString(state.WorkloadImage) {
		args = append(args, "--env", "AIV_SANDBOX_ENHANCED_IMAGE="+state.WorkloadImage)
	}
	env("GATEWAY", state.Gateway)
	env("WORKSPACE", state.Workspace)
	args = append(args, state.Image)
	data, err := m.Docker.Run(ctx, args...)
	if err != nil {
		return nil, errors.New("plugin startup failed; saved pending state and journal were retained")
	}
	state.ContainerID = strings.TrimSpace(string(data))
	if !containerIDPattern.MatchString(state.ContainerID) {
		return nil, errors.New("Docker returned an invalid container identity; pending state was retained")
	}
	if err = m.save(state); err != nil {
		return nil, err
	}
	progress.finish("done", "")
	progress.start("readiness")
	// Container startup can precede the HTTP listener. Keep the saved identity and
	// retry a bounded initialization window without replacing the installation.
	deadline := time.Now().Add(30 * time.Second)
	for {
		result, checkErr := m.status(ctx, state)
		if checkErr == nil && result != nil && !result.Running {
			return result, errors.New("plugin exited before readiness could be verified; installation state and journal retained")
		}
		if checkErr == nil || time.Now().After(deadline) {
			if checkErr == nil {
				detail := "not_ready"
				if result != nil && result.Health != nil && result.Health.Ready != nil && *result.Health.Ready {
					detail = "ready"
				}
				progress.finish("done", detail)
			}
			return result, checkErr
		}
		select {
		case <-ctx.Done():
			return result, errors.New("plugin initialization could not be verified; installation state and journal retained")
		case <-time.After(250 * time.Millisecond):
		}
	}
}

func (m *Manager) status(ctx context.Context, state *installation) (*Status, error) {
	if state == nil {
		return &Status{}, nil
	}
	container, err := m.inspect(ctx, state)
	if err != nil {
		return nil, err
	}
	result := &Status{Installed: true, ContainerID: container.ID, Endpoint: fmt.Sprintf("http://127.0.0.1:%d", state.Port), Running: container.State.Running}
	if !container.State.Running {
		return result, nil
	}
	health, err := m.health(ctx, state, false)
	if err != nil {
		return result, err
	}
	result.Health = health
	return result, nil
}
func (m *Manager) Status(ctx context.Context) (*Status, error) {
	var err error
	m.StateDir, err = filepath.Abs(m.StateDir)
	if err != nil {
		return nil, err
	}
	unlock, err := lock(ctx, m.StateDir)
	if err != nil {
		return nil, err
	}
	defer unlock()
	state, err := m.load()
	if err != nil {
		return nil, err
	}
	return m.status(ctx, state)
}
func (m *Manager) Logs(ctx context.Context, out io.Writer, lines int, follow bool) error {
	var err error
	m.StateDir, err = filepath.Abs(m.StateDir)
	if err != nil {
		return err
	}
	unlock, err := lock(ctx, m.StateDir)
	if err != nil {
		return err
	}
	state, err := m.load()
	if err != nil {
		unlock()
		return err
	}
	if state == nil {
		unlock()
		return errors.New("plugin is not installed")
	}
	container, err := m.inspect(ctx, state)
	unlock()
	if err != nil {
		return err
	}
	args := []string{"logs", "--tail", strconv.Itoa(lines)}
	if follow {
		args = append(args, "--follow")
	}
	args = append(args, container.ID)
	return m.Docker.Stream(ctx, out, args...)
}
func (m *Manager) Remove(ctx context.Context) error {
	var err error
	m.StateDir, err = filepath.Abs(m.StateDir)
	if err != nil {
		return err
	}
	unlock, err := lock(ctx, m.StateDir)
	if err != nil {
		return err
	}
	defer unlock()
	state, err := m.load()
	if err != nil {
		return err
	}
	if state == nil {
		return nil
	}
	if err = m.verifyJournalOwner(state); err != nil {
		return err
	}
	container, err := m.inspect(ctx, state)
	if err != nil {
		return err
	}
	if !container.State.Running {
		return errors.New("plugin is stopped; restart the owned container to verify journal cleanup before removal")
	}
	startedAt, generationErr := time.Parse(time.RFC3339Nano, container.State.StartedAt)
	if generationErr != nil || startedAt.IsZero() || container.RestartCount == nil || *container.RestartCount < 0 {
		return errors.New("plugin process generation cannot be verified; state and journal retained")
	}
	health, err := m.health(ctx, state, true)
	if err != nil {
		return err
	}
	if !*health.Draining || *health.Ready {
		return errors.New("plugin did not fence new leases; removal refused")
	}
	if *health.ActiveLeases != 0 {
		return errors.New("plugin has active or cleanup-pending leases; it remains draining; retry after cleanup")
	}
	drained, err := m.inspect(ctx, state)
	if err != nil {
		return err
	}
	if !drained.State.Running || !sameGeneration(container, drained) {
		return errors.New("plugin restarted or exited during drain; reconcile leases before retrying; state and journal retained")
	}
	if _, err = m.Docker.Run(ctx, "stop", "--time", "60", container.ID); err != nil {
		return errors.New("plugin stop failed; state and journal retained")
	}
	stopped, err := m.inspect(ctx, state)
	if err != nil {
		return err
	}
	if !sameGeneration(container, stopped) {
		return errors.New("plugin restarted after drain; reconcile leases before retrying; state and journal retained")
	}
	if stopped.State.Running || (stopped.State.ExitCode != 0 && stopped.State.ExitCode != 143) || stopped.State.OOMKilled {
		return errors.New("plugin did not exit cleanly after draining; removal refused and journal retained")
	}
	if _, err = m.Docker.Run(ctx, "rm", container.ID); err != nil {
		return errors.New("plugin removal failed; state and journal retained")
	}
	if err = os.Remove(filepath.Join(state.JournalDir, ".aiv-plugin-owner.json")); err != nil {
		return err
	}
	return os.Remove(m.statePath()) // Persistent lease journal and operator files are never purged.
}
