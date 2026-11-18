package kubernetes

import (
	"context"
	"fmt"
	"io/fs"
	"math"
	"reflect"
	"strings"
	"testing"
	"time"

	manifests "github.com/antgroup/ai-vision/aivision-cli/deploy/kubernetes"
)

func lifecycleResources(t *testing.T) map[string]object {
	t.Helper()
	a, _ := testApp(t, newCluster())
	a.Config.Values["STORAGE_CLASS"] = "local-path"
	a.orgID = "test-org"
	files, err := fs.Glob(manifests.Files, "*.yaml")
	if err != nil {
		t.Fatal(err)
	}
	resources := map[string]object{}
	for _, file := range files {
		rendered, err := a.render(file)
		if err != nil {
			t.Fatal(err)
		}
		documents, err := decodeDocuments(rendered)
		if err != nil {
			t.Fatal(err)
		}
		for _, document := range documents {
			resources[str(document, "kind")+"/"+str(document, "metadata", "name")] = document
		}
	}
	return resources
}

func TestWorkloadLifecycleContracts(t *testing.T) {
	resources := lifecycleResources(t)
	for name, workload := range resources {
		if str(workload, "kind") != "Deployment" {
			continue
		}
		t.Run(name, func(t *testing.T) {
			if integer(workload, "spec", "replicas") != 1 {
				t.Fatal("fixed deployment has not validated multiple replicas")
			}
			if name == "Deployment/aivision-ingress" {
				if integer(workload, "spec", "strategy", "rollingUpdate", "maxSurge") != 1 ||
					integer(workload, "spec", "strategy", "rollingUpdate", "maxUnavailable") != 0 {
					t.Fatal("ingress must have a bounded one-Pod rollout surge")
				}
			} else if str(workload, "spec", "strategy", "type") != "Recreate" {
				t.Fatal("application startup migrations and metadata writers must not overlap")
			}
			pod := at(workload, "spec", "template", "spec")
			if integer(pod, "terminationGracePeriodSeconds") < 60 {
				t.Fatal("application shutdown needs an explicit grace budget")
			}
			for _, container := range list(pod, "containers") {
				for _, probe := range []string{"startupProbe", "readinessProbe", "livenessProbe"} {
					if integer(container, probe, "timeoutSeconds") < 5 ||
						integer(container, probe, "periodSeconds") < 5 ||
						integer(container, probe, "failureThreshold") < 3 {
						t.Fatalf("%s needs a nontrivial explicit failure allowance", probe)
					}
				}
				startup := integer(container, "startupProbe", "periodSeconds") *
					integer(container, "startupProbe", "failureThreshold")
				deadline := integer(workload, "spec", "progressDeadlineSeconds")
				if deadline < startup+120 || time.Duration(deadline)*time.Second >= (Config{Values: defaults()}).timeout() {
					t.Fatal("rollout and CLI deadlines must allow the entire startup budget plus margin")
				}
				for _, dimension := range []string{"cpu", "memory"} {
					request := quantity(fmt.Sprint(at(container, "resources", "requests", dimension)))
					limit := quantity(fmt.Sprint(at(container, "resources", "limits", dimension)))
					if request <= 0 || limit < request {
						t.Fatalf("missing or invalid %s scheduling/runtime budget", dimension)
					}
				}
			}
		})
	}
	firstContainer := func(name string) any {
		return list(resources["Deployment/"+name], "spec", "template", "spec", "containers")[0]
	}
	for name, paths := range map[string][2]string{
		"aivision-be": {"/api/v1/ready", "/api/v1/health"},
		"agentlens":   {"/internal/readyz", "/internal/healthz"},
	} {
		container := firstContainer(name)
		if str(container, "readinessProbe", "httpGet", "path") != paths[0] ||
			str(container, "livenessProbe", "httpGet", "path") != paths[1] {
			t.Fatalf("%s must check SQL readiness without making SQL outages restart the process", name)
		}
	}
	gateway := firstContainer("antcollector-gw")
	if !reflect.DeepEqual(list(gateway, "readinessProbe", "exec", "command"), []any{"/app/collector", "healthcheck", "--ready"}) {
		t.Fatal("scratch gateway must use its native readiness binary and local-only liveness")
	}
	for _, probe := range []string{"startupProbe", "livenessProbe"} {
		if !reflect.DeepEqual(list(gateway, probe, "exec", "command"), []any{"/app/collector", "healthcheck"}) {
			t.Fatalf("gateway %s must stay local-only instead of restarting on downstream failure", probe)
		}
	}
	if str(firstContainer("signoz-ob"), "livenessProbe", "tcpSocket", "port") != "http" {
		t.Fatal("upstream dependency failures must not cause SQLite-writer restart loops")
	}
	for _, probe := range []string{"startupProbe", "readinessProbe"} {
		if str(firstContainer("signoz-ob"), probe, "httpGet", "path") != "/api/v1/ready" {
			t.Fatalf("data-backend %s must verify storage readiness independently of liveness", probe)
		}
	}
}

