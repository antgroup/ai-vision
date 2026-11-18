package kubernetes

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"strings"

	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/components"
)

var secretKeys = []string{"OB_ROOT_PASSWORD", "OB_TENANT_PASSWORD", "OB_OPERATOR_PASSWORD", "OB_MONITOR_PASSWORD", "OB_PROXYRO_PASSWORD", "SECRET_KEY", "AIVISION_INGEST_SECRET", "SIGNOZ_INGEST_INTERNAL_KEY", "AGENTLENS_TOKEN", "SIGNOZ_USER_ROOT_PASSWORD"}

func randomSecret() (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	// Hex is safe in MySQL DSNs, URLs and JSON; prefix satisfies password rules.
	return "Aiv1_" + hex.EncodeToString(b), nil
}

func (a *App) rememberSecret(s string) {
	if s != "" {
		a.redactions = append(a.redactions, s, base64.StdEncoding.EncodeToString([]byte(s)))
	}
}

func (a *App) secretData(ctx context.Context, name string) (map[string]string, object, error) {
	obj, err := a.get(ctx, a.Config.Namespace, "secret", name)
	if err != nil || obj == nil {
		return nil, obj, err
	}
	result := make(map[string]string)
	data, _ := obj["data"].(map[string]any)
	for k, v := range data {
		encoded, _ := v.(string)
		decoded, err := base64.StdEncoding.DecodeString(encoded)
		if err != nil {
			return nil, obj, fmt.Errorf("Secret %s has invalid data for %s", name, k)
		}
		result[k] = string(decoded)
		a.rememberSecret(string(decoded))
	}
	return result, obj, nil
}

func secretObject(ns, name, kind string, data map[string]string) object {
	encoded := map[string]string{}
	for k, v := range data {
		encoded[k] = base64.StdEncoding.EncodeToString([]byte(v))
	}
	return object{"apiVersion": "v1", "kind": "Secret", "metadata": map[string]any{"name": name, "namespace": ns}, "type": kind, "data": encoded}
}

func (a *App) createSecret(ctx context.Context, name string, data map[string]string) error {
	obj := secretObject(a.Config.Namespace, name, "Opaque", data)
	markOwned(obj)
	b, _ := json.Marshal(obj)
	if _, err := a.run(ctx, "", b, "create", "-f", "-"); err != nil {
		// A concurrent invocation may have created it. Re-read rather than
		// overwriting a password already used by the database.
		if existing, getErr := a.get(ctx, a.Config.Namespace, "secret", name); getErr == nil && existing != nil {
			return nil
		}
		return fmt.Errorf("could not create Secret %s (response hidden to protect credentials)", name)
	}
	return nil
}

func (a *App) ensureSecrets(ctx context.Context) error {
	data, existing, err := a.secretData(ctx, "aivision-secrets")
	if err != nil {
		return err
	}
	if existing == nil {
		cluster, err := a.get(ctx, a.Config.Namespace, "obcluster", "oceanbase")
		if err != nil {
			return err
		}
		if cluster != nil {
			return fmt.Errorf("aivision-secrets is missing but OceanBase exists; restore the original Secret before redeploying")
		}
		data = map[string]string{}
		for _, key := range secretKeys {
			value, err := randomSecret()
			if err != nil {
				return err
			}
			data[key] = value
			a.rememberSecret(value)
		}
		if err := a.createSecret(ctx, "aivision-secrets", data); err != nil {
			return err
		}
		data, existing, err = a.secretData(ctx, "aivision-secrets")
		if err != nil {
			return err
		}
	}
	if !owned(existing) {
		return fmt.Errorf("aivision-secrets is not managed by aiv; use another namespace")
	}
	for _, key := range secretKeys {
		if data[key] == "" {
			return fmt.Errorf("aivision-secrets is missing %s; restore it instead of generating new credentials", key)
		}
	}
	password := data["OB_TENANT_PASSWORD"]
	// Passwords created by aiv are URL-safe. Do not insert a manually rotated,
	// unescaped password into a connection string.
	if strings.ContainsAny(password, "@:/?#%\\\"' \r\n") {
		return fmt.Errorf("OB_TENANT_PASSWORD must use URL-safe characters")
	}
	for _, item := range []struct{ name, key string }{{"aivision-ob-root", "OB_ROOT_PASSWORD"}, {"aivision-ob-tenant", "OB_TENANT_PASSWORD"}, {"aivision-ob-operator", "OB_OPERATOR_PASSWORD"}, {"aivision-ob-monitor", "OB_MONITOR_PASSWORD"}, {"aivision-ob-proxyro", "OB_PROXYRO_PASSWORD"}} {
		values, obj, err := a.secretData(ctx, item.name)
		if err != nil {
			return err
		}
		if obj == nil {
			if err := a.createSecret(ctx, item.name, map[string]string{"password": data[item.key]}); err != nil {
				return err
			}
			values, obj, err = a.secretData(ctx, item.name)
			if err != nil {
				return err
			}
		}
		if !owned(obj) || values["password"] != data[item.key] {
			return fmt.Errorf("Secret %s does not match aivision-secrets; restore consistent database credentials", item.name)
		}
	}
	for name, values := range map[string]map[string]string{
		"aivision-backend":   {"DB_PASSWORD": password, "SECRET_KEY": data["SECRET_KEY"], "AIVISION_INGEST_SECRET": data["AIVISION_INGEST_SECRET"], "AGENTLENS_TOKEN": data["AGENTLENS_TOKEN"], "SIGNOZ_USER_ROOT_PASSWORD": data["SIGNOZ_USER_ROOT_PASSWORD"]},
		"aivision-gateway":   {"AIVISION_INGEST_SECRET": data["AIVISION_INGEST_SECRET"], "SIGNOZ_INGEST_INTERNAL_KEY": data["SIGNOZ_INGEST_INTERNAL_KEY"]},
		"aivision-signoz":    {"SIGNOZ_INGEST_INTERNAL_KEY": data["SIGNOZ_INGEST_INTERNAL_KEY"], "SIGNOZ_TELEMETRYSTORE_OCEANBASE_DSN": "root@aivision:" + password + "@tcp(oceanbase:2881)/signoz?charset=utf8mb4"},
		"aivision-agentlens": {"AGENTLENS_TOKEN": data["AGENTLENS_TOKEN"], "AGENTLENS_OB_URL": "mysql+pymysql://root%40aivision:" + password + "@oceanbase:2881/agentlens?charset=utf8mb4", "SIGNOZ_OB_URL": "mysql+pymysql://root%40aivision:" + password + "@oceanbase:2881/signoz?charset=utf8mb4"},
	} {
		old, err := a.get(ctx, a.Config.Namespace, "secret", name)
		if err != nil {
			return err
		}
		if old != nil && !owned(old) {
			return fmt.Errorf("Secret %s exists and is not managed by aiv", name)
		}
		for _, value := range values {
			a.rememberSecret(value)
		}
		if err := a.applyObject(ctx, secretObject(a.Config.Namespace, name, "Opaque", values), true); err != nil {
			return err
		}
	}
	return nil
}

