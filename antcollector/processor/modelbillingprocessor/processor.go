package modelbillingprocessor

import (
	"context"
	"encoding/json"
	"net/url"
	"strings"

	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/consumer"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/processor"
	"go.opentelemetry.io/collector/processor/processorhelper"
)

const (
	billingMode          = "aivision.billing.mode"
	providerName         = "gen_ai.provider.name"
	normalizationSources = "aivision.normalization.sources"
)

// NewFactory creates the trace processor that infers subscription use from
// recognized model endpoints when the client has not supplied a billing mode.
func NewFactory() processor.Factory {
	return processor.NewFactory(
		component.MustNewType("modelbilling"),
		func() component.Config { return &struct{}{} },
		processor.WithTraces(createTracesProcessor, component.StabilityLevelAlpha),
	)
}

func createTracesProcessor(ctx context.Context, set processor.CreateSettings, cfg component.Config, next consumer.Traces) (processor.Traces, error) {
	return processorhelper.NewTracesProcessor(ctx, set, cfg, next, processTraces,
		processorhelper.WithCapabilities(consumer.Capabilities{MutatesData: true}))
}

func processTraces(_ context.Context, traces ptrace.Traces) (ptrace.Traces, error) {
	resources := traces.ResourceSpans()
	for i := 0; i < resources.Len(); i++ {
		scopes := resources.At(i).ScopeSpans()
		for j := 0; j < scopes.Len(); j++ {
			spans := scopes.At(j).Spans()
			for k := 0; k < spans.Len(); k++ {
				applySubscription(spans.At(k).Attributes())
			}
		}
	}
	return traces, nil
}

func applySubscription(attrs pcommon.Map) {
	if !isModelCall(attrs) {
		return
	}
	provider := subscriptionProvider(modelEndpoint(attrs))
	if provider == "" {
		return
	}
	if _, hasMode := attrs.Get(billingMode); !hasMode {
		attrs.PutStr(billingMode, "subscription")
	}
	if _, hasProvider := attrs.Get(providerName); !hasProvider || clearInferredProvider(attrs) {
		attrs.PutStr(providerName, provider)
	}
}

func isModelCall(attrs pcommon.Map) bool {
	switch stringAttribute(attrs, "gen_ai.operation.name") {
	case "chat", "text_completion", "embeddings", "embedding":
		return true
	case "execute_tool", "invoke_agent", "retrieval":
		return false
	}
	for _, key := range []string{"langfuse.observation.type", "gen_ai.span.kind", "openinference.span.kind"} {
		switch strings.ToLower(stringAttribute(attrs, key)) {
		case "generation", "embedding", "llm":
			return true
		case "":
			continue
		default:
			return false
		}
	}
	return false
}

func stringAttribute(attrs pcommon.Map, key string) string {
	value, ok := attrs.Get(key)
	if !ok || value.Type() != pcommon.ValueTypeStr {
		return ""
	}
	return value.Str()
}

// modelEndpoint reads only the current span. Full request URLs take precedence
// over split attributes. Return the escaped path so alternate path encodings
// cannot match a documented route, and never copy URL data to new attributes.
func modelEndpoint(attrs pcommon.Map) (host, path string) {
	for _, key := range []string{"url.full", "http.url"} {
		value, ok := attrs.Get(key)
		if !ok {
			continue
		}
		if value.Type() != pcommon.ValueTypeStr {
			return "", ""
		}
		endpoint, err := url.Parse(value.Str())
		if err != nil || endpoint.Scheme != "https" || endpoint.User != nil || endpoint.Fragment != "" {
			return "", ""
		}
		return strings.TrimSuffix(strings.ToLower(endpoint.Host), ":443"), endpoint.EscapedPath()
	}
	if scheme, ok := attrs.Get("url.scheme"); ok && (scheme.Type() != pcommon.ValueTypeStr || scheme.Str() != "https") {
		return "", ""
	}
	if port, ok := attrs.Get("server.port"); ok && (port.Type() != pcommon.ValueTypeInt || port.Int() != 443) {
		return "", ""
	}
	return strings.ToLower(stringAttribute(attrs, "server.address")), stringAttribute(attrs, "url.path")
}

func subscriptionProvider(host, path string) string {
	path = strings.TrimSuffix(path, "/")
	switch host {
	case "qianfan.baidubce.com":
		switch path {
		case "/v2/tokenplan/personal", "/v2/tokenplan/personal/chat/completions",
			"/anthropic/tokenplan/personal", "/anthropic/tokenplan/personal/v1/messages":
			return "baidu"
		}
	case "open.bigmodel.cn", "api.z.ai":
		// Other protocols use endpoints shared with the standard API. Those
		// calls need a mode supplied explicitly by their connection or SDK.
		if path == "/api/coding/paas/v4" || path == "/api/coding/paas/v4/chat/completions" {
			if host == "open.bigmodel.cn" {
				return "zhipuai"
			}
			return "zai"
		}
	}
	return ""
}

// The Langfuse bridge may infer a provider from a model name. A dedicated
// endpoint identifies the service more precisely. Keep explicit provider data
// and unrelated provenance, and clear the replaced provider's old source.
func clearInferredProvider(attrs pcommon.Map) bool {
	var sources map[string]json.RawMessage
	raw := stringAttribute(attrs, normalizationSources)
	if raw == "" || json.Unmarshal([]byte(raw), &sources) != nil {
		return false
	}
	var providerSource string
	if json.Unmarshal(sources[providerName], &providerSource) != nil || providerSource != "model_prefix_inference" {
		return false
	}
	delete(sources, providerName)
	if len(sources) == 0 {
		attrs.Remove(normalizationSources)
		return true
	}
	remaining, err := json.Marshal(sources)
	if err != nil {
		return false
	}
	attrs.PutStr(normalizationSources, string(remaining))
	return true
}