func TestInitializationJobsAreBoundedAndSerial(t *testing.T) {
	for name, resource := range lifecycleResources(t) {
		if str(resource, "kind") != "Job" {
			continue
		}
		if integer(resource, "spec", "parallelism") != 1 ||
			integer(resource, "spec", "completions") != 1 ||
			str(resource, "spec", "podReplacementPolicy") != "Failed" ||
			str(resource, "spec", "template", "spec", "restartPolicy") != "Never" {
			t.Fatalf("%s must not overlap initialization attempts", name)
		}
		deadline := integer(resource, "spec", "activeDeadlineSeconds")
		if deadline < 810 || deadline >= int((Config{Values: defaults()}).timeout()/time.Second) {
			t.Fatalf("%s deadline does not cover bounded SQL retries within the CLI wait", name)
		}
		if integer(resource, "spec", "backoffLimit") > 2 ||
			integer(resource, "spec", "template", "spec", "terminationGracePeriodSeconds") != 30 {
			t.Fatalf("%s must bound retry and shutdown", name)
		}
		for _, container := range list(resource, "spec", "template", "spec", "containers") {
			for _, dimension := range []string{"cpu", "memory"} {
				request := quantity(fmt.Sprint(at(container, "resources", "requests", dimension)))
				limit := quantity(fmt.Sprint(at(container, "resources", "limits", dimension)))
				if request <= 0 || limit < request {
					t.Fatalf("%s missing %s job resource budget", name, dimension)
				}
			}
		}
	}
}

func TestDocumentedSchedulingEnvelopeFitsPreflight(t *testing.T) {
	resources := lifecycleResources(t)
	var cpu, memory float64
	add := func(containers []any, multiplier float64) {
		for _, container := range containers {
			cpu += multiplier * quantity(fmt.Sprint(at(container, "resources", "requests", "cpu")))
			memory += multiplier * quantity(fmt.Sprint(at(container, "resources", "requests", "memory")))
		}
	}
	for name, resource := range resources {
		if kind := str(resource, "kind"); kind == "Deployment" || kind == "Job" {
			multiplier := 1.0
			if name == "Deployment/aivision-ingress" {
				multiplier += float64(integer(resource, "spec", "strategy", "rollingUpdate", "maxSurge"))
			}
			add(list(resource, "spec", "template", "spec", "containers"), multiplier)
		}
	}
	observer := at(resources["OBCluster/oceanbase"], "spec", "observer", "resource")
	cpu += quantity(fmt.Sprint(at(observer, "cpu")))
	memory += quantity(fmt.Sprint(at(observer, "memory")))
	operator, err := manifests.Files.ReadFile("dependencies/ob-operator.yaml")
	if err != nil {
		t.Fatal(err)
	}
	documents, err := decodeDocuments(operator)
	if err != nil {
		t.Fatal(err)
	}
	for _, resource := range documents {
		if str(resource, "kind") == "Deployment" {
			add(list(resource, "spec", "template", "spec", "containers"), 1)
		}
	}
	if math.Abs(cpu-3.315) > 0.00001 || memory != 12576*(1<<20) {
		t.Fatalf("requests changed: %.3f CPU / %.0fMi; update lifecycle.md and preflight together", cpu, memory/(1<<20))
	}
	cluster := newCluster()
	node := cluster.obj("node", "", "worker")
	node["status"].(map[string]any)["allocatable"] = map[string]string{"cpu": "3.4", "memory": "14Gi"}
	a, _ := testApp(t, cluster)
	if err := a.checkNodes(context.Background()); err != nil {
		t.Fatal(err)
	}
	node["status"].(map[string]any)["allocatable"] = map[string]string{"cpu": "3.3", "memory": "14Gi"}
	if err := a.checkNodes(context.Background()); err == nil {
		t.Fatal("preflight admitted less CPU than the declared scheduling envelope")
	}
}

type deadlineRecordingRunner struct {
	*fakeCluster
	contexts map[string]context.Context
}

func (r *deadlineRecordingRunner) Run(ctx context.Context, input []byte, args ...string) ([]byte, error) {
	command := strings.Join(args, " ")
	for _, name := range []string{"signoz-ob", "antcollector-gw", "aivision-be", "agentlens", "aivision-fe"} {
		if strings.Contains(command, "rollout status deployment/"+name+" ") {
			r.contexts[name] = ctx
		}
	}
	return r.fakeCluster.Run(ctx, input, args...)
}

func TestApplicationStartupDeadlinesAreIndependent(t *testing.T) {
	cluster := newCluster()
	a, _ := testApp(t, cluster)
	runner := &deadlineRecordingRunner{fakeCluster: cluster, contexts: map[string]context.Context{}}
	a.runner = runner
	if err := a.Cast(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(runner.contexts) != 5 {
		t.Fatal("missing an application rollout")
	}
	seen := map[context.Context]bool{}
	for name, ctx := range runner.contexts {
		if seen[ctx] {
			t.Fatalf("%s startup consumes another application's deadline", name)
		}
		seen[ctx] = true
		if ctx.Err() != context.Canceled {
			t.Fatalf("%s stage did not release its deadline", name)
		}
	}
}
