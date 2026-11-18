package installprogress

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/sandboxplugin"
)

type capturedBatch struct {
	Operation string `json:"operation"`
	RunID     string `json:"run_id"`
	Events    []struct {
		Seq       int    `json:"seq"`
		Stage     string `json:"stage"`
		State     string `json:"state"`
		Detail    string `json:"detail"`
		ElapsedMS int64  `json:"elapsed_ms"`
		Image     *struct {
			Layer   string `json:"layer"`
			Status  string `json:"status"`
			Current string `json:"current"`
			Total   string `json:"total"`
		} `json:"image"`
	} `json:"events"`
}

func readBatch(t *testing.T, req *http.Request) ([]byte, capturedBatch) {
	t.Helper()
	raw, err := io.ReadAll(req.Body)
	if err != nil {
		t.Error(err)
	}
	var batch capturedBatch
	if err := json.Unmarshal(raw, &batch); err != nil {
		t.Error(err)
	}
	if req.Method != "POST" || req.Header.Get("Content-Type") != "application/json" || req.Header.Get("Authorization") != "Bearer secret-report-token" || req.Header.Get("Cookie") != "" {
		t.Error("invalid report request headers")
	}
	if len(raw) > 16384 || len(batch.Events) < 1 || len(batch.Events) > 32 || strings.Contains(string(raw), "secret-report-token") || !regexp.MustCompile(`^[0-9a-f]{32}$`).MatchString(batch.RunID) {
		t.Errorf("invalid report envelope: %s", raw)
	}
	return raw, batch
}
func acknowledge(w http.ResponseWriter, batch capturedBatch) {
	fmt.Fprintf(w, `{"code":0,"message":"success","data":{"last_seq":%d}}`, batch.Events[len(batch.Events)-1].Seq)
}

func TestReporterValidatesEndpointAndCredentialWithoutEcho(t *testing.T) {
	for _, endpoint := range []string{"/relative", "ftp://example.test/events", "https://secret@example.test/events", "https://example.test/events?secret", "https://example.test/events?", "https://example.test/events#secret", "https://example.test/events#", "http:///events"} {
		reporter, err := New(endpoint, "secret-report-token", "install", io.Discard)
		if err == nil || reporter != nil || strings.Contains(err.Error(), "secret") || strings.Contains(err.Error(), endpoint) {
			t.Fatalf("endpoint validation %q: %v", endpoint, err)
		}
	}
	for _, token := range []string{"", "secret\nvalue", "secret value", strings.Repeat("s", 4097)} {
		reporter, err := New("https://example.test/events", token, "install", io.Discard)
		if err == nil || reporter != nil {
			t.Fatalf("invalid credential accepted")
		}
	}
}

func TestReporterRetriesStableSequencesAfter503AndLostAcknowledgment(t *testing.T) {
	var mu sync.Mutex
	var bodies [][]byte
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		raw, batch := readBatch(t, req)
		mu.Lock()
		bodies = append(bodies, raw)
		attempt := len(bodies)
		mu.Unlock()
		if attempt == 1 {
			http.Error(w, "secret-report-token and private provider errors", 503)
			return
		}
		if attempt == 2 {
			conn, _, _ := w.(http.Hijacker).Hijack()
			conn.Close()
			return
		}
		acknowledge(w, batch)
	}))
	defer server.Close()
	var warning bytes.Buffer
	reporter, err := New(server.URL+"/events", "secret-report-token", "install", &warning)
	if err != nil {
		t.Fatal(err)
	}
	reporter.Observe(sandboxplugin.ProgressEvent{Stage: "configuration", State: "start"})
	reporter.Close()
	mu.Lock()
	defer mu.Unlock()
	if len(bodies) != 3 || !bytes.Equal(bodies[0], bodies[1]) || !bytes.Equal(bodies[1], bodies[2]) {
		t.Fatalf("retries changed assigned events: %q", bodies)
	}
	if strings.Count(warning.String(), "Warning:") != 1 || strings.Contains(warning.String(), "secret") || strings.Contains(warning.String(), server.URL) {
		t.Fatalf("unsafe or repeated warning: %q", &warning)
	}
}

