package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"time"

	langfuse "github.com/cloudwego/eino-ext/callbacks/langfuse/v2"
	"github.com/cloudwego/eino-ext/components/model/openai"
	"github.com/cloudwego/eino/components/model"
	"github.com/cloudwego/eino/components/tool"
	"github.com/cloudwego/eino/components/tool/utils"
	"github.com/cloudwego/eino/compose"
	"github.com/cloudwego/eino/schema"
	"go.opentelemetry.io/otel/trace"
)

type config struct{ Mode, Host, PublicKey, SecretKey, ModelBaseURL, ModelAPIKey, ModelName string }
type expectedSpan struct {
	Key            string           `json:"key"`
	Name           string           `json:"name"`
	ParentKey      string           `json:"parent_key"`
	Kind           string           `json:"kind"`
	Status         int              `json:"status"`
	InputContains  string           `json:"input_contains,omitempty"`
	OutputContains string           `json:"output_contains,omitempty"`
	Usage          map[string]int64 `json:"usage"`
}
type observedSpan struct {
	Key             string           `json:"key"`
	SpanID          string           `json:"span_id"`
	ParentSpanID    string           `json:"parent_span_id"`
	Input           string           `json:"input,omitempty"`
	Output          string           `json:"output,omitempty"`
	StartTime       string           `json:"start_time_unix_nano"`
	EndTime         string           `json:"end_time_unix_nano"`
	UsageAttributes map[string]int64 `json:"usage_attributes,omitempty"`
}
type expectation struct {
	SpanCount int            `json:"span_count"`
	Spans     []expectedSpan `json:"spans"`
}
type scenarioLedger struct {
	Scenario      string         `json:"scenario"`
	TraceID       string         `json:"trace_id"`
	FromMS        int64          `json:"from_ms"`
	ToMS          int64          `json:"to_ms"`
	Expected      expectation    `json:"expected"`
	ObservedSpans []observedSpan `json:"observed_spans"`
}
type ledger struct {
	SchemaVersion int              `json:"schema_version"`
	Mode          string           `json:"mode"`
	Scenarios     []scenarioLedger `json:"scenarios"`
}

const prompt = `Call the echo tool exactly once with text "hello-eino". Do not change the text.`
const toolResult = "Eino tool received: hello-eino"

func runSuite(ctx context.Context, cfg config) (ledger, error) {
	result := ledger{SchemaVersion: 1, Mode: cfg.Mode}
	if err := validateConfig(cfg); err != nil {
		return result, err
	}
	scenarios := []string{"normal", "stream"}
	var fixtureURL string
	var cancelStarted <-chan struct{}
	if cfg.Mode == "fixture" {
		server, started := modelFixture()
		defer server.Close()
		fixtureURL, cancelStarted = server.URL, started
		scenarios = append(scenarios, "missing_usage", "error", "cancel", "shutdown")
	}
	for _, scenario := range scenarios {
		runConfig := cfg
		if cfg.Mode == "fixture" {
			runConfig.ModelBaseURL = fixtureURL + "/" + scenario + "/v1"
			runConfig.ModelAPIKey, runConfig.ModelName = "fixture-model-key", "fixture-model"
		}
		entry, err := runScenario(ctx, runConfig, scenario, cancelStarted)
		if err != nil {
			return result, err
		}
		result.Scenarios = append(result.Scenarios, entry)
	}
	return result, nil
}

func validateConfig(cfg config) error {
	if cfg.Mode != "fixture" && cfg.Mode != "live" {
		return errors.New("mode must be fixture or live")
	}
	if cfg.PublicKey == "" || cfg.SecretKey == "" {
		return errors.New("LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are required")
	}
	if err := validateEndpoint(cfg.Host); err != nil {
		return errors.New("LANGFUSE_HOST must be an HTTP(S) URL without credentials, query, or fragment")
	}
	if cfg.Mode == "live" {
		if cfg.ModelAPIKey == "" || cfg.ModelName == "" {
			return errors.New("MODEL_API_KEY and MODEL_NAME are required for live mode")
		}
		if err := validateEndpoint(cfg.ModelBaseURL); err != nil {
			return errors.New("MODEL_BASE_URL must be an HTTP(S) URL without credentials, query, or fragment")
		}
	}
	return nil
}

