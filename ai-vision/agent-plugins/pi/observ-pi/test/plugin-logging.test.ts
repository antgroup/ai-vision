import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { ExtensionAPI, ExtensionContext } from '@earendil-works/pi-coding-agent';
import observPi from '../src/index.js';

test('plugin diagnostic log records lifecycle and signal counts without payload content', async () => {
  const root = mkdtempSync(join(tmpdir(), 'observ-pi-plugin-log-'));
  const agentDir = join(root, 'agent');
  const logDir = join(agentDir, 'logs');
  mkdirSync(agentDir, { recursive: true });
  writeFileSync(join(agentDir, 'observ-pi.json'), JSON.stringify({
    enableTelemetry: true,
    logDir,
    tracesConfig: { enabled: false },
    metricsConfig: { enabled: false },
    eventsConfig: { enabled: false },
  }));

  const handlers = new Map<string, (raw: unknown, ctx: ExtensionContext) => Promise<void>>();
  const pi = {
    on(name: string, handler: (raw: unknown, ctx: ExtensionContext) => Promise<void>) {
      handlers.set(name, handler);
    },
  } as unknown as ExtensionAPI;
  const ctx = {
    cwd: root,
    isProjectTrusted: () => false,
    sessionManager: { getSessionId: () => 'private-session-id' },
  } as unknown as ExtensionContext;
  const previousAgentDir = process.env.PI_CODING_AGENT_DIR;
  process.env.PI_CODING_AGENT_DIR = agentDir;
  try {
    observPi(pi);
    await handlers.get('session_start')?.({ reason: 'private-reason' }, ctx);
    await handlers.get('provider_stream_event')?.({
      provider: 'nvidia', api: 'openai-completions', model: 'test-model',
      data: { choices: [{ delta: { content: 'private-token-content' } }] },
    }, ctx);
    await handlers.get('provider_stream_event')?.({
      provider: 'nvidia', api: 'openai-completions', model: 'test-model',
      data: { choices: [], usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 } },
    }, ctx);
    await handlers.get('session_shutdown')?.({ reason: 'private-shutdown-reason' }, ctx);
  } finally {
    if (previousAgentDir === undefined) delete process.env.PI_CODING_AGENT_DIR;
    else process.env.PI_CODING_AGENT_DIR = previousAgentDir;
  }

  const log = readFileSync(join(logDir, 'observ-pi.log'), 'utf8');
  assert.match(log, /config loaded .*global_loaded=true .*project_exists=false .*project_trusted=false .*project_loaded=false/);
  assert.match(log, /config resolved telemetry_enabled=true service=pi collection_mode=full protocol=http\/(?:json|protobuf) debug=false/);
  assert.match(log, /telemetry initialized service=pi collection_mode=full debug=false/);
  assert.match(log, /signal configured name=traces enabled=false .*header_count=0 header_names=\[\]/);
  assert.match(log, /hook processed name=session_start sequence=1 .*logs=1 metrics=0/);
  assert.equal((log.match(/hook processed name=provider_stream_event/g) ?? []).length, 1,
    'only usage-bearing provider chunks should enter semantic processing');
  assert.match(log, /hook processed name=provider_stream_event sequence=2/);
  assert.match(log, /hook processed name=session_shutdown sequence=3 .*logs=1 metrics=0/);
  assert.match(log, /telemetry shutdown complete flush_failures=0 shutdown_failures=0/);
  assert.doesNotMatch(log, /private-session-id|private-reason|private-shutdown-reason|private-token-content/);
});

