import assert from 'node:assert/strict';
import test from 'node:test';
import { trace, type Context, type SpanOptions } from '@opentelemetry/api';
import { AggregationTemporality, InMemoryMetricExporter, MeterProvider, PeriodicExportingMetricReader } from '@opentelemetry/sdk-metrics';
import { InMemorySpanExporter, SimpleSpanProcessor } from '@opentelemetry/sdk-trace-base';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import { parseConfig } from '../src/config.js';
import { createHooks, type OpenClawApi } from '../src/hooks/index.js';
import { classifyTool } from '../src/semconv.js';
import {
  classifySkill,
  detectSkillCandidate,
  extractLiteralSkillName,
  isAcceptableCatalogSkillPath,
  MAX_SKILL_ENTRIES,
  MAX_SKILL_LOCATION_CHARS,
  MAX_SKILL_NAME_CHARS,
  MAX_SKILL_SECTION_CHARS,
  MAX_SKILL_VERSION_CHARS,
  MAX_SYSTEM_PROMPT_CHARS,
  parseAvailableSkills,
} from '../src/hooks/skill-detection.js';
import { evictIdleSessions, finalizeTurn, getActiveTurn, runRegistry, sessionMap, turnMap } from '../src/hooks/state.js';
import { addToolTraceTag } from '../src/hooks/trace-tags.js';
import type { TelemetryRuntime } from '../src/telemetry.js';

const WEATHER_LOCATION = '/opt/homebrew/lib/node_modules/openclaw/skills/weather/SKILL.md';

function skillSection(entries: string): string {
  return `<available_skills>${entries}</available_skills>`;
}

function skillEntry(name: string, location: string, version = ''): string {
  return `<skill><name>${name}</name><location>${location}</location>${version ? `<version>${version}</version>` : ''}</skill>`;
}

function promptAtLength(length: number): string {
  const section = skillSection('');
  assert.ok(length >= section.length);
  return `${' '.repeat(length - section.length)}${section}`;
}

function skillPathAtLength(length: number): string {
  const suffix = '/SKILL.md';
  return `/${'a'.repeat(length - suffix.length - 1)}${suffix}`;
}

function clearState(): void {
  sessionMap.clear();
  turnMap.clear();
  runRegistry.clear();
}

test('available_skills parser creates a deterministic location catalog', () => {
  const parsed = parseAvailableSkills(skillSection(
    skillEntry('weather', WEATHER_LOCATION, 'sha256:abc')
      + skillEntry('atc', '/Users/test/.codex/skills/atc-cli/SKILL.md', 'v2'),
  ));
  assert.equal(parsed.status, 'valid');
  if (parsed.status !== 'valid') return;
  assert.deepEqual(parsed.entries.get(WEATHER_LOCATION), {
    name: 'weather',
    location: WEATHER_LOCATION,
    version: 'sha256:abc',
  });
  assert.equal(parsed.entries.get('/Users/test/.codex/skills/atc-cli/SKILL.md')?.name, 'atc');
});

test('parser decodes only the five bounded XML text entities', () => {
  const parsed = parseAvailableSkills(skillSection(
    skillEntry('weather', '/opt/openclaw/skills/weather&amp;radar/SKILL.md', 'v&amp;1'),
  ));
  assert.equal(parsed.status, 'valid');
  if (parsed.status === 'valid') {
    assert.deepEqual(parsed.entries.get('/opt/openclaw/skills/weather&radar/SKILL.md'), {
      name: 'weather',
      location: '/opt/openclaw/skills/weather&radar/SKILL.md',
      version: 'v&1',
    });
  }
  assert.equal(parseAvailableSkills(skillSection(
    skillEntry('weather&#38;radar', WEATHER_LOCATION),
  )).status, 'invalid');
});

test('parser fails closed and keeps conflicting locations unavailable', () => {
  const conflict = parseAvailableSkills(skillSection(
    skillEntry('weather', WEATHER_LOCATION, 'v1')
      + skillEntry('forecast', WEATHER_LOCATION, 'v2')
      + skillEntry('safe', '/opt/openclaw/skills/safe/SKILL.md'),
  ));
  assert.equal(conflict.status, 'valid');
  if (conflict.status === 'valid') {
    assert.equal(conflict.entries.has(WEATHER_LOCATION), false);
    assert.equal(conflict.entries.get('/opt/openclaw/skills/safe/SKILL.md')?.name, 'safe');
  }

  assert.equal(parseAvailableSkills('no catalog').status, 'absent');
  assert.equal(parseAvailableSkills('<available_skills>').status, 'invalid');
  assert.equal(parseAvailableSkills(skillSection(
    skillEntry('x'.repeat(MAX_SKILL_NAME_CHARS + 1), '/opt/openclaw/skills/x/SKILL.md'),
  )).status, 'invalid');
  assert.equal(parseAvailableSkills(
    skillSection(skillEntry('safe', '/opt/openclaw/skills/safe/../evil/SKILL.md')),
  ).status, 'invalid');
  assert.equal(parseAvailableSkills(
    `${skillSection('')}${skillSection('')}`,
  ).status, 'invalid');
});

test('parser enforces every fixed max-1/max/max+1 boundary', () => {
  for (const length of [MAX_SYSTEM_PROMPT_CHARS - 1, MAX_SYSTEM_PROMPT_CHARS]) {
    assert.equal(parseAvailableSkills(promptAtLength(length)).status, 'valid');
  }
  assert.equal(parseAvailableSkills(promptAtLength(MAX_SYSTEM_PROMPT_CHARS + 1)).status, 'invalid');

  for (const length of [MAX_SKILL_SECTION_CHARS - 1, MAX_SKILL_SECTION_CHARS]) {
    assert.equal(parseAvailableSkills(skillSection(' '.repeat(length))).status, 'valid');
  }
  assert.equal(parseAvailableSkills(skillSection(' '.repeat(MAX_SKILL_SECTION_CHARS + 1))).status, 'invalid');

  const entries = Array.from({ length: MAX_SKILL_ENTRIES + 1 }, (_, index) =>
    skillEntry(`s${index}`, `/opt/skills/s${index}/SKILL.md`));
  assert.equal(parseAvailableSkills(skillSection(entries.slice(0, MAX_SKILL_ENTRIES - 1).join(''))).status, 'valid');
  assert.equal(parseAvailableSkills(skillSection(entries.slice(0, MAX_SKILL_ENTRIES).join(''))).status, 'valid');
  assert.equal(parseAvailableSkills(skillSection(entries.join(''))).status, 'invalid');

  for (const length of [MAX_SKILL_NAME_CHARS - 1, MAX_SKILL_NAME_CHARS]) {
    assert.equal(parseAvailableSkills(skillSection(skillEntry('a'.repeat(length), WEATHER_LOCATION))).status, 'valid');
  }
  assert.equal(parseAvailableSkills(skillSection(skillEntry('a'.repeat(MAX_SKILL_NAME_CHARS + 1), WEATHER_LOCATION))).status, 'invalid');

  for (const length of [MAX_SKILL_LOCATION_CHARS - 1, MAX_SKILL_LOCATION_CHARS]) {
    assert.equal(parseAvailableSkills(skillSection(skillEntry('weather', skillPathAtLength(length)))).status, 'valid');
  }
  assert.equal(parseAvailableSkills(skillSection(skillEntry('weather', skillPathAtLength(MAX_SKILL_LOCATION_CHARS + 1)))).status, 'invalid');

  for (const length of [MAX_SKILL_VERSION_CHARS - 1, MAX_SKILL_VERSION_CHARS]) {
    assert.equal(parseAvailableSkills(skillSection(skillEntry('weather', WEATHER_LOCATION, 'v'.repeat(length)))).status, 'valid');
  }
  assert.equal(parseAvailableSkills(skillSection(skillEntry('weather', WEATHER_LOCATION, 'v'.repeat(MAX_SKILL_VERSION_CHARS + 1)))).status, 'invalid');
});

