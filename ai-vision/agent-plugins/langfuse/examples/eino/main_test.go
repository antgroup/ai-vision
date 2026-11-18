package main

import (
	"bytes"
	"compress/gzip"
	"context"
	"encoding/hex"
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"

	collector "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
	"google.golang.org/protobuf/proto"
)

// This server receives real gzip/protobuf requests emitted by the official
// callback while the official OpenAI component executes an Eino Chain.
func TestOfficialCallbackHTTPContract(t *testing.T) {
	var mu sync.Mutex
	var exported []*tracepb.Span
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/public/otel/v1/traces" {
			t.Errorf("unexpected OTLP path: %s", r.URL.Path)
		}
		user, password, ok := r.BasicAuth()
		if !ok || user != "test-agent" || password != "test-space-key" {
			t.Error("Basic authentication was not preserved")
		}
		if r.Header.Get("Content-Type") != "application/x-protobuf" || r.Header.Get("Content-Encoding") != "gzip" {
			t.Error("expected gzip/protobuf export")
		}
		reader, err := gzip.NewReader(r.Body)
		if err != nil {
			t.Error(err)
			w.WriteHeader(400)
			return
		}
		defer reader.Close()
		body, err := io.ReadAll(reader)
		if err != nil {
			t.Error(err)
			w.WriteHeader(400)
			return
		}
		var request collector.ExportTraceServiceRequest
		if err := proto.Unmarshal(body, &request); err != nil {
			t.Error(err)
			w.WriteHeader(400)
			return
		}
		mu.Lock()
		for _, resource := range request.ResourceSpans {
			for _, scope := range resource.ScopeSpans {
				exported = append(exported, scope.Spans...)
			}
		}
		mu.Unlock()
		w.Header().Set("Content-Type", "application/x-protobuf")
	}))
	defer server.Close()
	ledger, err := runSuite(context.Background(), config{Mode: "fixture", Host: server.URL, PublicKey: "test-agent", SecretKey: "test-space-key"})
	if err != nil {
		t.Fatal(err)
	}
	if len(ledger.Scenarios) != 6 {
		t.Fatalf("got %d scenarios, want 6", len(ledger.Scenarios))
	}
	mu.Lock()
	defer mu.Unlock()
	for _, scenario := range ledger.Scenarios {
		t.Run(scenario.Scenario, func(t *testing.T) {
			byName := map[string]*tracepb.Span{}
			deliveries := 0
			for _, span := range exported {
				if hex.EncodeToString(span.TraceId) == scenario.TraceID {
					deliveries++
					byName[span.Name] = span
				}
			}
			wantCount := 5
			if scenario.Scenario == "error" || scenario.Scenario == "cancel" {
				wantCount = 3
			}
			if len(byName) != wantCount {
				t.Fatalf("got %d exported spans, want %d: %v", len(byName), wantCount, byName)
			}
			if deliveries != wantCount {
				t.Errorf("got %d successful deliveries, want exactly %d", deliveries, wantCount)
			}
			if scenario.Expected.SpanCount != wantCount || len(scenario.ObservedSpans) != wantCount {
				t.Fatal("ledger count disagrees with actual export")
			}
			for _, want := range scenario.Expected.Spans {
				span := byName[want.Name]
				if span == nil {
					t.Fatalf("missing span %q", want.Name)
				}
				attrs := map[string]string{}
				ints := map[string]int64{}
				for _, attr := range span.Attributes {
					attrs[attr.Key] = attr.Value.GetStringValue()
					ints[attr.Key] = attr.Value.GetIntValue()
				}
				if attrs["langfuse.observation.type"] != want.Kind {
					t.Errorf("%s kind=%q, want %q", want.Name, attrs["langfuse.observation.type"], want.Kind)
				}
				if int(span.GetStatus().GetCode()) != want.Status {
					t.Errorf("%s status=%d, want %d", want.Name, span.GetStatus().GetCode(), want.Status)
				}
				if want.ParentKey == "" {
					if len(span.ParentSpanId) != 0 {
						t.Error("application root has a parent")
					}
				} else {
					parent := byName[want.ParentKey]
					if parent == nil || hex.EncodeToString(span.ParentSpanId) != hex.EncodeToString(parent.SpanId) {
						t.Errorf("wrong parent for %s", want.Name)
					}
				}
				for _, check := range []struct{ key, contains string }{{"langfuse.observation.input", want.InputContains}, {"langfuse.observation.output", want.OutputContains}} {
					if check.contains != "" && !strings.Contains(attrs[check.key], check.contains) {
						t.Errorf("%s %s lacks %q", want.Name, check.key, check.contains)
					}
				}
				if want.Kind == "generation" {
					for field, key := range map[string]string{"input_tokens": "gen_ai.usage.input_tokens", "output_tokens": "gen_ai.usage.output_tokens"} {
						value, present := ints[key]
						if want.Usage == nil {
							if present {
								t.Errorf("missing usage became %s=%d", field, value)
							}
						} else if !present || value != want.Usage[field] {
							t.Errorf("usage %s=%d present=%v, want %d", field, value, present, want.Usage[field])
						}
					}
				}
			}
		})
	}
}

