package kubernetes

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"regexp"
	"strconv"
	"strings"
)

func (a *App) Gauge(ctx context.Context) error {
	fmt.Fprintf(a.out, "==> Kubernetes preflight: context=%s namespace=%s\n", a.Context, a.Config.Namespace)
	ctx, cancel := context.WithTimeout(ctx, a.Config.timeout())
	defer cancel()
	b, err := a.run(ctx, "", nil, "get", "--raw=/version")
	if err != nil {
		return err
	}
	var version object
	if err := json.Unmarshal(b, &version); err != nil {
		return err
	}
	if err := checkVersion(str(version, "gitVersion")); err != nil {
		return err
	}
	if err := a.checkNodes(ctx); err != nil {
		return err
	}
	if err := a.checkStorage(ctx); err != nil {
		return err
	}
	if err := a.checkPorts(ctx); err != nil {
		return err
	}
	if err := a.inspectIngress(ctx); err != nil {
		return err
	}
	if err := a.inspectDependencies(ctx); err != nil {
		return err
	}
	if err := a.checkPermissions(ctx); err != nil {
		return err
	}
	fmt.Fprintf(a.out, "    preflight passed; StorageClass=%s (PVC requests: 130Gi; provision at least 140Gi of backing disk)\n", a.Config.Values["STORAGE_CLASS"])
	return nil
}

var versionPattern = regexp.MustCompile(`^v?(\d+)\.(\d+)\.(\d+)`)

func checkVersion(version string) error {
	m := versionPattern.FindStringSubmatch(version)
	if len(m) == 4 {
		minor, _ := strconv.Atoi(m[2])
		if m[1] == "1" && minor >= 33 && minor <= 36 {
			return nil
		}
	}
	return fmt.Errorf("Kubernetes %s is outside the supported 1.33–1.36 range", version)
}

var quantityPattern = regexp.MustCompile(`^([0-9]+(?:\.[0-9]+)?)([a-zA-Z]*)$`)