test('parser rejects malformed field cardinality and deduplicates identical entries', () => {
  for (const body of [
    '<skill><name>x</name></skill>',
    `<skill><name>x</name><name>y</name><location>${WEATHER_LOCATION}</location></skill>`,
    `<skill><name>x</name><location>${WEATHER_LOCATION}</location><location>${WEATHER_LOCATION}</location></skill>`,
    `<skill><name>x</name><location>${WEATHER_LOCATION}</location><version>1</version><version>2</version></skill>`,
    `<skill><name><name>x</name></name><location>${WEATHER_LOCATION}</location></skill>`,
    skillEntry('bad name', WEATHER_LOCATION),
    skillEntry('weather', WEATHER_LOCATION, 'bad\u0000version'),
  ]) {
    assert.equal(parseAvailableSkills(skillSection(body)).status, 'invalid');
  }
  const duplicate = skillEntry('weather', WEATHER_LOCATION, 'v1');
  const parsed = parseAvailableSkills(skillSection(duplicate + duplicate));
  assert.equal(parsed.status, 'valid');
  if (parsed.status === 'valid') assert.equal(parsed.entries.size, 1);
});

test('candidate detection gives the session catalog authority over strict fallback', () => {
  const entry = { name: 'weather', location: WEATHER_LOCATION, version: 'v1' };
  const catalog = new Map([[WEATHER_LOCATION, entry]]);
  assert.deepEqual(detectSkillCandidate('read', { path: WEATHER_LOCATION }, catalog), {
    entry,
    source: 'catalog',
  });
  assert.equal(
    detectSkillCandidate('read', { path: '/opt/openclaw/skills/other/SKILL.md' }, catalog),
    undefined,
  );
  assert.equal(
    detectSkillCandidate('read', { path: '/opt/openclaw/skills/other/SKILL.md' }, new Map()),
    undefined,
  );
  assert.equal(
    detectSkillCandidate('read', { path: '/opt/openclaw/skills/other/SKILL.md' }, undefined)?.entry.name,
    'other',
  );
  assert.equal(
    detectSkillCandidate('read', { path: '~/openclaw/skills/other/SKILL.md' }, undefined),
    undefined,
  );
  assert.equal(
    detectSkillCandidate('read', { path: '/opt/openclaw/skills/a/b/SKILL.md' }, undefined),
    undefined,
  );
  assert.equal(detectSkillCandidate('exec', { path: WEATHER_LOCATION }, catalog), undefined);
  for (const path of [
    '~/openclaw/skills/weather/SKILL.md',
    'openclaw/skills/weather/SKILL.md',
    '/opt/openclaw/skills/weather/skill.md',
    '/opt/openclaw/skills/weather/SKILL.md.bak',
    '/opt/openclaw/skills/weather%2fSKILL.md',
    '/opt/openclaw/skills/../weather/SKILL.md',
    '/opt/openclaw//skills/weather/SKILL.md',
    '/opt/openclaw/skills/a/b/SKILL.md',
    `/opt/openclaw/skills/${'a'.repeat(129)}/SKILL.md`,
  ]) {
    assert.equal(detectSkillCandidate('read', { path }, undefined), undefined, path);
  }
});

test('path validation is host-independent and rejects traversal-shaped inputs', () => {
  assert.equal(isAcceptableCatalogSkillPath(WEATHER_LOCATION), true);
  assert.equal(isAcceptableCatalogSkillPath('~/.codex/skills/weather/SKILL.md'), true);
  for (const value of [
    'skills/weather/SKILL.md',
    '/opt//skills/weather/SKILL.md',
    '/opt/skills/../weather/SKILL.md',
    '/opt/skills/weather/skill.md',
    '/opt/skills/weather/SKILL.md.bak',
    '/opt/skills/weather%2fSKILL.md',
    'file:///opt/skills/weather/SKILL.md',
    'C:\\skills\\weather\\SKILL.md',
  ]) {
    assert.equal(isAcceptableCatalogSkillPath(value), false, value);
  }
});

test('raw-identical safe tilde paths match without host expansion', () => {
  const location = '~/.openclaw/skills/weather/SKILL.md';
  const entry = { name: 'weather', location };
  assert.deepEqual(detectSkillCandidate('read', { path: location }, new Map([[location, entry]])), {
    entry,
    source: 'catalog',
  });
  assert.equal(detectSkillCandidate('read', { path: WEATHER_LOCATION }, new Map([[location, entry]])), undefined);
});

test('Skill identity follows the cchooks real-id-first contract', () => {
  const entry = { name: 'weather', location: WEATHER_LOCATION };
  assert.deepEqual(classifySkill(entry, 'read', 'call-weather'), {
    'agentic.tool.type': 'skill',
    'agentic.tool.name': 'weather',
    'agentic.tool.raw_name': 'read',
    'agentic.tool.id': 'call-weather',
  });
  assert.equal(classifySkill(entry, 'read')['agentic.tool.id'], 'skill-weather');
  assert.equal(extractLiteralSkillName({ skill: 'medeval:medeval:init-plugin' }), 'medeval:init-plugin');
  assert.equal(extractLiteralSkillName({ skill: 'weather' }), 'weather');
  assert.deepEqual(classifyTool('Skill', 'call-native', 'medeval:init-plugin'), {
    'agentic.tool.type': 'skill',
    'agentic.tool.name': 'medeval:init-plugin',
    'agentic.tool.raw_name': 'Skill',
    'agentic.tool.id': 'call-native',
  });
});

