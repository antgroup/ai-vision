package components

import "testing"

func TestRegistryIdentitiesAreUnambiguous(t *testing.T) {
	selectors := map[string]string{}
	configKeys := map[string]string{}
	for _, c := range All() {
		if c.ID == "" || c.LegacyName == "" || c.DisplayName == "" || c.Kubernetes.Name == "" {
			t.Fatalf("incomplete component: %+v", c)
		}
		for _, name := range append([]string{c.ID, c.LegacyName, c.DockerName, c.Kubernetes.Name}, c.Aliases...) {
			if existing := selectors[name]; existing != "" && existing != c.ID {
				t.Errorf("selector %q maps to both %s and %s", name, existing, c.ID)
			}
			selectors[name] = c.ID
			if Canonical(name) != c.ID || Legacy(name) != c.LegacyName {
				t.Errorf("selector %q did not retain its deployment identity", name)
			}
		}
		for role, old := range c.ConfigAliases {
			if existing := configKeys[old]; existing != "" && existing != role {
				t.Errorf("legacy config key %q has ambiguous display aliases", old)
			}
			configKeys[old] = role
		}
	}
	if Canonical("custom-component") != "custom-component" || Legacy("custom-component") != "custom-component" {
		t.Fatal("custom blueprint component name was rewritten")
	}
}

func TestNormalizeVarsPreservesLayersAndUnknownKeys(t *testing.T) {
	low := NormalizeVars(map[string]string{"DATA_BACKEND_IMAGE": "low", "CUSTOM": "untouched"})
	high := NormalizeVars(map[string]string{"SIGNOZ_IMAGE": "high"})
	for key, value := range high {
		low[key] = value
	}
	if low["SIGNOZ_IMAGE"] != "high" || low["CUSTOM"] != "untouched" {
		t.Fatal("aliases changed precedence or custom variables", low)
	}
	both := NormalizeVars(map[string]string{"SIGNOZ_IMAGE": "legacy", "DATA_BACKEND_IMAGE": "role"})
	if both["SIGNOZ_IMAGE"] != "role" || len(both) != 1 {
		t.Fatal("role key must win within one map", both)
	}
}

func TestQueryControlsHaveOneImplementationKeyPerRoleAlias(t *testing.T) {
	for _, item := range []struct{ role, legacy string }{
		{"BACKEND_QUERY_MODE", "AIVISION_QUERY_MODE"},
		{"DATA_BACKEND_PROJECTION_MAINTENANCE_ENABLED", "SIGNOZ_AIVISION_PROJECTION_MAINTENANCE_ENABLED"},
	} {
		if VarKey(item.role) != item.legacy || DisplayVar(item.legacy) != item.role {
			t.Errorf("query control alias %s does not resolve to %s", item.role, item.legacy)
		}
		values := NormalizeVars(map[string]string{item.role: "role", item.legacy: "legacy"})
		if values[item.legacy] != "role" || len(values) != 1 {
			t.Errorf("query control role alias must win without adding a control: %#v", values)
		}
	}
}
