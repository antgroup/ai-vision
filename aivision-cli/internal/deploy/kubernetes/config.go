// Package kubernetes deploys the embedded, fixed Kubernetes resources via kubectl.
package kubernetes

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"github.com/antgroup/ai-vision/aivision-cli/internal/distribution"
	"net"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/components"
)

const ownerKey = "aivision.ob-labs.io/managed-by"
const ownerValue = "aiv-kubernetes"
const defaultIngressImage = "traefik:v3.7.13"

var serviceImages = components.ServiceImageComponents()

var dnsLabel = regexp.MustCompile(`^[a-z0-9]([-a-z0-9]*[a-z0-9])?$`)
var dnsName = regexp.MustCompile(`^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$`)
var tagPattern = regexp.MustCompile(`^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$`)

func defaults() map[string]string {
	return map[string]string{
		"IMAGE_REGISTRY": components.ProductImageRegistry, "IMAGE_TAG": "latest", "IMAGE_PULL": "Always",
		"IMAGE_PULL_SECRET": "", "IMAGE_AUTH": "auto", "STORAGE_CLASS": "",
		"PUBLIC_HOST": "127.0.0.1", "PUBLIC_PORT": "5174", "PUBLIC_SCHEME": "http",
		"INGRESS_CONTROLLER": "managed", "INGRESS_CLASS": "aivision",
		"INGRESS_HTTP_NODE_PORT": "30080", "INGRESS_HTTPS_NODE_PORT": "30443",
		"INGRESS_TLS_SECRET": "", "INGRESS_IMAGE": defaultIngressImage,
		"TIMEOUT": "30m", "OB_IMAGE": "oceanbase/oceanbase-cloud-native:4.4.2.1-101000022026050611",
		"SIGNOZ_IMAGE": "", "GATEWAY_IMAGE": "", "BE_IMAGE": "", "AGENTLENS_IMAGE": "", "FE_IMAGE": "",
		"AIVISION_RELEASE_MANIFEST_BASE64":  "",
		"NATIVE_PYTHON_RUNTIME":             distribution.PythonRuntime,
		"AIVISION_TRACE_RESPONSE_MAX_BYTES": "33554432", "AIVISION_TRACE_REQUEST_MAX_BYTES": "67108864", "AIVISION_TRACE_QUERY_CONCURRENCY": "2",
		"BE_CONTAINER_MEMORY": "2G", "SIGNOZ_CONTAINER_MEMORY": "2G",
		"AIVISION_QUERY_MODE": "auto", "SIGNOZ_AIVISION_PROJECTION_MAINTENANCE_ENABLED": "true",
	}
}

type Config struct {
	Namespace string
	Values    map[string]string
}

func validateOverrides(values map[string]string) error {
	known := defaults()
	for key := range values {
		if _, ok := known[key]; !ok {
			return fmt.Errorf("unsupported Kubernetes variable %s (credentials belong in Secrets or AIV_GHCR_* environment variables)", key)
		}
	}
	return nil
}

func validateNamespace(namespace string) error {
	if len(namespace) > 63 || !dnsLabel.MatchString(namespace) || namespace == "default" || namespace == "cert-manager" || namespace == "oceanbase-system" || strings.HasPrefix(namespace, "kube-") {
		return fmt.Errorf("use a dedicated Kubernetes namespace (default: aivision)")
	}
	return nil
}

