package receiver_test

import (
	"bytes"
	"compress/gzip"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/golang/snappy"
	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/component/componenttest"
	"go.opentelemetry.io/collector/config/configauth"
	"go.opentelemetry.io/collector/confmap"
	"go.opentelemetry.io/collector/consumer"
	"go.opentelemetry.io/collector/consumer/consumererror"
	"go.opentelemetry.io/collector/extension/auth"
	"go.opentelemetry.io/collector/pdata/plog"
	"go.opentelemetry.io/collector/pdata/plog/plogotlp"
	"go.opentelemetry.io/collector/pdata/pmetric"
	"go.opentelemetry.io/collector/pdata/pmetric/pmetricotlp"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"go.opentelemetry.io/collector/receiver"
	"go.opentelemetry.io/collector/receiver/otlpreceiver"
	spb "google.golang.org/genproto/googleapis/rpc/status"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/encoding/protowire"
	"google.golang.org/protobuf/proto"
)

// Exercise the public factory over HTTP so the production vendored receiver is
// covered by go test ./..., including its standard and namespace/task routes.
func TestOTLPHTTPExportErrors(t *testing.T) {
	var mu sync.Mutex
	var exportErr error
	var calls int
	consume := func() error {
		mu.Lock()
		defer mu.Unlock()
		calls++
		return exportErr
	}
	baseURL := startReceiver(t, consume, func(context.Context, pmetric.Metrics) error { return consume() })
	client := &http.Client{Timeout: 5 * time.Second}
	t.Cleanup(client.CloseIdleConnections)

	for _, signal := range []string{"traces", "logs", "metrics"} {
		for _, route := range []string{"/v1/", "/custom/ns/task/"} {
			for _, encoding := range []string{"json", "protobuf"} {
				t.Run(signal+route+encoding, func(t *testing.T) {
					payload := exportPayload(t, signal, encoding)
					contentType := "application/json"
					if encoding == "protobuf" {
						contentType = "application/x-protobuf"
					}
					cases := []struct {
						name   string
						err    error
						status int
						code   codes.Code
					}{
						// The memory limiter returns a plain, non-permanent error.
						{"memory_refused", errors.New("data refused due to high memory usage"), 503, codes.Unavailable},
						{"unavailable", status.Error(codes.Unavailable, "temporarily busy"), 503, codes.Unavailable},
						{"permanent", consumererror.NewPermanent(errors.New("invalid data")), 500, codes.Internal},
						{"internal", status.Error(codes.Internal, "internal failure"), 500, codes.Internal},
						{"unknown", status.Error(codes.Unknown, "unknown failure"), 500, codes.Unknown},
						{"recovered", nil, 200, codes.OK},
					}
					for _, tc := range cases {
						t.Run(tc.name, func(t *testing.T) {
							mu.Lock()
							exportErr, calls = tc.err, 0
							mu.Unlock()
							resp, body := post(t, client, baseURL+route+signal, contentType, payload, true)
							if resp.StatusCode != tc.status {
								t.Errorf("HTTP status = %d, want %d; body = %q", resp.StatusCode, tc.status, body)
							}
							wantRetry := ""
							if tc.status == http.StatusServiceUnavailable {
								wantRetry = "1"
							}
							if got := resp.Header.Get("Retry-After"); got != wantRetry {
								t.Errorf("Retry-After = %q, want %q", got, wantRetry)
							}
							if got := resp.Header.Get("Content-Type"); got != contentType {
								t.Errorf("Content-Type = %q, want %q", got, contentType)
							}
							if tc.err != nil {
								var result spb.Status
								var err error
								if encoding == "json" {
									err = json.Unmarshal(body, &result)
								} else {
									err = proto.Unmarshal(body, &result)
								}
								if err != nil || result.Code != int32(tc.code) {
									t.Errorf("OTLP status = %v, decode error = %v, want code %v", &result, err, tc.code)
								}
							}
							mu.Lock()
							defer mu.Unlock()
							if calls != 1 {
								t.Errorf("consumer calls = %d, want 1", calls)
							}
						})
					}
					for _, invalid := range []struct {
						name       string
						payload    []byte
						authorized bool
						status     int
					}{
						{"malformed", []byte{0xff}, true, http.StatusBadRequest},
						{"unauthenticated", payload, false, http.StatusUnauthorized},
					} {
						t.Run(invalid.name, func(t *testing.T) {
							mu.Lock()
							calls = 0
							mu.Unlock()
							resp, _ := post(t, client, baseURL+route+signal, contentType, invalid.payload, invalid.authorized)
							if resp.StatusCode != invalid.status || resp.Header.Get("Retry-After") != "" {
								t.Errorf("HTTP status = %d, Retry-After = %q; want %d without retry", resp.StatusCode, resp.Header.Get("Retry-After"), invalid.status)
							}
							mu.Lock()
							defer mu.Unlock()
							if calls != 0 {
								t.Errorf("invalid request reached consumer %d times", calls)
							}
						})
					}
				})
			}
		}
	}
}