func quantity(s string) float64 {
	m := quantityPattern.FindStringSubmatch(s)
	if len(m) != 3 {
		return 0
	}
	n, _ := strconv.ParseFloat(m[1], 64)
	scale, ok := map[string]float64{"": 1, "n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18, "Ki": 1024, "Mi": 1 << 20, "Gi": 1 << 30, "Ti": 1 << 40, "Pi": 1 << 50, "Ei": 1 << 60}[m[2]]
	if !ok {
		return 0
	}
	return n * scale
}

func (a *App) checkNodes(ctx context.Context) error {
	nodes, err := a.get(ctx, "", "nodes", "")
	if err != nil {
		return err
	}
	capacity := map[string][2]float64{}
	for _, node := range list(nodes, "items") {
		if at(node, "spec", "unschedulable") == true {
			continue
		}
		arch := str(node, "metadata", "labels", "kubernetes.io/arch")
		if str(node, "metadata", "labels", "kubernetes.io/os") != "linux" || (arch != "amd64" && arch != "arm64") {
			continue
		}
		ready, tainted := false, false
		for _, condition := range list(node, "status", "conditions") {
			if str(condition, "type") == "Ready" && str(condition, "status") == "True" {
				ready = true
			}
		}
		for _, taint := range list(node, "spec", "taints") {
			if effect := str(taint, "effect"); effect == "NoSchedule" || effect == "NoExecute" {
				tainted = true
			}
		}
		if ready && !tainted {
			capacity[str(node, "metadata", "name")] = [2]float64{quantity(str(node, "status", "allocatable", "cpu")), quantity(str(node, "status", "allocatable", "memory"))}
		}
	}
	// Subtract requests of other workloads. This stack's existing Pods are
	// replaced, so repeating cast must not count their requests twice.
	b, err := a.run(ctx, "", nil, "get", "pods", "--all-namespaces", "-o", "json")
	if err != nil {
		return err
	}
	var pods object
	if err := json.Unmarshal(b, &pods); err != nil {
		return err
	}
	for _, pod := range list(pods, "items") {
		if phase := str(pod, "status", "phase"); phase == "Succeeded" || phase == "Failed" {
			continue
		}
		if str(pod, "metadata", "namespace") == a.Config.Namespace && (str(pod, "metadata", "labels", "app.kubernetes.io/part-of") == "aivision" || str(pod, "metadata", "labels", "ref-obcluster") == "oceanbase") {
			continue
		}
		name := str(pod, "spec", "nodeName")
		c, ok := capacity[name]
		if !ok {
			continue
		}
		var cpu, memory float64
		for _, container := range list(pod, "spec", "containers") {
			cpu += quantity(str(container, "resources", "requests", "cpu"))
			memory += quantity(str(container, "resources", "requests", "memory"))
		}
		for _, container := range list(pod, "spec", "initContainers") {
			cpu = math.Max(cpu, quantity(str(container, "resources", "requests", "cpu")))
			memory = math.Max(memory, quantity(str(container, "resources", "requests", "memory")))
		}
		capacity[name] = [2]float64{c[0] - cpu, c[1] - memory}
	}
	var cpu, memory float64
	fitsDB := false
	for _, c := range capacity {
		cpu += math.Max(0, c[0])
		memory += math.Max(0, c[1])
		if c[0] >= 2 && c[1] >= 10*(1<<30) {
			fitsDB = true
		}
	}
	// Covers the declared stack, both one-shot Jobs and one ingress surge Pod
	// (3.315 CPU / 12.28125Gi), with headroom. Pinned cert-manager has no requests:
	// this is a scheduling floor, not a bound on runtime memory or burst demand.
	if !fitsDB || cpu < 3.4 || memory < 14*(1<<30) {
		return fmt.Errorf("insufficient schedulable resources (%.1f CPU, %.1f GiB free by requests); provision at least 4 CPU/16Gi, with 2 CPU/10Gi on one untainted Linux node", cpu, memory/(1<<30))
	}
	return nil
}

func (a *App) checkStorage(ctx context.Context) error {
	classes, err := a.get(ctx, "", "storageclasses", "")
	if err != nil {
		return err
	}
	selected := a.Config.Values["STORAGE_CLASS"]
	if selected == "" {
		for _, sc := range list(classes, "items") {
			if str(sc, "metadata", "annotations", "storageclass.kubernetes.io/is-default-class") == "true" || str(sc, "metadata", "annotations", "storageclass.beta.kubernetes.io/is-default-class") == "true" {
				if selected != "" {
					return fmt.Errorf("multiple default StorageClasses; set --var STORAGE_CLASS=<name>")
				}
				selected = str(sc, "metadata", "name")
			}
		}
	}
	found := false
	for _, sc := range list(classes, "items") {
		if str(sc, "metadata", "name") == selected {
			found = true
			provisioner := strings.ToLower(str(sc, "provisioner"))
			if strings.Contains(provisioner, "nfs") || strings.Contains(provisioner, "cephfs") {
				return fmt.Errorf("StorageClass %s uses %s; OceanBase requires a supported block/local filesystem", selected, provisioner)
			}
		}
	}
	if selected == "" || !found {
		return fmt.Errorf("no matching StorageClass; set --var STORAGE_CLASS=<name>")
	}
	a.Config.Values["STORAGE_CLASS"] = selected
	// Changing this field on an existing PVC cannot migrate the database.
	pvc, err := a.get(ctx, a.Config.Namespace, "persistentvolumeclaims", "signoz-metastore")
	if err != nil {
		return err
	}
	if pvc != nil && str(pvc, "spec", "storageClassName") != selected {
		return fmt.Errorf("STORAGE_CLASS differs from the existing data-backend PVC; storage migration is not supported")
	}
	return nil
}

func (a *App) checkPorts(ctx context.Context) error {
	if !a.managedIngress() {
		return nil
	}
	b, err := a.run(ctx, "", nil, "get", "services", "--all-namespaces", "-o", "json")
	if err != nil {
		return err
	}
	var services object
	if err := json.Unmarshal(b, &services); err != nil {
		return err
	}
	ports := map[string]string{a.Config.Values["INGRESS_HTTP_NODE_PORT"]: "aivision-ingress", a.Config.Values["INGRESS_HTTPS_NODE_PORT"]: "aivision-ingress"}
	for _, svc := range list(services, "items") {
		for _, port := range list(svc, "spec", "ports") {
			if protocol := str(port, "protocol"); protocol != "" && protocol != "TCP" {
				continue
			}
			n := strconv.Itoa(integer(port, "nodePort"))
			expected, relevant := ports[n]
			if !relevant {
				continue
			}
			if str(svc, "metadata", "namespace") != a.Config.Namespace || str(svc, "metadata", "name") != expected || str(svc, "metadata", "labels", ownerKey) != ownerValue {
				return fmt.Errorf("NodePort %s is already used by %s/%s", n, str(svc, "metadata", "namespace"), str(svc, "metadata", "name"))
			}
		}
	}
	// A local cluster may bind the public host ports before Services exist to
	// forward them to NodePorts. That listener is not a conflict; cast
	// verifies actual access after rollout.
	return nil
}

func (a *App) can(ctx context.Context, ns, verb, resource string) error {
	base, subresource, _ := strings.Cut(resource, "/")
	args := []string{"auth", "can-i", verb, base}
	if subresource != "" {
		args = append(args, "--subresource="+subresource)
	}
	b, err := a.run(ctx, ns, nil, args...)
	if err != nil || strings.TrimSpace(string(b)) != "yes" {
		return fmt.Errorf("Kubernetes permission required: %s %s (namespace %s)", verb, resource, ns)
	}
	return nil
}

func (a *App) checkPermissions(ctx context.Context) error {
	for _, verb := range []string{"get", "create", "patch"} {
		for _, resource := range []string{"secrets", "configmaps", "serviceaccounts", "services", "persistentvolumeclaims", "deployments.apps", "jobs.batch", "obclusters.oceanbase.oceanbase.com", "obtenants.oceanbase.oceanbase.com", "issuers.cert-manager.io", "certificates.cert-manager.io", "ingresses.networking.k8s.io"} {
			if err := a.can(ctx, a.Config.Namespace, verb, resource); err != nil {
				return err
			}
		}
	}
	for _, check := range []struct{ ns, verb, resource string }{
		{"", "create", "namespaces"}, {"", "patch", "namespaces"},
		{a.Config.Namespace, "delete", "jobs.batch"}, {a.Config.Namespace, "create", "pods/exec"}, {a.Config.Namespace, "get", "pods/log"},
		{a.Config.Namespace, "list", "pods"}, {a.Config.Namespace, "list", "events"},
		{a.Config.Namespace, "watch", "deployments.apps"}, {a.Config.Namespace, "watch", "jobs.batch"},
		{a.Config.Namespace, "watch", "certificates.cert-manager.io"}, {"", "watch", "customresourcedefinitions.apiextensions.k8s.io"},
	} {
		if err := a.can(ctx, check.ns, check.verb, check.resource); err != nil {
			return err
		}
	}
	if a.managedIngress() {
		// RBAC escalation prevention requires holding every permission granted
		// to the controller, in addition to creating its resources.
		ns := a.Config.Namespace
		checks := []struct{ ns, verb, resource string }{{ns, "update", "ingresses.networking.k8s.io/status"}}
		for _, verb := range []string{"get", "create", "patch"} {
			for _, resource := range []string{"roles.rbac.authorization.k8s.io", "rolebindings.rbac.authorization.k8s.io"} {
				checks = append(checks, struct{ ns, verb, resource string }{ns, verb, resource})
			}
			for _, resource := range []string{"clusterroles.rbac.authorization.k8s.io", "clusterrolebindings.rbac.authorization.k8s.io", "ingressclasses.networking.k8s.io"} {
				checks = append(checks, struct{ ns, verb, resource string }{"", verb, resource})
			}
		}
		for _, verb := range []string{"get", "list", "watch"} {
			for _, resource := range []string{"services", "secrets", "endpointslices.discovery.k8s.io", "ingresses.networking.k8s.io"} {
				checks = append(checks, struct{ ns, verb, resource string }{ns, verb, resource})
			}
			for _, resource := range []string{"nodes", "ingressclasses.networking.k8s.io"} {
				checks = append(checks, struct{ ns, verb, resource string }{"", verb, resource})
			}
		}
		for _, check := range checks {
			if err := a.can(ctx, check.ns, check.verb, check.resource); err != nil {
				return fmt.Errorf("installing managed Ingress controller: %w", err)
			}
		}
	}
	for _, dep := range a.dependencies {
		if err := a.can(ctx, dep.namespace, "watch", "deployments.apps"); err != nil {
			return err
		}
		if !dep.install {
			continue
		}
		// Installing cluster-wide CRDs/RBAC and admission webhooks requires
		// cluster-admin privileges. Reusing existing dependencies does not.
		if err := a.can(ctx, "", "*", "*"); err != nil {
			return fmt.Errorf("installing %s: %w", dep.name, err)
		}
	}
	return nil
}