func (c Config) validate() error {
	if err := validateNamespace(c.Namespace); err != nil {
		return err
	}
	if err := validateOverrides(c.Values); err != nil {
		return err
	}
	v := c.Values
	if distribution.NativeOnly && v["NATIVE_PYTHON_RUNTIME"] != "true" {
		return fmt.Errorf("native distribution requires NATIVE_PYTHON_RUNTIME=true")
	}
	if v["NATIVE_PYTHON_RUNTIME"] != "true" && v["NATIVE_PYTHON_RUNTIME"] != "false" {
		return fmt.Errorf("NATIVE_PYTHON_RUNTIME must be true or false")
	}
	for _, key := range []string{"AIVISION_TRACE_RESPONSE_MAX_BYTES", "AIVISION_TRACE_REQUEST_MAX_BYTES", "AIVISION_TRACE_QUERY_CONCURRENCY"} {
		value, err := strconv.ParseInt(v[key], 10, 64)
		if err != nil || value <= 0 {
			return fmt.Errorf("%s must be a positive integer", key)
		}
	}
	for _, key := range []string{"BE_CONTAINER_MEMORY", "SIGNOZ_CONTAINER_MEMORY"} {
		if !regexp.MustCompile(`^[1-9][0-9]*([kKmMgGtT]([iI][bB]?|[bB])?)?$`).MatchString(v[key]) {
			return fmt.Errorf("invalid %s memory quantity", key)
		}
	}
	if v["AIVISION_QUERY_MODE"] != "auto" && v["AIVISION_QUERY_MODE"] != "raw" {
		return fmt.Errorf("AIVISION_QUERY_MODE must be auto or raw")
	}
	if v["SIGNOZ_AIVISION_PROJECTION_MAINTENANCE_ENABLED"] != "true" && v["SIGNOZ_AIVISION_PROJECTION_MAINTENANCE_ENABLED"] != "false" {
		return fmt.Errorf("SIGNOZ_AIVISION_PROJECTION_MAINTENANCE_ENABLED must be true or false")
	}
	if !tagPattern.MatchString(v["IMAGE_TAG"]) {
		return fmt.Errorf("invalid IMAGE_TAG")
	}
	if v["IMAGE_REGISTRY"] == "" || strings.ContainsAny(v["IMAGE_REGISTRY"], " \t\r\n@") || strings.Contains(v["IMAGE_REGISTRY"], "://") {
		return fmt.Errorf("IMAGE_REGISTRY must be a registry/repository prefix, without a URL scheme")
	}
	switch strings.ToLower(v["IMAGE_PULL"]) {
	case "always":
		v["IMAGE_PULL"] = "Always"
	case "missing", "ifnotpresent":
		v["IMAGE_PULL"] = "IfNotPresent"
	case "never":
		v["IMAGE_PULL"] = "Never"
	default:
		return fmt.Errorf("IMAGE_PULL must be Always, IfNotPresent or Never")
	}
	if v["IMAGE_AUTH"] != "auto" && v["IMAGE_AUTH"] != "anonymous" {
		return fmt.Errorf("IMAGE_AUTH must be auto or anonymous")
	}
	for _, key := range []string{"STORAGE_CLASS", "IMAGE_PULL_SECRET", "INGRESS_CLASS", "INGRESS_TLS_SECRET"} {
		if value := v[key]; value != "" && (len(value) > 253 || !dnsName.MatchString(value)) {
			return fmt.Errorf("invalid %s", key)
		}
	}
	if v["INGRESS_CLASS"] == "" {
		return fmt.Errorf("INGRESS_CLASS is required")
	}
	if v["INGRESS_CONTROLLER"] != "managed" && v["INGRESS_CONTROLLER"] != "existing" {
		return fmt.Errorf("INGRESS_CONTROLLER must be managed or existing")
	}
	if v["INGRESS_IMAGE"] == "" || strings.ContainsAny(v["INGRESS_IMAGE"], " \t\r\n") {
		return fmt.Errorf("INGRESS_IMAGE must be a container image reference")
	}
	host := v["PUBLIC_HOST"]
	if net.ParseIP(host) == nil && (len(host) > 253 || !dnsName.MatchString(strings.ToLower(host))) {
		return fmt.Errorf("PUBLIC_HOST must be an IP address or hostname, without scheme or port")
	}
	v["PUBLIC_HOST"] = strings.ToLower(host)
	if v["PUBLIC_SCHEME"] != "http" && v["PUBLIC_SCHEME"] != "https" {
		return fmt.Errorf("PUBLIC_SCHEME must be http or https")
	}
	if v["PUBLIC_SCHEME"] == "https" && (net.ParseIP(host) != nil || v["INGRESS_TLS_SECRET"] == "") {
		return fmt.Errorf("HTTPS requires a DNS PUBLIC_HOST and an existing INGRESS_TLS_SECRET in the application namespace")
	}
	if v["PUBLIC_SCHEME"] == "http" && v["INGRESS_TLS_SECRET"] != "" {
		return fmt.Errorf("INGRESS_TLS_SECRET requires PUBLIC_SCHEME=https")
	}
	for _, key := range []string{"PUBLIC_PORT"} {
		port, err := strconv.Atoi(v[key])
		if err != nil || port < 1 || port > 65535 {
			return fmt.Errorf("%s must be a TCP port between 1 and 65535", key)
		}
		v[key] = strconv.Itoa(port)
	}
	for _, key := range []string{"INGRESS_HTTP_NODE_PORT", "INGRESS_HTTPS_NODE_PORT"} {
		port, err := strconv.Atoi(v[key])
		if err != nil || port < 30000 || port > 32767 {
			return fmt.Errorf("%s must be a NodePort between 30000 and 32767", key)
		}
		v[key] = strconv.Itoa(port)
	}
	if v["INGRESS_HTTP_NODE_PORT"] == v["INGRESS_HTTPS_NODE_PORT"] {
		return fmt.Errorf("INGRESS_HTTP_NODE_PORT and INGRESS_HTTPS_NODE_PORT must differ")
	}
	d, err := time.ParseDuration(v["TIMEOUT"])
	if err != nil || d < time.Second || d > 2*time.Hour {
		return fmt.Errorf("TIMEOUT must be a duration between 1s and 2h")
	}
	return nil
}

