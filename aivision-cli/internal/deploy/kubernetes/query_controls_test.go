package kubernetes

import (
	"context"
	"reflect"
	"strings"
	"testing"
)

func TestQueryRecoveryControlsReachRenderedWorkloads(t *testing.T) {
	for _, scenario := range []struct {
		name, query, maintenance string
		overrides                map[string]string
	}{
		{"default", "auto", "true", nil},
		{"legacy-emergency", "raw", "false", map[string]string{
			"AIVISION_QUERY_MODE": "raw", "SIGNOZ_AIVISION_PROJECTION_MAINTENANCE_ENABLED": "false",
		}},
		{"role-wins", "raw", "false", map[string]string{
			"AIVISION_QUERY_MODE": "auto", "BACKEND_QUERY_MODE": "raw",
			"SIGNOZ_AIVISION_PROJECTION_MAINTENANCE_ENABLED": "true", "DATA_BACKEND_PROJECTION_MAINTENANCE_ENABLED": "false",
		}},
	} {
		t.Run(scenario.name, func(t *testing.T) {
			query, maintenance, overrides := scenario.query, scenario.maintenance, scenario.overrides
			a, err := New(context.Background(), Options{Runner: newCluster(), StateRoot: t.TempDir(), Overrides: overrides})
			if err != nil {
				t.Fatal(err)
			}
			data, err := a.render("config.yaml")
			if err != nil {
				t.Fatal(err)
			}
			docs, err := decodeDocuments(data)
			if err != nil {
				t.Fatal(err)
			}
			if got := str(docs[0], "data", "AIVISION_QUERY_MODE"); got != query {
				t.Fatalf("reader = %q, want %q", got, query)
			}
			data, err = a.render("signoz.yaml")
			if err != nil {
				t.Fatal(err)
			}
			docs, err = decodeDocuments(data)
			if err != nil {
				t.Fatal(err)
			}
			container := docs[1]["spec"].(map[string]any)["template"].(map[string]any)["spec"].(map[string]any)["containers"].([]any)[0].(map[string]any)
			found := false
			for _, raw := range container["env"].([]any) {
				entry := raw.(map[string]any)
				if entry["name"] == "SIGNOZ_AIVISION_PROJECTION_MAINTENANCE_ENABLED" {
					found = entry["value"] == maintenance
				}
			}
			if !found {
				t.Fatal("maintenance control missing or wrong in SigNoz workload")
			}
			data, err = a.render("backend.yaml")
			if err != nil {
				t.Fatal(err)
			}
			docs, err = decodeDocuments(data)
			if err != nil {
				t.Fatal(err)
			}
			backend := list(docs[0], "spec", "template", "spec", "containers")[0]
			if len(list(backend, "command")) != 0 || len(list(backend, "args")) != 0 {
				t.Error("backend must inherit its supervised image entrypoint")
			}
			data, err = a.render("bootstrap.yaml")
			if err != nil {
				t.Fatal(err)
			}
			docs, err = decodeDocuments(data)
			if err != nil {
				t.Fatal(err)
			}
			bootstrap := list(docs[0], "spec", "template", "spec", "containers")[0]
			if !reflect.DeepEqual(list(bootstrap, "command"), []any{"/bin/sh", "-ec"}) {
				t.Error("one-shot bootstrap must override the API/projector image entrypoint")
			}
			for _, arg := range list(bootstrap, "args") {
				if text := arg.(string); strings.Contains(text, "serve_with_projector") || strings.Contains(text, "maintain_trace_read_model") {
					t.Error("one-shot bootstrap starts a persistent projector")
				}
			}
		})
	}
}

func TestQueryRecoveryControlValidation(t *testing.T) {
	for _, item := range []struct{ key, value string }{
		{"AIVISION_QUERY_MODE", "shadow"},
		{"SIGNOZ_AIVISION_PROJECTION_MAINTENANCE_ENABLED", "sometimes"},
	} {
		values := defaults()
		values[item.key] = item.value
		err := (Config{Namespace: "aivision", Values: values}).validate()
		if err == nil || !strings.Contains(err.Error(), item.key) {
			t.Fatalf("%s=%q: expected explicit validation error, got %v", item.key, item.value, err)
		}
	}
}

func TestQueryRecoveryRoleAliasesRejectInvalidValues(t *testing.T) {
	for _, item := range []struct{ role, value, legacy string }{
		{"BACKEND_QUERY_MODE", "shadow", "AIVISION_QUERY_MODE"},
		{"DATA_BACKEND_PROJECTION_MAINTENANCE_ENABLED", "sometimes", "SIGNOZ_AIVISION_PROJECTION_MAINTENANCE_ENABLED"},
	} {
		_, err := New(context.Background(), Options{Runner: newCluster(), StateRoot: t.TempDir(), Overrides: map[string]string{item.role: item.value}})
		if err == nil || !strings.Contains(err.Error(), item.legacy) {
			t.Errorf("%s=%q: expected value validation for %s, got %v", item.role, item.value, item.legacy, err)
		}
	}
}

func TestMemoryBudgetsReachSupportedKubernetes(t *testing.T) {
	a, err := New(context.Background(), Options{Runner: newCluster(), StateRoot: t.TempDir(), Overrides: map[string]string{
		"AIVISION_TRACE_RESPONSE_MAX_BYTES": "1048576", "AIVISION_TRACE_REQUEST_MAX_BYTES": "2097152", "AIVISION_TRACE_QUERY_CONCURRENCY": "1", "BE_CONTAINER_MEMORY": "3G", "SIGNOZ_CONTAINER_MEMORY": "4G"}})
	if err != nil {
		t.Fatal(err)
	}
	data, err := a.render("config.yaml")
	if err != nil {
		t.Fatal(err)
	}
	docs, err := decodeDocuments(data)
	if err != nil {
		t.Fatal(err)
	}
	if str(docs[0], "data", "AIVISION_TRACE_QUERY_CONCURRENCY") != "1" {
		t.Fatal("query admission override missing")
	}
	for _, spec := range []struct {
		file, memory string
		index        int
	}{{"backend.yaml", "3Gi", 0}, {"signoz.yaml", "4Gi", 1}} {
		data, err = a.render(spec.file)
		if err != nil {
			t.Fatal(err)
		}
		docs, err = decodeDocuments(data)
		if err != nil {
			t.Fatal(err)
		}
		container := list(docs[spec.index], "spec", "template", "spec", "containers")[0]
		if got := str(container, "resources", "limits", "memory"); got != spec.memory {
			t.Fatalf("%s memory=%s", spec.file, got)
		}
	}
}