func TestReporterCoalescesWithoutBlockingOrLosingEdges(t *testing.T) {
	entered, release := make(chan struct{}), make(chan struct{})
	var once sync.Once
	var captureMu sync.Mutex
	var batches []capturedBatch
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		_, batch := readBatch(t, req)
		once.Do(func() { close(entered); <-release })
		captureMu.Lock()
		batches = append(batches, batch)
		captureMu.Unlock()
		acknowledge(w, batch)
	}))
	defer server.Close()
	reporter, err := New(server.URL, "secret-report-token", "install", io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	reporter.Observe(sandboxplugin.ProgressEvent{Stage: "configuration", State: "start"})
	select {
	case <-entered:
	case <-time.After(time.Second):
		t.Fatal("no asynchronous request")
	}
	started := time.Now()
	reporter.Observe(sandboxplugin.ProgressEvent{Stage: "configuration", State: "done"})
	reporter.Observe(sandboxplugin.ProgressEvent{Stage: "docker", State: "start"})
	reporter.Observe(sandboxplugin.ProgressEvent{Stage: "docker", State: "done"})
	reporter.Observe(sandboxplugin.ProgressEvent{Stage: "image", State: "start"})
	for i := 0; i < 10000; i++ {
		reporter.Observe(sandboxplugin.ProgressEvent{Stage: "image", State: "wait", Detail: "pulling", Elapsed: time.Duration(i) * time.Millisecond, Image: &sandboxplugin.ImageProgress{Layer: fmt.Sprintf("%012x", i%256), Status: "Downloading", Current: "1MB", Total: "2MB"}})
	}
	reporter.Observe(sandboxplugin.ProgressEvent{Stage: "image", State: "done", Detail: "pulled", Elapsed: 10 * time.Second})
	for _, stage := range []string{"state", "container", "readiness"} {
		reporter.Observe(sandboxplugin.ProgressEvent{Stage: stage, State: "start"})
		detail := ""
		if stage == "readiness" {
			detail = "not_ready"
		}
		reporter.Observe(sandboxplugin.ProgressEvent{Stage: stage, State: "done", Detail: detail})
	}
	if time.Since(started) > time.Second {
		t.Fatal("network blocked the progress observer")
	}
	close(release)
	reporter.Close()
	captureMu.Lock()
	defer captureMu.Unlock()
	seq, edges, waits := 0, []string{}, 0
	var imageElapsed int64
	for _, batch := range batches {
		for _, event := range batch.Events {
			seq++
			if event.Seq != seq {
				t.Fatalf("sequence gap or replay: %d -> %d", seq, event.Seq)
			}
			if event.Stage == "image" {
				if event.ElapsedMS < imageElapsed {
					t.Fatal("coalescing moved events backward")
				}
				imageElapsed = event.ElapsedMS
			}
			if event.State == "wait" {
				waits++
			} else {
				edges = append(edges, event.Stage+":"+event.State)
			}
		}
	}
	want := []string{"configuration:start", "configuration:done", "docker:start", "docker:done", "image:start", "image:done", "state:start", "state:done", "container:start", "container:done", "readiness:start", "readiness:done"}
	if fmt.Sprint(edges) != fmt.Sprint(want) || waits > 128 || waits == 0 {
		t.Fatalf("bounded coalescing lost edges: %v, waits=%d", edges, waits)
	}
}

