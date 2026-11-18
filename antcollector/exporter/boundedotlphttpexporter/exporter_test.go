package boundedotlphttpexporter

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"sort"
	"strings"
	"sync"
	"testing"

	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/component/componenttest"
	"go.opentelemetry.io/collector/config/configcompression"
	"go.opentelemetry.io/collector/consumer"
	"go.opentelemetry.io/collector/consumer/consumererror"
	"go.opentelemetry.io/collector/exporter"
	"go.opentelemetry.io/collector/exporter/otlphttpexporter"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	metricnoop "go.opentelemetry.io/otel/metric/noop"
	"go.opentelemetry.io/otel/trace"
	"go.uber.org/zap"
)

type recordingExporter struct {
	component.StartFunc
	component.ShutdownFunc
	bodies [][]byte
	names  []string
	fail   string
}

func (*recordingExporter) Capabilities() consumer.Capabilities { return consumer.Capabilities{} }
func (e *recordingExporter) ConsumeTraces(_ context.Context, td ptrace.Traces) error {
	name := td.ResourceSpans().At(0).ScopeSpans().At(0).Spans().At(0).Name()
	if e.fail == name {
		return errors.New("temporary failure")
	}
	body, _ := ptraceotlp.NewExportRequestFromTraces(td).MarshalProto()
	e.bodies = append(e.bodies, body)
	for r := 0; r < td.ResourceSpans().Len(); r++ {
		scopes := td.ResourceSpans().At(r).ScopeSpans()
		for s := 0; s < scopes.Len(); s++ {
			spans := scopes.At(s).Spans()
			for i := 0; i < spans.Len(); i++ {
				e.names = append(e.names, spans.At(i).Name())
			}
		}
	}
	return nil
}

func traces(names ...string) ptrace.Traces {
	td := ptrace.NewTraces()
	for _, name := range names {
		rs := td.ResourceSpans().AppendEmpty()
		rs.Resource().Attributes().PutStr("space", "retained-space")
		rs.SetSchemaUrl("resource-schema")
		ss := rs.ScopeSpans().AppendEmpty()
		ss.Scope().SetName("sdk-scope")
		ss.SetSchemaUrl("scope-schema")
		sp := ss.Spans().AppendEmpty()
		sp.SetName(name)
		sp.Attributes().PutStr("payload", strings.Repeat("x", 256))
		sp.Events().AppendEmpty().SetName("retained-event")
	}
	return td
}

func TestTraceBatchSplitPreservesEverySpanAndScope(t *testing.T) {
	for _, encoding := range []otlphttpexporter.EncodingType{otlphttpexporter.EncodingProto, otlphttpexporter.EncodingJSON} {
		t.Run(string(encoding), func(t *testing.T) {
			source := traces("input-image", "input-audio", "output-video", "output-pdf")
			original, _ := ptraceotlp.NewExportRequestFromTraces(source).MarshalProto()
			next := &recordingExporter{}
			wrapper := &tracesExporter{Traces: next, encoding: encoding, maxBytes: 700}
			if err := wrapper.ConsumeTraces(context.Background(), source); err != nil {
				t.Fatal(err)
			}
			if len(next.bodies) <= 1 || len(next.names) != 4 {
				t.Fatalf("requests=%d names=%v", len(next.bodies), next.names)
			}
			for _, body := range next.bodies {
				req := ptraceotlp.NewExportRequest()
				if err := req.UnmarshalProto(body); err != nil {
					t.Fatal(err)
				}
				actualSize, err := requestSize(req.Traces(), encoding)
				if err != nil || actualSize > 700 {
					t.Fatalf("size=%d err=%v", actualSize, err)
				}
				rs := req.Traces().ResourceSpans().At(0)
				value, _ := rs.Resource().Attributes().Get("space")
				if rs.SchemaUrl() != "resource-schema" || value.Str() != "retained-space" || rs.ScopeSpans().At(0).SchemaUrl() != "scope-schema" || rs.ScopeSpans().At(0).Spans().At(0).Events().At(0).Name() != "retained-event" {
					t.Fatal("span context changed")
				}
			}
			after, _ := ptraceotlp.NewExportRequestFromTraces(source).MarshalProto()
			if string(after) != string(original) {
				t.Fatal("source changed while splitting")
			}
		})
	}
}

func TestOversizedSpanDoesNotDiscardOtherSpans(t *testing.T) {
	source := traces("oversized", "valid")
	source.ResourceSpans().At(0).ScopeSpans().At(0).Spans().At(0).Attributes().PutStr("payload", strings.Repeat("x", 2000))
	next := &recordingExporter{}
	wrapper := &tracesExporter{Traces: next, encoding: otlphttpexporter.EncodingProto, maxBytes: 700}
	err := wrapper.ConsumeTraces(context.Background(), source)
	var partial consumererror.Traces
	if !consumererror.IsPermanent(err) || !errors.As(err, &partial) || partial.Data().SpanCount() != 1 {
		t.Fatalf("want permanent error for one span, got %v", err)
	}
	if len(next.names) != 1 || next.names[0] != "valid" {
		t.Fatalf("valid span lost: %v", next.names)
	}
}

