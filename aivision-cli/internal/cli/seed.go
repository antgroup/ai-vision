package cli

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/config"
	"github.com/antgroup/ai-vision/aivision-cli/internal/query"
)

// cmdSeed bulk-imports demo telemetry so a fresh space's dashboards are not
// empty. It is deliberately write-path honest: rather than writing rows
// straight into the datastore (which would bypass the authoritative identity
// stamping), it mints a real v2 ingest token from the backend for a registered
// Agent and pushes OTLP traces through the gateway exactly like a live coding
// agent would. Space, user and agent product are therefore stamped server-side
// from the token, so seeded data is attributed and partitioned identically to
// production traffic and is immediately queryable via `aiv query`.
//
// It needs one thing the profile does not carry: the target agent's uuid. The
// SpaceKey is used only to mint the token; the OTLP push uses the short-lived
// ingest token, never the SpaceKey.
func cmdSeed(args []string) error {
	fs := flag.NewFlagSet("seed", flag.ContinueOnError)
	endpoint := fs.String("endpoint", "", "AI Vision base URL (default: $"+config.EnvEndpoint+" or active profile)")
	spaceKey := fs.String("space-key", "", "SpaceKey, sk-aiv-… used to mint the ingest token (default: $"+config.EnvSpaceKey+" or active profile)")
	space := fs.String("space", "", "space id (default: $"+config.EnvSpaceID+" or active profile)")
	agentUUID := fs.String("agent-uuid", "", "registered agent uuid to seed data for (required; e.g. from aiv connect)")
	gateway := fs.String("gateway", "", "OTLP gateway base URL or complete /v1/traces endpoint (default: the endpoint returned when minting)")
	sessions := fs.Int("sessions", 8, "number of sessions to generate")
	turns := fs.Int("turns", 4, "turns (traces) per session")
	windowMin := fs.Int("window-minutes", 60, "spread generated data across the last N minutes")
	ttl := fs.Int("ttl", 0, "ingest token TTL in seconds (0 = backend default; capped by the server)")
	dryRun := fs.Bool("dry-run", false, "build the payload and print counts without pushing")
	model := fs.String("model", "seed-model", "model name to attribute LLM spans to")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *sessions < 1 || *turns < 1 {
		return fmt.Errorf("seed: --sessions and --turns must be >= 1")
	}

	// Resolve write coordinates: flags → env → active profile. Product is not
	// needed here; it comes back on the minted token from the registered agent.
	ep, sk, sp := *endpoint, *spaceKey, *space
	if ep == "" || sk == "" || sp == "" {
		if p, ok := activeProfile(); ok {
			ep = firstNonEmpty(ep, p.Endpoint)
			sk = firstNonEmpty(sk, p.SpaceKey)
			sp = firstNonEmpty(sp, p.SpaceID)
		}
	}
	ep = firstNonEmpty(ep, os.Getenv(config.EnvEndpoint))
	sk = firstNonEmpty(sk, os.Getenv(config.EnvSpaceKey))
	sp = firstNonEmpty(sp, os.Getenv(config.EnvSpaceID))
	au := firstNonEmpty(*agentUUID, os.Getenv("AIVISION_AGENT_UUID"))
	if ep == "" {
		return fmt.Errorf("no endpoint: pass --endpoint, set %s, or run: aiv auth login", config.EnvEndpoint)
	}
	if sk == "" {
		return fmt.Errorf("no space key: pass --space-key, set %s, or run: aiv auth login", config.EnvSpaceKey)
	}
	if sp == "" {
		return fmt.Errorf("no space id: pass --space, set %s, or run: aiv auth login", config.EnvSpaceID)
	}
	if au == "" {
		return fmt.Errorf("no agent uuid: pass --agent-uuid (a registered Agent in the space; see `aiv connect` output or the Agent market)")
	}

	ctx := context.Background()
	cl := query.New(ep, sk, sp, "")

	// Mint a v2 ingest token for the target agent. The backend bakes the agent's
	// product and the space/user into the token, so the gateway stamps them and
	// the data lands under a product that `aiv query` can already route to.
	mintBody := map[string]any{"agent_uuid": au}
	if *ttl > 0 {
		mintBody["ttl_seconds"] = *ttl
	}
	mint, err := cl.Post(ctx, cl.Endpoint+"/openapi/v1/ingest-tokens", mintBody)
	if err != nil {
		return fmt.Errorf("mint ingest token: %w", err)
	}
	var tok struct {
		Token        string `json:"token"`
		AgentProduct string `json:"agent_product"`
		Endpoint     string `json:"endpoint"`
	}
	if err := json.Unmarshal(mint, &tok); err != nil {
		return fmt.Errorf("parse mint response: %w", err)
	}
	if tok.Token == "" {
		return fmt.Errorf("mint returned an empty token")
	}
	product := firstNonEmpty(tok.AgentProduct, "aiv-seed")
	gw := firstNonEmpty(*gateway, tok.Endpoint, "http://127.0.0.1:4318")

	payload, spanCount := buildSeedPayload(product, *model, *sessions, *turns, *windowMin)
	if *dryRun {
		fmt.Printf("dry-run: %d trace(s) x %d turn(s) = %d span(s) as product %q (not pushed)\n",
			*sessions, *turns, spanCount, product)
		return nil
	}
	ingestURL, err := traceIngestURL(gw)
	if err != nil {
		return err
	}
	if err := pushTraces(ctx, ingestURL, tok.Token, payload); err != nil {
		return err
	}
	fmt.Printf("seeded %d span(s) across %d session(s) (%d turns each) into %s as product %q\n",
		spanCount, *sessions, *turns, ingestURL, product)
	fmt.Printf("verify: aiv query session list --agent-product %q --since %dm\n", product, *windowMin+5)
	return nil
}