func TestReporterDoesNotFollowRedirectOrExposeResponse(t *testing.T) {
	var leakMu sync.Mutex
	leaked := false
	destination := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { leakMu.Lock(); leaked = true; leakMu.Unlock() }))
	defer destination.Close()
	source := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, destination.URL+"/secret", http.StatusTemporaryRedirect)
	}))
	defer source.Close()
	var warning bytes.Buffer
	reporter, err := New(source.URL, "secret-report-token", "status", &warning)
	if err != nil {
		t.Fatal(err)
	}
	reporter.Observe(sandboxplugin.ProgressEvent{Stage: "readiness", State: "start"})
	reporter.Close()
	leakMu.Lock()
	defer leakMu.Unlock()
	if leaked || strings.Contains(warning.String(), "secret") || strings.Contains(warning.String(), destination.URL) || strings.Count(warning.String(), "Warning:") != 1 {
		t.Fatalf("redirect/diagnostic leak: %t %s", leaked, &warning)
	}
}

func TestReporterCloseDeadlineCancelsBlockedRequest(t *testing.T) {
	release := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { <-release }))
	defer server.Close()
	defer close(release)
	var warning bytes.Buffer
	reporter, err := New(server.URL, "secret-report-token", "status", &warning)
	if err != nil {
		t.Fatal(err)
	}
	reporter.Observe(sandboxplugin.ProgressEvent{Stage: "readiness", State: "start"})
	reporter.Observe(sandboxplugin.ProgressEvent{Stage: "readiness", State: "failed", Detail: "canceled"})
	started := time.Now()
	reporter.Close()
	if elapsed := time.Since(started); elapsed > 4500*time.Millisecond {
		t.Fatalf("close exceeded deadline: %s", elapsed)
	}
	if strings.Count(warning.String(), "Warning:") != 1 {
		t.Fatalf("warnings=%q", &warning)
	}
}

// Cancellation of the host install must still flush its terminal typed event.
func TestReporterFlushesRealManagerCancellation(t *testing.T) {
	var mu sync.Mutex
	var terminal string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		_, batch := readBatch(t, req)
		mu.Lock()
		for _, e := range batch.Events {
			if e.State == "failed" {
				terminal = e.Stage + ":" + e.Detail
			}
		}
		mu.Unlock()
		acknowledge(w, batch)
	}))
	defer server.Close()
	reporter, err := New(server.URL, "secret-report-token", "install", io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	directory, err := os.MkdirTemp("", "aiv-report-cancel-")
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(directory)
	token, policy, socket := filepath.Join(directory, "token"), filepath.Join(directory, "policy"), filepath.Join(directory, "docker.sock")
	if err = os.WriteFile(token, []byte("provider-secret-token"), 0600); err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(policy, []byte("{}"), 0600); err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	manager := sandboxplugin.New(filepath.Join(directory, "state"))
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	manager.Progress = func(e sandboxplugin.ProgressEvent) {
		reporter.Observe(e)
		if e.Stage == "docker" && e.State == "start" {
			cancel()
		}
	}
	_, err = manager.Install(ctx, sandboxplugin.Options{Image: "example/plugin:test", WorkloadImage: "example/rules:test", Gateway: "gateway.example:30051", Workspace: "default", Port: 8092, TokenFile: token, PolicyFile: policy, DockerSocket: socket, JournalDir: filepath.Join(directory, "journal")})
	if err == nil || !strings.Contains(err.Error(), "cancel") {
		t.Fatalf("manager did not cancel: %v", err)
	}
	reporter.Close()
	mu.Lock()
	defer mu.Unlock()
	if terminal != "docker:canceled" {
		t.Fatalf("terminal not flushed after cancellation: %s", terminal)
	}
}