func validateEndpoint(value string) error {
	u, err := url.Parse(value)
	if err != nil || u.Host == "" || (u.Scheme != "https" && u.Scheme != "http") || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return errors.New("invalid endpoint")
	}
	return nil
}

func runScenario(parent context.Context, cfg config, scenario string, cancelStarted <-chan struct{}) (scenarioLedger, error) {
	entry := scenarioLedger{Scenario: scenario, FromMS: time.Now().Add(-time.Second).UnixMilli()}
	ctx, cancel := context.WithTimeout(parent, 90*time.Second)
	defer cancel()
	observer := &exportObserver{base: http.DefaultTransport, spans: map[string]observedSpan{}}
	handler, err := langfuse.NewHandler(ctx, &langfuse.Config{
		Host: cfg.Host, PublicKey: cfg.PublicKey, SecretKey: cfg.SecretKey,
		ServiceName: "ai-vision-eino-example", Environment: "eino-example",
		BatchTimeout: time.Hour, Timeout: 10 * time.Second,
		RetryConfig: &langfuse.RetryConfig{Enabled: true, InitialInterval: time.Second, MaxInterval: 2 * time.Second, MaxElapsedTime: 10 * time.Second},
		HTTPClient:  &http.Client{Transport: observer, Timeout: 10 * time.Second},
		MaskFunc: func(value string) string {
			for _, secret := range []string{cfg.SecretKey, cfg.ModelAPIKey} {
				if secret != "" {
					value = strings.ReplaceAll(value, secret, "[redacted]")
				}
			}
			return value
		},
	})
	if err != nil {
		return entry, errors.New("could not initialize official Langfuse callback")
	}
	stopped := false
	defer func() {
		if !stopped {
			cleanup, done := context.WithTimeout(context.Background(), 15*time.Second)
			defer done()
			handler.Shutdown(cleanup)
		}
	}()
	provider := &providerObserver{base: http.DefaultTransport}
	maxTokens := 256
	modelClient, err := openai.NewChatModel(ctx, &openai.ChatModelConfig{
		APIKey: cfg.ModelAPIKey, BaseURL: cfg.ModelBaseURL, Model: cfg.ModelName,
		HTTPClient: &http.Client{Transport: provider, Timeout: 80 * time.Second},
		MaxTokens:  &maxTokens,
	})
	if err != nil {
		return entry, errors.New("could not initialize official OpenAI model")
	}
	// The tool validates the fixed business contract independently of telemetry.
	type echoInput struct {
		Text string `json:"text" jsonschema:"description=The exact text to echo"`
	}
	echoTool, err := utils.InferTool("echo", "Echo the supplied text", func(ctx context.Context, input *echoInput) (string, error) {
		if input.Text != "hello-eino" {
			return "", errors.New("model did not follow the example tool argument contract")
		}
		return toolResult, nil
	})
	if err != nil {
		return entry, errors.New("could not initialize example tool")
	}
	toolInfo, err := echoTool.Info(ctx)
	if err != nil {
		return entry, errors.New("could not read tool schema")
	}
	boundModel, err := modelClient.WithTools([]*schema.ToolInfo{toolInfo})
	if err != nil {
		return entry, errors.New("could not bind example tool")
	}
	tools, err := compose.NewToolNode(ctx, &compose.ToolsNodeConfig{Tools: []tool.BaseTool{echoTool}})
	if err != nil {
		return entry, errors.New("could not initialize Eino ToolsNode")
	}
	chain := compose.NewChain[[]*schema.Message, []*schema.Message]().
		AppendChatModel(boundModel, compose.WithNodeName("model"), compose.WithNodeKey("model")).
		AppendToolsNode(tools, compose.WithNodeName("tools"))
	runnable, err := chain.Compile(ctx, compose.WithGraphName("workflow"))
	if err != nil {
		return entry, errors.New("could not compile Eino workflow")
	}
	rootName := "eino-" + scenario
	traceCtx := handler.StartTrace(ctx, langfuse.WithName(rootName), langfuse.WithInput(prompt), langfuse.WithObservationType(langfuse.ObservationTypeChain))
	entry.TraceID = trace.SpanContextFromContext(traceCtx).TraceID().String()
	if scenario == "cancel" {
		go func() {
			select {
			case <-cancelStarted:
				cancel()
			case <-ctx.Done():
			}
		}()
	}
	opts := []compose.Option{compose.WithCallbacks(handler), compose.WithChatModelOption(model.WithToolChoice(schema.ToolChoiceForced, "echo")).DesignateNode("model")}
	var output []*schema.Message
	var runErr error
	if scenario == "stream" {
		var stream *schema.StreamReader[[]*schema.Message]
		stream, runErr = runnable.Stream(traceCtx, []*schema.Message{schema.UserMessage(prompt)}, opts...)
		if runErr == nil {
			for {
				chunk, readErr := stream.Recv()
				if readErr == io.EOF {
					break
				}
				if readErr != nil {
					runErr = readErr
					break
				}
				output = append(output, chunk...)
			}
			stream.Close()
		}
	} else {
		output, runErr = runnable.Invoke(traceCtx, []*schema.Message{schema.UserMessage(prompt)}, opts...)
	}
	if scenario != "shutdown" {
		finalOutput := ""
		if runErr == nil && len(output) == 1 {
			finalOutput = output[0].Content
		}
		handler.EndTrace(traceCtx, finalOutput)
	}
	// Cancellation must not cancel telemetry cleanup. Shutdown is deliberately
	// the only flush in its scenario, exercising process-exit drainage.
	cleanup, done := context.WithTimeout(context.Background(), 20*time.Second)
	defer done()
	if scenario != "shutdown" {
		if err := handler.Flush(cleanup); err != nil {
			return entry, errors.New("official callback Flush failed")
		}
	}
	if err := handler.Shutdown(cleanup); err != nil {
		return entry, errors.New("official callback Shutdown failed")
	}
	stopped = true
	if scenario == "error" || scenario == "cancel" {
		if runErr == nil {
			return entry, fmt.Errorf("%s did not stop the workflow as expected", scenario)
		}
	} else if runErr != nil || len(output) != 1 || !strings.Contains(output[0].Content, toolResult) {
		return entry, fmt.Errorf("%s failed the fixed tool-call contract; check the provider configuration and trace", scenario)
	}
	usage, err := provider.snapshot()
	if err != nil {
		return entry, err
	}
	entry.Expected = expectedWorkflow(rootName, scenario, usage)
	entry.ObservedSpans, err = observer.snapshot()
	if err != nil {
		return entry, err
	}
	sort.Slice(entry.ObservedSpans, func(i, j int) bool { return entry.ObservedSpans[i].Key < entry.ObservedSpans[j].Key })
	entry.ToMS = time.Now().Add(time.Second).UnixMilli()
	if len(entry.ObservedSpans) != entry.Expected.SpanCount {
		return entry, fmt.Errorf("%s exported %d spans, expected %d", scenario, len(entry.ObservedSpans), entry.Expected.SpanCount)
	}
	return entry, nil
}

func expectedWorkflow(rootName, scenario string, usage map[string]int64) expectation {
	status, output := 0, toolResult
	if scenario == "error" {
		status, output = 2, "provider request failed"
	}
	if scenario == "cancel" {
		output = "cancelled"
	}
	spans := []expectedSpan{
		{Key: rootName, Name: rootName, Kind: "chain", Status: status, InputContains: "hello-eino", OutputContains: output},
		{Key: "workflow", Name: "workflow", ParentKey: rootName, Kind: "span", Status: status, InputContains: "hello-eino", OutputContains: output},
		{Key: "model", Name: "model", ParentKey: "workflow", Kind: "generation", Status: status, InputContains: "hello-eino", OutputContains: "echo", Usage: usage},
	}
	if scenario == "error" || scenario == "cancel" {
		spans[2].OutputContains = output
	} else {
		spans = append(spans,
			expectedSpan{Key: "tools", Name: "tools", ParentKey: "workflow", Kind: "span", InputContains: "hello-eino", OutputContains: toolResult},
			expectedSpan{Key: "echo", Name: "echo", ParentKey: "tools", Kind: "tool", InputContains: "hello-eino", OutputContains: toolResult},
		)
	}
	return expectation{SpanCount: len(spans), Spans: spans}
}
