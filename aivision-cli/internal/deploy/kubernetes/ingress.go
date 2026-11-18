package kubernetes

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
)

const ingressNamespaceKey = "aivision.ob-labs.io/namespace"

func (a *App) managedIngress() bool { return a.Config.Values["INGRESS_CONTROLLER"] == "managed" }

// Preflight never adopts an existing controller or silently takes over another
// application's route. Reusing a controller must be explicitly requested.
func (a *App) inspectIngress(ctx context.Context) error {
	className := a.Config.Values["INGRESS_CLASS"]
	class, err := a.get(ctx, "", "ingressclass", className)
	if err != nil {
		return err
	}
	if ownerNamespace := str(class, "metadata", "annotations", ingressNamespaceKey); owned(class) && ownerNamespace != "" && ownerNamespace != a.Config.Namespace {
		return fmt.Errorf("IngressClass %s belongs to a managed controller watching only namespace %s; select a separate managed class or an existing controller that watches namespace %s", className, ownerNamespace, a.Config.Namespace)
	}
	a.ingressClassController = str(class, "spec", "controller")
	if a.managedIngress() {
		if class != nil && (!owned(class) || str(class, "metadata", "annotations", ingressNamespaceKey) != a.Config.Namespace || str(class, "spec", "controller") != "traefik.io/ingress-controller") {
			return fmt.Errorf("IngressClass %s already exists outside this installation; use INGRESS_CONTROLLER=existing to reuse it, or select a different INGRESS_CLASS", className)
		}
		// Catch ownership conflicts before any application configuration changes.
		b, err := a.render("ingress-controller.yaml")
		if err != nil {
			return err
		}
		docs, err := decodeDocuments(b)
		if err != nil {
			return err
		}
		for _, doc := range docs {
			ns, name := str(doc, "metadata", "namespace"), str(doc, "metadata", "name")
			existing, err := a.get(ctx, ns, str(doc, "kind"), name)
			if err != nil {
				return err
			}
			if existing != nil && !owned(existing) {
				return fmt.Errorf("%s/%s exists and is not managed by aiv", str(doc, "kind"), name)
			}
		}
	} else if class == nil {
		return fmt.Errorf("IngressClass %s does not exist; install its controller first or use INGRESS_CONTROLLER=managed", className)
	}
	if secretName := a.Config.Values["INGRESS_TLS_SECRET"]; secretName != "" {
		secret, err := a.get(ctx, a.Config.Namespace, "secret", secretName)
		if err != nil {
			return fmt.Errorf("cannot read INGRESS_TLS_SECRET %s", secretName)
		}
		if str(secret, "type") != "kubernetes.io/tls" || str(secret, "data", "tls.crt") == "" || str(secret, "data", "tls.key") == "" {
			return fmt.Errorf("INGRESS_TLS_SECRET %s must be an existing kubernetes.io/tls Secret with tls.crt and tls.key in namespace %s", secretName, a.Config.Namespace)
		}
	}
	b, err := a.run(ctx, "", nil, "get", "ingresses", "--all-namespaces", "-o", "json")
	if err != nil {
		return err
	}
	var ingresses object
	if err := json.Unmarshal(b, &ingresses); err != nil {
		return err
	}
	for _, ing := range list(ingresses, "items") {
		ns, name := str(ing, "metadata", "namespace"), str(ing, "metadata", "name")
		if ns == a.Config.Namespace && name == "aivision" {
			if str(ing, "metadata", "labels", ownerKey) != ownerValue {
				return fmt.Errorf("Ingress %s/%s exists and is not managed by aiv", ns, name)
			}
			continue
		}
		selected := str(ing, "spec", "ingressClassName")
		if selected == "" {
			selected = str(ing, "metadata", "annotations", "kubernetes.io/ingress.class")
		}
		if selected == "" && str(class, "metadata", "annotations", "ingressclass.kubernetes.io/is-default-class") == "true" {
			selected = className
		}
		if selected != className {
			continue
		}
		for _, rule := range list(ing, "spec", "rules") {
			host := str(rule, "host")
			publicHost := a.Config.Values["PUBLIC_HOST"]
			matches := host == "" || host == publicHost
			if strings.HasPrefix(host, "*.") {
				suffix := strings.TrimPrefix(host, "*")
				prefix := strings.TrimSuffix(publicHost, suffix)
				matches = strings.HasSuffix(publicHost, suffix) && prefix != "" && !strings.Contains(prefix, ".")
			}
			if matches && len(list(rule, "http", "paths")) != 0 {
				return fmt.Errorf("Ingress %s/%s already routes PUBLIC_HOST through class %s; choose a separate host or class", ns, name, className)
			}
		}
	}
	fmt.Fprintf(a.out, "    Ingress class=%s controller=%s endpoint=%s\n", className, a.Config.Values["INGRESS_CONTROLLER"], a.Config.webURL())
	return nil
}

func (a *App) ensureIngress(ctx context.Context) error {
	if a.managedIngress() {
		if err := a.applyFile(ctx, "ingress-controller.yaml"); err != nil {
			return err
		}
		if err := a.rolloutWait(ctx, a.Config.Namespace, "aivision-ingress"); err != nil {
			return err
		}
	}
	return a.applyFile(ctx, "ingress.yaml")
}
