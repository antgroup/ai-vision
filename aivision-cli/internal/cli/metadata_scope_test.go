package cli

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"

	"github.com/antgroup/ai-vision/aivision-cli/internal/config"
)

func TestMetadataAndLogPreflightPreserveSelectedAgent(t *testing.T) {
	t.Setenv(config.EnvConfigDir, t.TempDir())
	t.Setenv(config.EnvProfile, "")
	for _, operation := range []string{"metadata", "metadata events", "metadata traces", "log list", "log query", "log export"} {
		t.Run(operation, func(t *testing.T) {
			var metadataCalls, dataCalls int
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				if r.Method == http.MethodGet && strings.Contains(r.URL.Path, "/metadata") {
					metadataCalls++
					if r.URL.Query().Get("agent_uuid") != "selected-agent" {
						w.WriteHeader(http.StatusBadRequest)
						_, _ = w.Write([]byte(`{"code":3011,"message":"Agent query route not found"}`))
						return
					}
					_, _ = w.Write([]byte(`{"code":0,"data":{"capabilities":{"events":{"list":true,"query":true,"modes":{"fieldsJson":true}}}}}`))
					return
				}
				dataCalls++
				var body map[string]any
				if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
					t.Error(err)
				}
				if body["agent_uuid"] != "selected-agent" {
					t.Errorf("data request lost selected Agent: %#v", body)
				}
				_, _ = w.Write([]byte(`{"code":0,"data":{"data":[]}}`))
			}))
			defer server.Close()
			args := strings.Fields(operation)
			if operation == "log export" {
				args = append(args, filepath.Join(t.TempDir(), "export"))
			}
			args = append(args, "--endpoint", server.URL, "--space-key", "sk-aiv-test",
				"--space", "space", "--agent-product", "product", "--param", "agent_uuid=selected-agent",
				"--format", "json", "--out", filepath.Join(t.TempDir(), "result.json"))
			if err := cmdQuery(args); err != nil {
				t.Fatal(err)
			}
			wantDataCalls := 0
			if strings.HasPrefix(operation, "log ") {
				wantDataCalls = 1
			}
			if metadataCalls != 1 || dataCalls != wantDataCalls {
				t.Fatalf("metadata=%d data=%d, want metadata=1 data=%d", metadataCalls, dataCalls, wantDataCalls)
			}
		})
	}
}