// Keep this pinned upstream limitation visible. A future callback upgrade that
// preserves explicit zero counters should update this test and the README.
// The business ledger retains 0, so platform verification reports the mismatch.
func TestOfficialCallbackKnownZeroUsageLimitation(t *testing.T) {
	receiver := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.Copy(io.Discard, r.Body)
		w.Header().Set("Content-Type", "application/x-protobuf")
	}))
	defer receiver.Close()
	modelServer, started := modelFixture()
	defer modelServer.Close()
	entry, err := runScenario(context.Background(), config{Mode: "fixture", Host: receiver.URL, PublicKey: "test-agent", SecretKey: "test-space-key", ModelBaseURL: modelServer.URL + "/zero_usage/v1", ModelAPIKey: "fixture-key", ModelName: "fixture-model"}, "zero_usage", started)
	if err != nil {
		t.Fatal(err)
	}
	var sawExpected, sawObserved bool
	for _, span := range entry.Expected.Spans {
		if span.Kind == "generation" {
			value, present := span.Usage["output_tokens"]
			if !present || value != 0 {
				t.Fatal("independent provider expectation lost the explicit zero")
			}
			sawExpected = true
		}
	}
	for _, span := range entry.ObservedSpans {
		if span.Key == "model" {
			if _, present := span.UsageAttributes["gen_ai.usage.output_tokens"]; present {
				t.Fatal("upstream now preserves zero: update the documented compatibility boundary")
			}
			sawObserved = true
		}
	}
	if !sawExpected || !sawObserved {
		t.Fatal("missing model observation")
	}
}

func TestRejectedTelemetryBodyIsRedacted(t *testing.T) {
	const sensitiveBody = "proxy echoed a secret that must not enter logs"
	receiver := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.Copy(io.Discard, r.Body)
		w.WriteHeader(http.StatusUnauthorized)
		io.WriteString(w, sensitiveBody)
	}))
	defer receiver.Close()
	modelServer, started := modelFixture()
	defer modelServer.Close()
	var logs bytes.Buffer
	originalOutput := log.Writer()
	log.SetOutput(&logs)
	defer log.SetOutput(originalOutput)
	_, err := runScenario(context.Background(), config{Mode: "fixture", Host: receiver.URL, PublicKey: "test-agent", SecretKey: "test-space-key", ModelBaseURL: modelServer.URL + "/normal/v1", ModelAPIKey: "fixture-key", ModelName: "fixture-model"}, "normal", started)
	if err == nil {
		t.Fatal("rejected telemetry was reported as a successful export")
	}
	if strings.Contains(err.Error(), sensitiveBody) || strings.Contains(logs.String(), sensitiveBody) {
		t.Fatal("collector response body leaked into diagnostics")
	}
	if !strings.Contains(logs.String(), "telemetry ingestion rejected the request") {
		t.Fatal("expected sanitized export failure diagnostic")
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestObserverDetectsDuplicateSuccessfulDelivery(t *testing.T) {
	var wire []byte
	var mu sync.Mutex
	receiver := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		data, err := io.ReadAll(r.Body)
		if err != nil {
			t.Error(err)
			w.WriteHeader(400)
			return
		}
		mu.Lock()
		wire = data
		mu.Unlock()
		w.Header().Set("Content-Type", "application/x-protobuf")
	}))
	defer receiver.Close()
	modelServer, started := modelFixture()
	defer modelServer.Close()
	_, err := runScenario(context.Background(), config{Mode: "fixture", Host: receiver.URL, PublicKey: "test-agent", SecretKey: "test-space-key", ModelBaseURL: modelServer.URL + "/normal/v1", ModelAPIKey: "fixture-key", ModelName: "fixture-model"}, "normal", started)
	if err != nil {
		t.Fatal(err)
	}
	mu.Lock()
	payload := append([]byte(nil), wire...)
	mu.Unlock()
	observer := &exportObserver{spans: map[string]observedSpan{}, base: roundTripFunc(func(r *http.Request) (*http.Response, error) {
		io.Copy(io.Discard, r.Body)
		r.Body.Close()
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(""))}, nil
	})}
	// Reuse a genuine framework-produced request; no synthetic spans are built.
	for i := 0; i < 2; i++ {
		req, _ := http.NewRequest(http.MethodPost, "http://localhost/api/public/otel/v1/traces", bytes.NewReader(payload))
		req.Header.Set("Content-Encoding", "gzip")
		response, err := observer.RoundTrip(req)
		if err != nil {
			t.Fatal(err)
		}
		response.Body.Close()
	}
	if _, err := observer.snapshot(); err == nil {
		t.Fatal("duplicate successful delivery was silently deduplicated")
	}
}

func TestPartialRejectionFailsAndDiagnosticsAreRedacted(t *testing.T) {
	const secret = "private-space-key-test-marker"
	var deliveries atomic.Int32
	payload, err := proto.Marshal(&collector.ExportTraceServiceResponse{PartialSuccess: &collector.ExportTracePartialSuccess{RejectedSpans: 1, ErrorMessage: secret}})
	if err != nil {
		t.Fatal(err)
	}
	receiver := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		deliveries.Add(1)
		io.Copy(io.Discard, r.Body)
		w.Header().Set("Content-Type", "application/x-protobuf")
		w.Write(payload)
	}))
	defer receiver.Close()
	// Do not replace OTel's global ErrorHandler: its first replacement binds
	// the original delegator, so restoring the old pointer cannot undo it.
	var diagnostics bytes.Buffer
	originalOutput := log.Writer()
	log.SetOutput(&diagnostics)
	defer log.SetOutput(originalOutput)
	_, err = runSuite(context.Background(), config{Mode: "fixture", Host: receiver.URL, PublicKey: "test-agent", SecretKey: secret})
	if err == nil {
		t.Error("partial span rejection was reported as a successful export")
	}
	if deliveries.Load() != 1 {
		t.Errorf("partial rejection caused %d deliveries; it must not retry the accepted subset", deliveries.Load())
	}
	if strings.Contains(diagnostics.String(), secret) {
		t.Error("OTLP partial acceptance response leaked into diagnostics")
	}
	if !strings.Contains(diagnostics.String(), "telemetry ingestion reported partial acceptance") {
		t.Error("expected sanitized partial acceptance diagnostic")
	}
}
