package io.aivision.demo;

import io.opentelemetry.api.trace.Span;
import io.opentelemetry.api.trace.SpanKind;
import io.opentelemetry.api.trace.StatusCode;
import io.opentelemetry.api.trace.Tracer;
import io.opentelemetry.context.Context;
import io.opentelemetry.exporter.otlp.http.trace.OtlpHttpSpanExporter;
import io.opentelemetry.exporter.otlp.http.trace.OtlpHttpSpanExporterBuilder;
import io.opentelemetry.sdk.OpenTelemetrySdk;
import io.opentelemetry.sdk.resources.Resource;
import io.opentelemetry.sdk.trace.SdkTracerProvider;
import io.opentelemetry.sdk.trace.export.SimpleSpanProcessor;

import io.aivision.demo.semconv.AgentResource;

import java.util.UUID;

import static io.aivision.demo.semconv.SpanAttributes.*;

/**
 * Simulates a multi-turn AI Agent conversation producing OTel spans
 * that conform to aivision-agents semantic conventions.
 *
 * Span topology (A1):
 * <pre>
 * Turn 1 (CHAT, invoke_agent) [SERVER]
 * ├── chat claude-sonnet-4-6  (LLM, generation 0) [CLIENT]
 * ├── execute_tool Read       (TOOL, builtin)      [INTERNAL]
 * └── chat claude-sonnet-4-6  (LLM, generation 1) [CLIENT]
 *
 * Turn 2 (CHAT, invoke_agent) [SERVER]
 * ├── chat claude-sonnet-4-6  (LLM, generation 0) [CLIENT]
 * ├── execute_tool mcp search (TOOL, mcp, ERROR)   [INTERNAL]
 * ├── chat claude-sonnet-4-6  (LLM, generation 1) [CLIENT]
 * └── execute_tool Bash       (TOOL, builtin)      [INTERNAL]
 * </pre>
 *
 * Run: mvn compile exec:java
 *
 * OTLP exporter configuration (all optional, falls back to console-only):
 *   OTEL_EXPORTER_OTLP_ENDPOINT  — HTTP endpoint, e.g. "http://localhost:4318"
 *   OTEL_EXPORTER_OTLP_HEADERS   — comma-separated "key=value" pairs,
 *                                   e.g. "Authorization=Basic xxx,X-Tenant=demo"
 */
public class AgentOtelDemo {

    private static final String MODEL = "claude-sonnet-4-6";
    private static final String RESPONSE_MODEL = "claude-sonnet-4-6-20250514";
    private static final String USERNAME = "100001";

    private static final String DEFAULT_OTLP_ENDPOINT = "http://localhost:4318/v1/traces";

    public static void main(String[] args) {
        Resource resource = AgentResource.create(
                "aivision-demo", "aivision-demo", USERNAME, "default", "Dev");

        OtlpHttpSpanExporter otlpExporter = buildOtlpExporter();

        SdkTracerProvider tracerProvider = SdkTracerProvider.builder()
                .setResource(resource)
                .addSpanProcessor(SimpleSpanProcessor.create(otlpExporter))
                .build();

        OpenTelemetrySdk otel = OpenTelemetrySdk.builder()
                .setTracerProvider(tracerProvider)
                .build();

        Tracer tracer = otel.getTracer("aivision-agent-demo", "1.0.0");

        String conversationId = "conv-" + UUID.randomUUID().toString().substring(0, 8);

        System.out.println("=== AI Agent OTel Demo ===");
        System.out.println("conversation.id: " + conversationId);
        System.out.println();

        simulateTurn1(tracer, conversationId);
        simulateTurn2(tracer, conversationId);

        tracerProvider.forceFlush().join(10, java.util.concurrent.TimeUnit.SECONDS);
        tracerProvider.shutdown().join(10, java.util.concurrent.TimeUnit.SECONDS);

        System.out.println();
        System.out.println("=== Done. All spans exported. ===");
    }

