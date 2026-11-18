package main

import (
	"bufio"
	"bytes"
	"compress/gzip"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strconv"
	"strings"
	"sync"

	collector "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	"google.golang.org/protobuf/proto"
)

// exportObserver observes the official exporter's request. It does not create
// spans, change their attributes, or implement another OTLP exporter.
type exportObserver struct {
	base                http.RoundTripper
	mu                  sync.Mutex
	spans               map[string]observedSpan
	duplicateDeliveries int
	rejectedSpans       int64
}

func (o *exportObserver) RoundTrip(req *http.Request) (*http.Response, error) {
	// The official OTLP client does not set GetBody. Restore the exact bytes
	// after observing them, retaining headers and ContentLength unchanged.
	wire, err := io.ReadAll(io.LimitReader(req.Body, (8<<20)+1))
	req.Body.Close()
	if err != nil || len(wire) > 8<<20 {
		return nil, errors.New("cannot inspect bounded OTLP request")
	}
	req.Body = io.NopCloser(bytes.NewReader(wire))
	var reader io.Reader = bytes.NewReader(wire)
	if req.Header.Get("Content-Encoding") == "gzip" {
		gz, err := gzip.NewReader(reader)
		if err != nil {
			return nil, errors.New("invalid OTLP compression")
		}
		defer gz.Close()
		reader = gz
	}
	data, err := io.ReadAll(io.LimitReader(reader, (8<<20)+1))
	if err != nil || len(data) > 8<<20 {
		return nil, errors.New("cannot inspect OTLP payload")
	}
	var exported collector.ExportTraceServiceRequest
	if err := proto.Unmarshal(data, &exported); err != nil {
		return nil, errors.New("invalid OTLP protobuf")
	}
	response, err := o.base.RoundTrip(req)
	if err != nil {
		return nil, errors.New("OTLP network request failed")
	}
	if response.StatusCode >= 200 && response.StatusCode < 300 {
		body, err := io.ReadAll(io.LimitReader(response.Body, (1<<20)+1))
		response.Body.Close()
		if err != nil || len(body) > 1<<20 {
			return nil, errors.New("cannot inspect bounded OTLP response")
		}
		var acceptance collector.ExportTraceServiceResponse
		if err := proto.Unmarshal(body, &acceptance); err != nil {
			return nil, errors.New("invalid OTLP acceptance response")
		}
		if partial := acceptance.PartialSuccess; partial != nil {
			// OTLP partial rejection is not a retryable HTTP failure. Preserve its
			// count and HTTP status for the official exporter, redact its message,
			// and fail our acceptance ledger even when the exporter returns nil.
			if partial.ErrorMessage != "" {
				partial.ErrorMessage = "telemetry ingestion reported partial acceptance"
			}
			body, err = proto.Marshal(&acceptance)
			if err != nil {
				return nil, errors.New("cannot sanitize OTLP acceptance response")
			}
		}
		response.Body = io.NopCloser(bytes.NewReader(body))
		response.ContentLength = int64(len(body))
		o.mu.Lock()
		defer o.mu.Unlock()
		if acceptance.GetPartialSuccess().GetRejectedSpans() > 0 {
			o.rejectedSpans += acceptance.GetPartialSuccess().GetRejectedSpans()
			return response, nil
		}
		for _, resource := range exported.ResourceSpans {
			for _, scope := range resource.ScopeSpans {
				for _, span := range scope.Spans {
					observed := observedSpan{Key: span.Name, SpanID: hex.EncodeToString(span.SpanId), ParentSpanID: hex.EncodeToString(span.ParentSpanId), StartTime: strconv.FormatUint(span.StartTimeUnixNano, 10), EndTime: strconv.FormatUint(span.EndTimeUnixNano, 10), UsageAttributes: map[string]int64{}}
					for _, attr := range span.Attributes {
						switch attr.Key {
						case "langfuse.observation.input":
							observed.Input = attr.Value.GetStringValue()
						case "langfuse.observation.output":
							observed.Output = attr.Value.GetStringValue()
						}
						if strings.HasPrefix(attr.Key, "gen_ai.usage.") {
							observed.UsageAttributes[attr.Key] = attr.Value.GetIntValue()
						}
					}
					if _, exists := o.spans[observed.SpanID]; exists {
						o.duplicateDeliveries++
					}
					o.spans[observed.SpanID] = observed
				}
			}
		}
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		response.Body.Close()
		body := "telemetry ingestion rejected the request"
		response.Body = io.NopCloser(strings.NewReader(body))
		response.ContentLength = int64(len(body))
	}
	return response, nil
}

