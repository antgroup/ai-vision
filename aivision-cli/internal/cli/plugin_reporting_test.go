package cli

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/antgroup/ai-vision/aivision-cli/internal/installprogress"
	"github.com/antgroup/ai-vision/aivision-cli/internal/sandboxplugin"
	"io"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

// Missing one flag must fail before touching installation state, without exposing credentials.
func TestPluginReportingFlagsRequirePair(t *testing.T) {
	for _, action := range []string{"install", "status"} {
		for _, flag := range []string{"--progress-url=http://127.0.0.1/events", "--progress-token=private-report-token"} {
			_, diagnostic, err := runPluginCommand(t, action, "openshell", flag, "--state-dir", filepath.Join(t.TempDir(), "state"))
			if err == nil || !strings.Contains(diagnostic, "--progress-url and --progress-token must be provided together") || strings.Contains(diagnostic, "private-report-token") {
				t.Fatalf("%s incomplete reporting flags: %v %q", action, err, diagnostic)
			}
		}
	}
}

func TestPluginStatusReportsNotInstalledWithoutChangingExit(t *testing.T) {
	var mu sync.Mutex
	var received []map[string]any
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		data, _ := io.ReadAll(req.Body)
		var batch struct {
			Events []map[string]any `json:"events"`
		}
		if err := json.Unmarshal(data, &batch); err != nil {
			t.Error(err)
		}
		if req.Header.Get("Authorization") != "Bearer private-report-token" || strings.Contains(string(data), "private-report-token") {
			t.Error("credential transport violated")
		}
		mu.Lock()
		received = append(received, batch.Events...)
		mu.Unlock()
		last := batch.Events[len(batch.Events)-1]["seq"]
		fmt.Fprintf(w, `{"code":0,"message":"success","data":{"last_seq":%v}}`, last)
	}))
	defer server.Close()
	out, diagnostic, err := runPluginCommand(t, "status", "openshell", "--format=json", "--state-dir", filepath.Join(t.TempDir(), "state"), "--progress-url", server.URL+"/events", "--progress-token", "private-report-token")
	if err != nil || diagnostic != "" || !strings.Contains(out, `"installed": false`) {
		t.Fatalf("status contract changed: %v %q %q", err, out, diagnostic)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(received) != 2 || received[0]["stage"] != "readiness" || received[0]["state"] != "start" || received[1]["state"] != "failed" || received[1]["detail"] != "not_installed" {
		t.Fatalf("status report = %#v", received)
	}
}

func TestPluginInstallReportsValidationFailureWithoutLeakingOptions(t *testing.T) {
	var mu sync.Mutex
	var payloads []string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		body, _ := io.ReadAll(req.Body)
		mu.Lock()
		payloads = append(payloads, string(body))
		mu.Unlock()
		var batch struct {
			Events []struct {
				Seq int `json:"seq"`
			} `json:"events"`
		}
		json.Unmarshal(body, &batch)
		fmt.Fprintf(w, `{"code":0,"message":"success","data":{"last_seq":%d}}`, batch.Events[len(batch.Events)-1].Seq)
	}))
	defer server.Close()
	_, diagnostic, err := runPluginCommand(t, "install", "openshell", "--image", "private-registry.example/secret-path:latest", "--state-dir", filepath.Join(t.TempDir(), "state"), "--progress-url", server.URL, "--progress-token", "private-report-token")
	if err == nil || !strings.Contains(diagnostic, "[1/6]") {
		t.Fatalf("invalid install result: %v %s", err, diagnostic)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(payloads) == 0 {
		t.Fatal("no report received")
	}
	report := strings.Join(payloads, "")
	if !strings.Contains(report, `"state":"failed"`) || strings.Contains(report, "secret-path") || strings.Contains(report, "private-report-token") {
		t.Fatalf("invalid trusted report: %s", report)
	}
}

func TestPluginReportingRejectsExplicitEmptyFlags(t *testing.T) {
	_, diagnostic, err := runPluginCommand(t, "status", "openshell", "--progress-url=", "--progress-token=", "--state-dir", filepath.Join(t.TempDir(), "state"))
	if err == nil || !strings.Contains(diagnostic, "--progress-url must be") {
		t.Fatalf("empty reporting endpoint was silently disabled: %v %q", err, diagnostic)
	}
}