// --- OTLP/HTTP JSON construction --------------------------------------------
//
// The gateway's otlphttp receiver decodes application/json with W3C-style hex
// identifiers (traceId 32 hex chars, spanId 16), fixed64 timestamps as strings,
// and every attribute value as a stringValue (matching how real agent spans
// store numeric fields as strings). We deliberately omit any product-steering
// resource attribute (service.namespace / ant.agent.product): agent product is
// derived from the token-stamped aivision.agent.product, so seeded rows
// attribute to the very product the mint chose.

type otlpAnyValue struct {
	StringValue string `json:"stringValue,omitempty"`
}

type otlpKV struct {
	Key   string       `json:"key"`
	Value otlpAnyValue `json:"value"`
}

type otlpSpan struct {
	TraceID       string      `json:"traceId"`
	SpanID        string      `json:"spanId"`
	ParentSpanID  string      `json:"parentSpanId,omitempty"`
	Name          string      `json:"name"`
	Kind          int         `json:"kind,omitempty"`
	StartUnixNano string      `json:"startTimeUnixNano"`
	EndUnixNano   string      `json:"endTimeUnixNano"`
	Attributes    []otlpKV    `json:"attributes,omitempty"`
	Status        *otlpStatus `json:"status,omitempty"`
}

type otlpStatus struct {
	Code int `json:"code,omitempty"`
}

type otlpScope struct {
	Name string `json:"name"`
}

type otlpScopeSpans struct {
	Scope otlpScope  `json:"scope"`
	Spans []otlpSpan `json:"spans"`
}

type otlpResource struct {
	Attributes []otlpKV `json:"attributes"`
}

type otlpResourceSpans struct {
	Resource   otlpResource     `json:"resource"`
	ScopeSpans []otlpScopeSpans `json:"scopeSpans"`
}

type otlpExportRequest struct {
	ResourceSpans []otlpResourceSpans `json:"resourceSpans"`
}

func strKV(k, v string) otlpKV { return otlpKV{Key: k, Value: otlpAnyValue{StringValue: v}} }