func TestOTLPHTTPCompressedTrace(t *testing.T) {
	baseURL := startReceiver(t, func() error { return nil }, func(context.Context, pmetric.Metrics) error { return nil })
	client := &http.Client{Timeout: 5 * time.Second}
	t.Cleanup(client.CloseIdleConnections)
	payload := exportPayload(t, "traces", "json")
	for _, encoding := range []string{"gzip", "snappy"} {
		t.Run(encoding, func(t *testing.T) {
			var body bytes.Buffer
			var writer io.WriteCloser
			if encoding == "gzip" {
				writer = gzip.NewWriter(&body)
			} else {
				writer = snappy.NewBufferedWriter(&body)
			}
			if _, err := writer.Write(payload); err != nil {
				t.Fatal(err)
			}
			if err := writer.Close(); err != nil {
				t.Fatal(err)
			}
			req, err := http.NewRequest(http.MethodPost, baseURL+"/custom/ns/task/traces", &body)
			if err != nil {
				t.Fatal(err)
			}
			req.Header.Set("Content-Type", "application/json")
			req.Header.Set("Content-Encoding", encoding)
			req.Header.Set("Authorization", "Bearer test-token")
			resp, err := client.Do(req)
			if err != nil {
				t.Fatal(err)
			}
			defer resp.Body.Close()
			if resp.StatusCode != http.StatusOK {
				t.Fatalf("compressed trace returned %s", resp.Status)
			}
		})
	}
}

func TestOTLPHTTPTableMetricPreservesRetry(t *testing.T) {
	var points atomic.Int64
	baseURL := startReceiver(t, func() error { return errors.New("unexpected signal") },
		func(_ context.Context, md pmetric.Metrics) error {
			points.Add(int64(md.DataPointCount()))
			return status.Error(codes.Unavailable, "temporarily busy")
		})
	client := &http.Client{Timeout: 5 * time.Second}
	t.Cleanup(client.CloseIdleConnections)

	// Use the wire format without fork-specific Go types so this test can catch
	// a pdata update that silently drops the Table field.
	jsonPayload := []byte(`{
		"resourceMetrics": [{
			"scopeMetrics": [{
				"metrics": [{
					"name": "table-retry-test",
					"table": {"dataRows": [{"timeUnixNano": "1234"}]}
				}]
			}]
		}]
	}`)
	// Encode the legacy field numbers directly so a renamed or dropped Table
	// field cannot result in both the producer and consumer making the same mistake.
	message := func(field protowire.Number, body []byte) []byte {
		return protowire.AppendBytes(protowire.AppendTag(nil, field, protowire.BytesType), body)
	}
	row := protowire.AppendFixed64(protowire.AppendTag(nil, 4, protowire.Fixed64Type), 1234)
	metric := append(message(1, []byte("table-retry-test")), message(13, message(1, row))...)
	protoPayload := message(1, message(2, message(2, metric)))

	for _, encoding := range []struct {
		contentType string
		body        []byte
	}{
		{"application/json", jsonPayload},
		{"application/x-protobuf", protoPayload},
	} {
		for _, route := range []string{"/v1/metrics", "/custom/ns/task/metrics"} {
			t.Run(encoding.contentType+route, func(t *testing.T) {
				points.Store(0)
				resp, body := post(t, client, baseURL+route, encoding.contentType, encoding.body, true)
				if got := points.Load(); got != 1 {
					t.Errorf("consumer received %d datapoints, want one Table datapoint", got)
				}
				if retry := resp.Header.Get("Retry-After"); resp.StatusCode != http.StatusServiceUnavailable || retry != "1" {
					t.Errorf("HTTP status = %d, Retry-After = %q, want 503 and 1; body = %q", resp.StatusCode, retry, body)
				}
			})
		}
	}
}