func (a *App) ensurePullSecret(ctx context.Context) error {
	v := a.Config.Values
	if v["IMAGE_AUTH"] == "anonymous" {
		// Switching from private to public images must also remove the saved
		// pull-secret reference from the ServiceAccount template.
		v["IMAGE_PULL_SECRET"] = ""
		return nil
	}
	name := v["IMAGE_PULL_SECRET"]
	if name == "" {
		name = "aivision-registry"
	}
	data, existing, err := a.secretData(ctx, name)
	if err != nil {
		return err
	}
	username, token := os.Getenv("AIV_GHCR_USERNAME"), os.Getenv("AIV_GHCR_TOKEN")
	a.rememberSecret(token)
	if v["IMAGE_PULL_SECRET"] != "" && name != "aivision-registry" {
		if existing == nil || str(existing, "type") != "kubernetes.io/dockerconfigjson" || data[".dockerconfigjson"] == "" {
			return fmt.Errorf("IMAGE_PULL_SECRET=%s must reference a dockerconfigjson Secret in namespace %s", name, a.Config.Namespace)
		}
		return nil
	}
	if existing != nil && username == "" && token == "" {
		if str(existing, "type") != "kubernetes.io/dockerconfigjson" || data[".dockerconfigjson"] == "" {
			return fmt.Errorf("invalid image pull Secret %s", name)
		}
		v["IMAGE_PULL_SECRET"] = name
		return nil
	}
	if (username == "") != (token == "") {
		return fmt.Errorf("set both AIV_GHCR_USERNAME and AIV_GHCR_TOKEN")
	}
	if username == "" {
		defaultIngress := !a.managedIngress() || v["INGRESS_IMAGE"] == defaultIngressImage
		if v["IMAGE_PULL_SECRET"] == "" && defaultIngress && usesPublicServiceImages(a.templateData()) {
			return nil
		}
		if a.credentials == nil {
			return fmt.Errorf("private images need IMAGE_PULL_SECRET or AIV_GHCR_USERNAME/AIV_GHCR_TOKEN; for public images set IMAGE_AUTH=anonymous")
		}
		username, token, err = a.credentials()
		if err != nil {
			return err
		}
		a.rememberSecret(token)
	}
	if username == "" || token == "" {
		return fmt.Errorf("GHCR username and token must not be empty")
	}
	if existing != nil && !owned(existing) {
		return fmt.Errorf("refusing to overwrite unmanaged image pull Secret %s", name)
	}
	encodedAuth := base64.StdEncoding.EncodeToString([]byte(username + ":" + token))
	a.rememberSecret(encodedAuth)
	b, _ := json.Marshal(map[string]any{"auths": map[string]any{"ghcr.io": map[string]string{"auth": encodedAuth}}})
	a.rememberSecret(string(b))
	if err := a.applyObject(ctx, secretObject(a.Config.Namespace, name, "kubernetes.io/dockerconfigjson", map[string]string{".dockerconfigjson": string(b)}), true); err != nil {
		return err
	}
	v["IMAGE_PULL_SECRET"] = name
	return nil
}

func usesPublicServiceImages(images map[string]string) bool {
	for key, component := range serviceImages {
		repository := components.ProductImageRegistry + "/" + component.ID
		ref := images[key]
		if !strings.HasPrefix(ref, repository+":") && !strings.HasPrefix(ref, repository+"@sha256:") {
			return false
		}
	}
	return true
}