test('trace tags keep canonical first-seen values and enforce the full-tag length limit', () => {
  const turn = { traceTags: [] as string[] };
  addToolTraceTag(turn, classifyTool('read'));
  addToolTraceTag(turn, classifyTool('Skill', 'call-skill', 'weather'));
  addToolTraceTag(turn, classifyTool('Skill', 'call-skill-duplicate', 'weather'));
  addToolTraceTag(turn, classifyTool('mcp__weather__forecast'));
  assert.deepEqual(turn.traceTags, ['skill:weather', 'mcp:weather.forecast']);

  for (const fullLength of [199, 200, 201]) {
    const boundaryTurn = { traceTags: [] as string[] };
    addToolTraceTag(
      boundaryTurn,
      classifyTool('Skill', `call-${fullLength}`, 'x'.repeat(fullLength - 'skill:'.length)),
    );
    assert.equal(boundaryTurn.traceTags.length, fullLength <= 200 ? 1 : 0);
    if (boundaryTurn.traceTags[0]) assert.equal(boundaryTurn.traceTags[0].length, fullLength);
  }
});

test('session catalog is atomic, cross-turn, isolated, and resettable', () => {
  clearState();
  const handlers = new Map<string, (event: unknown, ctx: unknown) => unknown>();
  let commandHandler: ((event: unknown) => void | Promise<void>) | undefined;
  const api: OpenClawApi = {
    on(name, handler) { handlers.set(name, handler); },
    registerHook(_events, handler) { commandHandler = handler; },
    logger: {},
  };
  createHooks(api, null, parseConfig({ enableTelemetry: false }));

  const startTurn = (sessionKey: string) => handlers.get('before_model_resolve')?.(
    { sessionKey, prompt: 'test' },
    { sessionKey, sessionId: `${sessionKey}-id` },
  );
  const input = (sessionKey: string, systemPrompt: string) => handlers.get('llm_input')?.(
    { runId: `${sessionKey}-${Math.random()}`, sessionId: `${sessionKey}-id`, model: 'test', systemPrompt },
    { sessionKey },
  );

  startTurn('session-a');
  input('session-a', skillSection(skillEntry('weather', WEATHER_LOCATION, 'v1')));
  const firstCatalog = sessionMap.get('session-a')?.skillCatalogByLocation;
  assert.equal(firstCatalog?.get(WEATHER_LOCATION)?.version, 'v1');
  input('session-a', skillSection(skillEntry('weather', WEATHER_LOCATION, 'v1')));
  const stableCatalog = sessionMap.get('session-a')?.skillCatalogByLocation;
  assert.equal(stableCatalog?.size, 1);
  input('session-a', '<available_skills>');
  assert.equal(sessionMap.get('session-a')?.skillCatalogByLocation, stableCatalog);
  input('session-a', 'catalog absent');
  assert.equal(sessionMap.get('session-a')?.skillCatalogByLocation, stableCatalog);
  input('session-a', 'x'.repeat(MAX_SYSTEM_PROMPT_CHARS + 1));
  assert.equal(sessionMap.get('session-a')?.skillCatalogByLocation, stableCatalog);

  const firstTurn = getActiveTurn('session-a');
  assert.ok(firstTurn);
  finalizeTurn(firstTurn, api.logger);
  startTurn('session-a');
  assert.equal(sessionMap.get('session-a')?.skillCatalogByLocation, stableCatalog);

  startTurn('session-b');
  input('session-b', skillSection(skillEntry('forecast', WEATHER_LOCATION, 'v-b')));
  assert.equal(sessionMap.get('session-b')?.skillCatalogByLocation?.get(WEATHER_LOCATION)?.name, 'forecast');
  assert.equal(sessionMap.get('session-a')?.skillCatalogByLocation?.get(WEATHER_LOCATION)?.name, 'weather');

  const atcLocation = '/opt/openclaw/skills/atc-cli/SKILL.md';
  input('session-a', skillSection(skillEntry('renamed-weather', WEATHER_LOCATION, 'v2')));
  assert.deepEqual(sessionMap.get('session-a')?.skillCatalogByLocation?.get(WEATHER_LOCATION), {
    name: 'renamed-weather',
    location: WEATHER_LOCATION,
    version: 'v2',
  });
  input('session-a', skillSection(skillEntry('atc', atcLocation)));
  assert.equal(sessionMap.get('session-a')?.skillCatalogByLocation?.has(WEATHER_LOCATION), false);
  assert.equal(sessionMap.get('session-a')?.skillCatalogByLocation?.get(atcLocation)?.name, 'atc');
  input('session-a', skillSection(''));
  assert.equal(sessionMap.get('session-a')?.skillCatalogByLocation?.size, 0);
  assert.equal(detectSkillCandidate('read', { path: WEATHER_LOCATION }, sessionMap.get('session-a')?.skillCatalogByLocation), undefined);

  handlers.get('before_reset')?.({ sessionKey: 'session-a' }, { sessionKey: 'session-a' });
  assert.equal(sessionMap.get('session-a')?.skillCatalogByLocation, undefined);
  input('session-a', skillSection(skillEntry('weather', WEATHER_LOCATION, 'v2')));
  void commandHandler?.({ action: 'new', sessionKey: 'session-a' });
  assert.equal(sessionMap.get('session-a')?.skillCatalogByLocation, undefined);
  void commandHandler?.({ action: 'reset', sessionKey: 'session-a' });
  void commandHandler?.({ action: 'reset', sessionKey: 'unknown-session' });
  handlers.get('session_end')?.({ sessionKey: 'session-b' }, { sessionKey: 'session-b' });
  assert.equal(sessionMap.has('session-b'), false);
  clearState();
});

test('command:new clears the session catalog without exporting a span', async () => {
  clearState();
  sessionMap.set('session-new', {
    sessionKey: 'session-new',
    activeTurnId: '',
    turnCounter: 1,
    lastActiveAt: Date.now(),
    skillCatalogByLocation: new Map([
      [WEATHER_LOCATION, { name: 'weather', location: WEATHER_LOCATION }],
    ]),
  });

  const spanExporter = new InMemorySpanExporter();
  const traceProvider = new NodeTracerProvider({
    spanProcessors: [new SimpleSpanProcessor(spanExporter)],
  });
  let commandHandler: ((event: unknown) => void | Promise<void>) | undefined;
  const api: OpenClawApi = {
    on() {},
    registerHook(_events, handler) { commandHandler = handler; },
    logger: {},
  };
  const runtime = {
    tracer: traceProvider.getTracer('command-new-test'),
    metricsEnabled: false,
    eventsEnabled: false,
    resolvedUsername: 'command-new-test',
    logger: { emit() {} },
  } as unknown as TelemetryRuntime;

  createHooks(api, runtime, parseConfig({ enableTelemetry: true }));
  await commandHandler?.({ action: 'new', sessionKey: 'session-new' });
  await traceProvider.forceFlush();

  assert.equal(sessionMap.get('session-new')?.skillCatalogByLocation, undefined);
  assert.deepEqual(spanExporter.getFinishedSpans(), []);

  await traceProvider.shutdown();
  clearState();
});

