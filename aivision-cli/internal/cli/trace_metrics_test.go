package cli

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/antgroup/ai-vision/aivision-cli/internal/config"
)

func TestTraceMetricsUsesProfileScopeAndFullWindow(t *testing.T) {
	t.Setenv(config.EnvConfigDir, t.TempDir())
	t.Setenv(config.EnvProfile, "")
	response := `{"traceCount":201,"spanCount":405,"generationCount":203,"toolCount":1,"errorCount":2,"errorRate":0.00995,"usage":{"total":null,"cost":null},"coverage":{"knownUsageCalls":202,"billableCalls":203,"knownTokens":812},"latencyMs":{"mean":12.5,"p95":20},"timeseries":[],"groups":[],"meta":{"complete":true,"from":1735689600000,"to":1735776000000,"filters":[{"field":"status_code","op":"IN","value":[1,2]}]}}`
	var requests int
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		if r.Method != http.MethodPost || r.URL.Path != "/openapi/v1/spaces/space-profile/agents/product-profile/traces/metrics" {
			t.Errorf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
		if r.Header.Get("Authorization") != "Bearer sk-aiv-profile" {
			t.Error("metrics did not use the selected space's SpaceKey")
		}
		var got map[string]any
		if err := json.NewDecoder(r.Body).Decode(&got); err != nil {
			t.Error(err)
		}
		var want map[string]any
		_ = json.Unmarshal([]byte(`{"from":1735689600000,"to":1735776000000,"agent_uuid":"agent-one","bucket":"day","groupBy":"model","filters":[{"field":"status_code","op":"IN","value":[1,2]}]}`), &want)
		if !reflect.DeepEqual(got, want) {
			t.Errorf("metrics body = %#v, want %#v (no list pagination)", got, want)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"code":0,"data":` + response + `}`))
	}))
	defer server.Close()
	cfg, err := config.Load()
	if err != nil {
		t.Fatal(err)
	}
	cfg.Set("metrics", config.Profile{Endpoint: server.URL, SpaceID: "space-profile", SpaceKey: "sk-aiv-profile", AgentProduct: "product-profile"})
	if err := cfg.Save(); err != nil {
		t.Fatal(err)
	}
	out := filepath.Join(t.TempDir(), "metrics.json")
	err = queryTrace([]string{"metrics", "--profile", "metrics", "--since", "2025-01-01T00:00:00Z", "--until", "2025-01-02T00:00:00Z", "--agent-uuid", "agent-one", "--bucket", "day", "--group-by", "model", "--filter", `[{"field":"status_code","op":"IN","value":[1,2]}]`, "-o", out})
	if err != nil {
		t.Fatal(err)
	}
	if requests != 1 {
		t.Fatalf("requests = %d, want 1", requests)
	}
	raw, err := os.ReadFile(out)
	if err != nil {
		t.Fatal(err)
	}
	var got, want any
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatal(err)
	}
	_ = json.Unmarshal([]byte(response), &want)
	if !reflect.DeepEqual(got, want) {
		t.Errorf("metrics output changed null/coverage or server values: %s", raw)
	}
}

func TestTraceMetricsFailsWithoutWritingPartialOrErrorResults(t *testing.T) {
	t.Setenv(config.EnvConfigDir, t.TempDir())
	t.Setenv(config.EnvProfile, "")
	for _, tc := range []struct {
		name   string
		status int
		body   string
		want   string
	}{
		{"scan-limit", 422, `{"code":1001,"message":"trace scan exceeds 10000 limit","data":null}`, "10000"},
		{"forbidden", 403, `{"code":4003,"message":"scope mismatch"}`, "forbidden"},
		{"unavailable", 503, `{"code":1001,"message":"storage unavailable"}`, "storage unavailable"},
		{"partial", 200, `{"code":0,"data":{"traceCount":100,"meta":{"complete":false}}}`, "complete"},
		{"missing-completeness", 200, `{"code":0,"data":{"traceCount":100}}`, "complete"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(tc.status)
				_, _ = w.Write([]byte(tc.body))
			}))
			defer server.Close()
			out := filepath.Join(t.TempDir(), "must-not-exist.json")
			err := queryTrace([]string{"metrics", "--endpoint", server.URL, "--space-key", "sk-aiv-test", "--space", "space", "--agent-product", "product", "-o", out})
			if err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Errorf("error = %v, want %q", err, tc.want)
			}
			if _, err := os.Stat(out); !os.IsNotExist(err) {
				t.Errorf("failed metrics created an output file: %v", err)
			}
		})
	}
}

func TestTraceMetricsRejectsInvalidAggregationOptions(t *testing.T) {
	t.Setenv(config.EnvConfigDir, t.TempDir())
	t.Setenv(config.EnvProfile, "")
	for _, tc := range []struct {
		args []string
		want string
	}{
		{[]string{"--bucket", "week"}, "bucket"},
		{[]string{"--group-by", "session"}, "group"},
		{[]string{"--param", "bucket=week"}, "bucket"},
		{[]string{"--param", "groupBy=session"}, "group"},
		{[]string{"--limit", "20"}, "limit"},
		{[]string{"--param", "limit=20"}, "limit"},
		{[]string{"--filter", "status_code=2"}, "filters"},
		{[]string{"--format", "table"}, "format"},
	} {
		err := queryTrace(append([]string{"metrics"}, tc.args...))
		if err == nil || !strings.Contains(err.Error(), tc.want) {
			t.Errorf("args %v: error = %v, want %q", tc.args, err, tc.want)
		}
	}
}