func TestReporterPartialAcknowledgmentKeepsUnacknowledgedSequences(t *testing.T) {
	var mu sync.Mutex
	var bodies []capturedBatch
	entered, release := make(chan struct{}), make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		_, batch := readBatch(t, req)
		mu.Lock()
		bodies = append(bodies, batch)
		count := len(bodies)
		mu.Unlock()
		if count == 1 {
			close(entered)
			<-release
			acknowledge(w, batch)
			return
		}
		if count == 2 {
			fmt.Fprintf(w, `{"code":0,"data":{"last_seq":%d}}`, batch.Events[0].Seq)
			return
		}
		acknowledge(w, batch)
	}))
	defer server.Close()
	reporter, err := New(server.URL, "secret-report-token", "install", io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	reporter.Observe(sandboxplugin.ProgressEvent{Stage: "configuration", State: "start"})
	<-entered
	reporter.Observe(sandboxplugin.ProgressEvent{Stage: "configuration", State: "done"})
	reporter.Observe(sandboxplugin.ProgressEvent{Stage: "docker", State: "start"})
	reporter.Observe(sandboxplugin.ProgressEvent{Stage: "docker", State: "failed"})
	close(release)
	reporter.Close()
	mu.Lock()
	defer mu.Unlock()
	if len(bodies) != 3 || len(bodies[1].Events) != 3 || len(bodies[2].Events) != 2 || bodies[2].Events[0].Seq != 3 || bodies[2].Events[1].Seq != 4 {
		t.Fatalf("partial ACK altered sequence: %#v", bodies)
	}
}

// Replacing A@5 in place with A@20 would send A@20 before B@10, which the
// server rejects. Coalescing must move the latest A update after B.
func TestReporterCoalescingMovesUpdatedLayerAfterOtherLayers(t *testing.T) {
	entered, release := make(chan struct{}), make(chan struct{})
	var once sync.Once
	var mu sync.Mutex
	var waits []string
	var elapsed []int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		_, batch := readBatch(t, req)
		once.Do(func() { close(entered); <-release })
		mu.Lock()
		for _, event := range batch.Events {
			if event.State == "wait" {
				waits = append(waits, event.Image.Layer)
				elapsed = append(elapsed, event.ElapsedMS)
			}
		}
		mu.Unlock()
		acknowledge(w, batch)
	}))
	defer server.Close()
	reporter, err := New(server.URL, "secret-report-token", "install", io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	reporter.Observe(sandboxplugin.ProgressEvent{Stage: "configuration", State: "start"})
	<-entered
	reporter.Observe(sandboxplugin.ProgressEvent{Stage: "configuration", State: "done"})
	reporter.Observe(sandboxplugin.ProgressEvent{Stage: "docker", State: "start"})
	reporter.Observe(sandboxplugin.ProgressEvent{Stage: "docker", State: "done"})
	reporter.Observe(sandboxplugin.ProgressEvent{Stage: "image", State: "start"})
	for _, update := range []struct {
		layer string
		ms    time.Duration
	}{{"aaaaaaaaaaaa", 5}, {"bbbbbbbbbbbb", 10}, {"aaaaaaaaaaaa", 20}} {
		reporter.Observe(sandboxplugin.ProgressEvent{Stage: "image", State: "wait", Detail: "pulling", Elapsed: update.ms * time.Millisecond, Image: &sandboxplugin.ImageProgress{Layer: update.layer, Status: "Downloading", Current: "1MB", Total: "2MB"}})
	}
	reporter.Observe(sandboxplugin.ProgressEvent{Stage: "image", State: "failed", Elapsed: 25 * time.Millisecond})
	close(release)
	reporter.Close()
	mu.Lock()
	defer mu.Unlock()
	if fmt.Sprint(waits) != "[bbbbbbbbbbbb aaaaaaaaaaaa]" || fmt.Sprint(elapsed) != "[10 20]" {
		t.Fatalf("coalescing reordered elapsed values: layers=%v elapsed=%v", waits, elapsed)
	}
}

