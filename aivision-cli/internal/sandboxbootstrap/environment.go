package sandboxbootstrap

import (
	"context"
	"encoding/json"
	"errors"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	"github.com/antgroup/ai-vision/aivision-cli/internal/sandboxplugin"
)

// EnvironmentError carries only allowlisted public facts to installation progress.
// The terminal error also remains safe; raw Docker output is never attached.
type EnvironmentError struct {
	Diagnostic sandboxplugin.InstallationDiagnostic
}

func (e *EnvironmentError) Error() string {
	switch e.Diagnostic.Code {
	case "docker_version_unsupported":
		return "OpenShell requires Docker Engine 28.0 or newer; update Docker or use a supported execution host"
	case "landlock_unavailable":
		return "the execution host does not provide Landlock; use a host with enabled Landlock ABI 3 or newer"
	case "landlock_abi_unsupported":
		return "OpenShell requires enabled Landlock ABI 3 or newer; use a supported execution host"
	case "docker_context_unsupported":
		return "default setup requires the deployment host's local Docker Unix socket"
	case "docker_permission_denied":
		return "Docker access was denied; ask the deployment administrator to grant access to this daemon"
	case "docker_unavailable":
		return "the local Docker daemon is unavailable; check Docker on the deployment host"
	case "deployment_not_found":
		return "run this command on the matching AI Vision deployment host with one running backend and diagnosis service"
	case "deployment_ambiguous":
		return "multiple matching AI Vision diagnosis services were found; ask the deployment administrator to confirm the target"
	default:
		return "the execution environment could not be verified; inspect the host and matching plugin version before retrying"
	}
}
func diagnosticError(code, actual, required string) error {
	return &EnvironmentError{sandboxplugin.InstallationDiagnostic{Code: code, Actual: actual, Required: required}}
}
func dockerReadError(err error) error {
	if err != nil && (os.IsPermission(err) || strings.Contains(strings.ToLower(err.Error()), "permission denied")) {
		return diagnosticError("docker_permission_denied", "", "")
	}
	return diagnosticError("docker_unavailable", "", "")
}

var numericEngine = regexp.MustCompile(`^([0-9]+(?:\.[0-9]+){1,3})(?:[-+][0-9A-Za-z.-]+)?$`)

func engineVersion(value string) (string, int, bool) {
	match := numericEngine.FindStringSubmatch(value)
	if len(match) != 2 || len(match[1]) > 64 {
		return "", 0, false
	}
	major, err := strconv.Atoi(strings.Split(match[1], ".")[0])
	return match[1], major, err == nil
}

type localEnvironment struct {
	Linux  bool
	Kernel string
	ABI    int
	Err    error
}

func validateEnvironment(info daemonInfo, local localEnvironment) (bool, error) {
	version, major, ok := engineVersion(info.ServerVersion)
	if !ok {
		return false, diagnosticError("environment_probe_failed", "", "")
	}
	if major < 28 {
		return false, diagnosticError("docker_version_unsupported", version, "28.0")
	}
	// A Darwin kernel or another Linux kernel is never a proxy for Docker's kernel.
	// The authenticated plugin checks the actual daemon/workload environment later.
	if !local.Linux || local.Kernel == "" || local.Kernel != info.KernelVersion {
		return true, nil
	}
	if local.Err != nil || local.ABI <= 0 {
		return false, diagnosticError("landlock_unavailable", "0", "3")
	}
	if local.ABI < 3 {
		return false, diagnosticError("landlock_abi_unsupported", strconv.Itoa(local.ABI), "3")
	}
	return false, nil
}

type EnvironmentCheck struct {
	DaemonID      string `json:"daemon_id"`
	DockerVersion string `json:"docker_version"`
	KernelVersion string `json:"kernel_version"`
	LandlockABI   *int   `json:"landlock_abi,omitempty"`
	Deferred      bool   `json:"runtime_check_deferred"`
	Socket        string `json:"-"`
}

