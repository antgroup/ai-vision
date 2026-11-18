import { trace, SpanKind, SpanStatusCode, context } from '@opentelemetry/api';
import { BasicTracerProvider, SimpleSpanProcessor } from '@opentelemetry/sdk-trace-base';
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-http';
import { createAgentResource } from './semconv/agent-resource.mjs';
import * as A from './semconv/span-attributes.mjs';
import crypto from 'node:crypto';

/**
 * Simulates a multi-turn AI Agent conversation producing OTel spans
 * that conform to aivision-agents semantic conventions.
 *
 * Span topology (A1):
 *
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
 *
 * Run: npm start
 *
 * OTLP exporter configuration (all optional, falls back to localhost):
 *   OTEL_EXPORTER_OTLP_ENDPOINT  — HTTP endpoint, e.g. "http://localhost:4318/v1/traces"
 *   OTEL_EXPORTER_OTLP_HEADERS   — comma-separated "key=value" pairs
 */

const MODEL = 'claude-sonnet-4-6';
const RESPONSE_MODEL = 'claude-sonnet-4-6-20250514';
const USERNAME = '100001';
const DEFAULT_OTLP_ENDPOINT = 'http://localhost:4318/v1/traces';

function shortId() {
  return crypto.randomUUID().slice(0, 8);
}

// ── OTLP Exporter ──

function buildOtlpExporter() {
  let endpoint = process.env.OTEL_EXPORTER_OTLP_ENDPOINT || DEFAULT_OTLP_ENDPOINT;

  const headers = {};
  const rawHeaders = process.env.OTEL_EXPORTER_OTLP_HEADERS;
  if (rawHeaders) {
    for (const pair of rawHeaders.split(',')) {
      const idx = pair.indexOf('=');
      if (idx > 0) {
        headers[pair.slice(0, idx).trim()] = pair.slice(idx + 1).trim();
      }
    }
  }

  console.log(`[OTLP] Exporting to ${endpoint}`);
  return new OTLPTraceExporter({ url: endpoint, headers });
}

// ── Span Builders ──

function createLlmSpan(tracer, parentCtx, conversationId, responseIndex, opts) {
  const { inputTokens, outputTokens, cacheCreationTokens, cacheReadTokens, reasoningTokens, outputMessages, durationMs, responseId } = opts;

  const freshInput = inputTokens - cacheReadTokens - cacheCreationTokens;
  const cacheHitRate = inputTokens > 0 ? cacheReadTokens / inputTokens : 0;

  const span = tracer.startSpan(`chat ${MODEL}`, {
    kind: SpanKind.CLIENT,
    attributes: {
      [A.GEN_AI_SPAN_KIND]: A.SPAN_KIND_LLM,
      [A.GEN_AI_OPERATION_NAME]: A.OP_CHAT,
      [A.GEN_AI_SYSTEM]: 'anthropic',
      [A.GEN_AI_REQUEST_MODEL]: MODEL,
      [A.GEN_AI_RESPONSE_MODEL]: RESPONSE_MODEL,
      [A.GEN_AI_RESPONSE_ID]: responseId,
      [A.GEN_AI_REQUEST_TEMPERATURE]: 1.0,
      [A.GEN_AI_REQUEST_MAX_TOKENS]: 16000,
      [A.GEN_AI_USAGE_INPUT_TOKENS]: inputTokens,
      [A.GEN_AI_USAGE_OUTPUT_TOKENS]: outputTokens,
      [A.GEN_AI_USAGE_CACHE_READ]: cacheReadTokens,
      [A.GEN_AI_USAGE_CACHE_CREATION]: cacheCreationTokens,
      [A.AGENTIC_USAGE_FRESH_INPUT]: freshInput,
      [A.AGENTIC_USAGE_CACHE_READ]: cacheReadTokens,
      [A.AGENTIC_USAGE_CACHE_WRITE]: cacheCreationTokens,
      [A.AGENTIC_USAGE_REASONING]: reasoningTokens,
      [A.AGENTIC_RESPONSE_INDEX]: responseIndex,
      [A.AGENTIC_LLM_DURATION_MS]: durationMs,
      [A.AGENTIC_LLM_STEP]: responseIndex,
      [A.GEN_AI_RESPONSE_TTFT]: 538,
      [A.GEN_AI_OUTPUT_MESSAGES]: outputMessages,
      [A.ANT_USERNAME]: USERNAME,
    },
  }, parentCtx);
  span.end();
}

