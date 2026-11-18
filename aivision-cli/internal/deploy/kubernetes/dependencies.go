package kubernetes

import (
	"context"
	"encoding/json"
	"fmt"
	"strconv"
	"strings"

	manifests "github.com/antgroup/ai-vision/aivision-cli/deploy/kubernetes"
)

type dependency struct {
	name, namespace, file string
	install               bool
	controllers           []string
	crds                  []string
}

func dependencyDefaults() []dependency {
	return []dependency{
		{name: "cert-manager", namespace: "cert-manager", file: "dependencies/cert-manager.yaml", controllers: []string{"cert-manager", "cert-manager-cainjector", "cert-manager-webhook"}},
		{name: "ob-operator", namespace: "oceanbase-system", file: "dependencies/ob-operator.yaml", controllers: []string{"oceanbase-controller-manager"}},
	}
}

func compatibleImage(image, component string) bool {
	image = strings.SplitN(image, "@", 2)[0]
	if component == "ob-operator" {
		return strings.HasSuffix(image, "/ob-operator:2.3.4")
	}
	i := strings.LastIndex(image, ":")
	if i < 0 {
		return false
	}
	m := versionPattern.FindStringSubmatch(image[i+1:])
	if len(m) != 4 || m[0] != image[i+1:] {
		return false
	}
	patch, _ := strconv.Atoi(m[3])
	return m[1] == "1" && m[2] == "21" && patch >= 2
}

func (a *App) inspectDependencies(ctx context.Context) error {
	b, err := a.run(ctx, "", nil, "get", "deployments", "--all-namespaces", "-o", "json")
	if err != nil {
		return err
	}
	var deployments object
	if err := json.Unmarshal(b, &deployments); err != nil {
		return err
	}
	crds, err := a.get(ctx, "", "customresourcedefinitions", "")
	if err != nil {
		return err
	}
	existingCRDs := map[string]bool{}
	for _, crd := range list(crds, "items") {
		existingCRDs[str(crd, "metadata", "name")] = true
	}
	a.dependencies = nil
	for _, dep := range dependencyDefaults() {
		b, err := manifests.Files.ReadFile(dep.file)
		if err != nil {
			return err
		}
		docs, err := decodeDocuments(b)
		if err != nil {
			return err
		}
		foundCRDs := 0
		for _, doc := range docs {
			if str(doc, "kind") == "CustomResourceDefinition" {
				name := str(doc, "metadata", "name")
				dep.crds = append(dep.crds, name)
				if existingCRDs[name] {
					foundCRDs++
				}
			}
		}
		found := map[string]string{}
		var ns string
		for _, deployment := range list(deployments, "items") {
			for _, container := range list(deployment, "spec", "template", "spec", "containers") {
				image := str(container, "image")
				component := ""
				for _, candidate := range []string{"cert-manager-controller", "cert-manager-cainjector", "cert-manager-webhook", "ob-operator"} {
					if strings.Contains(image, "/"+candidate+":") || strings.Contains(image, "/"+candidate+"@") {
						component = candidate
						break
					}
				}
				if component == "" || (dep.name == "ob-operator") != (component == "ob-operator") {
					continue
				}
				if !compatibleImage(image, component) {
					return fmt.Errorf("existing %s image %s is outside the validated version range; aiv will not replace it", component, image)
				}
				if found[component] != "" {
					return fmt.Errorf("multiple %s installations found; select a cluster with one compatible installation", component)
				}
				currentNS := str(deployment, "metadata", "namespace")
				if ns != "" && ns != currentNS {
					return fmt.Errorf("%s components span multiple namespaces", dep.name)
				}
				ns = currentNS
				found[component] = str(deployment, "metadata", "name")
			}
		}
		installedNS, err := a.get(ctx, "", "namespace", dep.namespace)
		if err != nil {
			return err
		}
		complete := len(found) == len(dep.controllers) && foundCRDs == len(dep.crds)
		if complete {
			dep.namespace = ns
			dep.controllers = nil
			for _, key := range []string{"cert-manager-controller", "cert-manager-cainjector", "cert-manager-webhook", "ob-operator"} {
				if name := found[key]; name != "" {
					dep.controllers = append(dep.controllers, name)
				}
			}
			fmt.Fprintf(a.out, "    reuse %s in %s\n", dep.name, dep.namespace)
		} else {
			if (foundCRDs != 0 || len(found) != 0 || installedNS != nil) && !owned(installedNS) {
				return fmt.Errorf("incomplete unmanaged %s installation; repair it with its original installer", dep.name)
			}
			dep.install = true
			fmt.Fprintf(a.out, "    %s will be installed/repaired by cast\n", dep.name)
		}
		a.dependencies = append(a.dependencies, dep)
	}
	if existingCRDs["obclusters.oceanbase.oceanbase.com"] {
		cluster, err := a.get(ctx, a.Config.Namespace, "obcluster", "oceanbase")
		if err != nil {
			return err
		}
		if cluster != nil {
			if !owned(cluster) {
				return fmt.Errorf("OceanBase already exists in this namespace and is not managed by aiv")
			}
			if str(cluster, "spec", "observer", "image") != a.Config.Values["OB_IMAGE"] {
				return fmt.Errorf("OB_IMAGE differs from the existing database; automatic database upgrades are not supported")
			}
			for _, key := range []string{"dataStorage", "redoLogStorage", "logStorage"} {
				if str(cluster, "spec", "observer", "storage", key, "storageClass") != a.Config.Values["STORAGE_CLASS"] {
					return fmt.Errorf("STORAGE_CLASS differs from the existing OceanBase volumes; storage migration is not supported")
				}
			}
		}
	}
	return nil
}

