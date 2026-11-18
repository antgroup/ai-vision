package sandboxbootstrap

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/sandboxplugin"
)

type Docker interface {
	sandboxplugin.Docker
	Pull(context.Context, string, func(sandboxplugin.ImageProgress)) error
}
type Host struct {
	DaemonID    string `json:"daemon_id"`
	Platform    string `json:"platform"`
	Socket      string `json:"socket"`
	BackendID   string `json:"backend_id"`
	DiagnosisID string `json:"diagnosis_id"`
	NetworkName string `json:"network_name"`
	NetworkID   string `json:"network_id"`
}
type daemonInfo struct{ ID, OSType, Architecture, ServerVersion, KernelVersion string }
type container struct {
	ID     string `json:"Id"`
	Name   string
	Config struct {
		Image        string
		Hostname     string
		User         string
		Cmd          []string
		Labels       map[string]string
		Env          []string
		ExposedPorts map[string]json.RawMessage
	}
	State struct {
		Running   bool
		ExitCode  int
		OOMKilled bool
	}
	HostConfig struct {
		NetworkMode  string
		PortBindings map[string][]struct {
			HostIP   string `json:"HostIp"`
			HostPort string
		}
		Binds       []string
		GroupAdd    []string
		Privileged  bool
		CapAdd      []string
		CapDrop     []string
		SecurityOpt []string
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
	Mounts []struct {
		Source, Destination string
		RW                  bool
	}
}

func envValue(environment []string, key string) string {
	for _, entry := range environment {
		if name, value, ok := strings.Cut(entry, "="); ok && name == key {
			return value
		}
	}
	return ""
}
func inspect(ctx context.Context, docker Docker, id string) (container, error) {
	var result container
	raw, err := docker.Run(ctx, "container", "inspect", "--format", "{{json .}}", id)
	if err != nil || len(raw) > 1024*1024 || json.Unmarshal(raw, &result) != nil || !fullID.MatchString(result.ID) {
		return result, errors.New("could not verify Docker resource identity")
	}
	return result, nil
}
func socketDaemon(ctx context.Context, path string) (daemonInfo, error) {
	transport := &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "unix", path)
	}}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, Timeout: 2 * time.Second}
	request, _ := http.NewRequestWithContext(ctx, http.MethodGet, "http://docker/info", nil)
	response, err := client.Do(request)
	var info daemonInfo
	if err != nil {
		return info, dockerReadError(err)
	}
	defer response.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(response.Body, 1024*1024+1))
	if err != nil || response.StatusCode != 200 || len(raw) > 1024*1024 || json.Unmarshal(raw, &info) != nil {
		return info, diagnosticError("environment_probe_failed", "", "")
	}
	return info, nil
}

// Discover performs only reads. The exact browser-side backend identity, role
// labels, diagnosis route, and one shared network must all agree before pulls
// or private state are created.
func Discover(ctx context.Context, docker Docker, target Target) (Host, error) {
	var result Host
	refused := diagnosticError("deployment_not_found", "", "")
	raw, err := docker.Run(ctx, "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}")
	var endpoint string
	if err != nil || json.Unmarshal(raw, &endpoint) != nil {
		return result, diagnosticError("docker_unavailable", "", "")
	}
	if override := os.Getenv("DOCKER_HOST"); override != "" && os.Getenv("DOCKER_CONTEXT") == "" {
		endpoint = override
	}
	parsed, err := url.Parse(endpoint)
	if err != nil || parsed.Scheme != "unix" || parsed.Host != "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" || !filepath.IsAbs(parsed.Path) || strings.ContainsAny(parsed.Path, ",\n\r") {
		return result, diagnosticError("docker_context_unsupported", "", "")
	}
	socket, err := filepath.EvalSymlinks(parsed.Path)
	if err != nil {
		return result, dockerReadError(err)
	}
	actual, err := socketDaemon(ctx, socket)
	if err != nil {
		return result, err
	}
	infoRaw, err := docker.Run(ctx, "info", "--format", "{{json .}}")
	var info daemonInfo
	if err != nil || len(infoRaw) > 1024*1024 || json.Unmarshal(infoRaw, &info) != nil || info.ID == "" || len(info.ID) > 128 {
		return result, dockerReadError(err)
	}
	if actual.ID != info.ID || actual.OSType != info.OSType || actual.Architecture != info.Architecture {
		return result, diagnosticError("environment_probe_failed", "", "")
	}
	arch := info.Architecture
	switch arch {
	case "aarch64":
		arch = "arm64"
	case "x86_64":
		arch = "amd64"
	}
	if info.OSType != "linux" || (arch != "amd64" && arch != "arm64") {
		return result, diagnosticError("environment_probe_failed", "", "")
	}
	backend, err := inspect(ctx, docker, target.BackendHostname)
	if err != nil || !backend.State.Running || (!strings.HasPrefix(backend.ID, target.BackendHostname) && backend.Config.Hostname != target.BackendHostname) || backend.Config.Labels["io.aivision.product"] != "aivision" || backend.Config.Labels["io.aivision.component"] != "backend" || envValue(backend.Config.Env, "AGENTLENS_BASE_URL") != target.DiagnosisURL {
		return result, refused
	}
	listed, err := docker.Run(ctx, "container", "ls", "--filter", "label=io.aivision.product=aivision", "--filter", "label=io.aivision.component=diagnosis", "--format", "{{.ID}}")
	if err != nil || len(listed) > 65536 {
		return result, refused
	}
	diagnosisURL, _ := url.Parse(target.DiagnosisURL)
	count := 0
	for _, id := range strings.Fields(string(listed)) {
		candidate, e := inspect(ctx, docker, id)
		if e != nil {
			return result, refused
		}
		if !candidate.State.Running || candidate.Config.Labels["io.aivision.product"] != "aivision" || candidate.Config.Labels["io.aivision.component"] != "diagnosis" {
			continue
		}
		matches := []string{}
		for name, network := range backend.NetworkSettings.Networks {
			other, ok := candidate.NetworkSettings.Networks[name]
			if ok && network.NetworkID != "" && network.NetworkID == other.NetworkID {
				matches = append(matches, name)
			}
		}
		if len(matches) == 0 {
			continue
		}
		count++
		if len(matches) != 1 {
			return result, diagnosticError("deployment_ambiguous", "", "")
		}
		sort.Strings(matches)
		name := matches[0]
		network := candidate.NetworkSettings.Networks[name]
		routed := strings.TrimPrefix(candidate.Name, "/") == diagnosisURL.Hostname()
		for _, alias := range network.Aliases {
			routed = routed || alias == diagnosisURL.Hostname()
		}
		port := envValue(candidate.Config.Env, "AGENTLENS_PORT")
		_, exposed := candidate.Config.ExposedPorts[diagnosisURL.Port()+"/tcp"]
		parsedPort, _ := strconv.Atoi(diagnosisURL.Port())
		if !routed || parsedPort < 1 || (port != diagnosisURL.Port() && !exposed) {
			return result, refused
		}
		for _, label := range []string{"aivision.forge.namespace", "com.docker.compose.project"} {
			left, right := backend.Config.Labels[label], candidate.Config.Labels[label]
			if left != "" && right != "" && left != right {
				return result, refused
			}
		}
		result = Host{DaemonID: info.ID, Platform: "linux/" + arch, Socket: socket, BackendID: backend.ID, DiagnosisID: candidate.ID, NetworkName: name, NetworkID: network.NetworkID}
	}
	if count > 1 {
		return Host{}, diagnosticError("deployment_ambiguous", "", "")
	}
	if count != 1 {
		return Host{}, refused
	}
	return result, nil
}