func (o *exportObserver) snapshot() ([]observedSpan, error) {
	o.mu.Lock()
	defer o.mu.Unlock()
	result := make([]observedSpan, 0, len(o.spans))
	for _, span := range o.spans {
		result = append(result, span)
	}
	if o.rejectedSpans != 0 {
		return result, errors.New("OTLP receiver partially rejected exported spans")
	}
	if o.duplicateDeliveries != 0 {
		return result, errors.New("official callback exported duplicate successful span deliveries")
	}
	return result, nil
}

// Provider usage is independent of telemetry. Only the provider's explicit
// counters become expectations; absent usage is never replaced with zero.
type providerObserver struct {
	base     http.RoundTripper
	mu       sync.Mutex
	usage    map[string]int64
	readErr  error
	requests int
}

func (o *providerObserver) RoundTrip(req *http.Request) (*http.Response, error) {
	o.mu.Lock()
	o.requests++
	o.mu.Unlock()
	response, err := o.base.RoundTrip(req)
	if err != nil {
		if req.Context().Err() != nil {
			return nil, req.Context().Err()
		}
		return nil, errors.New("model provider network request failed")
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		response.Body.Close()
		// Provider error bodies may echo credentials or requests. Keep the HTTP
		// status while allowing the real model component to exercise OnError.
		body := `{"error":{"message":"provider request failed","type":"provider_error"}}`
		response.Body = io.NopCloser(strings.NewReader(body))
		response.ContentLength = int64(len(body))
		return response, nil
	}
	response.Body = &observedBody{ReadCloser: response.Body, done: o.record, stream: strings.Contains(response.Header.Get("Content-Type"), "text/event-stream")}
	return response, nil
}

type observedBody struct {
	io.ReadCloser
	buffer   bytes.Buffer
	once     sync.Once
	done     func([]byte, bool, bool)
	stream   bool
	overflow bool
}

func (b *observedBody) Read(p []byte) (int, error) {
	n, err := b.ReadCloser.Read(p)
	if b.buffer.Len()+n <= 1<<20 {
		b.buffer.Write(p[:n])
	} else {
		b.overflow = true
	}
	if err == io.EOF {
		b.finish()
	}
	return n, err
}
func (b *observedBody) Close() error { err := b.ReadCloser.Close(); b.finish(); return err }
func (b *observedBody) finish()      { b.once.Do(func() { b.done(b.buffer.Bytes(), b.stream, b.overflow) }) }

func (o *providerObserver) record(data []byte, stream, overflow bool) {
	o.mu.Lock()
	defer o.mu.Unlock()
	if overflow {
		o.readErr = errors.New("model response exceeded verification capture limit")
		return
	}
	parse := func(data []byte) {
		var payload struct {
			Usage *struct {
				PromptTokens     *int64 `json:"prompt_tokens"`
				CompletionTokens *int64 `json:"completion_tokens"`
				TotalTokens      *int64 `json:"total_tokens"`
			} `json:"usage"`
		}
		if err := json.Unmarshal(data, &payload); err != nil {
			o.readErr = errors.New("model usage response is invalid JSON")
			return
		}
		if payload.Usage == nil {
			return
		}
		usage := map[string]int64{}
		if payload.Usage.PromptTokens != nil {
			usage["input_tokens"] = *payload.Usage.PromptTokens
		}
		if payload.Usage.CompletionTokens != nil {
			usage["output_tokens"] = *payload.Usage.CompletionTokens
		}
		if payload.Usage.TotalTokens != nil {
			usage["total_tokens"] = *payload.Usage.TotalTokens
		}
		o.usage = usage
	}
	if !stream {
		parse(data)
		return
	}
	scanner := bufio.NewScanner(bytes.NewReader(data))
	scanner.Buffer(make([]byte, 4096), 1<<20)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if strings.HasPrefix(line, "data:") {
			payload := strings.TrimSpace(strings.TrimPrefix(line, "data:"))
			if payload != "[DONE]" {
				parse([]byte(payload))
			}
		}
	}
	if scanner.Err() != nil {
		o.readErr = errors.New("model stream exceeded verification line limit")
	}
}

func (o *providerObserver) snapshot() (map[string]int64, error) {
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.requests != 1 {
		return o.usage, errors.New("example must make exactly one model HTTP request per scenario")
	}
	return o.usage, o.readErr
}