    /**
     * Turn 1: user asks to read a file. Agent calls LLM → Read tool → LLM.
     */
    private static void simulateTurn1(Tracer tracer, String conversationId) {
        String chatId = "chat-" + UUID.randomUUID().toString().substring(0, 8);

        // ── Root CHAT span (SERVER, A1 topology) ──
        Span chatSpan = tracer.spanBuilder("Turn 1")
                .setSpanKind(SpanKind.SERVER)
                .setAttribute(GEN_AI_SPAN_KIND, SPAN_KIND_CHAT)
                .setAttribute(GEN_AI_OPERATION_NAME, OP_INVOKE_AGENT)
                .setAttribute(GEN_AI_CONVERSATION_ID, conversationId)
                .setAttribute(GEN_AI_CHAT_ID, chatId)
                .setAttribute(ANT_USERNAME, USERNAME)
                .setAttribute(AGENTIC_RUNTIME_NAME, "claude-code")
                .setAttribute(AGENTIC_RUNTIME_CWD, "/Users/demo/project")
                .setAttribute(AGENTIC_TURN_NUMBER, 1L)
                .setAttribute(GEN_AI_INPUT_MESSAGES,
                        "[{\"role\":\"user\",\"content\":\"Help me read src/main.ts\"}]")
                .startSpan();

        Context chatCtx = Context.current().with(chatSpan);

        try {
            // ── LLM span: generation 0 (decides to call Read tool) ──
            createLlmSpan(tracer, chatCtx, conversationId, 0,
                    1500, 200, 0, 1200, 0,
                    "[{\"role\":\"assistant\",\"content\":\"I'll read that file for you.\","
                            + "\"tool_calls\":[{\"id\":\"toolu_001\",\"type\":\"function\","
                            + "\"function\":{\"name\":\"Read\",\"arguments\":\"{\\\"file_path\\\":\\\"src/main.ts\\\"}\"}}]}]",
                    3200, "msg_turn1_gen0");

            // ── TOOL span: Read (builtin) ──
            createBuiltinToolSpan(tracer, chatCtx,
                    "Read", "toolu_001", 85,
                    "read", "/src/main.ts", 0);

            // ── LLM span: generation 1 (summarizes file content) ──
            createLlmSpan(tracer, chatCtx, conversationId, 1,
                    1800, 350, 0, 1500, 0,
                    "[{\"role\":\"assistant\",\"content\":\"The file contains a TypeScript entry point...\"}]",
                    2800, "msg_turn1_gen1");

        } finally {
            chatSpan.end();
        }
    }

    /**
     * Turn 2: user asks to search code via MCP. MCP tool fails with timeout (B2),
     * agent recovers and uses Bash as fallback.
     */
    private static void simulateTurn2(Tracer tracer, String conversationId) {
        String chatId = "chat-" + UUID.randomUUID().toString().substring(0, 8);

        // ── Root CHAT span ──
        Span chatSpan = tracer.spanBuilder("Turn 2")
                .setSpanKind(SpanKind.SERVER)
                .setAttribute(GEN_AI_SPAN_KIND, SPAN_KIND_CHAT)
                .setAttribute(GEN_AI_OPERATION_NAME, OP_INVOKE_AGENT)
                .setAttribute(GEN_AI_CONVERSATION_ID, conversationId)
                .setAttribute(GEN_AI_CHAT_ID, chatId)
                .setAttribute(ANT_USERNAME, USERNAME)
                .setAttribute(AGENTIC_RUNTIME_NAME, "claude-code")
                .setAttribute(AGENTIC_RUNTIME_CWD, "/Users/demo/project")
                .setAttribute(AGENTIC_TURN_NUMBER, 2L)
                .setAttribute(GEN_AI_INPUT_MESSAGES,
                        "[{\"role\":\"user\",\"content\":\"Search for authentication code via a code search MCP tool\"}]")
                .startSpan();

        Context chatCtx = Context.current().with(chatSpan);

        try {
            // ── LLM span: generation 0 (decides to call MCP tool) ──
            createLlmSpan(tracer, chatCtx, conversationId, 0,
                    2000, 180, 0, 1600, 0,
                    "[{\"role\":\"assistant\",\"content\":\"\","
                            + "\"tool_calls\":[{\"id\":\"toolu_002\",\"type\":\"function\","
                            + "\"function\":{\"name\":\"mcp__code_search__query_code_with_keyword\","
                            + "\"arguments\":\"{\\\"searchPattern\\\":\\\"auth\\\"}\"}}]}]",
                    5100, "msg_turn2_gen0");

            // ── TOOL span: MCP tool (ERROR — timeout, B2 scenario) ──
            createMcpToolSpan(tracer, chatCtx,
                    "mcp__code_search__query_code_with_keyword",
                    "code_search.query_code_with_keyword",
                    "toolu_002", 30000, "timeout");

            // ── LLM span: generation 1 (recovery, decides to use Bash fallback) ──
            createLlmSpan(tracer, chatCtx, conversationId, 1,
                    2200, 250, 0, 1800, 0,
                    "[{\"role\":\"assistant\",\"content\":\"MCP search timed out, let me try grep.\","
                            + "\"tool_calls\":[{\"id\":\"toolu_003\",\"type\":\"function\","
                            + "\"function\":{\"name\":\"Bash\","
                            + "\"arguments\":\"{\\\"command\\\":\\\"grep -r auth src/\\\"}\"}}]}]",
                    3500, "msg_turn2_gen1");

            // ── TOOL span: Bash (builtin, success) ──
            createBuiltinToolSpan(tracer, chatCtx,
                    "Bash", "toolu_003", 450,
                    "bash", null, 0);

        } finally {
            chatSpan.end();
        }
    }

