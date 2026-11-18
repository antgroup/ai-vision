package cli

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/antgroup/ai-vision/aivision-cli/internal/config"
)

// Dropping an Agent/source selector or recomputing the window between list and
// detail would export a different instance or lose historical traces.
func TestTraceExportPreservesDetailScope(t *testing.T) {
	t.Setenv(config.EnvConfigDir, t.TempDir())
	t.Setenv(config.EnvProfile, "")
	for _, mode := range []string{"jsonl", "per-trace"} {
		for _, source := range []string{"sourceSystem", "source_system"} {
			t.Run(mode+"/"+source, func(t *testing.T) {
				var listBody map[string]any
				var detailCalls int
				detail := `{"traceId":"historical-B","spans":[{"span_id":"root-B","attributes":{"output":"selected B"}}]}`
				server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					if r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, "/traces/list") {
						if err := json.NewDecoder(r.Body).Decode(&listBody); err != nil {
							t.Error(err)
						}
						if listBody["agent_uuid"] != "agent-B" || listBody[source] != "signoz" || listBody["from"] != float64(1735689600000) || listBody["to"] != float64(1735776000000) {
							t.Errorf("list scope = %#v", listBody)
						}
						if listBody["page"] != float64(2) || listBody["fields"] == nil || listBody["filters"] == nil || listBody["orderBy"] != "trace_id.asc" {
							t.Errorf("list options were lost: %#v", listBody)
						}
						_, _ = w.Write([]byte(`{"code":0,"data":{"data":[{"trace_id":"historical-B","span_count":1}],"meta":{"page":2,"limit":1,"totalItems":2,"totalPages":2}}}`))
						return
					}
					if r.Method != http.MethodGet || !strings.HasSuffix(r.URL.Path, "/trace/historical-B/detail") {
						t.Errorf("unexpected request: %s %s", r.Method, r.URL.Path)
					}
					detailCalls++
					want := url.Values{"agent_uuid": {"agent-B"}, source: {"signoz"}, "from": {"1735689600000"}, "to": {"1735776000000"}}
					if !reflect.DeepEqual(r.URL.Query(), want) {
						t.Errorf("detail scope = %v, want %v (no list options)", r.URL.Query(), want)
						w.WriteHeader(http.StatusNotFound)
						_, _ = w.Write([]byte(`{"code":3019,"message":"wrong Agent or historical window"}`))
						return
					}
					_, _ = w.Write([]byte(`{"code":0,"data":` + detail + `}`))
				}))
				defer server.Close()
				dir := t.TempDir()
				args := []string{dir, "--endpoint", server.URL, "--space-key", "sk-aiv-test", "--space", "space", "--agent-product", "langfuse",
					"--param", "agent_uuid=agent-B", "--param", source + "=signoz", "--since", "2025-01-01T00:00:00Z", "--until", "2025-01-02T00:00:00Z",
					"--page", "2", "--limit", "1", "--fields", "trace_id,span_count", "--filter", `[{"field":"status_code","op":"=","value":1}]`, "--order-by", "trace_id.asc"}
				if source == "source_system" {
					// JSON numeric passthrough must remain decimal epoch milliseconds.
					args = append(args, "--param", "from=1735689600000", "--param", "to=1735776000000")
				}
				path := filepath.Join(dir, "traces.jsonl")
				if mode == "per-trace" {
					args = append(args, "--filename-pattern", "{trace_id}.json")
					path = filepath.Join(dir, "historical-B.json")
				}
				if err, _ := runTraceExportWithStdout(t, args); err != nil {
					t.Fatal(err)
				}
				if detailCalls != 1 {
					t.Fatalf("detail calls = %d, want 1", detailCalls)
				}
				raw, err := os.ReadFile(path)
				if err != nil {
					t.Fatal(err)
				}
				var got, want any
				if err := json.Unmarshal(raw, &got); err != nil {
					t.Fatal(err)
				}
				_ = json.Unmarshal([]byte(detail), &want)
				if !reflect.DeepEqual(got, want) {
					t.Errorf("export = %s, want selected Agent's full detail", raw)
				}
			})
		}
	}
}