test('idle-session eviction removes the session-scoped Skill catalog', () => {
  clearState();
  sessionMap.set('idle-session', {
    sessionKey: 'idle-session',
    activeTurnId: '',
    turnCounter: 1,
    lastActiveAt: 1,
    skillCatalogByLocation: new Map([[WEATHER_LOCATION, { name: 'weather', location: WEATHER_LOCATION }]]),
  });
  assert.deepEqual(evictIdleSessions(10_001, 10_000, {}), ['idle-session']);
  assert.equal(sessionMap.has('idle-session'), false);
  clearState();
});

async function runSkillLifecycle(options: {
  sessionKey: string;
  systemPrompt: string;
  afterError?: string;
  persistError?: boolean;
  skipBefore?: boolean;
  toolName?: string;
  toolCallId?: string;
  toolParams?: Record<string, unknown>;
  refreshedSystemPrompt?: string;
  omitToolCallId?: boolean;
  persistToolName?: string;
  addDownstreamWebFetch?: boolean;
  collectionMode?: 'full' | 'privacy';
  turnStartLeadMs?: number;
  failDerivedSkillSpan?: boolean;
}) {
  clearState();
  const spanExporter = new InMemorySpanExporter();
  const traceProvider = new NodeTracerProvider({
    spanProcessors: [new SimpleSpanProcessor(spanExporter)],
  });
  const metricExporter = new InMemoryMetricExporter(AggregationTemporality.DELTA);
  const metricReader = new PeriodicExportingMetricReader({
    exporter: metricExporter,
    exportIntervalMillis: 60_000,
  });
  const meterProvider = new MeterProvider({ readers: [metricReader] });
  const handlers = new Map<string, (event: unknown, ctx: unknown) => unknown>();
  const events: Array<Record<string, any>> = [];
  const api: OpenClawApi = {
    on(name, handler) { handlers.set(name, handler); },
    registerHook() {},
    logger: {},
  };
  const config = parseConfig({
    enableTelemetry: true,
    collectionMode: options.collectionMode || 'privacy',
    otelExporter: { attributes: { 'ant.username': 'skill-test' } },
  });
  const runtime: TelemetryRuntime = {
    tracer: options.failDerivedSkillSpan
      ? {
          startSpan(name: string, spanOptions?: SpanOptions, parentContext?: Context) {
            if (name.startsWith('skill ')) throw new Error('forced derived Skill span failure');
            return traceProvider.getTracer('skill-detection-test').startSpan(name, spanOptions, parentContext);
          },
        } as TelemetryRuntime['tracer']
      : traceProvider.getTracer('skill-detection-test'),
    meter: meterProvider.getMeter('skill-detection-test'),
    logger: { emit(record: Record<string, unknown>) { events.push(record); } } as unknown as TelemetryRuntime['logger'],
    tracesEnabled: true,
    metricsEnabled: true,
    eventsEnabled: true,
    resolvedUsername: 'skill-test',
    forceFlush: async () => {
      await traceProvider.forceFlush();
      await meterProvider.forceFlush();
    },
    shutdown: async () => {
      await traceProvider.shutdown();
      await meterProvider.shutdown();
    },
  };
  createHooks(api, runtime, config);
  const toolName = options.toolName || 'read';
  const toolCallId = options.toolCallId || 'call-weather';
  const toolParams = options.toolParams || { path: WEATHER_LOCATION };
  const toolIdFields = options.omitToolCallId ? {} : { toolCallId };

  handlers.get('before_model_resolve')?.(
    { sessionKey: options.sessionKey, prompt: 'check weather' },
    { sessionKey: options.sessionKey, sessionId: `${options.sessionKey}-id` },
  );
  const activeTurn = getActiveTurn(options.sessionKey);
  if (activeTurn && options.turnStartLeadMs) {
    activeTurn.createdAt += options.turnStartLeadMs;
  }
  handlers.get('llm_input')?.(
    { runId: `${options.sessionKey}-run`, sessionId: `${options.sessionKey}-id`, model: 'test', systemPrompt: options.systemPrompt },
    { sessionKey: options.sessionKey },
  );
  if (!options.skipBefore) {
    handlers.get('before_tool_call')?.(
      { toolName, ...toolIdFields, params: toolParams },
      { sessionKey: options.sessionKey, ...toolIdFields },
    );
  }
  if (options.refreshedSystemPrompt) {
    handlers.get('llm_input')?.(
      {
        runId: `${options.sessionKey}-refresh-run`,
        sessionId: `${options.sessionKey}-id`,
        model: 'test',
        systemPrompt: options.refreshedSystemPrompt,
      },
      { sessionKey: options.sessionKey },
    );
  }
  handlers.get('after_tool_call')?.(
    {
      toolName,
      ...toolIdFields,
      params: toolParams,
      durationMs: 12,
      ...(options.afterError ? { error: options.afterError } : { result: { details: { status: 'ok' } } }),
    },
    { sessionKey: options.sessionKey, ...toolIdFields },
  );
  handlers.get('tool_result_persist')?.(
    {
      toolName: options.persistToolName || toolName,
      ...toolIdFields,
      message: {
        content: [{ type: 'text', text: 'skill content' }],
        details: options.persistError ? { status: 'error', error: 'read failed' } : { status: 'ok' },
      },
    },
    { sessionKey: options.sessionKey },
  );
  if (options.addDownstreamWebFetch) {
    handlers.get('before_tool_call')?.(
      { toolName: 'web_fetch', toolCallId: 'call-web', params: { url: 'https://example.invalid' } },
      { sessionKey: options.sessionKey, toolCallId: 'call-web' },
    );
    handlers.get('after_tool_call')?.(
      { toolName: 'web_fetch', toolCallId: 'call-web', params: { url: 'https://example.invalid' }, durationMs: 3, result: { details: { status: 'ok' } } },
      { sessionKey: options.sessionKey },
    );
    handlers.get('tool_result_persist')?.(
      { toolName: 'web_fetch', toolCallId: 'call-web', message: { content: [{ type: 'text', text: 'ok' }], details: { status: 'ok' } } },
      { sessionKey: options.sessionKey },
    );
  }
  await runtime.forceFlush();
  return { handlers, events, spanExporter, metricExporter, runtime };
}