    /**
     * Creates an LLM span (gen_ai.span.kind=LLM) as a sibling under the parent CHAT span.
     * Conforms to span.ant.llm in spans.yaml and capability C1/C7.
     */
    private static void createLlmSpan(Tracer tracer, Context parentCtx,
                                      String conversationId, int responseIndex,
                                      long inputTokens, long outputTokens,
                                      long cacheCreationTokens, long cacheReadTokens,
                                      long reasoningTokens,
                                      String outputMessages,
                                      long durationMs, String responseId) {
        long totalTokens = inputTokens + outputTokens;
        long freshInput = inputTokens - cacheReadTokens - cacheCreationTokens;
        double cacheHitRate = inputTokens > 0 ? (double) cacheReadTokens / inputTokens : 0.0;

        Span span = tracer.spanBuilder("chat " + MODEL)
                .setParent(parentCtx)
                .setSpanKind(SpanKind.CLIENT)
                // ── Core identity ──
                .setAttribute(GEN_AI_SPAN_KIND, SPAN_KIND_LLM)
                .setAttribute(GEN_AI_OPERATION_NAME, OP_CHAT)
                .setAttribute(GEN_AI_SYSTEM, "anthropic")
                // ── Model ──
                .setAttribute(GEN_AI_REQUEST_MODEL, MODEL)
                .setAttribute(GEN_AI_RESPONSE_MODEL, RESPONSE_MODEL)
                .setAttribute(GEN_AI_RESPONSE_ID, responseId)
                .setAttribute(GEN_AI_REQUEST_TEMPERATURE, 1.0)
                .setAttribute(GEN_AI_REQUEST_MAX_TOKENS, 16000L)
                // ── Token L1 (normalized) ──
                .setAttribute(GEN_AI_USAGE_INPUT_TOKENS, inputTokens)
                .setAttribute(GEN_AI_USAGE_OUTPUT_TOKENS, outputTokens)
                // ── Token L1 detail (Ant extended) ──
                .setAttribute(GEN_AI_USAGE_DETAILS_CACHE_READ, cacheReadTokens)
                .setAttribute(GEN_AI_USAGE_DETAILS_CACHE_CREATION, cacheCreationTokens)
                .setAttribute(GEN_AI_USAGE_DETAILS_CACHE_HIT_RATE, cacheHitRate)
                // ── Token L2 breakdown ──
                .setAttribute(AGENTIC_USAGE_FRESH_INPUT, freshInput)
                .setAttribute(AGENTIC_USAGE_CACHE_READ, cacheReadTokens)
                .setAttribute(AGENTIC_USAGE_CACHE_WRITE, cacheCreationTokens)
                .setAttribute(AGENTIC_USAGE_REASONING, reasoningTokens)
                // ── Agentic context ──
                .setAttribute(AGENTIC_RESPONSE_INDEX, (long) responseIndex)
                .setAttribute(AGENTIC_LLM_DURATION_MS, durationMs)
                .setAttribute(AGENTIC_LLM_STEP, (long) responseIndex)
                .setAttribute(GEN_AI_RESPONSE_TTFT, 538L)
                // ── Content ──
                .setAttribute(GEN_AI_OUTPUT_MESSAGES, outputMessages)
                // ── Identity ──
                .setAttribute(ANT_USERNAME, USERNAME)
                .startSpan();
        span.end();
    }