test('debug diagnostics include OTLP endpoints and non-sensitive headers', async () => {
  const root = mkdtempSync(join(tmpdir(), 'observ-pi-debug-log-'));
  const agentDir = join(root, 'agent');
  const logDir = join(agentDir, 'logs');
  mkdirSync(agentDir, { recursive: true });
  writeFileSync(join(agentDir, 'observ-pi.json'), JSON.stringify({
    enableTelemetry: true,
    logDir,
    otelExporter: {
      endpoint: 'https://debug-user:debug-password@example.test/otlp?token=debug-token&region=cn',
      headers: {
        Authorization: 'Bearer debug-secret',
        'x-debug': 'visible',
      },
    },
    tracesConfig: { enabled: false },
    metricsConfig: { enabled: false },
    eventsConfig: { enabled: false },
  }));

  const handlers = new Map<string, (raw: unknown, ctx: ExtensionContext) => Promise<void>>();
  const pi = {
    on(name: string, handler: (raw: unknown, ctx: ExtensionContext) => Promise<void>) {
      handlers.set(name, handler);
    },
  } as unknown as ExtensionAPI;
  const ctx = {
    cwd: root,
    isProjectTrusted: () => false,
    sessionManager: { getSessionId: () => 'private-debug-session' },
  } as unknown as ExtensionContext;
  const previousAgentDir = process.env.PI_CODING_AGENT_DIR;
  const previousDebug = process.env.OBSERV_PI_DEBUG;
  const previousStateDir = process.env.OBSERV_PI_STATE_DIR;
  process.env.PI_CODING_AGENT_DIR = agentDir;
  process.env.OBSERV_PI_DEBUG = 'true';
  process.env.OBSERV_PI_STATE_DIR = join(root, 'state');
  try {
    observPi(pi);
    await handlers.get('session_start')?.({}, ctx);
    await handlers.get('message_update')?.({ assistantMessageEvent: { delta: 'private-stream-content' } }, ctx);
    await handlers.get('before_agent_start')?.({ prompt: 'private prompt' }, ctx);
    await handlers.get('turn_start')?.({ turnIndex: 7 }, ctx);
    await handlers.get('turn_start')?.(null, ctx);
    await handlers.get('session_shutdown')?.({}, ctx);
  } finally {
    if (previousAgentDir === undefined) delete process.env.PI_CODING_AGENT_DIR;
    else process.env.PI_CODING_AGENT_DIR = previousAgentDir;
    if (previousDebug === undefined) delete process.env.OBSERV_PI_DEBUG;
    else process.env.OBSERV_PI_DEBUG = previousDebug;
    if (previousStateDir === undefined) delete process.env.OBSERV_PI_STATE_DIR;
    else process.env.OBSERV_PI_STATE_DIR = previousStateDir;
  }

  const log = readFileSync(join(logDir, 'observ-pi.log'), 'utf8');
  assert.match(log, /telemetry initialized service=pi collection_mode=full debug=true/);
  assert.match(log, /DEBUG otel exporter signal=traces endpoint=https:\/\/example\.test\/otlp\?token=%5BREDACTED%5D&region=cn/);
  assert.match(log, /"Authorization":"\[REDACTED\]"/);
  assert.match(log, /"x-debug":"visible"/);
  assert.match(log, /DEBUG hook ignored name=message_update reason=no_semantic_observation/);
  assert.match(log, /hook processed name=session_shutdown .*pi_turn_index=7/);
  assert.doesNotMatch(log, /debug-user|debug-password|debug-token|debug-secret|private-debug-session|private-stream-content/);
});

test('disabled telemetry stops before normalizer, engine, and exporters are created', async () => {
  const root = mkdtempSync(join(tmpdir(), 'observ-pi-disabled-'));
  const agentDir = join(root, 'agent');
  const logDir = join(agentDir, 'logs');
  mkdirSync(agentDir, { recursive: true });
  writeFileSync(join(agentDir, 'observ-pi.json'), JSON.stringify({
    enableTelemetry: false,
    logDir,
    tracesConfig: { enabled: true, endpoint: 'http://127.0.0.1:1/v1/traces' },
    metricsConfig: { enabled: true, endpoint: 'http://127.0.0.1:1/v1/metrics' },
    eventsConfig: { enabled: true, endpoint: 'http://127.0.0.1:1/v1/logs' },
  }));
  const handlers = new Map<string, (raw: unknown, ctx: ExtensionContext) => Promise<void>>();
  const pi = {
    on(name: string, handler: (raw: unknown, ctx: ExtensionContext) => Promise<void>) {
      handlers.set(name, handler);
    },
  } as unknown as ExtensionAPI;
  const ctx = {
    cwd: root,
    isProjectTrusted: () => false,
    sessionManager: { getSessionId: () => 'disabled-session' },
  } as unknown as ExtensionContext;
  const previousAgentDir = process.env.PI_CODING_AGENT_DIR;
  process.env.PI_CODING_AGENT_DIR = agentDir;
  try {
    observPi(pi);
    await handlers.get('before_agent_start')?.({ prompt: 'must-not-normalize' }, ctx);
    await handlers.get('tool_execution_start')?.({ toolCallId: 'call-1', toolName: 'read' }, ctx);
    await handlers.get('session_shutdown')?.({}, ctx);
  } finally {
    if (previousAgentDir === undefined) delete process.env.PI_CODING_AGENT_DIR;
    else process.env.PI_CODING_AGENT_DIR = previousAgentDir;
  }

  const log = readFileSync(join(logDir, 'observ-pi.log'), 'utf8');
  assert.match(log, /telemetry disabled by configuration/);
  assert.doesNotMatch(log, /telemetry initialized|hook processed|telemetry export/);
});