func (c Config) timeout() time.Duration { d, _ := time.ParseDuration(c.Values["TIMEOUT"]); return d }
func (c Config) webURL() string {
	return c.Values["PUBLIC_SCHEME"] + "://" + net.JoinHostPort(c.Values["PUBLIC_HOST"], c.Values["PUBLIC_PORT"])
}
func (c Config) otlpURL() string { return c.webURL() }

func statePath(root, clusterUID, namespace string) string {
	sum := sha256.Sum256([]byte(clusterUID))
	return filepath.Join(root, fmt.Sprintf("%x", sum[:16]), namespace, "config.json")
}

func loadConfig(path string, reset bool, overrides map[string]string) (map[string]string, error) {
	values := defaults()
	if !reset {
		b, err := os.ReadFile(path)
		if err != nil && !os.IsNotExist(err) {
			return nil, err
		}
		if err == nil {
			var saved map[string]string
			if err := json.Unmarshal(b, &saved); err != nil {
				return nil, fmt.Errorf("read Kubernetes state: %w", err)
			}
			saved = components.NormalizeVars(saved)
			if err := validateOverrides(saved); err != nil {
				return nil, fmt.Errorf("read Kubernetes state: %w; rerun with --reset-vars", err)
			}
			for k, v := range saved {
				values[k] = v
			}
		}
	}
	overrides = components.NormalizeVars(overrides)
	if err := validateOverrides(overrides); err != nil {
		return nil, err
	}
	for k, v := range overrides {
		values[k] = v
	}
	return values, nil
}

func (a *App) saveConfig() error {
	if err := os.MkdirAll(filepath.Dir(a.stateFile), 0700); err != nil {
		return err
	}
	b, err := json.MarshalIndent(a.Config.Values, "", "  ")
	if err != nil {
		return err
	}
	f, err := os.CreateTemp(filepath.Dir(a.stateFile), ".config-*")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	if _, err = f.Write(b); err != nil {
		f.Close()
		return err
	}
	if err = f.Close(); err != nil {
		return err
	}
	return os.Rename(f.Name(), a.stateFile)
}