    /**
     * Creates a builtin TOOL span (gen_ai.span.kind=TOOL, agentic.tool.type=builtin).
     * Conforms to span.ant.tool in spans.yaml and capabilities B1-B4.
     */
    private static void createBuiltinToolSpan(Tracer tracer, Context parentCtx,
                                              String toolName, String callId,
                                              long durationMs,
                                              String codeToolName, String filePath,
                                              int exitCode) {
        Span span = tracer.spanBuilder("execute_tool " + toolName)
                .setParent(parentCtx)
                .setSpanKind(SpanKind.INTERNAL)
                // ── Core identity ──
                .setAttribute(GEN_AI_SPAN_KIND, SPAN_KIND_TOOL)
                .setAttribute(GEN_AI_OPERATION_NAME, OP_EXECUTE_TOOL)
                // ── Tool identification ──
                .setAttribute(GEN_AI_TOOL_NAME, toolName)
                .setAttribute(GEN_AI_TOOL_CALL_ID, callId)
                .setAttribute(GEN_AI_TOOL_TYPE, "function")
                // ── Agentic tool classification ──
                .setAttribute(AGENTIC_TOOL_TYPE, TOOL_TYPE_BUILTIN)
                .setAttribute(AGENTIC_TOOL_NAME, toolName)
                .setAttribute(AGENTIC_TOOL_RAW_NAME, toolName)
                .setAttribute(AGENTIC_TOOL_ID, callId)
                .setAttribute(AGENTIC_TOOL_DURATION_MS, durationMs)
                .setAttribute(AGENTIC_TOOL_DECISION, "allow")
                .setAttribute(AGENTIC_TOOL_DECISION_SOURCE, "auto")
                // ── Coding agent specifics ──
                .setAttribute(CODE_TOOL_NAME, codeToolName)
                .setAttribute(CODE_TOOL_EXIT_CODE, (long) exitCode)
                .setAttribute(CODE_FILE_OPERATION, filePath != null ? "read" : "exec")
                .setAttribute(CODE_FILE_PATH, filePath != null ? filePath : "")
                // ── Identity ──
                .setAttribute(ANT_USERNAME, USERNAME)
                .startSpan();
        span.end();
    }

    /**
     * Creates an MCP TOOL span with ERROR status (B2 error scenario).
     * agentic.tool.type=mcp, derived from mcp__server__method naming.
     */
    private static void createMcpToolSpan(Tracer tracer, Context parentCtx,
                                          String rawName, String logicalName,
                                          String callId, long durationMs,
                                          String errorType) {
        Span span = tracer.spanBuilder("execute_tool " + logicalName)
                .setParent(parentCtx)
                .setSpanKind(SpanKind.INTERNAL)
                // ── Core identity ──
                .setAttribute(GEN_AI_SPAN_KIND, SPAN_KIND_TOOL)
                .setAttribute(GEN_AI_OPERATION_NAME, OP_EXECUTE_TOOL)
                // ── Tool identification ──
                .setAttribute(GEN_AI_TOOL_NAME, rawName)
                .setAttribute(GEN_AI_TOOL_CALL_ID, callId)
                .setAttribute(GEN_AI_TOOL_TYPE, "function")
                // ── Agentic tool classification (mcp) ──
                .setAttribute(AGENTIC_TOOL_TYPE, TOOL_TYPE_MCP)
                .setAttribute(AGENTIC_TOOL_NAME, logicalName)
                .setAttribute(AGENTIC_TOOL_RAW_NAME, rawName)
                .setAttribute(AGENTIC_TOOL_ID, callId)
                .setAttribute(AGENTIC_TOOL_DURATION_MS, durationMs)
                .setAttribute(AGENTIC_TOOL_DECISION, "allow")
                .setAttribute(AGENTIC_TOOL_DECISION_SOURCE, "policy")
                // ── Identity ──
                .setAttribute(ANT_USERNAME, USERNAME)
                .startSpan();

        if (errorType != null) {
            span.setStatus(StatusCode.ERROR, errorType);
            span.setAttribute(ERROR_TYPE, errorType);
        }

        span.end();
    }

    /**
     * Builds an OTLP HTTP/protobuf span exporter.
     * Endpoint: env OTEL_EXPORTER_OTLP_ENDPOINT, default "http://localhost:4318".
     * Headers:  env OTEL_EXPORTER_OTLP_HEADERS, comma-separated "key=value" pairs.
     */
    private static OtlpHttpSpanExporter buildOtlpExporter() {
        String endpoint = System.getenv("OTEL_EXPORTER_OTLP_ENDPOINT");
        if (endpoint == null || endpoint.isEmpty()) {
            endpoint = DEFAULT_OTLP_ENDPOINT;
        }

        OtlpHttpSpanExporterBuilder builder = OtlpHttpSpanExporter.builder()
                .setEndpoint(endpoint);

        String headers = System.getenv("OTEL_EXPORTER_OTLP_HEADERS");
        if (headers != null && !headers.isEmpty()) {
            for (String pair : headers.split(",")) {
                String[] kv = pair.split("=", 2);
                if (kv.length == 2) {
                    builder.addHeader(kv[0].trim(), kv[1].trim());
                }
            }
        }

        System.out.println("[OTLP] Exporting to " + endpoint);
        return builder.build();
    }
}
