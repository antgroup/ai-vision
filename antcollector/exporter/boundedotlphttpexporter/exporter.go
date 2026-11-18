// Package boundedotlphttpexporter bounds gateway requests after trace batching.
package boundedotlphttpexporter

import (
	"context"
	"errors"
	"fmt"

	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/consumer/consumererror"
	"go.opentelemetry.io/collector/exporter"
	"go.opentelemetry.io/collector/exporter/otlphttpexporter"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"go.uber.org/zap"
)

const maxTraceRequestBytes = 60 * 1024 * 1024

// NewFactory keeps the standard OTLP HTTP configuration, lifecycle and queues.
// Splitting after processors and before queue admission prevents count-based
// batching from exceeding SigNoz's 64 MiB request admission limit.
func NewFactory() exporter.Factory {
	base := otlphttpexporter.NewFactory()
	return exporter.NewFactory(base.Type(), base.CreateDefaultConfig,
		exporter.WithTraces(func(ctx context.Context, set exporter.CreateSettings, cfg component.Config) (exporter.Traces, error) {
			next, err := base.CreateTracesExporter(ctx, set, cfg)
			if err != nil {
				return nil, err
			}
			return &tracesExporter{Traces: next, encoding: cfg.(*otlphttpexporter.Config).Encoding, maxBytes: maxTraceRequestBytes, logger: set.Logger}, nil
		}, base.TracesExporterStability()),
		exporter.WithLogs(base.CreateLogsExporter, base.LogsExporterStability()),
		exporter.WithMetrics(base.CreateMetricsExporter, base.MetricsExporterStability()),
	)
}

type tracesExporter struct {
	exporter.Traces
	encoding otlphttpexporter.EncodingType
	maxBytes int
	logger   *zap.Logger
}

func requestSize(td ptrace.Traces, encoding otlphttpexporter.EncodingType) (int, error) {
	if encoding == otlphttpexporter.EncodingProto {
		return (&ptrace.ProtoMarshaler{}).TracesSize(td), nil
	}
	data, err := ptraceotlp.NewExportRequestFromTraces(td).MarshalJSON()
	return len(data), err
}

func (e *tracesExporter) ConsumeTraces(ctx context.Context, td ptrace.Traces) error {
	retryable, rejected := ptrace.NewTraces(), ptrace.NewTraces()
	var retryErrors, permanentErrors []error
	var send func(ptrace.Traces)
	send = func(part ptrace.Traces) {
		size, err := requestSize(part, e.encoding)
		if err == nil && size > e.maxBytes && part.SpanCount() > 1 {
			middle := part.SpanCount() / 2
			send(spanRange(part, 0, middle))
			send(spanRange(part, middle, part.SpanCount()))
			return
		}
		if err != nil || size > e.maxBytes {
			if err == nil {
				err = fmt.Errorf("trace span exceeds the %d byte request limit", e.maxBytes)
			}
			appendTraces(rejected, part)
			permanentErrors = append(permanentErrors, err)
			return
		}
		err = e.Traces.ConsumeTraces(ctx, part)
		if err == nil {
			return
		}
		failed := part
		var partial consumererror.Traces
		if errors.As(err, &partial) {
			failed = partial.Data()
		}
		if consumererror.IsPermanent(err) {
			appendTraces(rejected, failed)
			permanentErrors = append(permanentErrors, err)
		} else {
			appendTraces(retryable, failed)
			retryErrors = append(retryErrors, err)
		}
	}
	send(td)
	if rejected.SpanCount() > 0 && e.logger != nil {
		e.logger.Warn("Trace spans permanently rejected during bounded export", zap.Int("rejected_spans", rejected.SpanCount()))
	}
	if retryable.SpanCount() > 0 {
		// A permanent cause here would also discard the recoverable subset.
		return consumererror.NewTraces(errors.Join(retryErrors...), retryable)
	}
	if rejected.SpanCount() > 0 {
		return consumererror.NewPermanent(consumererror.NewTraces(errors.Join(permanentErrors...), rejected))
	}
	return nil
}

func appendTraces(destination, source ptrace.Traces) {
	resources := source.ResourceSpans()
	for i := 0; i < resources.Len(); i++ {
		resources.At(i).CopyTo(destination.ResourceSpans().AppendEmpty())
	}
}

// spanRange retains resource/scope grouping and every span field without
// mutating the original pdata, which may be shared with another consumer.
func spanRange(source ptrace.Traces, start, end int) ptrace.Traces {
	result := ptrace.NewTraces()
	index := 0
	resources := source.ResourceSpans()
	for i := 0; i < resources.Len(); i++ {
		resource := resources.At(i)
		scopes := resource.ScopeSpans()
		var copiedResource ptrace.ResourceSpans
		hasResource := false
		for j := 0; j < scopes.Len(); j++ {
			scope := scopes.At(j)
			spans := scope.Spans()
			var copiedScope ptrace.ScopeSpans
			hasScope := false
			for k := 0; k < spans.Len(); k++ {
				selected := index >= start && index < end
				index++
				if !selected {
					continue
				}
				if !hasResource {
					copiedResource = result.ResourceSpans().AppendEmpty()
					resource.Resource().CopyTo(copiedResource.Resource())
					copiedResource.SetSchemaUrl(resource.SchemaUrl())
					hasResource = true
				}
				if !hasScope {
					copiedScope = copiedResource.ScopeSpans().AppendEmpty()
					scope.Scope().CopyTo(copiedScope.Scope())
					copiedScope.SetSchemaUrl(scope.SchemaUrl())
					hasScope = true
				}
				spans.At(k).CopyTo(copiedScope.Spans().AppendEmpty())
			}
		}
	}
	return result
}