func TestBootstrapReporterSharesRunIDAndFlushesWithoutClosing(t *testing.T) {
	var mu sync.Mutex
	var seen []string
	var runIDs []string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		_, batch := readBatch(t, req)
		mu.Lock()
		runIDs = append(runIDs, batch.RunID)
		for _, e := range batch.Events {
			seen = append(seen, e.Stage+":"+e.State)
		}
		mu.Unlock()
		acknowledge(w, batch)
	}))
	defer server.Close()
	reporter, err := New(server.URL, "secret-report-token", "bootstrap", io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	reporter.Observe(sandboxplugin.ProgressEvent{Stage: "host", State: "start"})
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if err = reporter.Flush(ctx); err != nil {
		t.Fatal(err)
	}
	mu.Lock()
	if fmt.Sprint(seen) != "[host:start]" {
		t.Fatalf("flush returned before acknowledgment: %v", seen)
	}
	mu.Unlock()
	reporter.Observe(sandboxplugin.ProgressEvent{Stage: "host", State: "done"})
	reporter.Close()
	mu.Lock()
	defer mu.Unlock()
	if fmt.Sprint(seen) != "[host:start host:done]" {
		t.Fatalf("flush closed reporter: %v", seen)
	}
	for _, id := range runIDs {
		if id != reporter.RunID() {
			t.Fatal("claim/event run identities differ")
		}
	}
}

func TestBootstrapFlushFailureAndCancellationDoNotPretendAcknowledgment(t *testing.T) {
	for _, blocked := range []bool{false, true} {
		t.Run(fmt.Sprint(blocked), func(t *testing.T) {
			release := make(chan struct{})
			var releaseOnce sync.Once
			unblock := func() { releaseOnce.Do(func() { close(release) }) }
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
				if blocked {
					<-release
				}
				http.Error(w, "secret-report-token response", 403)
			}))
			defer server.Close()
			defer unblock()
			reporter, err := New(server.URL, "secret-report-token", "bootstrap", io.Discard)
			if err != nil {
				t.Fatal(err)
			}
			reporter.Observe(sandboxplugin.ProgressEvent{Stage: "readiness", State: "done", Detail: "ready"})
			ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
			defer cancel()
			if err = reporter.Flush(ctx); err == nil || strings.Contains(err.Error(), "secret") {
				t.Fatalf("unacknowledged ready event passed barrier: %v", err)
			}
			unblock()
			reporter.Close()
		})
	}
}

func TestSafeBootstrapDiagnosticsAreCopiedAndLegacyEventsRemainCompatible(t *testing.T) {
	for _, tt := range []struct {
		operation, stage, state, detail string
		diagnostic                      *sandboxplugin.InstallationDiagnostic
		accepted                        bool
	}{
		{"bootstrap", "host", "failed", "", &sandboxplugin.InstallationDiagnostic{Code: "docker_version_unsupported", Actual: "26.1.3", Required: "28.0"}, true},
		{"bootstrap", "readiness", "done", "not_ready", &sandboxplugin.InstallationDiagnostic{Code: "provider_not_ready"}, true},
		{"bootstrap", "readiness", "done", "ready", &sandboxplugin.InstallationDiagnostic{Code: "provider_not_ready"}, false},
		{"bootstrap", "host", "failed", "", &sandboxplugin.InstallationDiagnostic{Code: "SECRET", Actual: "1"}, false},
		{"bootstrap", "host", "failed", "", &sandboxplugin.InstallationDiagnostic{Code: "environment_probe_failed", Actual: "SECRET /private/path"}, false},
		{"bootstrap", "host", "failed", "", nil, false},
		{"install", "readiness", "failed", "", &sandboxplugin.InstallationDiagnostic{Code: "provider_not_ready"}, false},
	} {
		r := &Reporter{operation: tt.operation, wake: make(chan struct{}, 1)}
		r.Observe(sandboxplugin.ProgressEvent{Stage: tt.stage, State: tt.state, Detail: tt.detail, Diagnostic: tt.diagnostic})
		if len(r.pending) != 1 {
			t.Fatal("old event was rejected")
		}
		next := r.pending[0]
		if (next.Diagnostic != nil) != tt.accepted {
			t.Fatalf("unexpected safe diagnostic: %#v", next)
		}
		if tt.accepted {
			before := next.Diagnostic.Actual
			tt.diagnostic.Actual = "SECRET"
			if next.Diagnostic.Actual != before {
				t.Fatal("queued diagnostic aliases caller")
			}
		}
	}
}