function createBuiltinToolSpan(tracer, parentCtx, toolName, callId, durationMs, codeToolName, filePath, exitCode) {
  const span = tracer.startSpan(`execute_tool ${toolName}`, {
    kind: SpanKind.INTERNAL,
    attributes: {
      [A.GEN_AI_SPAN_KIND]: A.SPAN_KIND_TOOL,
      [A.GEN_AI_OPERATION_NAME]: A.OP_EXECUTE_TOOL,
      [A.GEN_AI_TOOL_NAME]: toolName,
      [A.GEN_AI_TOOL_CALL_ID]: callId,
      [A.GEN_AI_TOOL_TYPE]: 'function',
      [A.AGENTIC_TOOL_TYPE]: A.TOOL_TYPE_BUILTIN,
      [A.AGENTIC_TOOL_NAME]: toolName,
      [A.AGENTIC_TOOL_RAW_NAME]: toolName,
      [A.AGENTIC_TOOL_ID]: callId,
      [A.AGENTIC_TOOL_DURATION_MS]: durationMs,
      [A.AGENTIC_TOOL_DECISION]: 'allow',
      [A.AGENTIC_TOOL_DECISION_SOURCE]: 'auto',
      [A.CODE_TOOL_NAME]: codeToolName,
      [A.CODE_TOOL_EXIT_CODE]: exitCode,
      [A.CODE_FILE_OPERATION]: filePath ? 'read' : 'exec',
      [A.CODE_FILE_PATH]: filePath || '',
      [A.ANT_USERNAME]: USERNAME,
    },
  }, parentCtx);
  span.end();
}

function createMcpToolSpan(tracer, parentCtx, rawName, logicalName, callId, durationMs, errorType) {
  const span = tracer.startSpan(`execute_tool ${logicalName}`, {
    kind: SpanKind.INTERNAL,
    attributes: {
      [A.GEN_AI_SPAN_KIND]: A.SPAN_KIND_TOOL,
      [A.GEN_AI_OPERATION_NAME]: A.OP_EXECUTE_TOOL,
      [A.GEN_AI_TOOL_NAME]: rawName,
      [A.GEN_AI_TOOL_CALL_ID]: callId,
      [A.GEN_AI_TOOL_TYPE]: 'function',
      [A.AGENTIC_TOOL_TYPE]: A.TOOL_TYPE_MCP,
      [A.AGENTIC_TOOL_NAME]: logicalName,
      [A.AGENTIC_TOOL_RAW_NAME]: rawName,
      [A.AGENTIC_TOOL_ID]: callId,
      [A.AGENTIC_TOOL_DURATION_MS]: durationMs,
      [A.AGENTIC_TOOL_DECISION]: 'allow',
      [A.AGENTIC_TOOL_DECISION_SOURCE]: 'policy',
      [A.ANT_USERNAME]: USERNAME,
    },
  }, parentCtx);

  if (errorType) {
    span.setStatus({ code: SpanStatusCode.ERROR, message: errorType });
    span.setAttribute(A.ERROR_TYPE, errorType);
  }
  span.end();
}

// ── Turn Simulations ──

function simulateTurn1(tracer, conversationId) {
  const chatId = `chat-${shortId()}`;

  const chatSpan = tracer.startSpan('Turn 1', {
    kind: SpanKind.SERVER,
    attributes: {
      [A.GEN_AI_SPAN_KIND]: A.SPAN_KIND_CHAT,
      [A.GEN_AI_OPERATION_NAME]: A.OP_INVOKE_AGENT,
      [A.GEN_AI_CONVERSATION_ID]: conversationId,
      [A.GEN_AI_CHAT_ID]: chatId,
      [A.ANT_USERNAME]: USERNAME,
      [A.AGENTIC_RUNTIME_NAME]: 'claude-code',
      [A.AGENTIC_RUNTIME_CWD]: '/Users/demo/project',
      [A.AGENTIC_TURN_NUMBER]: 1,
      [A.GEN_AI_INPUT_MESSAGES]: JSON.stringify([{ role: 'user', content: 'Help me read src/main.ts' }]),
    },
  });

  const chatCtx = trace.setSpan(context.active(), chatSpan);

  try {
    createLlmSpan(tracer, chatCtx, conversationId, 0, {
      inputTokens: 1500, outputTokens: 200, cacheCreationTokens: 0, cacheReadTokens: 1200, reasoningTokens: 0,
      outputMessages: JSON.stringify([{
        role: 'assistant', content: "I'll read that file for you.",
        tool_calls: [{ id: 'toolu_001', type: 'function', function: { name: 'Read', arguments: '{"file_path":"src/main.ts"}' } }],
      }]),
      durationMs: 3200, responseId: 'msg_turn1_gen0',
    });

    createBuiltinToolSpan(tracer, chatCtx, 'Read', 'toolu_001', 85, 'read', '/src/main.ts', 0);

    createLlmSpan(tracer, chatCtx, conversationId, 1, {
      inputTokens: 1800, outputTokens: 350, cacheCreationTokens: 0, cacheReadTokens: 1500, reasoningTokens: 0,
      outputMessages: JSON.stringify([{ role: 'assistant', content: 'The file contains a TypeScript entry point...' }]),
      durationMs: 2800, responseId: 'msg_turn1_gen1',
    });
  } finally {
    chatSpan.end();
  }
}