async function finalizeLifecycleTurn(
  result: Awaited<ReturnType<typeof runSkillLifecycle>>,
  sessionKey: string,
) {
  const turn = getActiveTurn(sessionKey);
  assert.ok(turn);
  finalizeTurn(turn, {});
  await result.runtime.forceFlush();
  const turnSpan = result.spanExporter.getFinishedSpans().find((span) =>
    span.attributes['gen_ai.span.kind'] === 'CHAT' && span.name.startsWith('Turn '));
  assert.ok(turnSpan);
  return turnSpan;
}

function emitAdditionalTool(
  handlers: Map<string, (event: unknown, ctx: unknown) => unknown>,
  sessionKey: string,
  toolName: string,
  toolCallId: string,
  params: Record<string, unknown>,
): void {
  handlers.get('before_tool_call')?.(
    { toolName, toolCallId, params },
    { sessionKey, toolCallId },
  );
  handlers.get('after_tool_call')?.(
    { toolName, toolCallId, params, durationMs: 1, result: { details: { status: 'ok' } } },
    { sessionKey, toolCallId },
  );
  handlers.get('tool_result_persist')?.(
    { toolName, toolCallId, message: { content: [{ type: 'text', text: 'ok' }], details: { status: 'ok' } } },
    { sessionKey, toolCallId },
  );
}

function hrTimeToNanoseconds(value: [number, number]): bigint {
  return BigInt(value[0]) * 1_000_000_000n + BigInt(value[1]);
}

test('multi-turn prompt and tool events retain 1-based turn numbers as event sequence advances', async () => {
  const sessionKey = 'turn-number-lifecycle';
  const result = await runSkillLifecycle({
    sessionKey,
    systemPrompt: '',
    toolName: 'exec',
    toolCallId: 'call-turn-1',
    toolParams: { command: 'true' },
  });

  const firstTurn = getActiveTurn(sessionKey);
  assert.ok(firstTurn);
  assert.equal(firstTurn.turnNumber, 1);
  finalizeTurn(firstTurn, {});

  result.handlers.get('before_model_resolve')?.(
    { sessionKey, prompt: 'second turn' },
    { sessionKey, sessionId: `${sessionKey}-id` },
  );
  const secondTurn = getActiveTurn(sessionKey);
  assert.ok(secondTurn);
  assert.equal(secondTurn.turnNumber, 2);
  emitAdditionalTool(
    result.handlers,
    sessionKey,
    'exec',
    'call-turn-2',
    { command: 'true' },
  );

  const lifecycleEvents = result.events.filter((record) =>
    ['user_prompt', 'tool_call', 'tool_result'].includes(record.attributes?.['event.name']),
  );
  assert.deepEqual(
    lifecycleEvents.map((record) => record.attributes?.['event.name']),
    ['user_prompt', 'tool_call', 'tool_result', 'user_prompt', 'tool_call', 'tool_result'],
  );
  assert.deepEqual(
    lifecycleEvents.map((record) => record.attributes?.['agentic.turn.number']),
    [1, 1, 1, 2, 2, 2],
  );

  const sequences = lifecycleEvents.map((record) => Number(record.attributes?.['event.sequence']));
  assert.ok(sequences.every((sequence, index) => index === 0 || sequence > sequences[index - 1]));
  assert.ok(sequences[3] > 2, 'second-turn event sequence must have advanced beyond the turn number');

  finalizeTurn(secondTurn, {});
  await result.runtime.shutdown();
  clearState();
});

test('successful exact read stays builtin and is followed by one zero-duration Skill span', async () => {
  const result = await runSkillLifecycle({
    sessionKey: 'skill-success',
    systemPrompt: skillSection(skillEntry('weather', WEATHER_LOCATION, 'secret-version-sentinel')),
    turnStartLeadMs: 100,
  });
  const toolSpans = result.spanExporter.getFinishedSpans().filter((span) => span.attributes['gen_ai.span.kind'] === 'TOOL');
  assert.deepEqual(toolSpans.map((span) => span.name), ['builtin read', 'skill weather']);
  const [readSpan, skillSpan] = toolSpans;
  assert.equal(readSpan.attributes['gen_ai.tool.name'], 'read');
  assert.equal(readSpan.attributes['gen_ai.tool.call.id'], 'call-weather');
  assert.equal(readSpan.attributes['agentic.tool.type'], 'builtin');
  assert.equal(readSpan.attributes['agentic.tool.duration_ms'], 12);
  assert.equal(readSpan.attributes['agentic.tool.derived'], undefined);
  assert.equal(skillSpan.attributes['gen_ai.tool.name'], 'Skill');
  assert.equal(skillSpan.attributes['gen_ai.tool.call.id'], 'skill-load-call-weather');
  assert.equal(skillSpan.attributes['gen_ai.tool.call.arguments'], '[REDACTED]');
  assert.equal(skillSpan.attributes['gen_ai.tool.call.result'], '[REDACTED]');
  assert.equal(skillSpan.attributes['agentic.tool.type'], 'skill');
  assert.equal(skillSpan.attributes['agentic.tool.name'], 'weather');
  assert.equal(skillSpan.attributes['agentic.tool.raw_name'], 'read');
  assert.equal(skillSpan.attributes['agentic.tool.id'], 'call-weather');
  assert.equal(skillSpan.attributes['agentic.tool.duration_ms'], 0);
  assert.equal(skillSpan.attributes['agentic.tool.derived'], true);
  assert.equal(skillSpan.attributes['agentic.tool.call_id.synthetic'], true);
  for (const key of [
    'ant.username',
    'user.id',
    'gen_ai.conversation.id',
    'gen_ai.chat.id',
  ]) {
    assert.equal(skillSpan.attributes[key], readSpan.attributes[key]);
  }
  assert.equal(hrTimeToNanoseconds(skillSpan.startTime), hrTimeToNanoseconds(skillSpan.endTime));
  assert.ok(hrTimeToNanoseconds(skillSpan.startTime) >= hrTimeToNanoseconds(readSpan.endTime));
  const turn = getActiveTurn('skill-success');
  assert.ok(turn, 'Skill span must retain its parent Turn while active');
  assert.equal(
    hrTimeToNanoseconds(skillSpan.startTime),
    BigInt(turn.createdAt + 1) * 1_000_000n,
    'Skill span must be clamped one millisecond after its parent Turn',
  );
  assert.equal(skillSpan.parentSpanContext?.spanId, readSpan.parentSpanContext?.spanId);
  assert.equal(skillSpan.links.length, 1);
  assert.equal(skillSpan.links[0].context.spanId, readSpan.spanContext().spanId);
  assert.equal(JSON.stringify(skillSpan.attributes).includes('secret-version-sentinel'), false);

  const event = result.events.find((record) => record.attributes?.['event.name'] === 'tool_result');
  const toolCallEvent = result.events.find((record) => record.attributes?.['event.name'] === 'tool_call');
  assert.equal(toolCallEvent?.attributes?.['gen_ai.tool.name'], 'read');
  assert.equal(toolCallEvent?.attributes?.['gen_ai.tool.call.id'], 'call-weather');
  assert.equal(toolCallEvent?.attributes?.['agentic.tool.type'], 'builtin');
  assert.equal(toolCallEvent?.attributes?.['gen_ai.tool.call.arguments'], '[REDACTED]');
  assert.equal(toolCallEvent?.attributes?.duration_ms, undefined);
  assert.equal(toolCallEvent?.attributes?.['gen_ai.tool.call.result'], undefined);
  assert.equal(
    toolCallEvent?.attributes?.['agentic.turn.number'],
    event?.attributes?.['agentic.turn.number'],
    'tool_call and tool_result must retain the same turn number',
  );
  assert.equal(
    trace.getSpanContext(toolCallEvent?.context)?.spanId,
    trace.getSpanContext(event?.context)?.spanId,
  );
  assert.equal(event?.attributes?.['gen_ai.tool.name'], 'read');
  assert.equal(event?.attributes?.['agentic.tool.type'], 'builtin');
  assert.equal(result.events.filter((record) => record.attributes?.['event.name'] === 'tool_result').length, 1);
  assert.equal(sessionMap.get('skill-success')?.skillCatalogByLocation?.get(WEATHER_LOCATION)?.name, 'weather');

  const metrics = result.metricExporter.getMetrics().flatMap((resource) =>
    resource.scopeMetrics.flatMap((scope) => scope.metrics),
  );
  const metricTypes = metrics.flatMap((metric) =>
    metric.dataPointType === 0 ? [] : metric.dataPoints.map((point) => point.attributes['agentic.tool.type']),
  );
  assert.ok(metricTypes.includes('builtin'));
  assert.equal(metricTypes.includes('skill'), false);
  const countMetric = metrics.find((metric) => metric.descriptor.name === 'agent.tool_call.count');
  assert.equal(countMetric?.dataPoints.length, 1);
  assert.equal(countMetric?.dataPoints[0].value, 1);
  assert.equal(JSON.stringify(result.events).includes('secret-version-sentinel'), false);
  assert.equal(JSON.stringify(result.metricExporter.getMetrics()).includes('secret-version-sentinel'), false);

  const turnSpan = await finalizeLifecycleTurn(result, 'skill-success');
  assert.deepEqual(turnSpan.attributes['langfuse.trace.tags'], ['skill:weather']);

  result.handlers.get('before_reset')?.({ sessionKey: 'skill-success' }, { sessionKey: 'skill-success' });
  assert.equal(sessionMap.get('skill-success')?.skillCatalogByLocation, undefined);
  await result.runtime.shutdown();
  clearState();
});