// buildSeedPayload assembles sessions × turns of CHAT-root + LLM-child spans
// (plus a TOOL child on every third turn) into a single OTLP export request,
// spread backwards across the last windowMinutes. It returns the marshalled
// body and the total span count.
func buildSeedPayload(product, model string, sessions, turns, windowMinutes int) ([]byte, int) {
	window := time.Duration(windowMinutes) * time.Minute
	now := time.Now()
	total := sessions * turns
	var spans []otlpSpan
	for s := 0; s < sessions; s++ {
		conversationID := newUUID() // promoted to the session_id column by the mapper
		chatID := newUUID()
		for t := 0; t < turns; t++ {
			// Evenly space each turn within the window, oldest first.
			step := time.Duration(int64(window) * int64(s*turns+t) / int64(total))
			start := now.Add(-window + step)
			turnDur := time.Duration(2000+(s*turns+t)%4000) * time.Millisecond

			traceID := randHex(16)
			chatSpan := randHex(8)
			userPrompt := fmt.Sprintf("seed scenario %d step %d", s+1, t+1)
			assistantMsg := fmt.Sprintf("seeded response for scenario %d step %d", s+1, t+1)

			// CHAT root span (kind=SERVER), no parent, marks the session/turn.
			spans = append(spans, otlpSpan{
				TraceID:       traceID,
				SpanID:        chatSpan,
				Name:          fmt.Sprintf("Turn %d", t+1),
				Kind:          2,
				StartUnixNano: fmt.Sprintf("%d", start.UnixNano()),
				EndUnixNano:   fmt.Sprintf("%d", start.Add(turnDur).UnixNano()),
				Status:        &otlpStatus{Code: 1},
				Attributes: []otlpKV{
					strKV("gen_ai.span.kind", "CHAT"),
					strKV("gen_ai.operation.name", "invoke_agent"),
					strKV("gen_ai.conversation.id", conversationID),
					strKV("gen_ai.chat.id", chatID),
					strKV("gen_ai.provider.name", "seed"),
					strKV("gen_ai.request.model", model),
					strKV("agentic.turn.number", fmt.Sprintf("%d", t+1)),
					strKV("agentic.turn.trigger", "interactive"),
					strKV("agentic.runtime.name", "aiv-seed"),
					strKV("_internal.meta.root", "1"),
					strKV("gen_ai.input.messages", fmt.Sprintf(`[{"role":"user","content":%q}]`, userPrompt)),
					strKV("gen_ai.output.messages", fmt.Sprintf(`[{"role":"assistant","content":%q,"finish_reason":"stop"}]`, assistantMsg)),
				},
			})

			// LLM child span with token usage so metrics/usage dashboards fill.
			inTok := 400 + (s*turns+t)*37
			outTok := 80 + (s*turns+t)*11
			llmStart := start.Add(200 * time.Millisecond)
			llmEnd := start.Add(turnDur - 200*time.Millisecond)
			spans = append(spans, otlpSpan{
				TraceID:       traceID,
				SpanID:        randHex(8),
				ParentSpanID:  chatSpan,
				Name:          "chat " + model,
				Kind:          1,
				StartUnixNano: fmt.Sprintf("%d", llmStart.UnixNano()),
				EndUnixNano:   fmt.Sprintf("%d", llmEnd.UnixNano()),
				Status:        &otlpStatus{Code: 1},
				Attributes: []otlpKV{
					strKV("gen_ai.span.kind", "LLM"),
					strKV("gen_ai.operation.name", "chat"),
					strKV("gen_ai.conversation.id", conversationID),
					strKV("gen_ai.chat.id", chatID),
					strKV("gen_ai.provider.name", "seed"),
					strKV("gen_ai.request.model", model),
					strKV("gen_ai.response.model", model),
					strKV("gen_ai.usage.input_tokens", fmt.Sprintf("%d", inTok)),
					strKV("gen_ai.usage.output_tokens", fmt.Sprintf("%d", outTok)),
					strKV("gen_ai.usage.total_tokens", fmt.Sprintf("%d", inTok+outTok)),
					strKV("gen_ai.response.finish_reasons", "stop"),
					strKV("gen_ai.input.messages", fmt.Sprintf(`[{"role":"user","content":%q}]`, userPrompt)),
					strKV("gen_ai.output.messages", fmt.Sprintf(`[{"role":"assistant","content":%q,"finish_reason":"stop"}]`, assistantMsg)),
				},
			})

			// A TOOL child on every third turn to exercise tool analytics.
			if (s*turns+t)%3 == 2 {
				toolStart := start.Add(400 * time.Millisecond)
				spans = append(spans, otlpSpan{
					TraceID:       traceID,
					SpanID:        randHex(8),
					ParentSpanID:  chatSpan,
					Name:          "tool read_file",
					Kind:          3,
					StartUnixNano: fmt.Sprintf("%d", toolStart.UnixNano()),
					EndUnixNano:   fmt.Sprintf("%d", toolStart.Add(300*time.Millisecond).UnixNano()),
					Status:        &otlpStatus{Code: 1},
					Attributes: []otlpKV{
						strKV("gen_ai.span.kind", "TOOL"),
						strKV("gen_ai.operation.name", "execute_tool"),
						strKV("gen_ai.conversation.id", conversationID),
						strKV("gen_ai.chat.id", chatID),
						strKV("gen_ai.tool.name", "read_file"),
						strKV("gen_ai.tool.call.id", "call_"+strings.TrimSuffix(chatSpan, "=")),
					},
				})
			}
		}
	}

	req := otlpExportRequest{ResourceSpans: []otlpResourceSpans{{
		Resource: otlpResource{Attributes: []otlpKV{
			strKV("service.name", product),
			strKV("telemetry.sdk.name", "aiv-seed"),
			strKV("agentic.collection.mode", "full"),
		}},
		ScopeSpans: []otlpScopeSpans{{
			Scope: otlpScope{Name: "aiv-seed"},
			Spans: spans,
		}},
	}}}
	body, err := json.Marshal(req)
	if err != nil {
		return nil, 0
	}
	return body, len(spans)
}