// InspectEnvironment performs read-only checks. It is also used by task-scoped
// qualification tools; it never creates private state, pulls images or runs code.
func InspectEnvironment(ctx context.Context, docker Docker) (EnvironmentCheck, error) {
	var result EnvironmentCheck
	raw, err := docker.Run(ctx, "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}")
	var endpoint string
	if err != nil || json.Unmarshal(raw, &endpoint) != nil {
		return result, dockerReadError(err)
	}
	if override := os.Getenv("DOCKER_HOST"); override != "" && os.Getenv("DOCKER_CONTEXT") == "" {
		endpoint = override
	}
	parsed, err := url.Parse(endpoint)
	if err != nil || parsed.Scheme != "unix" || parsed.Host != "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" || !filepath.IsAbs(parsed.Path) {
		return result, diagnosticError("docker_context_unsupported", "", "")
	}
	result.Socket, err = filepath.EvalSymlinks(parsed.Path)
	if err != nil {
		return result, dockerReadError(err)
	}
	actual, err := socketDaemon(ctx, result.Socket)
	if err != nil {
		return result, err
	}
	raw, err = docker.Run(ctx, "info", "--format", "{{json .}}")
	var info daemonInfo
	if err != nil || len(raw) > 1024*1024 || json.Unmarshal(raw, &info) != nil {
		return result, dockerReadError(err)
	}
	if info.ID == "" || actual.ID != info.ID || actual.KernelVersion != info.KernelVersion || actual.ServerVersion != info.ServerVersion || info.OSType != "linux" {
		return result, diagnosticError("environment_probe_failed", "", "")
	}
	result.DaemonID, result.KernelVersion = info.ID, info.KernelVersion
	result.DockerVersion, _, _ = engineVersion(info.ServerVersion)
	local := readLocalEnvironment()
	if local.Linux && local.Kernel == info.KernelVersion && local.Err == nil {
		abi := local.ABI
		result.LandlockABI = &abi
	}
	result.Deferred, err = validateEnvironment(info, local)
	return result, err
}
func runtimeEnvironmentError(health *sandboxplugin.Health) error {
	if health == nil || health.Environment == nil {
		return diagnosticError("environment_probe_failed", "", "")
	}
	probe := health.Environment
	if probe.Status == "failed" && probe.Diagnostic.Valid() {
		return &EnvironmentError{Diagnostic: *probe.Diagnostic}
	}
	version, major, ok := engineVersion(probe.DockerVersion)
	if ok && major < 28 {
		return diagnosticError("docker_version_unsupported", version, "28.0")
	}
	if !ok || probe.Status != "passed" || probe.LandlockABI == nil {
		return diagnosticError("environment_probe_failed", "", "")
	}
	if *probe.LandlockABI < 3 {
		return diagnosticError("landlock_abi_unsupported", strconv.Itoa(*probe.LandlockABI), "3")
	}
	return nil
}
func failureDiagnostic(err error, stage string, contextError error) *sandboxplugin.InstallationDiagnostic {
	code := "environment_probe_failed"
	if contextError == context.Canceled {
		code = "canceled"
	} else if contextError == context.DeadlineExceeded {
		code = "timeout"
	} else {
		var typed *EnvironmentError
		if errors.As(err, &typed) && typed.Diagnostic.Valid() {
			copy := typed.Diagnostic
			return &copy
		}
		switch stage {
		case "images":
			code = "image_download_failed"
		case "configuration":
			code = "configuration_failed"
		case "gateway":
			code = "gateway_start_failed"
		case "plugin":
			code = "plugin_start_failed"
		case "readiness":
			code = "provider_not_ready"
		case "registration":
			code = "registration_failed"
		}
	}
	return &sandboxplugin.InstallationDiagnostic{Code: code}
}
func checkDiscoveredEnvironment(ctx context.Context, docker Docker, host Host) (bool, error) {
	checked, err := InspectEnvironment(ctx, docker)
	if err != nil {
		return false, err
	}
	if checked.DaemonID != host.DaemonID || checked.Socket != host.Socket {
		return false, diagnosticError("environment_probe_failed", "", "")
	}
	return checked.Deferred, nil
}
