package kubernetes

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/components"
)

func TestRoleSelectorsKeepExistingWorkloadsAndState(t *testing.T) {
	f := newCluster()
	a, out := testApp(t, f)
	if err := a.Cast(context.Background()); err != nil {
		t.Fatal(err)
	}
	writes := len(f.writes)
	out.Reset()
	if err := a.Status(context.Background()); err != nil {
		t.Fatal(err)
	}
	for _, c := range components.All() {
		if !strings.Contains(out.String(), c.ID+"\tready") {
			t.Errorf("status missing canonical role %s: %s", c.ID, out.String())
		}
		workload := f.obj(c.Kubernetes.Kind, a.Config.Namespace, c.Kubernetes.Name)
		if str(workload, "metadata", "labels", "app.kubernetes.io/component") != c.ID {
			t.Errorf("%s lacks role metadata label", c.Kubernetes.Name)
		}
		if c.Kubernetes.Kind == "deployment" {
			if str(workload, "spec", "template", "metadata", "labels", "app.kubernetes.io/component") != c.ID {
				t.Errorf("%s lacks role Pod label", c.Kubernetes.Name)
			}
			if str(workload, "spec", "selector", "matchLabels", "app") != c.Kubernetes.Name {
				t.Errorf("%s changed its immutable legacy selector", c.Kubernetes.Name)
			}
		}
		service := f.obj("Service", a.Config.Namespace, c.Kubernetes.Name)
		if str(service, "metadata", "labels", "app.kubernetes.io/component") != c.ID {
			t.Errorf("%s lacks role Service label", c.Kubernetes.Name)
		}
		selectors := append([]string{c.ID, c.LegacyName}, c.Aliases...)
		for _, selector := range selectors {
			if err := a.Logs(context.Background(), selector, 7, false); err != nil {
				t.Fatalf("logs %s: %v", selector, err)
			}
			call := f.calls[len(f.calls)-1]
			want := c.Kubernetes.Kind + "/" + c.Kubernetes.Name
			if c.Kubernetes.LogSelector != "" {
				want = "-l " + c.Kubernetes.LogSelector
			}
			if !strings.Contains(call, want) {
				t.Errorf("logs %s addressed %q, want existing %q", selector, call, want)
			}
		}
	}
	if len(f.writes) != writes {
		t.Fatal("status/log aliases changed cluster resources")
	}
	if f.obj("PersistentVolumeClaim", a.Config.Namespace, "signoz-metastore") == nil {
		t.Fatal("existing metastore PVC identity changed")
	}
	for job, role := range map[string]string{"aivision-bootstrap": "backend", "aivision-database-init": "database"} {
		obj := f.obj("Job", a.Config.Namespace, job)
		if str(obj, "metadata", "labels", "app.kubernetes.io/component") != role || str(obj, "spec", "template", "metadata", "labels", "app.kubernetes.io/component") != role {
			t.Errorf("%s lacks role Job/Pod labels", job)
		}
	}
}

func TestSavedRoleConfigKeysNormalizeBeforeValidation(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.json")
	if err := os.WriteFile(path, []byte(`{"DATA_BACKEND_IMAGE":"example/saved:v1","SIGNOZ_IMAGE":"example/old:v1","DIAGNOSIS_IMAGE":"example/diagnosis:v1"}`), 0600); err != nil {
		t.Fatal(err)
	}
	values, err := loadConfig(path, false, nil)
	if err != nil || values["SIGNOZ_IMAGE"] != "example/saved:v1" || values["AGENTLENS_IMAGE"] != "example/diagnosis:v1" {
		t.Fatalf("saved aliases were rejected/lost: %#v, %v", values, err)
	}
	values, err = loadConfig(path, false, map[string]string{"SIGNOZ_IMAGE": "example/override:v1"})
	if err != nil || values["SIGNOZ_IMAGE"] != "example/override:v1" {
		t.Fatalf("legacy higher layer did not override saved role: %#v, %v", values, err)
	}
	a, err := New(context.Background(), Options{Runner: newCluster(), StateRoot: t.TempDir(), Overrides: map[string]string{"DATA_BACKEND_IMAGE": "example/direct:v1"}})
	if err != nil || a.Config.Values["SIGNOZ_IMAGE"] != "example/direct:v1" {
		t.Fatalf("direct options role alias: %v", err)
	}
}
