package cli

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"github.com/antgroup/ai-vision/aivision-cli/internal/config"
)

func TestSpanGetParsesFlagsAroundBothIDs(t *testing.T) {
	t.Setenv(config.EnvConfigDir, t.TempDir())
	t.Setenv(config.EnvProfile, "")
	response := `{"trace_id":"trace-one","span_id":"span-one","status":{"code":"ERROR"},"attributes":{"input":"raw input","output":null}}`
	for _, order := range []string{"ids-first", "flags-first", "interspersed", "double-dash"} {
		t.Run(order, func(t *testing.T) {
			var requests int
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				requests++
				if r.Method != http.MethodGet || r.URL.Path != "/openapi/v1/spaces/space/agents/product/trace/trace-one/span/span-one" {
					t.Errorf("wrong span request: %s %s", r.Method, r.URL.Path)
				}
				if r.URL.Query().Get("agent_uuid") != "agent-one" || r.Header.Get("Authorization") != "Bearer sk-aiv-test" {
					t.Errorf("requested span lost selected Agent or SpaceKey scope")
				}
				w.Header().Set("Content-Type", "application/json")
				_, _ = w.Write([]byte(`{"code":0,"data":` + response + `}`))
			}))
			defer server.Close()
			out := filepath.Join(t.TempDir(), "span.json")
			args := []string{"--endpoint", server.URL, "--space-key", "sk-aiv-test", "--space", "space", "--agent-product", "product"}
			flags := []string{"--param", "agent_uuid=agent-one", "--format=json", "--out", out}
			switch order {
			case "ids-first":
				args = append(args, "trace-one", "span-one")
				args = append(args, flags...)
			case "flags-first":
				args = append(args, flags...)
				args = append(args, "trace-one", "span-one")
			case "interspersed":
				args = append(args, "trace-one")
				args = append(args, flags...)
				args = append(args, "span-one")
			case "double-dash":
				args = append(args, flags...)
				args = append(args, "--", "trace-one", "span-one")
			}
			if err := querySpanGet(args); err != nil {
				t.Fatal(err)
			}
			if requests != 1 {
				t.Fatalf("requests = %d, want 1", requests)
			}
			raw, err := os.ReadFile(out)
			if err != nil {
				t.Fatalf("span --out was not honored: %v", err)
			}
			var got, want any
			if err := json.Unmarshal(raw, &got); err != nil {
				t.Fatalf("span --format json lost raw fields: %v", err)
			}
			_ = json.Unmarshal([]byte(response), &want)
			if !reflect.DeepEqual(got, want) {
				t.Errorf("span output = %s, want %s", raw, response)
			}
		})
	}
}

func TestSpanGetRejectsInvalidArgumentsBeforeRequest(t *testing.T) {
	t.Setenv(config.EnvConfigDir, t.TempDir())
	t.Setenv(config.EnvProfile, "")
	for _, trailing := range []string{"--unknown", "--out", "extra-span-id"} {
		t.Run(trailing, func(t *testing.T) {
			var requests int
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				requests++
				_, _ = w.Write([]byte(`{"code":0,"data":{}}`))
			}))
			defer server.Close()
			args := []string{"--endpoint", server.URL, "--space-key", "sk-aiv-test", "--space", "space", "--agent-product", "product", "trace-one", "span-one", trailing}
			if err := querySpanGet(args); err == nil {
				t.Error("invalid trailing argument accepted")
			}
			if requests != 0 {
				t.Errorf("invalid arguments reached backend: %d requests", requests)
			}
		})
	}
}
