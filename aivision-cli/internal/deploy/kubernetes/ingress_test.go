package kubernetes

import (
	"context"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestHTTPSConfigurationAndCertificateRotation(t *testing.T) {
	f := newCluster()
	a, _ := testApp(t, f)
	a.Config.Values["PUBLIC_HOST"] = "vision.example.com"
	a.Config.Values["PUBLIC_SCHEME"] = "https"
	a.Config.Values["PUBLIC_PORT"] = "443"
	a.Config.Values["INGRESS_TLS_SECRET"] = "old-cert"
	if err := a.saveConfig(); err != nil {
		t.Fatal(err)
	}
	root := filepath.Dir(filepath.Dir(filepath.Dir(a.stateFile)))
	b, err := New(context.Background(), Options{Runner: f, StateRoot: root, Overrides: map[string]string{"INGRESS_TLS_SECRET": "new-cert"}})
	if err != nil {
		t.Fatal(err)
	}
	if b.Config.Values["INGRESS_TLS_SECRET"] != "new-cert" || b.Config.webURL() != "https://vision.example.com:443" {
		t.Fatal("saved HTTPS configuration was lost during certificate rotation")
	}
	data, err := b.render("ingress.yaml")
	if err != nil {
		t.Fatal(err)
	}
	docs, err := decodeDocuments(data)
	if err != nil {
		t.Fatal(err)
	}
	ing := docs[0]
	if str(ing, "metadata", "annotations", "traefik.ingress.kubernetes.io/router.entrypoints") != "websecure" || str(list(ing, "spec", "tls")[0], "secretName") != "new-cert" || str(list(ing, "spec", "rules")[0], "host") != "vision.example.com" {
		t.Fatal("HTTPS route does not terminate TLS for the advertised host")
	}
	if err := b.inspectIngress(context.Background()); err == nil || !strings.Contains(err.Error(), "INGRESS_TLS_SECRET") {
		t.Fatalf("missing TLS Secret was accepted: %v", err)
	}
	b.Config.Values["PUBLIC_HOST"] = "127.0.0.1"
	if err := b.Config.validate(); err == nil {
		t.Fatal("IP address accepted for a hostname TLS rule")
	}
}

func TestExistingIngressControllerIsReusedWithoutMutation(t *testing.T) {
	f := newCluster()
	a, _ := testApp(t, f)
	class := fixture("IngressClass", "", "external")
	class["spec"] = map[string]any{"controller": "example.com/ingress"}
	f.put(class)
	a.Config.Values["INGRESS_CONTROLLER"] = "existing"
	a.Config.Values["INGRESS_CLASS"] = "external"
	if err := a.Cast(context.Background()); err != nil {
		t.Fatal(err)
	}
	for _, write := range f.writes {
		if strings.Contains(write, "aivision-ingress") || strings.HasPrefix(write, "IngressClass/") {
			t.Fatalf("mutated an external controller: %s", write)
		}
	}
	if !reflect.DeepEqual(f.obj("IngressClass", "", "external")["spec"], class["spec"]) {
		t.Fatal("external IngressClass changed")
	}
	ing := f.obj("Ingress", a.Config.Namespace, "aivision")
	if str(ing, "metadata", "annotations", "traefik.ingress.kubernetes.io/router.entrypoints") != "" {
		t.Fatal("external controller received Traefik-specific configuration")
	}
	delete(f.objects, objectKey("IngressClass", "", "external"))
	if err := a.Gauge(context.Background()); err == nil {
		t.Fatal("missing external controller class accepted")
	}
}

func TestExistingControllerTLSAnnotations(t *testing.T) {
	for _, controller := range []string{"traefik.io/ingress-controller", "example.com/ingress"} {
		t.Run(controller, func(t *testing.T) {
			f := newCluster()
			a, _ := testApp(t, f)
			class := fixture("IngressClass", "", "external")
			class["spec"] = map[string]any{"controller": controller}
			f.put(class)
			a.Config.Values["INGRESS_CONTROLLER"] = "existing"
			a.Config.Values["INGRESS_CLASS"] = "external"
			a.Config.Values["PUBLIC_SCHEME"] = "https"
			a.Config.Values["PUBLIC_HOST"] = "vision.example.com"
			a.Config.Values["INGRESS_TLS_SECRET"] = "certificate"
			f.put(secretObject(a.Config.Namespace, "certificate", "kubernetes.io/tls", map[string]string{"tls.crt": "certificate", "tls.key": "private-key"}))
			if err := a.inspectIngress(context.Background()); err != nil {
				t.Fatal(err)
			}
			data, err := a.render("ingress.yaml")
			if err != nil {
				t.Fatal(err)
			}
			docs, err := decodeDocuments(data)
			if err != nil {
				t.Fatal(err)
			}
			ing := docs[0]
			wantTLS := ""
			if controller == "traefik.io/ingress-controller" {
				wantTLS = "true"
			}
			if str(ing, "metadata", "annotations", "traefik.ingress.kubernetes.io/router.tls") != wantTLS || str(ing, "metadata", "annotations", "traefik.ingress.kubernetes.io/router.entrypoints") != "" {
				t.Fatal("TLS must follow the controller type without assuming an external entrypoint name")
			}
		})
	}
}

func TestExistingManagedControllerMustWatchTheNamespace(t *testing.T) {
	f := newCluster()
	a, _ := testApp(t, f)
	a.Config.Values["INGRESS_CONTROLLER"] = "existing"
	class := fixture("IngressClass", "", "aivision")
	markOwned(class)
	class["metadata"].(map[string]any)["annotations"] = map[string]string{ingressNamespaceKey: "other"}
	class["spec"] = map[string]any{"controller": "traefik.io/ingress-controller"}
	f.put(class)
	if err := a.inspectIngress(context.Background()); err == nil || !strings.Contains(err.Error(), "watching only namespace other") {
		t.Fatalf("out-of-scope controller accepted: %v", err)
	}
}

func TestIngressPreflightRejectsOwnershipAndRouteConflicts(t *testing.T) {
	for _, mode := range []string{"foreign class", "other namespace", "foreign ingress", "duplicate route"} {
		t.Run(mode, func(t *testing.T) {
			f := newCluster()
			a, _ := testApp(t, f)
			switch mode {
			case "foreign class", "other namespace":
				obj := fixture("IngressClass", "", "aivision")
				obj["spec"] = map[string]any{"controller": "traefik.io/ingress-controller"}
				if mode == "other namespace" {
					markOwned(obj)
					obj["metadata"].(map[string]any)["annotations"] = map[string]string{ingressNamespaceKey: "other"}
				}
				f.put(obj)
			default:
				ns, name := "aivision", "aivision"
				if mode == "duplicate route" {
					ns, name = "other", "other"
				}
				obj := fixture("Ingress", ns, name)
				obj["spec"] = map[string]any{"ingressClassName": "aivision", "rules": []any{map[string]any{"http": map[string]any{"paths": []any{map[string]string{"path": "/"}}}}}}
				f.put(obj)
			}
			if err := a.Gauge(context.Background()); err == nil {
				t.Fatal("conflict accepted")
			}
			if len(f.writes) != 0 {
				t.Fatal("preflight mutated existing resources")
			}
		})
	}
}

func TestStatusDetectsIngressFailure(t *testing.T) {
	f := newCluster()
	a, _ := testApp(t, f)
	if err := a.Cast(context.Background()); err != nil {
		t.Fatal(err)
	}
	for _, target := range []struct{ kind, name, namespace string }{{"Deployment", "aivision-ingress", a.Config.Namespace}, {"Ingress", "aivision", a.Config.Namespace}, {"IngressClass", "aivision", ""}} {
		key := objectKey(target.kind, target.namespace, target.name)
		obj := f.objects[key]
		delete(f.objects, key)
		if err := a.Status(context.Background()); err == nil {
			t.Fatalf("missing %s did not affect status", target.kind)
		}
		f.objects[key] = obj
	}
}
