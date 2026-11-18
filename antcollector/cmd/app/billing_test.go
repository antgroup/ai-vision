package main

import (
	"context"
	"reflect"
	"testing"

	"go.opentelemetry.io/collector/component"
	"go.opentelemetry.io/collector/consumer"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/processor"
)

func TestGatewayModelBilling(t *testing.T) {
	const (
		baiduURL  = "https://qianfan.baidubce.com/v2/tokenplan/personal/chat/completions"
		glmPath   = "/api/coding/paas/v4/chat/completions"
		operation = "gen_ai.operation.name"
		provider  = "gen_ai.provider.name"
		mode      = "aivision.billing.mode"
		sources   = "aivision.normalization.sources"
	)
	cases := []struct {
		name      string
		attrs     map[string]any
		additions map[string]any
	}{
		{"Baidu Langfuse generation", map[string]any{"langfuse.observation.type": "GENERATION", "url.full": baiduURL}, map[string]any{mode: "subscription", provider: "baidu"}},
		{"Baidu Anthropic legacy URL", map[string]any{operation: "chat", "http.url": "https://qianfan.baidubce.com/anthropic/tokenplan/personal/v1/messages"}, map[string]any{mode: "subscription", provider: "baidu"}},
		{"GLM domestic split endpoint", map[string]any{operation: "chat", "server.address": "open.bigmodel.cn", "url.path": glmPath, "server.port": int64(443)}, map[string]any{mode: "subscription", provider: "zhipuai"}},
		{"GLM international endpoint", map[string]any{operation: "chat", "url.full": "https://api.z.ai" + glmPath}, map[string]any{mode: "subscription", provider: "zai"}},
		{"unknown provider explicit subscription", map[string]any{operation: "chat", mode: "subscription", provider: "my-company", "url.full": "https://models.example/v1/chat/completions"}, nil},
		{"explicit pay as you go survives", map[string]any{operation: "chat", mode: "pay_as_you_go", provider: "my-company", "url.full": baiduURL}, nil},
		{"explicit provider survives endpoint inference", map[string]any{operation: "chat", provider: "my-company", "url.full": baiduURL}, map[string]any{mode: "subscription"}},
		{"weak Langfuse model prefix replaced", map[string]any{operation: "chat", provider: "deepseek", "url.full": baiduURL, sources: `{"gen_ai.provider.name":"model_prefix_inference","gen_ai.request.model":"langfuse.observation.model.name"}`}, map[string]any{mode: "subscription", provider: "baidu", sources: `{"gen_ai.request.model":"langfuse.observation.model.name"}`}},
		{"pay as you go on same GLM host", map[string]any{operation: "chat", "url.full": "https://api.z.ai/api/paas/v4/chat/completions"}, nil},
		{"shared GLM Responses route", map[string]any{operation: "chat", "url.full": "https://api.z.ai/api/v1/responses"}, nil},
		{"provider hostname alone", map[string]any{operation: "chat", "server.address": "qianfan.baidubce.com"}, nil},
		{"endpoint on non-model span", map[string]any{operation: "invoke_agent", "url.full": baiduURL}, nil},
		{"lookalike host", map[string]any{operation: "chat", "url.full": "https://qianfan.baidubce.com.example/v2/tokenplan/personal/chat/completions"}, nil},
		{"lookalike path", map[string]any{operation: "chat", "url.full": "https://qianfan.baidubce.com/v2/tokenplan/personal-preview/chat/completions"}, nil},
		{"endpoint in URL userinfo", map[string]any{operation: "chat", "url.full": "https://qianfan.baidubce.com@elsewhere.example/v2/tokenplan/personal/chat/completions"}, nil},
		{"contradictory split endpoint", map[string]any{operation: "chat", "url.full": "https://api.z.ai/api/paas/v4/chat/completions", "server.address": "api.z.ai", "url.path": glmPath}, nil},
	}
	factories, err := components()
	if err != nil {
		t.Fatal(err)
	}
	processorType := component.MustNewType("modelbilling")
	factory := factories.Processors[processorType]
	if factory == nil {
		t.Fatal("gateway does not register modelbilling")
	}
	ctx := context.Background()
	var forwarded ptrace.Traces
	sink, err := consumer.NewTraces(func(_ context.Context, td ptrace.Traces) error {
		forwarded = td
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	p, err := factory.CreateTracesProcessor(ctx, processor.CreateSettings{ID: component.NewID(processorType)}, factory.CreateDefaultConfig(), sink)
	if err != nil {
		t.Fatal(err)
	}
	input := ptrace.NewTraces()
	spans := input.ResourceSpans().AppendEmpty().ScopeSpans().AppendEmpty().Spans()
	for _, tc := range cases {
		span := spans.AppendEmpty()
		span.SetName(tc.name)
		if err := span.Attributes().FromRaw(tc.attrs); err != nil {
			t.Fatal(err)
		}
		span.Attributes().PutInt("gen_ai.usage.input_tokens", 218)
		span.Attributes().PutStr("gen_ai.request.model", "test-model")
	}
	if err := p.ConsumeTraces(ctx, input); err != nil {
		t.Fatal(err)
	}
	gotSpans := forwarded.ResourceSpans().At(0).ScopeSpans().At(0).Spans()
	if gotSpans.Len() != len(cases) {
		t.Fatalf("forwarded %d spans, want %d", gotSpans.Len(), len(cases))
	}
	for i, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			want := map[string]any{"gen_ai.usage.input_tokens": int64(218), "gen_ai.request.model": "test-model"}
			for k, v := range tc.attrs {
				want[k] = v
			}
			for k, v := range tc.additions {
				want[k] = v
			}
			if got := gotSpans.At(i).Attributes().AsRaw(); !reflect.DeepEqual(got, want) {
				t.Fatalf("attributes = %#v, want %#v", got, want)
			}
		})
	}
}