test('plugin initialization retries after a malformed config is repaired', async () => {
  const root = mkdtempSync(join(tmpdir(), 'observ-pi-recover-config-'));
  const agentDir = join(root, 'agent');
  const logDir = join(agentDir, 'logs');
  const configPath = join(agentDir, 'observ-pi.json');
  mkdirSync(agentDir, { recursive: true });
  writeFileSync(configPath, '{ "enableTelemetry": true, }');

  const handlers = new Map<string, (raw: unknown, ctx: ExtensionContext) => Promise<void>>();
  const pi = {
    on(name: string, handler: (raw: unknown, ctx: ExtensionContext) => Promise<void>) {
      handlers.set(name, handler);
    },
  } as unknown as ExtensionAPI;
  const ctx = {
    cwd: root,
    isProjectTrusted: () => false,
    sessionManager: { getSessionId: () => 'recovered-session' },
  } as unknown as ExtensionContext;
  const previousAgentDir = process.env.PI_CODING_AGENT_DIR;
  const originalWarn = console.warn;
  const warnings: string[] = [];
  process.env.PI_CODING_AGENT_DIR = agentDir;
  console.warn = (...args: unknown[]) => warnings.push(args.map(String).join(' '));
  try {
    observPi(pi);
    await handlers.get('session_start')?.({}, ctx);
    writeFileSync(configPath, JSON.stringify({
      enableTelemetry: true,
      logDir,
      tracesConfig: { enabled: false },
      metricsConfig: { enabled: false },
      eventsConfig: { enabled: false },
    }));
    await handlers.get('session_start')?.({}, ctx);
    await handlers.get('session_shutdown')?.({}, ctx);
  } finally {
    console.warn = originalWarn;
    if (previousAgentDir === undefined) delete process.env.PI_CODING_AGENT_DIR;
    else process.env.PI_CODING_AGENT_DIR = previousAgentDir;
  }

  assert.ok(warnings.some(message => message.includes('invalid config')));
  const log = readFileSync(join(logDir, 'observ-pi.log'), 'utf8');
  assert.match(log, /telemetry initialized/);
  assert.match(log, /hook processed name=session_start sequence=1/);
  assert.match(log, /hook processed name=session_shutdown sequence=2/);
});

test('a rejecting telemetry shutdown is contained by the plugin hook boundary', async () => {
  const root = mkdtempSync(join(tmpdir(), 'observ-pi-shutdown-reject-'));
  const agentDir = join(root, 'agent');
  const logDir = join(agentDir, 'logs');
  mkdirSync(agentDir, { recursive: true });
  writeFileSync(join(agentDir, 'observ-pi.json'), JSON.stringify({
    enableTelemetry: true,
    logDir,
    tracesConfig: { enabled: false },
    metricsConfig: { enabled: false },
    eventsConfig: { enabled: false },
  }));
  const handlers = new Map<string, (raw: unknown, ctx: ExtensionContext) => Promise<void>>();
  const pi = {
    on(name: string, handler: (raw: unknown, ctx: ExtensionContext) => Promise<void>) {
      handlers.set(name, handler);
    },
  } as unknown as ExtensionAPI;
  const ctx = {
    cwd: root,
    isProjectTrusted: () => false,
    sessionManager: { getSessionId: () => 'shutdown-reject-session' },
  } as unknown as ExtensionContext;
  const previousAgentDir = process.env.PI_CODING_AGENT_DIR;
  process.env.PI_CODING_AGENT_DIR = agentDir;
  try {
    observPi(pi, {
      createTelemetry: (() => ({
        consume() {},
        async shutdown() { throw new Error('forced shutdown failure'); },
      })) as never,
    });
    await handlers.get('session_start')?.({}, ctx);
    await assert.doesNotReject(() => handlers.get('session_shutdown')!({}, ctx));
    await handlers.get('session_start')?.({}, ctx);
  } finally {
    if (previousAgentDir === undefined) delete process.env.PI_CODING_AGENT_DIR;
    else process.env.PI_CODING_AGENT_DIR = previousAgentDir;
  }

  const log = readFileSync(join(logDir, 'observ-pi.log'), 'utf8');
  assert.match(log, /WARN telemetry shutdown failed: forced shutdown failure/);
  assert.equal((log.match(/hook processed name=session_start/g) ?? []).length, 1, 'closed plugin must ignore later hooks');
});