func (a *App) ensureDependency(ctx context.Context, dep dependency) error {
	if dep.install {
		if err := a.ensureNamespace(ctx, dep.namespace); err != nil {
			return err
		}
		b, err := manifests.Files.ReadFile(dep.file)
		if err != nil {
			return err
		}
		docs, err := decodeDocuments(b)
		if err != nil {
			return err
		}
		for _, doc := range docs {
			markOwned(doc)
		}
		payload, err := json.Marshal(object{"apiVersion": "v1", "kind": "List", "items": docs})
		if err != nil {
			return err
		}
		// Server-side apply avoids the 256KiB last-applied annotation limit of
		// these large upstream CRDs. Never force ownership conflicts.
		if _, err := a.run(ctx, "", payload, "apply", "--server-side", "--field-manager=aiv-kubernetes", "-f", "-"); err != nil {
			return err
		}
	}
	for _, crd := range dep.crds {
		if _, err := a.run(ctx, "", nil, "wait", "--for=condition=Established", "crd/"+crd, "--timeout="+a.Config.Values["TIMEOUT"], "--request-timeout=0"); err != nil {
			return err
		}
	}
	for _, controller := range dep.controllers {
		if err := a.rolloutWait(ctx, dep.namespace, controller); err != nil {
			return err
		}
	}
	if dep.name == "cert-manager" {
		// Exercising the admission webhook and issuing a certificate catches
		// CA injection/startup races which Deployment readiness alone misses.
		var lastErr error
		if err := a.poll(ctx, func(ctx context.Context) (bool, error) {
			lastErr = a.applyFile(ctx, "certificate-check.yaml")
			return lastErr == nil, nil
		}); err != nil {
			return fmt.Errorf("cert-manager admission webhook did not become ready: %v: %w", lastErr, err)
		}
		_, err := a.run(ctx, a.Config.Namespace, nil, "wait", "--for=condition=Ready", "certificate/aivision-webhook-check", "--timeout="+a.Config.Values["TIMEOUT"], "--request-timeout=0")
		return err
	}
	return nil
}

func (a *App) waitDatabaseAdmission(ctx context.Context) error {
	// Credentials must exist before this dry-run: the OceanBase admission
	// webhook validates every referenced Secret, even for a dry-run request.
	b, err := a.render("database.yaml")
	if err != nil {
		return err
	}
	var lastErr error
	if err := a.poll(ctx, func(ctx context.Context) (bool, error) {
		_, lastErr = a.run(ctx, a.Config.Namespace, b, "apply", "--server-side", "--field-manager=aiv-kubernetes", "--dry-run=server", "-f", "-")
		return lastErr == nil, nil
	}); err != nil {
		return fmt.Errorf("OceanBase admission/conversion webhook is not ready: %v: %w", lastErr, err)
	}
	return nil
}