function simulateTurn2(tracer, conversationId) {
  const chatId = `chat-${shortId()}`;

  const chatSpan = tracer.startSpan('Turn 2', {
    kind: SpanKind.SERVER,
    attributes: {
      [A.GEN_AI_SPAN_KIND]: A.SPAN_KIND_CHAT,
      [A.GEN_AI_OPERATION_NAME]: A.OP_INVOKE_AGENT,
      [A.GEN_AI_CONVERSATION_ID]: conversationId,
      [A.GEN_AI_CHAT_ID]: chatId,
      [A.ANT_USERNAME]: USERNAME,
      [A.AGENTIC_RUNTIME_NAME]: 'claude-code',
      [A.AGENTIC_RUNTIME_CWD]: '/Users/demo/project',
      [A.AGENTIC_TURN_NUMBER]: 2,
      [A.GEN_AI_INPUT_MESSAGES]: JSON.stringify([{ role: 'user', content: 'Search for authentication code via a code search MCP tool' }]),
    },
  });

  const chatCtx = trace.setSpan(context.active(), chatSpan);

  try {
    createLlmSpan(tracer, chatCtx, conversationId, 0, {
      inputTokens: 2000, outputTokens: 180, cacheCreationTokens: 0, cacheReadTokens: 1600, reasoningTokens: 0,
      outputMessages: JSON.stringify([{
        role: 'assistant', content: '',
        tool_calls: [{ id: 'toolu_002', type: 'function', function: { name: 'mcp__code_search__query_code_with_keyword', arguments: '{"searchPattern":"auth"}' } }],
      }]),
      durationMs: 5100, responseId: 'msg_turn2_gen0',
    });

    createMcpToolSpan(tracer, chatCtx,
      'mcp__code_search__query_code_with_keyword',
      'code_search.query_code_with_keyword',
      'toolu_002', 30000, 'timeout');

    createLlmSpan(tracer, chatCtx, conversationId, 1, {
      inputTokens: 2200, outputTokens: 250, cacheCreationTokens: 0, cacheReadTokens: 1800, reasoningTokens: 0,
      outputMessages: JSON.stringify([{
        role: 'assistant', content: 'MCP search timed out, let me try grep.',
        tool_calls: [{ id: 'toolu_003', type: 'function', function: { name: 'Bash', arguments: '{"command":"grep -r auth src/"}' } }],
      }]),
      durationMs: 3500, responseId: 'msg_turn2_gen1',
    });

    createBuiltinToolSpan(tracer, chatCtx, 'Bash', 'toolu_003', 450, 'bash', null, 0);
  } finally {
    chatSpan.end();
  }
}

// ── Main ──

async function main() {
  const resource = createAgentResource('aivision-demo', 'aivision-demo', USERNAME, 'default', 'Dev');
  const exporter = buildOtlpExporter();

  const provider = new BasicTracerProvider({ resource });
  provider.addSpanProcessor(new SimpleSpanProcessor(exporter));
  provider.register();

  const tracer = trace.getTracer('aivision-agent-demo', '1.0.0');
  const conversationId = `conv-${shortId()}`;

  console.log('=== AI Agent OTel Demo (Node.js) ===');
  console.log(`conversation.id: ${conversationId}`);
  console.log();

  simulateTurn1(tracer, conversationId);
  simulateTurn2(tracer, conversationId);

  await provider.forceFlush();
  await provider.shutdown();

  console.log();
  console.log('=== Done. All spans exported. ===');
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