test('successful ID-less lifecycle emits a separate Skill span with the cchooks synthetic id', async () => {
  const result = await runSkillLifecycle({
    sessionKey: 'skill-no-id',
    systemPrompt: skillSection(skillEntry('weather', WEATHER_LOCATION)),
    omitToolCallId: true,
  });
  const toolSpans = result.spanExporter.getFinishedSpans().filter((span) => span.attributes['gen_ai.span.kind'] === 'TOOL');
  assert.deepEqual(toolSpans.map((span) => span.name), ['builtin read', 'skill weather']);
  const skillSpan = toolSpans[1];
  assert.equal(skillSpan.attributes['agentic.tool.id'], 'skill-weather');
  assert.match(String(skillSpan.attributes['gen_ai.tool.call.id']), /^skill-load-auto-/);
  const event = result.events.find((record) => record.attributes?.['event.name'] === 'tool_result');
  assert.equal(event?.attributes?.['agentic.tool.type'], 'builtin');
  assert.match(String(event?.attributes?.['gen_ai.tool.call.id']), /^auto-/);
  await result.runtime.shutdown();
  clearState();
});

test('full collection mode exports semantic input and output for the derived Skill span', async () => {
  const result = await runSkillLifecycle({
    sessionKey: 'skill-full-content',
    systemPrompt: skillSection(skillEntry('weather', WEATHER_LOCATION)),
    collectionMode: 'full',
  });
  const skillSpan = result.spanExporter.getFinishedSpans().find((span) => span.name === 'skill weather');
  assert.equal(skillSpan?.attributes['gen_ai.tool.call.arguments'], '{"skill":"weather"}');
  assert.equal(skillSpan?.attributes['gen_ai.tool.call.result'], '{"status":"loaded"}');
  await result.runtime.shutdown();
  clearState();
});

test('tool_result uses the frozen ToolRun raw name and call id', async () => {
  const result = await runSkillLifecycle({
    sessionKey: 'skill-frozen-event-identity',
    systemPrompt: skillSection(skillEntry('weather', WEATHER_LOCATION)),
    persistToolName: 'inconsistent-name',
  });
  const event = result.events.find((record) => record.attributes?.['event.name'] === 'tool_result');
  assert.equal(event?.attributes?.['gen_ai.tool.name'], 'read');
  assert.equal(event?.attributes?.['gen_ai.tool.call.id'], 'call-weather');
  await result.runtime.shutdown();
  clearState();
});

test('any failed phase keeps an inferred read builtin and emits no derived Skill span', async () => {
  const result = await runSkillLifecycle({
    sessionKey: 'skill-error',
    systemPrompt: skillSection(skillEntry('weather', WEATHER_LOCATION)),
    afterError: 'after failed',
  });
  const toolSpans = result.spanExporter.getFinishedSpans().filter((span) => span.attributes['gen_ai.span.kind'] === 'TOOL');
  assert.equal(toolSpans.length, 1);
  const span = toolSpans[0];
  assert.equal(span.name, 'builtin read');
  assert.equal(span.attributes['agentic.tool.type'], 'builtin');
  assert.equal(span.status.code, 2);
  const event = result.events.find((record) => record.attributes?.['event.name'] === 'tool_result');
  assert.equal(event?.attributes?.['agentic.tool.type'], 'builtin');
  assert.equal(event?.attributes?.['error.type'], 'tool_error');
  const turnSpan = await finalizeLifecycleTurn(result, 'skill-error');
  assert.equal(turnSpan.attributes['langfuse.trace.tags'], undefined);
  await result.runtime.shutdown();
  clearState();
});

