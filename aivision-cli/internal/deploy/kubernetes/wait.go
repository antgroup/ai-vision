package kubernetes

import (
	"context"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/components"
)

var regexpOrg = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_-]*$`)

func (a *App) poll(ctx context.Context, fn func(context.Context) (bool, error)) error {
	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		done, err := fn(ctx)
		if err != nil {
			return err
		}
		if done {
			return nil
		}
		timer := time.NewTimer(a.pollInterval)
		select {
		case <-ctx.Done():
			timer.Stop()
			return ctx.Err()
		case <-timer.C:
		}
	}
}

func (a *App) waitStatus(ctx context.Context, resource, name, want string) error {
	return a.poll(ctx, func(ctx context.Context) (bool, error) {
		obj, err := a.get(ctx, a.Config.Namespace, resource, name)
		if err != nil {
			return false, err
		}
		return str(obj, "status", "status") == want, nil
	})
}

func (a *App) rolloutWait(ctx context.Context, ns, name string) error {
	_, err := a.run(ctx, ns, nil, "rollout", "status", "deployment/"+name, "--timeout="+a.Config.Values["TIMEOUT"], "--request-timeout=0")
	return err
}

func (a *App) deploy(ctx context.Context, file, name string) error {
	fmt.Fprintf(a.out, "    starting %s\n", components.Canonical(name))
	if err := a.applyFile(ctx, file); err != nil {
		return err
	}
	return a.rolloutWait(ctx, a.Config.Namespace, name)
}

func (a *App) runJob(ctx context.Context, name, file string) error {
	job, err := a.get(ctx, a.Config.Namespace, "job", name)
	if err != nil {
		return err
	}
	if job != nil {
		if !owned(job) {
			return fmt.Errorf("Job %s exists and is not managed by aiv", name)
		}
		// Job Pod templates are immutable. Delete only this disposable Job,
		// and wait for its Pods to exit before replacing it.
		if _, err := a.run(ctx, a.Config.Namespace, nil, "delete", "job", name, "--cascade=foreground", "--wait=true", "--timeout="+a.Config.Values["TIMEOUT"]); err != nil {
			return err
		}
	}
	if err := a.applyFile(ctx, file); err != nil {
		return err
	}
	return a.poll(ctx, func(ctx context.Context) (bool, error) {
		job, err := a.get(ctx, a.Config.Namespace, "job", name)
		if err != nil {
			return false, err
		}
		for _, cond := range list(job, "status", "conditions") {
			if str(cond, "status") != "True" {
				continue
			}
			if str(cond, "type") == "Failed" {
				return false, fmt.Errorf("Job %s failed: %s", name, str(cond, "reason"))
			}
			if str(cond, "type") == "Complete" {
				return true, nil
			}
		}
		return false, nil
	})
}

func (a *App) diagnostics(ctx context.Context, ns string) {
	ctx, cancel := context.WithTimeout(ctx, 40*time.Second)
	defer cancel()
	fmt.Fprintf(a.out, "Diagnostics (namespace %s):\n", ns)
	if ns == a.Config.Namespace {
		// A repeated cast can fail before the credentials stage. Load the
		// existing redaction values before showing logs from its earlier Pods.
		_, _, _ = a.secretData(ctx, "aivision-secrets")
	}
	for _, args := range [][]string{
		{"get", "pods,pvc", "-o", "wide"},
		{"get", "events", "--field-selector=type=Warning", "--sort-by=.metadata.creationTimestamp"},
	} {
		if b, err := a.run(ctx, ns, nil, args...); err == nil {
			lines := strings.Split(strings.TrimSpace(string(b)), "\n")
			if len(lines) > 25 {
				lines = lines[len(lines)-25:]
			}
			fmt.Fprintln(a.out, a.redact(strings.Join(lines, "\n")))
		}
	}
	pods, err := a.get(ctx, ns, "pods", "")
	if err != nil {
		return
	}
	count := 0
	for _, pod := range list(pods, "items") {
		// Only print this stack's or dependency controller's logs, never logs
		// from unrelated workloads that happen to share a namespace.
		if ns == a.Config.Namespace && str(pod, "metadata", "labels", "app.kubernetes.io/part-of") != "aivision" && str(pod, "metadata", "labels", "ref-obcluster") != "oceanbase" {
			continue
		}
		name := str(pod, "metadata", "name")
		fmt.Fprintf(a.out, "--- %s logs ---\n", name)
		if b, err := a.run(ctx, ns, nil, "logs", name, "--all-containers=true", "--tail=30"); err == nil {
			fmt.Fprintln(a.out, a.redact(string(b)))
		}
		for _, status := range list(pod, "status", "containerStatuses") {
			if integer(status, "restartCount") > 0 {
				if b, err := a.run(ctx, ns, nil, "logs", name, "--container="+str(status, "name"), "--previous", "--tail=20"); err == nil {
					fmt.Fprintln(a.out, a.redact(string(b)))
				}
			}
		}
		count++
		if count == 6 {
			break
		}
	}
}

func (a *App) databaseReady(ctx context.Context) (bool, error) {
	// OBCluster can remain "running" while its observer Pod is being
	// recovered. The tenant and the serving Pod must also be ready.
	tenant, err := a.get(ctx, a.Config.Namespace, "obtenant", "aivision")
	if err != nil || str(tenant, "status", "status") != "running" {
		return false, err
	}
	pods, err := a.get(ctx, a.Config.Namespace, "pods", "")
	if err != nil {
		return false, err
	}
	for _, pod := range list(pods, "items") {
		if str(pod, "metadata", "labels", "ref-obcluster") != "oceanbase" || str(pod, "metadata", "deletionTimestamp") != "" || str(pod, "status", "phase") != "Running" {
			continue
		}
		for _, condition := range list(pod, "status", "conditions") {
			if str(condition, "type") == "Ready" && str(condition, "status") == "True" {
				return true, nil
			}
		}
	}
	return false, nil
}

func (a *App) Status(ctx context.Context) error {
	fmt.Fprintf(a.out, "Context: %s  Namespace: %s\nCOMPONENT\tSTATE\n", a.Context, a.Config.Namespace)
	allReady := true
	items := []struct{ component, resource, name string }{}
	for _, c := range components.All() {
		items = append(items, struct{ component, resource, name string }{c.ID, c.Kubernetes.Kind, c.Kubernetes.Name})
	}
	if a.managedIngress() {
		items = append(items, struct{ component, resource, name string }{"ingress-controller", "deployment", "aivision-ingress"})
	}
	for _, item := range items {
		obj, err := a.get(ctx, a.Config.Namespace, item.resource, item.name)
		if err != nil {
			return err
		}
		state := "absent"
		if obj != nil {
			state = "not ready"
			if item.resource == "obcluster" {
				state = str(obj, "status", "status")
				if state == "running" {
					ready, err := a.databaseReady(ctx)
					if err != nil {
						return err
					}
					state = "not ready"
					if ready {
						state = "ready"
					}
				}
			} else if integer(obj, "status", "observedGeneration") >= integer(obj, "metadata", "generation") && integer(obj, "status", "availableReplicas") == 1 && integer(obj, "status", "updatedReplicas") == 1 {
				state = "ready"
			}
		}
		if state != "ready" {
			allReady = false
		}
		fmt.Fprintf(a.out, "%s\t%s\n", item.component, state)
	}
	ingress, err := a.get(ctx, a.Config.Namespace, "ingress", "aivision")
	if err != nil {
		return err
	}
	class, err := a.get(ctx, "", "ingressclass", a.Config.Values["INGRESS_CLASS"])
	if err != nil {
		return err
	}
	if class == nil || str(ingress, "spec", "ingressClassName") != a.Config.Values["INGRESS_CLASS"] {
		fmt.Fprintln(a.out, "ingress\tabsent or class mismatch")
		allReady = false
	} else {
		fmt.Fprintf(a.out, "ingress\tconfigured (class=%s)\n", a.Config.Values["INGRESS_CLASS"])
	}
	fmt.Fprintf(a.out, "Web: %s\nOTLP HTTP: %s\n", a.Config.webURL(), a.Config.otlpURL())
	if !allReady {
		return fmt.Errorf("some Kubernetes components are not ready")
	}
	return nil
}

func (a *App) Logs(ctx context.Context, component string, tail int, follow bool) error {
	c, known := components.Lookup(component)
	if component == "ingress-controller" && a.managedIngress() {
		c = components.Component{ID: component, Kubernetes: components.Kubernetes{Kind: "deployment", Name: "aivision-ingress"}}
		known = true
	}
	if !known {
		return fmt.Errorf("unknown component %q", component)
	}
	if tail < 0 {
		return fmt.Errorf("-n must be non-negative")
	}
	if _, _, err := a.secretData(ctx, "aivision-secrets"); err != nil {
		return err
	}
	args := []string{"logs"}
	if c.Kubernetes.LogSelector != "" {
		args = append(args, "-l", c.Kubernetes.LogSelector, "--all-containers=true")
	} else {
		args = append(args, c.Kubernetes.Kind+"/"+c.Kubernetes.Name)
	}
	args = append(args, "--tail="+strconv.Itoa(tail))
	if follow {
		args = append(args, "--follow", "--request-timeout=0")
	}
	return a.stream(ctx, a.Config.Namespace, args...)
}