func TestPluginReportingFailureDoesNotChangeStatusResult(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		http.Error(w, "private-report-token secret server diagnostics", http.StatusForbidden)
	}))
	defer server.Close()
	out, diagnostic, err := runPluginCommand(t, "status", "openshell", "--format=json", "--state-dir", filepath.Join(t.TempDir(), "state"), "--progress-url", server.URL, "--progress-token", "private-report-token")
	if err != nil || !strings.Contains(out, `"installed": false`) || strings.Count(diagnostic, "Warning:") != 1 || strings.Contains(diagnostic, "private-report-token") || strings.Contains(diagnostic, "secret") || strings.Contains(diagnostic, server.URL) {
		t.Fatalf("reporting changed the host result: %v %q %q", err, out, diagnostic)
	}
}

func TestPluginStatusReportingOutcomes(t *testing.T) {
	ready, notReady := true, false
	canceled, cancel := context.WithCancel(context.Background())
	cancel()
	timedOut, stop := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
	defer stop()
	for _, test := range []struct {
		name          string
		status        *sandboxplugin.Status
		err           error
		ctx           context.Context
		state, detail string
	}{
		{"ready", &sandboxplugin.Status{Installed: true, Health: &sandboxplugin.Health{Ready: &ready}}, nil, context.Background(), "done", "ready"},
		{"not ready", &sandboxplugin.Status{Installed: true, Health: &sandboxplugin.Health{Ready: &notReady}}, nil, context.Background(), "done", "not_ready"},
		{"stopped", &sandboxplugin.Status{Installed: true}, nil, context.Background(), "done", "not_ready"},
		{"missing", &sandboxplugin.Status{}, nil, context.Background(), "failed", "not_installed"},
		{"failure", nil, errors.New("unsafe-provider-response"), context.Background(), "failed", ""},
		{"canceled", nil, context.Canceled, canceled, "failed", "canceled"},
		{"timeout", nil, context.DeadlineExceeded, timedOut, "failed", "timeout"},
	} {
		t.Run(test.name, func(t *testing.T) {
			var mu sync.Mutex
			terminalState, terminalDetail := "", ""
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
				var batch struct {
					Events []struct {
						Seq           int `json:"seq"`
						State, Detail string
					} `json:"events"`
				}
				json.NewDecoder(req.Body).Decode(&batch)
				mu.Lock()
				for _, e := range batch.Events {
					if e.State != "start" {
						terminalState, terminalDetail = e.State, e.Detail
					}
				}
				mu.Unlock()
				fmt.Fprintf(w, `{"code":0,"data":{"last_seq":%d}}`, batch.Events[len(batch.Events)-1].Seq)
			}))
			defer server.Close()
			reporter, err := installprogress.New(server.URL, "private-report-token", "status", io.Discard)
			if err != nil {
				t.Fatal(err)
			}
			reporter.Observe(sandboxplugin.ProgressEvent{Stage: "readiness", State: "start"})
			reportPluginStatus(reporter, test.status, test.err, test.ctx, time.Second)
			reporter.Close()
			mu.Lock()
			defer mu.Unlock()
			if terminalState != test.state || terminalDetail != test.detail {
				t.Fatalf("readiness outcome = %s/%s, want %s/%s", terminalState, terminalDetail, test.state, test.detail)
			}
		})
	}
}

func TestPluginSetupRequiresAllCapabilitiesWithoutEcho(t *testing.T) {
	_, diagnostic, err := runPluginCommand(t, "setup", "openshell", "--session-url", "https://example.test/api/public/sandbox-installations/01234567-89ab-cdef-0123-456789abcdef", "--bootstrap-token", "bootstrap-private")
	if err == nil || !strings.Contains(diagnostic, "--session-url, --bootstrap-token, --progress-token and --enrollment-token are required") || strings.Contains(diagnostic, "bootstrap-private") {
		t.Fatalf("setup guard missing: %v %q", err, diagnostic)
	}
}
