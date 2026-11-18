// Package sandboxbootstrap prepares an owned OpenShell service on the existing
// AI Vision Docker deployment host. It never runs commands supplied by a plan.
package sandboxbootstrap

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/url"
	"regexp"
	"strconv"
)

const maxPlanBytes = 16384

var hexID = regexp.MustCompile(`^[a-f0-9]{12,64}$`)
var fullID = regexp.MustCompile(`^[a-f0-9]{64}$`)
var digestRef = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._/:-]{0,430}@sha256:[a-f0-9]{64}$`)
var safeVersion = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9.+_-]{0,127}$`)
var safeDNS = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,252}$`)

type Images struct {
	Plugin     string `json:"plugin"`
	Worker     string `json:"worker"`
	Gateway    string `json:"gateway"`
	Runtime    string `json:"runtime"`
	Supervisor string `json:"supervisor"`
}
type Catalog struct {
	SchemaVersion   int               `json:"schema_version"`
	ProductVersion  string            `json:"product_version"`
	ProviderVersion string            `json:"provider_version"`
	ProtocolVersion int               `json:"protocol_version"`
	Images          map[string]Images `json:"images"`
}
type Target struct {
	BackendHostname string `json:"backend_hostname"`
	DiagnosisURL    string `json:"diagnosis_url"`
	DeploymentKey   string `json:"deployment_key"`
	ServiceName     string `json:"service_name"`
	GatewayName     string `json:"gateway_name"`
	Endpoint        string `json:"endpoint"`
}
type Plan struct {
	SchemaVersion int     `json:"schema_version"`
	PlanID        string  `json:"plan_id"`
	Catalog       Catalog `json:"catalog"`
	Target        Target  `json:"target"`
}

func deploymentKey(hostname string) string {
	sum := sha256.Sum256([]byte(hostname))
	return hex.EncodeToString(sum[:])[:16]
}
func planDigest(plan Plan) string {
	raw, _ := json.Marshal(map[string]any{"catalog": plan.Catalog, "target": plan.Target})
	var nested any
	_ = json.Unmarshal(raw, &nested)
	raw, _ = json.Marshal(nested)
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:])
}
func decodeStrict(raw []byte, value any) error {
	if len(raw) > maxPlanBytes {
		return errors.New("bootstrap metadata exceeds its limit")
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if decoder.Decode(value) != nil {
		return errors.New("invalid bootstrap metadata")
	}
	if decoder.Decode(new(any)) != io.EOF {
		return errors.New("invalid bootstrap metadata")
	}
	return nil
}
func DecodePlan(raw []byte) (Plan, error) {
	var plan Plan
	if err := decodeStrict(raw, &plan); err != nil {
		return plan, err
	}
	bad := errors.New("bootstrap catalog or deployment target is unavailable; use a matching qualified package")
	if plan.SchemaVersion != 1 || plan.Catalog.SchemaVersion != 1 || plan.Catalog.ProviderVersion != "0.1.6" || plan.Catalog.ProtocolVersion != 1 || !safeVersion.MatchString(plan.Catalog.ProductVersion) || len(plan.Catalog.Images) < 1 || len(plan.Catalog.Images) > 2 {
		return plan, bad
	}
	for platform, images := range plan.Catalog.Images {
		if platform != "linux/arm64" && platform != "linux/amd64" {
			return plan, bad
		}
		for _, ref := range []string{images.Plugin, images.Worker, images.Gateway, images.Runtime, images.Supervisor} {
			if !digestRef.MatchString(ref) {
				return plan, bad
			}
		}
	}
	target := plan.Target
	parsed, err := url.Parse(target.DiagnosisURL)
	if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.User != nil || parsed.RawQuery != "" || parsed.ForceQuery || parsed.Fragment != "" || (parsed.Path != "" && parsed.Path != "/") || !safeDNS.MatchString(parsed.Hostname()) {
		return plan, bad
	}
	port, err := strconv.Atoi(parsed.Port())
	if err != nil || port < 1 || port > 65535 {
		return plan, bad
	}
	key := deploymentKey(target.BackendHostname)
	name := "aiv-openshell-" + key
	if !hexID.MatchString(target.BackendHostname) || target.DeploymentKey != key || target.ServiceName != name || target.GatewayName != name+"-gateway" || target.Endpoint != "http://"+name+":8091" || plan.PlanID != planDigest(plan) {
		return plan, bad
	}
	return plan, nil
}