test('persist-phase failure keeps inferred read builtin across span event and error metrics', async () => {
  const result = await runSkillLifecycle({
    sessionKey: 'skill-persist-error',
    systemPrompt: skillSection(skillEntry('weather', WEATHER_LOCATION)),
    persistError: true,
  });
  const toolSpans = result.spanExporter.getFinishedSpans().filter((span) => span.attributes['gen_ai.span.kind'] === 'TOOL');
  assert.equal(toolSpans.length, 1);
  const span = toolSpans[0];
  assert.equal(span.attributes['agentic.tool.type'], 'builtin');
  const event = result.events.find((record) => record.attributes?.['event.name'] === 'tool_result');
  assert.equal(event?.attributes?.['agentic.tool.type'], 'builtin');
  const errorTypes = result.metricExporter.getMetrics().flatMap((resource) =>
    resource.scopeMetrics.flatMap((scope) => scope.metrics.flatMap((metric) =>
      metric.descriptor.name === 'agent.tool_call.error.count'
        ? metric.dataPoints.map((point) => point.attributes['agentic.tool.type'])
        : [],
    )),
  );
  assert.deepEqual(errorTypes, ['builtin']);
  await result.runtime.shutdown();
  clearState();
});

test('after_tool_call params support lazy Skill detection when before hook is missing', async () => {
  const result = await runSkillLifecycle({
    sessionKey: 'skill-lazy',
    systemPrompt: skillSection(skillEntry('weather', WEATHER_LOCATION)),
    skipBefore: true,
    collectionMode: 'full',
  });
  const physicalSpan = result.spanExporter.getFinishedSpans().find((candidate) => candidate.name === 'builtin read');
  assert.equal(
    physicalSpan?.attributes['gen_ai.tool.call.arguments'],
    JSON.stringify({ path: WEATHER_LOCATION }, null, 2),
  );
  const toolResultEvent = result.events.find((record) =>
    record.attributes?.['event.name'] === 'tool_result');
  assert.equal(
    toolResultEvent?.attributes?.['gen_ai.tool.call.arguments'],
    JSON.stringify({ path: WEATHER_LOCATION }, null, 2),
  );
  const span = result.spanExporter.getFinishedSpans().find((candidate) => candidate.name === 'skill weather');
  assert.equal(span?.name, 'skill weather');
  assert.equal(span?.attributes['agentic.tool.id'], 'call-weather');
  assert.equal(
    result.events.some((record) => record.attributes?.['event.name'] === 'tool_call'),
    false,
    'a missing before hook must not synthesize a historical tool_call event',
  );
  const turnSpan = await finalizeLifecycleTurn(result, 'skill-lazy');
  assert.deepEqual(turnSpan.attributes['langfuse.trace.tags'], ['skill:weather']);
  await result.runtime.shutdown();
  clearState();
});

test('missing before hook lazily classifies MCP and native Skill tags', async () => {
  const cases = [
    {
      sessionKey: 'lazy-mcp',
      toolName: 'mcp__weather__forecast',
      toolCallId: 'call-lazy-mcp',
      toolParams: { location: 'Hangzhou' },
      expectedSpanName: 'mcp weather.forecast',
      expectedType: 'mcp',
      expectedName: 'weather.forecast',
      expectedTag: 'mcp:weather.forecast',
    },
    {
      sessionKey: 'lazy-native-skill',
      toolName: 'Skill',
      toolCallId: 'call-lazy-skill',
      toolParams: { skill: 'medeval:medeval:init-plugin' },
      expectedSpanName: 'skill medeval:init-plugin',
      expectedType: 'skill',
      expectedName: 'medeval:init-plugin',
      expectedTag: 'skill:medeval:init-plugin',
    },
  ] as const;

  for (const scenario of cases) {
    const result = await runSkillLifecycle({
      sessionKey: scenario.sessionKey,
      systemPrompt: 'no available_skills section',
      skipBefore: true,
      toolName: scenario.toolName,
      toolCallId: scenario.toolCallId,
      toolParams: scenario.toolParams,
    });
    const span = result.spanExporter.getFinishedSpans().find((candidate) =>
      candidate.attributes['gen_ai.span.kind'] === 'TOOL');
    assert.equal(span?.name, scenario.expectedSpanName);
    assert.equal(span?.attributes['agentic.tool.type'], scenario.expectedType);
    assert.equal(span?.attributes['agentic.tool.name'], scenario.expectedName);
    const turnSpan = await finalizeLifecycleTurn(result, scenario.sessionKey);
    assert.deepEqual(turnSpan.attributes['langfuse.trace.tags'], [scenario.expectedTag]);
    await result.runtime.shutdown();
    clearState();
  }
});

test('strict path fallback classifies a successful read only while catalog state is unknown', async () => {
  const result = await runSkillLifecycle({
    sessionKey: 'skill-fallback',
    systemPrompt: 'no available_skills section',
  });
  const span = result.spanExporter.getFinishedSpans().find((candidate) => candidate.name === 'skill weather');
  assert.equal(span?.name, 'skill weather');
  assert.equal(span?.attributes['agentic.tool.type'], 'skill');
  assert.equal(span?.attributes['agentic.tool.name'], 'weather');
  await result.runtime.shutdown();
  clearState();
});

test('missing path evidence remains builtin even when output resembles Skill content', async () => {
  const result = await runSkillLifecycle({
    sessionKey: 'skill-no-path',
    systemPrompt: skillSection(skillEntry('weather', WEATHER_LOCATION)),
    toolParams: {},
  });
  const span = result.spanExporter.getFinishedSpans().find((candidate) => candidate.attributes['gen_ai.span.kind'] === 'TOOL');
  assert.equal(span?.name, 'builtin read');
  assert.equal(span?.attributes['agentic.tool.type'], 'builtin');
  await result.runtime.shutdown();
  clearState();
});

test('downstream web_fetch remains builtin after a Skill load', async () => {
  const result = await runSkillLifecycle({
    sessionKey: 'skill-downstream',
    systemPrompt: skillSection(skillEntry('weather', WEATHER_LOCATION)),
    addDownstreamWebFetch: true,
  });
  const toolSpans = result.spanExporter.getFinishedSpans().filter((span) => span.attributes['gen_ai.span.kind'] === 'TOOL');
  assert.deepEqual(toolSpans.map((span) => [span.name, span.attributes['agentic.tool.type']]), [
    ['builtin read', 'builtin'],
    ['skill weather', 'skill'],
    ['builtin web_fetch', 'builtin'],
  ]);
  await result.runtime.shutdown();
  clearState();
});