// A failed detail must not become a list summary or a successful export message;
// already exported details must remain identifiable for recovery.
func TestTraceExportReportsIncompleteDetails(t *testing.T) {
	t.Setenv(config.EnvConfigDir, t.TempDir())
	t.Setenv(config.EnvProfile, "")
	for _, mode := range []string{"jsonl", "per-trace"} {
		for _, failure := range []struct {
			name      string
			status    int
			body      string
			omitCount bool
		}{
			{"not-found", 404, `{"code":3019,"message":"detail unavailable"}`, false},
			{"forbidden", 403, `{"code":4003,"message":"scope mismatch"}`, false},
			{"storage-error", 503, `{"code":3035,"message":"storage unavailable"}`, false},
			{"missing-detail", 200, `{"code":0,"data":null}`, false},
			{"empty-detail", 200, `{"code":0,"data":{}}`, false},
			{"empty-spans", 200, `{"code":0,"data":{"traceId":"failed","spans":[]}}`, false},
			{"missing-spans", 200, `{"code":0,"data":{"traceId":"failed"}}`, false},
			{"short-spans", 200, `{"code":0,"data":{"traceId":"failed","spans":[{"span_id":"a"},{"span_id":"b"}]}}`, false},
			{"duplicate-spans", 200, `{"code":0,"data":{"traceId":"failed","spans":[{"span_id":"a"},{"span_id":"b"},{"span_id":"a"}]}}`, false},
			{"missing-span-id", 200, `{"code":0,"data":{"traceId":"failed","spans":[{"span_id":"a"},{"span_id":"b"},{}]}}`, false},
			{"wrong-trace", 200, `{"code":0,"data":{"traceId":"other","spans":[{"span_id":"a"},{"span_id":"b"},{"span_id":"c"}]}}`, false},
			{"wrong-span-trace", 200, `{"code":0,"data":{"traceId":"failed","spans":[{"span_id":"a"},{"span_id":"b"},{"span_id":"c","trace_id":"other"}]}}`, false},
			{"partial-detail", 200, `{"code":0,"data":{"traceId":"failed","spans":[{"span_id":"a"},{"span_id":"b"},{"span_id":"c"}],"meta":{"complete":false}}}`, false},
			{"projected-empty-spans", 200, `{"code":0,"data":{"traceId":"failed","spans":[]}}`, true},
			{"projected-duplicate-spans", 200, `{"code":0,"data":{"traceId":"failed","spans":[{"span_id":"a"},{"span_id":"a"}]}}`, true},
			{"projected-wrong-trace", 200, `{"code":0,"data":{"traceId":"other","spans":[{"span_id":"a"}]}}`, true},
			{"projected-null-spans", 200, `{"code":0,"data":{"traceId":"failed","spans":null}}`, true},
			{"wrong-legacy-trace", 200, `{"code":0,"data":{"id":"other","observations":[]}}`, true},
		} {
			t.Run(mode+"/"+failure.name, func(t *testing.T) {
				complete := `{"traceId":"first","spans":[{"span_id":"first-root"}]}`
				server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					if r.Method == http.MethodPost {
						failedRow := `{"trace_id":"failed","span_count":3}`
						if failure.omitCount {
							failedRow = `{"trace_id":"failed"}`
						}
						_, _ = w.Write([]byte(`{"code":0,"data":{"data":[{"trace_id":"first","span_count":1},` + failedRow + `],"meta":{"page":1,"limit":20,"totalItems":2,"totalPages":1}}}`))
					} else if strings.HasSuffix(r.URL.Path, "/trace/first/detail") {
						_, _ = w.Write([]byte(`{"code":0,"data":` + complete + `}`))
					} else {
						w.WriteHeader(failure.status)
						_, _ = w.Write([]byte(failure.body))
					}
				}))
				defer server.Close()
				dir := t.TempDir()
				args := []string{dir, "--endpoint", server.URL, "--space-key", "sk-aiv-test", "--space", "space", "--agent-product", "langfuse"}
				path := filepath.Join(dir, "traces.jsonl")
				if mode == "per-trace" {
					args = append(args, "--filename-pattern", "{trace_id}.json")
					path = filepath.Join(dir, "first.json")
				}
				err, stdout := runTraceExportWithStdout(t, args)
				if err == nil {
					t.Error("failed detail was reported as a successful export")
				} else if !strings.Contains(err.Error(), "failed") || !strings.Contains(err.Error(), "1") || !strings.Contains(err.Error(), dir) {
					t.Errorf("failure must identify trace, retained count and location: %v", err)
				}
				if strings.Contains(stdout, "exported") {
					t.Errorf("incomplete export printed success: %s", stdout)
				}
				raw, readErr := os.ReadFile(path)
				if readErr != nil {
					t.Fatal(readErr)
				}
				var got, want any
				if err := json.Unmarshal(raw, &got); err != nil {
					t.Errorf("partial file must contain only the first complete detail: %s (%v)", raw, err)
				}
				_ = json.Unmarshal([]byte(complete), &want)
				if !reflect.DeepEqual(got, want) {
					t.Errorf("partial export contains failed/null/list-summary data: %s", raw)
				}
				if mode == "per-trace" {
					if _, err := os.Stat(filepath.Join(dir, "failed.json")); !os.IsNotExist(err) {
						t.Errorf("failed detail created an export file: %v", err)
					}
				}
			})
		}
	}
}

