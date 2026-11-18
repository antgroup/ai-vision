package cli

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/antgroup/ai-vision/aivision-cli/internal/config"
	"github.com/antgroup/ai-vision/aivision-cli/internal/query"
)

func TestSessionUsageParsesFlagsAroundMultipleIDs(t *testing.T) {
	t.Setenv(config.EnvConfigDir, t.TempDir())
	t.Setenv(config.EnvProfile, "")
	response := `[{"id":"session-one","input":333,"output":69,"total":402},{"id":"session-two","input":449,"output":105,"total":554}]`
	for _, order := range []string{"ids-first", "flags-first", "interspersed", "double-dash"} {
		t.Run(order, func(t *testing.T) {
			var requests int
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				requests++
				if r.Method != http.MethodPost || r.URL.Path != "/openapi/v1/spaces/space/agents/product/sessions/usage" {
					t.Errorf("unexpected request: %s %s", r.Method, r.URL.Path)
				}
				var body struct {
					IDs  []string `json:"sessionIds"`
					From int64    `json:"from"`
					To   int64    `json:"to"`
				}
				if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
					t.Error(err)
				}
				if !reflect.DeepEqual(body.IDs, []string{"session-one", "session-two"}) || body.From != 1735689600000 || body.To != 1735776000000 {
					t.Errorf("wrong session IDs or historical time window: %+v", body)
				}
				w.Header().Set("Content-Type", "application/json")
				_, _ = w.Write([]byte(`{"code":0,"data":` + response + `}`))
			}))
			defer server.Close()
			out := filepath.Join(t.TempDir(), "usage.json")
			args := []string{"--endpoint", server.URL, "--space-key", "sk-aiv-test", "--space", "space", "--agent-product", "product", "-o", out}
			flags := []string{"--since", "2025-01-01T00:00:00Z", "--until=2025-01-02T00:00:00Z", "--format", "json"}
			switch order {
			case "ids-first":
				args = append(args, "session-one", "session-two")
				args = append(args, flags...)
			case "flags-first":
				args = append(args, flags...)
				args = append(args, "session-one", "session-two")
			case "interspersed":
				args = append(args, "session-one")
				args = append(args, flags...)
				args = append(args, "session-two")
			case "double-dash":
				args = append(args, flags...)
				args = append(args, "--", "session-one", "session-two")
			}
			if err := querySessionUsage(args); err != nil {
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
				t.Fatalf("--format json did not produce JSON: %s (%v)", raw, err)
			}
			_ = json.Unmarshal([]byte(response), &want)
			if !reflect.DeepEqual(got, want) {
				t.Errorf("usage JSON = %s, want %s", raw, response)
			}
		})
	}
}

func TestSessionUsageRejectsInvalidTrailingFlagsBeforeRequest(t *testing.T) {
	t.Setenv(config.EnvConfigDir, t.TempDir())
	t.Setenv(config.EnvProfile, "")
	for _, flag := range []string{"--unknown", "--since"} {
		t.Run(flag, func(t *testing.T) {
			var requests int
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				requests++
				_, _ = w.Write([]byte(`{"code":0,"data":[]}`))
			}))
			defer server.Close()
			args := []string{"--endpoint", server.URL, "--space-key", "sk-aiv-test", "--space", "space", "--agent-product", "product", "session-one", "session-two", flag}
			if err := querySessionUsage(args); err == nil {
				t.Error("invalid trailing flag accepted")
			}
			if requests != 0 {
				t.Errorf("invalid flags reached backend: %d requests", requests)
			}
		})
	}
}

func TestRawSQLBodyDropsServerControlledFields(t *testing.T) {
	c := &commonFlags{
		limit:   20,
		page:    2,
		orderBy: "timestamp",
		fields:  "body,severity_text",
		filter:  "severity_text = 'ERROR'",
		params:  varFlags{"refId=B", "limit=100", "format=table"},
	}
	body, err := rawSQLBody(c)
	if err != nil {
		t.Fatal(err)
	}
	for key := range body {
		if !rawSQLAllowedBodyFields[key] {
			t.Fatalf("raw SQL body kept forbidden field %q", key)
		}
	}
	if body["refId"] != "B" || body["format"] != "table" {
		t.Fatalf("raw SQL body dropped allowed values: %#v", body)
	}
	if _, ok := body["limit"]; ok {
		t.Fatalf("raw SQL body kept server-controlled limit: %#v", body)
	}
}

func TestRequireEventQueryMode(t *testing.T) {
	tests := []struct {
		name    string
		query   bool
		rawSQL  bool
		wantErr string
	}{
		{"supported", true, true, ""},
		{"disabled mode", true, false, "not supported"},
		{"disabled events query", false, true, "events queries are not supported"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path != "/openapi/v1/spaces/space/agents/product/metadata" {
					t.Fatalf("metadata request path = %s", r.URL.Path)
				}
				w.Header().Set("Content-Type", "application/json")
				_, _ = w.Write([]byte(`{"code":0,"data":{"capabilities":{"events":{"list":true,"query":` + boolJSON(tt.query) + `,"modes":{"rawSql":` + boolJSON(tt.rawSQL) + `}}}}}`))
			}))
			defer server.Close()

			client := query.New(server.URL, "sk-aiv-test", "space", "product")
			err := requireEventQueryMode(context.Background(), client, "rawSql", nil)
			if tt.wantErr == "" && err != nil {
				t.Fatal(err)
			}
			if tt.wantErr != "" && (err == nil || !strings.Contains(err.Error(), tt.wantErr)) {
				t.Fatalf("requireEventQueryMode() error = %v, want %q", err, tt.wantErr)
			}
		})
	}
}

func TestRequireEventList(t *testing.T) {
	tests := []struct {
		name    string
		list    bool
		wantErr bool
	}{
		{name: "supported", list: true},
		{name: "unsupported", list: false, wantErr: true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path != "/openapi/v1/spaces/space/agents/product/metadata" {
					t.Errorf("metadata request path = %s", r.URL.Path)
				}
				w.Header().Set("Content-Type", "application/json")
				_, _ = w.Write([]byte(`{"code":0,"data":{"capabilities":{"events":{"list":` + boolJSON(tt.list) + `}}}}`))
			}))
			defer server.Close()

			client := query.New(server.URL, "sk-aiv-test", "space", "product")
			err := requireEventList(context.Background(), client, "query log list", nil)
			if tt.wantErr && (err == nil || !strings.Contains(err.Error(), "event listing is not supported")) {
				t.Fatalf("requireEventList() error = %v, want unsupported capability", err)
			}
			if !tt.wantErr && err != nil {
				t.Fatal(err)
			}
		})
	}
}

func boolJSON(value bool) string {
	if value {
		return "true"
	}
	return "false"
}