test('a catalog refresh cannot reclassify a read already checked at tool start', async () => {
  const result = await runSkillLifecycle({
    sessionKey: 'skill-negative-snapshot',
    systemPrompt: skillSection(''),
    refreshedSystemPrompt: skillSection(skillEntry('weather', WEATHER_LOCATION)),
  });
  const span = result.spanExporter.getFinishedSpans().find((candidate) => candidate.attributes['gen_ai.span.kind'] === 'TOOL');
  assert.equal(span?.name, 'builtin read');
  assert.equal(span?.attributes['agentic.tool.type'], 'builtin');
  await result.runtime.shutdown();
  clearState();
});

test('an already-matched candidate keeps its original catalog snapshot across rename', async () => {
  const result = await runSkillLifecycle({
    sessionKey: 'skill-positive-snapshot',
    systemPrompt: skillSection(skillEntry('weather', WEATHER_LOCATION, 'v1')),
    refreshedSystemPrompt: skillSection(skillEntry('forecast', WEATHER_LOCATION, 'v2')),
  });
  const span = result.spanExporter.getFinishedSpans().find((candidate) => candidate.name === 'skill weather');
  assert.equal(span?.name, 'skill weather');
  assert.equal(span?.attributes['agentic.tool.name'], 'weather');
  await result.runtime.shutdown();
  clearState();
});

test('conflicting catalog location keeps the read builtin without stopping the hook pipeline', async () => {
  const result = await runSkillLifecycle({
    sessionKey: 'skill-conflict',
    systemPrompt: skillSection(
      skillEntry('weather', WEATHER_LOCATION, 'v1')
      + skillEntry('forecast', WEATHER_LOCATION, 'v2'),
    ),
  });
  const span = result.spanExporter.getFinishedSpans().find((candidate) => candidate.attributes['gen_ai.span.kind'] === 'TOOL');
  assert.equal(span?.name, 'builtin read');
  assert.equal(span?.attributes['agentic.tool.type'], 'builtin');
  await result.runtime.shutdown();
  clearState();
});

test('existing MCP raw-name classification remains unchanged', async () => {
  const result = await runSkillLifecycle({
    sessionKey: 'mcp-regression',
    systemPrompt: 'no available_skills section',
    toolName: 'mcp__weather__forecast',
    toolCallId: 'call-mcp',
    toolParams: { location: 'Hangzhou' },
  });
  const span = result.spanExporter.getFinishedSpans().find((candidate) => candidate.attributes['gen_ai.span.kind'] === 'TOOL');
  assert.equal(span?.name, 'mcp weather.forecast');
  assert.equal(span?.attributes['gen_ai.tool.name'], 'mcp__weather__forecast');
  assert.equal(span?.attributes['agentic.tool.type'], 'mcp');
  assert.equal(span?.attributes['agentic.tool.name'], 'weather.forecast');
  const turnSpan = await finalizeLifecycleTurn(result, 'mcp-regression');
  assert.deepEqual(turnSpan.attributes['langfuse.trace.tags'], ['mcp:weather.forecast']);
  await result.runtime.shutdown();
  clearState();
});

test('native literal Skill keeps cchooks invocation semantics on failure', async () => {
  const result = await runSkillLifecycle({
    sessionKey: 'native-skill-error',
    systemPrompt: 'no available_skills section',
    toolName: 'Skill',
    toolCallId: 'call-native',
    toolParams: { skill: 'medeval:medeval:init-plugin' },
    persistError: true,
  });
  const span = result.spanExporter.getFinishedSpans().find((candidate) => candidate.attributes['gen_ai.span.kind'] === 'TOOL');
  assert.equal(span?.name, 'skill medeval:init-plugin');
  assert.equal(span?.attributes['gen_ai.tool.name'], 'Skill');
  assert.equal(span?.attributes['agentic.tool.type'], 'skill');
  assert.equal(span?.attributes['agentic.tool.name'], 'medeval:init-plugin');
  assert.equal(span?.attributes['agentic.tool.raw_name'], 'Skill');
  assert.equal(span?.attributes['agentic.tool.id'], 'call-native');
  assert.equal(span?.attributes['agentic.tool.derived'], undefined);
  assert.equal(span?.status.code, 2);
  const event = result.events.find((record) => record.attributes?.['event.name'] === 'tool_result');
  assert.equal(event?.attributes?.['agentic.tool.type'], 'skill');
  assert.equal(event?.attributes?.['agentic.tool.id'], 'call-native');
  const turnSpan = await finalizeLifecycleTurn(result, 'native-skill-error');
  assert.deepEqual(turnSpan.attributes['langfuse.trace.tags'], ['skill:medeval:init-plugin']);
  await result.runtime.shutdown();
  clearState();
});

test('mixed repeated Skill and MCP recognition keeps first-seen Turn tag order', async () => {
  const result = await runSkillLifecycle({
    sessionKey: 'mixed-trace-tags',
    systemPrompt: skillSection(skillEntry('weather', WEATHER_LOCATION)),
  });
  emitAdditionalTool(result.handlers, 'mixed-trace-tags', 'Skill', 'call-native-duplicate', { skill: 'weather' });
  emitAdditionalTool(result.handlers, 'mixed-trace-tags', 'mcp__weather__forecast', 'call-mcp', {});
  emitAdditionalTool(result.handlers, 'mixed-trace-tags', 'mcp__weather__forecast', 'call-mcp-duplicate', {});
  const turnSpan = await finalizeLifecycleTurn(result, 'mixed-trace-tags');
  assert.deepEqual(turnSpan.attributes['langfuse.trace.tags'], ['skill:weather', 'mcp:weather.forecast']);
  await result.runtime.shutdown();
  clearState();
});

test('builtin-only Turn omits langfuse trace tags', async () => {
  const result = await runSkillLifecycle({
    sessionKey: 'builtin-only-tags',
    systemPrompt: 'no available_skills section',
    toolName: 'exec',
    toolCallId: 'call-exec',
    toolParams: { command: 'true' },
  });
  const turnSpan = await finalizeLifecycleTurn(result, 'builtin-only-tags');
  assert.equal(turnSpan.attributes['langfuse.trace.tags'], undefined);
  await result.runtime.shutdown();
  clearState();
});

test('failed derived Skill span construction does not add a Turn tag', async () => {
  const result = await runSkillLifecycle({
    sessionKey: 'derived-span-failure',
    systemPrompt: skillSection(skillEntry('weather', WEATHER_LOCATION)),
    failDerivedSkillSpan: true,
  });
  assert.equal(result.spanExporter.getFinishedSpans().some((span) => span.name === 'skill weather'), false);
  const turnSpan = await finalizeLifecycleTurn(result, 'derived-span-failure');
  assert.equal(turnSpan.attributes['langfuse.trace.tags'], undefined);
  await result.runtime.shutdown();
  clearState();
});