type authHost struct {
	component.Host
	extensions map[component.ID]component.Component
}

func (h authHost) GetExtensions() map[component.ID]component.Component { return h.extensions }

func startReceiver(t *testing.T, consume func() error, consumeMetrics consumer.ConsumeMetricsFunc) string {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	address := listener.Addr().String()
	if err := listener.Close(); err != nil {
		t.Fatal(err)
	}
	factory := otlpreceiver.NewFactory()
	cfg := factory.CreateDefaultConfig().(*otlpreceiver.Config)
	if err := cfg.Unmarshal(confmap.NewFromStringMap(map[string]any{
		"protocols": map[string]any{
			"http": map[string]any{
				"endpoint":         address,
				"traces_url_path":  "custom/{namespace}/{task}/traces",
				"logs_url_path":    "custom/{namespace}/{task}/logs",
				"metrics_url_path": "custom/{namespace}/{task}/metrics",
			},
		},
	})); err != nil {
		t.Fatal(err)
	}
	authID := component.NewID(component.MustNewType("testauth"))
	cfg.HTTP.Auth = &configauth.Authentication{AuthenticatorID: authID}
	host := authHost{extensions: map[component.ID]component.Component{
		authID: auth.NewServer(auth.WithServerAuthenticate(func(ctx context.Context, headers map[string][]string) (context.Context, error) {
			if http.Header(headers).Get("Authorization") != "Bearer test-token" {
				return ctx, errors.New("invalid token")
			}
			return ctx, nil
		})),
	}}
	settings := receiver.CreateSettings{ID: component.NewID(factory.Type()), TelemetrySettings: componenttest.NewNopTelemetrySettings()}
	traces, err := consumer.NewTraces(func(context.Context, ptrace.Traces) error { return consume() })
	if err != nil {
		t.Fatal(err)
	}
	logs, err := consumer.NewLogs(func(context.Context, plog.Logs) error { return consume() })
	if err != nil {
		t.Fatal(err)
	}
	metrics, err := consumer.NewMetrics(consumeMetrics)
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	r, err := factory.CreateTracesReceiver(ctx, settings, cfg, traces)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = factory.CreateLogsReceiver(ctx, settings, cfg, logs); err != nil {
		t.Fatal(err)
	}
	if _, err = factory.CreateMetricsReceiver(ctx, settings, cfg, metrics); err != nil {
		t.Fatal(err)
	}
	if err := r.Start(ctx, host); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := r.Shutdown(ctx); err != nil {
			t.Error(err)
		}
	})
	return "http://" + address
}

func exportPayload(t *testing.T, signal, encoding string) []byte {
	t.Helper()
	var req interface {
		MarshalJSON() ([]byte, error)
		MarshalProto() ([]byte, error)
	}
	switch signal {
	case "traces":
		r := ptraceotlp.NewExportRequest()
		r.Traces().ResourceSpans().AppendEmpty().ScopeSpans().AppendEmpty().Spans().AppendEmpty().SetName("retry-test")
		req = r
	case "logs":
		r := plogotlp.NewExportRequest()
		r.Logs().ResourceLogs().AppendEmpty().ScopeLogs().AppendEmpty().LogRecords().AppendEmpty().Body().SetStr("retry-test")
		req = r
	case "metrics":
		r := pmetricotlp.NewExportRequest()
		r.Metrics().ResourceMetrics().AppendEmpty().ScopeMetrics().AppendEmpty().Metrics().AppendEmpty().SetEmptyGauge().DataPoints().AppendEmpty().SetIntValue(1)
		req = r
	}
	var body []byte
	var err error
	if encoding == "json" {
		body, err = req.MarshalJSON()
	} else {
		body, err = req.MarshalProto()
	}
	if err != nil {
		t.Fatal(err)
	}
	return body
}

func post(t *testing.T, client *http.Client, url, contentType string, payload []byte, authorized bool) (*http.Response, []byte) {
	t.Helper()
	req, err := http.NewRequest(http.MethodPost, url, bytes.NewReader(payload))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Content-Type", contentType)
	if authorized {
		req.Header.Set("Authorization", "Bearer test-token")
	}
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	return resp, body
}
