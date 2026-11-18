package sandboxbootstrap

import (
	"encoding/json"
	"strings"
	"testing"
)

func validPlan(t *testing.T) Plan {
	t.Helper()
	image := "registry.example/aiv/native@sha256:" + strings.Repeat("a", 64)
	catalog := Catalog{SchemaVersion: 1, ProductVersion: "source-qa", ProviderVersion: "0.1.6", ProtocolVersion: 1, Images: map[string]Images{"linux/arm64": {Plugin: image, Worker: image, Gateway: image, Runtime: image, Supervisor: image}}}
	target := Target{BackendHostname: "0123456789ab", DiagnosisURL: "http://agentlens:8081"}
	target.DeploymentKey = deploymentKey(target.BackendHostname)
	target.ServiceName = "aiv-openshell-" + target.DeploymentKey
	target.GatewayName = target.ServiceName + "-gateway"
	target.Endpoint = "http://" + target.ServiceName + ":8091"
	plan := Plan{SchemaVersion: 1, Catalog: catalog, Target: target}
	plan.PlanID = planDigest(plan)
	return plan
}
func TestPlanRejectsUnknownUnpinnedAndChangedTarget(t *testing.T) {
	good := validPlan(t)
	raw, _ := json.Marshal(good)
	if _, err := DecodePlan(raw); err != nil {
		t.Fatal(err)
	}
	for _, mutate := range []func(Plan) Plan{
		func(p Plan) Plan {
			p.Catalog.Images = map[string]Images{"linux/arm64": {Plugin: "registry/image:latest"}}
			return p
		},
		func(p Plan) Plan { p.Target.BackendHostname = "some-other-host"; return p },
		func(p Plan) Plan { p.Target.ServiceName = "user-service"; return p },
		func(p Plan) Plan { p.Target.DiagnosisURL = "http://secret@agentlens:8081"; return p },
		func(p Plan) Plan { p.PlanID = strings.Repeat("b", 64); return p },
	} {
		changed := mutate(validPlan(t))
		body, _ := json.Marshal(changed)
		if _, err := DecodePlan(body); err == nil {
			t.Fatal("unsafe/mismatched plan accepted")
		}
	}
	raw = append(raw[:len(raw)-1], []byte(`,"untrusted":true}`)...)
	if _, err := DecodePlan(raw); err == nil {
		t.Fatal("unknown plan field accepted")
	}
}