func TestSplitBatchRetriesOnlyFailedSpans(t *testing.T) {
	next := &recordingExporter{fail: "retry-me"}
	wrapper := &tracesExporter{Traces: next, encoding: otlphttpexporter.EncodingProto, maxBytes: 500}
	err := wrapper.ConsumeTraces(context.Background(), traces("accepted", "retry-me"))
	var partial consumererror.Traces
	if err == nil || consumererror.IsPermanent(err) || !errors.As(err, &partial) || partial.Data().SpanCount() != 1 {
		t.Fatalf("want retryable single span error, got %v", err)
	}
	next.fail = ""
	if err := wrapper.ConsumeTraces(context.Background(), partial.Data()); err != nil {
		t.Fatal(err)
	}
	if strings.Join(next.names, ",") != "accepted,retry-me" {
		t.Fatalf("successful span replayed: %v", next.names)
	}
}

func TestMixedOversizeAndRetryKeepsRetryableSubset(t *testing.T) {
	source := traces("oversized", "retry-me", "accepted")
	source.ResourceSpans().At(0).ScopeSpans().At(0).Spans().At(0).Attributes().PutStr("payload", strings.Repeat("x", 2000))
	next := &recordingExporter{fail: "retry-me"}
	wrapper := &tracesExporter{Traces: next, encoding: otlphttpexporter.EncodingProto, maxBytes: 500}
	err := wrapper.ConsumeTraces(context.Background(), source)
	var partial consumererror.Traces
	if err == nil || consumererror.IsPermanent(err) || !errors.As(err, &partial) || partial.Data().SpanCount() != 1 {
		t.Fatalf("retryable span was not preserved: %v", err)
	}
	failed := partial.Data().ResourceSpans().At(0).ScopeSpans().At(0).Spans().At(0).Name()
	if failed != "retry-me" {
		t.Fatalf("retry set includes permanently rejected data: %s", failed)
	}
}

func TestDefaultFactoryRetainsStandardSignalConfiguration(t *testing.T) {
	factory := NewFactory()
	if factory.Type() != otlphttpexporter.NewFactory().Type() {
		t.Fatal("exporter configuration identity changed")
	}
	cfg, ok := factory.CreateDefaultConfig().(*otlphttpexporter.Config)
	if !ok || cfg.Encoding != otlphttpexporter.EncodingProto {
		t.Fatal("standard OTLP config lost")
	}
}

func TestGatewayLargeMediaRebatchUsesBoundedHTTPRequests(t *testing.T) {
	var mu sync.Mutex
	var received []string
	var sizes []int
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 64*1024*1024))
		if err != nil {
			http.Error(w, "request too large", 400)
			return
		}
		request := ptraceotlp.NewExportRequest()
		if err := request.UnmarshalProto(body); err != nil {
			http.Error(w, "invalid protobuf", 400)
			return
		}
		mu.Lock()
		sizes = append(sizes, len(body))
		scopes := request.Traces().ResourceSpans().At(0).ScopeSpans()
		for i := 0; i < scopes.Len(); i++ {
			for j := 0; j < scopes.At(i).Spans().Len(); j++ {
				received = append(received, scopes.At(i).Spans().At(j).Name())
			}
		}
		mu.Unlock()
		w.Header().Set("Content-Type", "application/x-protobuf")
		w.WriteHeader(200)
	}))
	defer server.Close()
	factory := NewFactory()
	cfg := factory.CreateDefaultConfig().(*otlphttpexporter.Config)
	cfg.TracesEndpoint = server.URL + "/v1/traces"
	cfg.Compression = configcompression.Type("none")
	cfg.QueueConfig.Enabled = true
	cfg.RetryConfig.Enabled = false
	settings := exporter.CreateSettings{
		ID:                component.NewID(factory.Type()),
		TelemetrySettings: component.TelemetrySettings{Logger: zap.NewNop(), TracerProvider: trace.NewNoopTracerProvider(), MeterProvider: metricnoop.NewMeterProvider()},
	}
	e, err := factory.CreateTracesExporter(context.Background(), settings, cfg)
	if err != nil {
		t.Fatal(err)
	}
	if err := e.Start(context.Background(), componenttest.NewNopHost()); err != nil {
		t.Fatal(err)
	}
	// Recreate the gateway's merged LLM+turn batch. Each span contains two
	// individually valid 16 MiB Base64 media payloads.
	source := ptrace.NewTraces()
	spans := source.ResourceSpans().AppendEmpty().ScopeSpans().AppendEmpty().Spans()
	media := strings.Repeat("A", 2*((16*1024*1024+2)/3*4))
	for _, name := range []string{"generation", "turn"} {
		sp := spans.AppendEmpty()
		sp.SetName(name)
		sp.Attributes().PutStr("gen_ai.input.messages", media)
		sp.Attributes().PutStr("gen_ai.output.messages", "retained output")
	}
	if err := e.ConsumeTraces(context.Background(), source); err != nil {
		t.Fatal(err)
	}
	if err := e.Shutdown(context.Background()); err != nil {
		t.Fatal(err)
	}
	mu.Lock()
	defer mu.Unlock()
	sort.Strings(received)
	if strings.Join(received, ",") != "generation,turn" || len(sizes) != 2 {
		t.Fatalf("received=%v sizes=%v", received, sizes)
	}
	for _, size := range sizes {
		if size > maxTraceRequestBytes {
			t.Fatalf("wire body too large: %d", size)
		}
	}
	t.Logf("merged payload was split into %v byte requests", sizes)
}