func TestTraceExportRejectsMissingTraceIdentity(t *testing.T) {
	t.Setenv(config.EnvConfigDir, t.TempDir())
	t.Setenv(config.EnvProfile, "")
	for _, mode := range []string{"jsonl", "per-trace"} {
		t.Run(mode, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodPost {
					t.Errorf("missing trace ID must not reach detail: %s", r.URL.Path)
				}
				_, _ = w.Write([]byte(`{"code":0,"data":{"data":[{"span_count":3}],"meta":{"page":1,"limit":20,"totalItems":1,"totalPages":1}}}`))
			}))
			defer server.Close()
			dir := t.TempDir()
			args := []string{dir, "--endpoint", server.URL, "--space-key", "sk-aiv-test", "--space", "space", "--agent-product", "langfuse"}
			if mode == "per-trace" {
				args = append(args, "--filename-pattern", "{trace_id}.json")
			}
			err, stdout := runTraceExportWithStdout(t, args)
			if err == nil || !strings.Contains(err.Error(), "trace ID") {
				t.Errorf("unretrievable list row must fail explicitly: %v", err)
			}
			if strings.Contains(stdout, "exported") {
				t.Errorf("missing trace identity printed success: %s", stdout)
			}
		})
	}
}

// Native projection can omit span_count; the older Langfuse adapter instead
// returns official trace id/observations, without a native spans property.
func TestTraceExportPreservesDetailsWithoutNativeCount(t *testing.T) {
	t.Setenv(config.EnvConfigDir, t.TempDir())
	t.Setenv(config.EnvProfile, "")
	for _, mode := range []string{"jsonl", "per-trace"} {
		for _, schema := range []struct {
			name   string
			row    string
			detail string
		}{
			{"native-projected", `{"trace_id":"trace-one"}`, `{"traceId":"trace-one","spans":[{"span_id":"root","trace_id":"trace-one"},{"span_id":"child","trace_id":"trace-one"}],"meta":{"complete":true}}`},
			{"legacy-langfuse", `{"id":"trace-one","name":"legacy trace"}`, `{"id":"trace-one","name":"legacy trace","input":{"question":"test"},"output":"answer","observations":[{"id":"generation-one","traceId":"trace-one","type":"GENERATION"}]}`},
		} {
			t.Run(mode+"/"+schema.name, func(t *testing.T) {
				server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					if r.Method == http.MethodPost {
						_, _ = w.Write([]byte(`{"code":0,"data":{"data":[` + schema.row + `],"meta":{"page":1,"limit":20,"totalItems":1,"totalPages":1}}}`))
					} else {
						_, _ = w.Write([]byte(`{"code":0,"data":` + schema.detail + `}`))
					}
				}))
				defer server.Close()
				dir := t.TempDir()
				args := []string{dir, "--endpoint", server.URL, "--space-key", "sk-aiv-test", "--space", "space", "--agent-product", "product"}
				path := filepath.Join(dir, "traces.jsonl")
				if mode == "per-trace" {
					args = append(args, "--filename-pattern", "{trace_id}.json")
					path = filepath.Join(dir, "trace-one.json")
				}
				if err, _ := runTraceExportWithStdout(t, args); err != nil {
					t.Fatal(err)
				}
				raw, err := os.ReadFile(path)
				if err != nil {
					t.Fatal(err)
				}
				var got, want any
				if err := json.Unmarshal(raw, &got); err != nil {
					t.Fatal(err)
				}
				_ = json.Unmarshal([]byte(schema.detail), &want)
				if !reflect.DeepEqual(got, want) {
					t.Errorf("detail schema changed during export: %s", raw)
				}
			})
		}
	}
}

func runTraceExportWithStdout(t *testing.T, args []string) (error, string) {
	t.Helper()
	output, err := os.CreateTemp(t.TempDir(), "stdout")
	if err != nil {
		t.Fatal(err)
	}
	original := os.Stdout
	os.Stdout = output
	defer func() {
		os.Stdout = original
		_ = output.Close()
	}()
	exportErr := queryTraceExport(args)
	if err := output.Close(); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(output.Name())
	if err != nil {
		t.Fatal(err)
	}
	return exportErr, string(raw)
}