// pushTraces sends the OTLP JSON body to a resolved trace endpoint with the
// minted ingest token as Bearer. A non-2xx response surfaces the gateway's
// error text so auth/expiry/routing problems are visible.
func pushTraces(ctx context.Context, endpoint, token string, payload []byte) error {
	if len(payload) == 0 {
		return fmt.Errorf("seed: empty payload")
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(payload))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+token)
	client := &http.Client{Timeout: 60 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("cannot reach gateway %s: %w", endpoint, err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("gateway rejected the push (HTTP %d): %s", resp.StatusCode, strings.TrimSpace(string(raw)))
	}
	return nil
}

// traceIngestURL accepts a gateway base or a complete trace endpoint. Other
// OTLP signal endpoints are rejected so seed cannot report success after
// sending trace data to a logs or metrics receiver.
func traceIngestURL(endpoint string) (string, error) {
	parsed, err := url.Parse(strings.TrimSpace(endpoint))
	if err != nil || parsed.Scheme == "" || parsed.Host == "" {
		return "", fmt.Errorf("seed: invalid OTLP gateway URL")
	}
	path := strings.TrimRight(parsed.Path, "/")
	switch {
	case strings.HasSuffix(path, "/v1/traces"):
		parsed.Path = path
	case strings.HasSuffix(path, "/v1/logs"), strings.HasSuffix(path, "/v1/metrics"), strings.HasSuffix(path, "/v1/profiles"):
		return "", fmt.Errorf("seed: OTLP gateway endpoint must target traces, not another signal")
	default:
		parsed.Path = path + "/v1/traces"
	}
	return parsed.String(), nil
}

// randHex returns n random bytes as lowercase hex (2n chars), matching the W3C
// OTLP/JSON identifier format the gateway's trace/span id parser expects.
func randHex(n int) string {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		for i := range b {
			b[i] = byte(time.Now().UnixNano() >> (8 * i))
		}
	}
	return hex.EncodeToString(b)
}

// newUUID renders a random (v4) UUID string, used for conversation and chat ids.
func newUUID() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		now := uint64(time.Now().UnixNano())
		for i := 0; i < 16; i++ {
			b[i] = byte(now >> (8 * (i % 8)))
		}
	}
	b[6] = (b[6] & 0x0f) | 0x40 // version 4
	b[8] = (b[8] & 0x3f) | 0x80 // variant 10xx
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:16])
}
