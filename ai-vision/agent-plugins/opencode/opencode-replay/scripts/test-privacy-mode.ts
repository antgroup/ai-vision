import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHooks, parseConfig, type ObservOpencodeConfig } from 'observ-opencode/core';
import { initReplayTelemetry } from '../src/telemetry.js';
import { createTranscriptRecorder } from '../src/transcript.js';
import { decorateHooksWithTranscript } from '../src/decorator.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, '..');

const tmpRoot = '/tmp/observ-opencode-privacy-mode';
const transcriptDir = join(tmpRoot, 'transcripts');
const replayDir = join(tmpRoot, 'replays');
const spansFile = join(replayDir, 'spans_generated_privacy.jsonl');
const eventsFile = join(replayDir, 'events_generated_privacy.jsonl');

const sentinels = [
  'PRIVATE_USER_SENTINEL',
  'PRIVATE_ASSISTANT_SENTINEL',
  'PRIVATE_TOOL_ARG_SENTINEL',
  'PRIVATE_ID_SENTINEL',
  'PRIVATE_NAME_SENTINEL',
  'PRIVATE_TOOL_OUTPUT_SENTINEL',
  'PRIVATE_ERROR_SENTINEL',
  'PRIVATE_SYSTEM_SENTINEL',
  'PRIVATE_ENV_TRANSCRIPT_SENTINEL',
];

const contentSwitchPaths = [
  ['log', 'events', 'userPrompts'],
  ['log', 'events', 'systemPrompts'],
  ['log', 'events', 'modelOutputs'],
  ['log', 'events', 'toolDetails'],
  ['log', 'events', 'toolOutputs'],
  ['log', 'spans', 'userPrompts'],
  ['log', 'spans', 'systemPrompts'],
  ['log', 'spans', 'modelOutputs'],
  ['log', 'spans', 'toolDetails'],
  ['log', 'spans', 'toolOutputs'],
] as const;

type Attr = { key?: string; value?: Record<string, unknown> };

type OtelLine = {
  resourceSpans?: Array<{ resource?: { attributes?: Attr[] } }>;
  resourceLogs?: Array<{
    resource?: { attributes?: Attr[] };
    scopeLogs?: Array<{ logRecords?: Array<{ traceId?: string; spanId?: string }> }>;
  }>;
};

type TranscriptLogEntry = {
  version?: number;
  schemaVersion?: number;
  plugin?: string;
  session: string;
  hook: string;
  collectionMode?: string;
  input?: any;
  output?: any;
};

function withEnv<T>(updates: Record<string, string | undefined>, fn: () => T): T {
  const before: Record<string, string | undefined> = {};
  for (const key of Object.keys(updates)) before[key] = process.env[key];
  try {
    for (const [key, value] of Object.entries(updates)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    return fn();
  } finally {
    for (const [key, value] of Object.entries(before)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
}

function resetDir(path: string): void {
  rmSync(path, { recursive: true, force: true });
  mkdirSync(path, { recursive: true });
}

function getNested(config: ObservOpencodeConfig, path: readonly string[]): unknown {
  let current: unknown = config;
  for (const segment of path) {
    if (!current || typeof current !== 'object') return undefined;
    current = (current as Record<string, unknown>)[segment];
  }
  return current;
}

function assertPrivacyContentBoundary(config: ObservOpencodeConfig): void {
  assert.equal(config.collectionMode, 'privacy');
  for (const path of contentSwitchPaths) {
    assert.equal(getNested(config, path), false, `${path.join('.')} must be false in privacy mode`);
  }
}

function assertNoSentinels(file: string): void {
  assert.ok(existsSync(file), `${file} should exist`);
  const text = readFileSync(file, 'utf8');
  assert.ok(text.trim().length > 0, `${file} should not be empty`);
  assertNoSentinelsInText(text, file);
}

function assertNoSentinelsInText(text: string, label: string): void {
  for (const sentinel of sentinels) {
    assert.equal(text.includes(sentinel), false, `${sentinel} leaked into ${label}`);
  }
}

function attrValue(attr: Attr): unknown {
  const value = attr.value ?? {};
  if ('stringValue' in value) return value.stringValue;
  if ('intValue' in value) return value.intValue;
  if ('doubleValue' in value) return value.doubleValue;
  if ('boolValue' in value) return value.boolValue;
  if ('arrayValue' in value) return value.arrayValue;
  if ('kvlistValue' in value) return value.kvlistValue;
  return undefined;
}

function parseJsonl(file: string): OtelLine[] {
  return readFileSync(file, 'utf8')
    .split('\n')
    .filter(line => line.trim().length > 0)
    .map(line => JSON.parse(line) as OtelLine);
}

function parseTranscriptJsonl(file: string): TranscriptLogEntry[] {
  return readFileSync(file, 'utf8')
    .split('\n')
    .filter(line => line.trim().length > 0)
    .map(line => JSON.parse(line) as TranscriptLogEntry);
}

function assertResourceCollectionMode(file: string, signal: 'spans' | 'logs'): void {
  const lines = parseJsonl(file);
  const resources = lines.flatMap(line => signal === 'spans' ? (line.resourceSpans ?? []) : (line.resourceLogs ?? []));
  assert.ok(resources.length > 0, `${file} should contain resource ${signal}`);

  for (const resource of resources) {
    const attrs = resource.resource?.attributes ?? [];
    const modeAttr = attrs.find(attr => attr.key === 'agentic.collection.mode');
    assert.ok(modeAttr, `${file} resource missing agentic.collection.mode`);
    assert.equal(attrValue(modeAttr), 'privacy', `${file} resource collection mode must be privacy`);
  }
}

function assertLogRecordsCorrelated(file: string): void {
  const records = parseJsonl(file).flatMap(line =>
    (line.resourceLogs ?? []).flatMap(resourceLog =>
      (resourceLog.scopeLogs ?? []).flatMap(scopeLog => scopeLog.logRecords ?? [])));
  assert.ok(records.length > 0, `${file} should contain OTLP log records`);
  for (const [index, record] of records.entries()) {
    assert.ok(record.traceId && record.traceId.length > 0, `${file} log record #${index} missing traceId`);
    assert.ok(record.spanId && record.spanId.length > 0, `${file} log record #${index} missing spanId`);
  }
}

function normalizeGeneratedValue(value: unknown): unknown {
  if (typeof value === 'string') return value.replace(/turn-\d+-\d+/gu, '<turn-id>');
  if (Array.isArray(value)) return value.map(normalizeGeneratedValue);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, nested]) => [key, normalizeGeneratedValue(nested)]));
  }
  return value;
}

function normalizedAttributes(attributes: Attr[] | undefined, ignored: Set<string>): unknown[] {
  return (attributes ?? [])
    .filter(attribute => attribute.key && !ignored.has(attribute.key))
    .map(attribute => normalizeGeneratedValue(attribute))
    .sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right)));
}

function normalizedSpans(file: string): unknown[] {
  const environmentIdentity = new Set(['ant.username', 'user.id']);
  return parseJsonl(file).flatMap(line => (line.resourceSpans ?? []).flatMap(resource =>
    ((resource as any).scopeSpans ?? []).flatMap((scope: any) => (scope.spans ?? []).map((span: any) => ({
      name: span.name,
      kind: span.kind,
      status: normalizeGeneratedValue(span.status),
      attributes: normalizedAttributes(span.attributes, new Set(['agentic.tool.duration_ms', ...environmentIdentity])),
    })))))
    .sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right)));
}

function normalizedLogs(file: string): unknown[] {
  const environmentIdentity = new Set(['ant.username', 'user.id']);
  return parseJsonl(file).flatMap(line => (line.resourceLogs ?? []).flatMap(resource =>
    (resource.scopeLogs ?? []).flatMap(scope => (scope.logRecords ?? []).map((record: any) => ({
      severityNumber: record.severityNumber,
      severityText: record.severityText,
      body: normalizeGeneratedValue(record.body),
      attributes: normalizedAttributes(record.attributes, new Set(['duration_ms', ...environmentIdentity])),
    })))))
    .sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right)));
}

function assertNormalizedLiveReplayEquality(replayedSpans: string, replayedEvents: string): void {
  assert.deepEqual(normalizedSpans(replayedSpans), normalizedSpans(spansFile),
    'live and offline replay spans differ after nondeterministic ID/time normalization');
  assert.deepEqual(normalizedLogs(replayedEvents), normalizedLogs(eventsFile),
    'live and offline replay events differ after nondeterministic ID/time normalization');
}

function assertPostedTraceCollectionMode(body: string): void {
  const payload = JSON.parse(body) as OtelLine;
  const resources = payload.resourceSpans ?? [];
  assert.ok(resources.length > 0, 'report --shift should POST resource spans');

  for (const resource of resources) {
    const attrs = resource.resource?.attributes ?? [];
    const modeAttr = attrs.find(attr => attr.key === 'agentic.collection.mode');
    assert.ok(modeAttr, 'posted report resource missing agentic.collection.mode');
    assert.equal(attrValue(modeAttr), 'privacy', 'posted report resource collection mode must be privacy');
  }
}

async function generatePrivacyRun(): Promise<void> {
  resetDir(tmpRoot);
  resetDir(transcriptDir);
  resetDir(replayDir);

  const config = withEnv({
    OBSERV_OPENCODE_COLLECTION_MODE: undefined,
    AIVISION_COLLECTION_MODE: undefined,
    OBSERV_OPENCODE_SESSION_DIR: transcriptDir,
  }, () => parseConfig({
    enableTelemetry: true,
    collectionMode: 'privacy',
    captureContent: true,
    log: {
      events: {
        userPrompts: true,
        systemPrompts: true,
        modelOutputs: true,
        toolDetails: true,
        toolOutputs: true,
      },
      spans: {
        userPrompts: true,
        systemPrompts: true,
        modelOutputs: true,
        toolDetails: true,
        toolOutputs: true,
      },
    },
  }));
  assertPrivacyContentBoundary(config);

  process.env.OBSERV_OPENCODE_SESSION_DIR = transcriptDir;
  const runtime = initReplayTelemetry({ spans: spansFile, events: eventsFile }, config.collectionMode);
  const recordTranscript = createTranscriptRecorder({ directory: transcriptDir, collectionMode: config.collectionMode });
  const hooks = decorateHooksWithTranscript(createHooks(runtime, config, {}), recordTranscript);
  const event = hooks.event as (input: { event: unknown }) => Promise<void>;
  const toolBefore = hooks['tool.execute.before'] as (
    input: { tool: string; sessionID: string; callID: string },
    output: { args: unknown },
  ) => Promise<void>;
  const toolAfter = hooks['tool.execute.after'] as (
    input: { tool: string; sessionID: string; callID: string; args: unknown },
    output: { title: string; output: string; metadata: unknown },
  ) => Promise<void>;
  const systemTransform = hooks['experimental.chat.system.transform'] as (
    input: { sessionID: string },
    output: { system: string[] },
  ) => Promise<void>;

  const sessionID = 'privacy_generated_session';
  const userMessageID = 'privacy_user_message';
  const assistantMessageID = 'privacy_assistant_message';
  const callID = 'privacy_tool_call';
  const start = 1_770_000_000_000;

  await event({ event: { type: 'session.created', properties: { sessionID, info: {
    id: sessionID,
    projectID: 'privacy-project',
    directory: '/tmp/privacy-project',
    title: 'privacy test',
    version: 'test',
  } } } });
  await event({ event: { type: 'message.updated', properties: { sessionID, info: {
    id: userMessageID,
    role: 'user',
    sessionID,
    time: { created: start },
  } } } });
  await event({ event: { type: 'message.part.updated', properties: { sessionID, part: {
    id: 'privacy_user_part',
    sessionID,
    messageID: userMessageID,
    type: 'text',
    text: 'PRIVATE_USER_SENTINEL: user secret',
  }, time: start + 1 } } });
  await systemTransform(
    { sessionID },
    { system: ['PRIVATE_SYSTEM_SENTINEL: system secret'] },
  );
  await event({ event: { type: 'message.updated', properties: { sessionID, info: {
    id: assistantMessageID,
    role: 'assistant',
    sessionID,
    modelID: 'theta/privacy-model',
    providerID: 'theta',
    tokens: { input: 0, output: 0, reasoning: 0, cache: { read: 0, write: 0 } },
    cost: 0,
    time: { created: start + 10 },
  } } } });
  await event({ event: { type: 'message.part.updated', properties: { sessionID, part: {
    id: 'privacy_tool_part_start',
    sessionID,
    messageID: assistantMessageID,
    type: 'tool',
    callID,
    tool: 'bash',
    state: {
      status: 'running',
      input: { command: 'echo PRIVATE_TOOL_ARG_SENTINEL', name: 'PRIVATE_NAME_SENTINEL', id: 'PRIVATE_ID_SENTINEL' },
      time: { start: start + 20 },
    },
  }, time: start + 20 } } });
  await toolBefore(
    { tool: 'bash', sessionID, callID },
    { args: { command: 'echo PRIVATE_TOOL_ARG_SENTINEL', name: 'PRIVATE_NAME_SENTINEL', id: 'PRIVATE_ID_SENTINEL' } },
  );
  await toolAfter(
    { tool: 'bash', sessionID, callID, args: { command: 'echo PRIVATE_TOOL_ARG_SENTINEL', name: 'PRIVATE_NAME_SENTINEL', id: 'PRIVATE_ID_SENTINEL' } },
    { title: 'bash', output: 'PRIVATE_TOOL_OUTPUT_SENTINEL: command output', metadata: { error: 'PRIVATE_ERROR_SENTINEL: tool error', exit: 2 } },
  );
  await event({ event: { type: 'message.part.updated', properties: { sessionID, part: {
    id: 'privacy_tool_part_end',
    sessionID,
    messageID: assistantMessageID,
    type: 'tool',
    callID,
    tool: 'bash',
    state: {
      status: 'completed',
      input: { command: 'echo PRIVATE_TOOL_ARG_SENTINEL', name: 'PRIVATE_NAME_SENTINEL', id: 'PRIVATE_ID_SENTINEL' },
      time: { start: start + 20, end: start + 60 },
    },
  }, time: start + 60 } } });
  await event({ event: { type: 'session.error', properties: { sessionID, error: {
    name: 'APIError',
    data: {
      message: 'PRIVATE_ERROR_SENTINEL: upstream API key leaked',
      statusCode: 401,
      isRetryable: false,
      responseBody: 'PRIVATE_ERROR_SENTINEL response body',
    },
  } } } });
  await event({ event: { type: 'message.part.updated', properties: { sessionID, part: {
    id: 'privacy_assistant_part',
    sessionID,
    messageID: assistantMessageID,
    type: 'text',
    text: 'PRIVATE_ASSISTANT_SENTINEL: assistant secret',
  }, time: start + 70 } } });
  await event({ event: { type: 'message.updated', properties: { sessionID, info: {
    id: assistantMessageID,
    role: 'assistant',
    sessionID,
    modelID: 'theta/privacy-model',
    providerID: 'theta',
    tokens: { input: 11, output: 7, reasoning: 2, cache: { read: 3, write: 5 } },
    cost: 0.001,
    time: { created: start + 10, completed: start + 90 },
    finish: 'stop',
  } } } });
  await event({ event: { type: 'session.idle', properties: { sessionID } } });

  await runtime.forceFlush();
  await runtime.shutdown();
}

function assertGeneratedTranscript(): void {
  const files = readdirSync(transcriptDir).filter(file => file.endsWith('.jsonl'));
  assert.ok(files.length > 0, 'privacy harness should generate transcript files');
  for (const file of files) {
    const fullPath = join(transcriptDir, file);
    assertNoSentinels(fullPath);
    const text = readFileSync(fullPath, 'utf8');
    assert.ok(text.includes('"collectionMode":"privacy"'), `${fullPath} should mark collectionMode=privacy`);
    assert.ok(text.includes('"schemaVersion":2'), `${fullPath} should mark raw schemaVersion=2`);
    assert.ok(text.includes('[REDACTED]'), `${fullPath} should contain the redaction placeholder`);
  }
  assertGeneratedTranscriptStructure();
}

function assertGeneratedTranscriptStructure(): void {
  const entries = parseTranscriptJsonl(join(transcriptDir, 'session_privacy_generated_session.jsonl'));
  assert.ok(entries.length > 0, 'generated privacy transcript should contain entries');

  for (const entry of entries) {
    assert.equal(entry.collectionMode, 'privacy', `${entry.hook} should mark collectionMode=privacy`);
    assert.equal(entry.schemaVersion, 2, `${entry.hook} should mark raw schemaVersion=2`);
    assert.equal(entry.plugin, 'opencode', `${entry.hook} should identify the replay plugin`);
  }

  const userPart = entries.find(entry =>
    entry.hook === 'event.message.part.updated'
    && entry.input?.properties?.part?.id === 'privacy_user_part');
  assert.ok(userPart, 'user text part transcript entry should preserve part id');
  assert.equal(userPart.input.type, 'message.part.updated');
  assert.equal(userPart.input.properties.sessionID, 'privacy_generated_session');
  assert.equal(userPart.input.properties.part.sessionID, 'privacy_generated_session');
  assert.equal(userPart.input.properties.part.messageID, 'privacy_user_message');
  assert.equal(userPart.input.properties.part.type, 'text');
  assert.equal(userPart.input.properties.part.text, '[REDACTED]');
  assert.equal(userPart.input.properties.time, 1_770_000_000_001);

  const systemTransform = entries.find(entry => entry.hook === 'experimental.chat.system.transform');
  assert.ok(systemTransform, 'system transform transcript entry should exist');
  assert.equal(systemTransform.input?.sessionID, 'privacy_generated_session');
  assert.deepEqual(systemTransform.output?.system, ['[REDACTED]']);

  const toolPartStart = entries.find(entry =>
    entry.hook === 'event.message.part.updated'
    && entry.input?.properties?.part?.id === 'privacy_tool_part_start');
  assert.ok(toolPartStart, 'tool part start transcript entry should exist');
  assert.equal(toolPartStart.input?.properties?.part?.state?.input?.command, '[REDACTED]');
  assert.equal(toolPartStart.input?.properties?.part?.state?.input?.name, '[REDACTED]');
  assert.equal(toolPartStart.input?.properties?.part?.state?.input?.id, '[REDACTED]');

  const toolBefore = entries.find(entry => entry.hook === 'tool.execute.before');
  assert.ok(toolBefore, 'tool before transcript entry should exist');
  assert.equal(toolBefore.input?.tool, 'bash');
  assert.equal(toolBefore.input?.sessionID, 'privacy_generated_session');
  assert.equal(toolBefore.input?.callID, 'privacy_tool_call');
  assert.equal(toolBefore.output?.args?.command, '[REDACTED]');
  assert.equal(toolBefore.output?.args?.name, '[REDACTED]');
  assert.equal(toolBefore.output?.args?.id, '[REDACTED]');

  const toolAfter = entries.find(entry => entry.hook === 'tool.execute.after');
  assert.ok(toolAfter, 'tool after transcript entry should exist');
  assert.equal(toolAfter.input?.tool, 'bash');
  assert.equal(toolAfter.input?.sessionID, 'privacy_generated_session');
  assert.equal(toolAfter.input?.callID, 'privacy_tool_call');
  assert.equal(toolAfter.input?.args?.command, '[REDACTED]');
  assert.equal(toolAfter.input?.args?.name, '[REDACTED]');
  assert.equal(toolAfter.input?.args?.id, '[REDACTED]');
  assert.equal(toolAfter.output?.output, '[REDACTED]');
  assert.equal(toolAfter.output?.metadata?.error, '[REDACTED]');
  assert.equal(toolAfter.output?.metadata?.exit, 2);

  const sessionError = entries.find(entry => entry.hook === 'event.session.error');
  assert.ok(sessionError, 'session error transcript entry should exist');
  assert.equal(sessionError.input?.properties?.sessionID, 'privacy_generated_session');
  assert.equal(sessionError.input?.properties?.error?.name, 'APIError');
  assert.equal(sessionError.input?.properties?.error?.data?.message, '[REDACTED]');
  assert.equal(sessionError.input?.properties?.error?.data?.statusCode, 401);
  assert.equal(sessionError.input?.properties?.error?.data?.isRetryable, false);
  assert.equal(sessionError.input?.properties?.error?.data?.responseBody, '[REDACTED]');

  const finalAssistant = entries.find(entry =>
    entry.hook === 'event.message.updated'
    && entry.input?.properties?.info?.id === 'privacy_assistant_message'
    && entry.input?.properties?.info?.tokens?.input === 11);
  assert.ok(finalAssistant, 'final assistant message entry should preserve IDs and tokens');
  assert.equal(finalAssistant.input.properties.info.role, 'assistant');
  assert.equal(finalAssistant.input.properties.info.sessionID, 'privacy_generated_session');
  assert.equal(finalAssistant.input.properties.info.modelID, 'theta/privacy-model');
  assert.equal(finalAssistant.input.properties.info.providerID, 'theta');
  assert.equal(finalAssistant.input.properties.info.tokens.output, 7);
  assert.equal(finalAssistant.input.properties.info.tokens.reasoning, 2);
  assert.equal(finalAssistant.input.properties.info.tokens.cache.read, 3);
  assert.equal(finalAssistant.input.properties.info.tokens.cache.write, 5);
  assert.equal(finalAssistant.input.properties.info.time.completed, 1_770_000_000_090);
}

function assertEnvResolvedTranscriptPrivacy(): void {
  withEnv({
    OBSERV_OPENCODE_SESSION_DIR: transcriptDir,
    OBSERV_OPENCODE_COLLECTION_MODE: 'privacy',
    AIVISION_COLLECTION_MODE: undefined,
  }, () => {
    const envConfig = parseConfig({ captureContent: true });
    assertPrivacyContentBoundary(envConfig);
    createTranscriptRecorder({ directory: transcriptDir, collectionMode: envConfig.collectionMode })(
      'privacy_env_session',
      'direct.env.privacy',
      { secret: 'PRIVATE_ENV_TRANSCRIPT_SENTINEL input' },
      { secret: 'PRIVATE_ENV_TRANSCRIPT_SENTINEL output' },
    );
  });

  const envFile = join(transcriptDir, 'session_privacy_env_session.jsonl');
  assertNoSentinels(envFile);
  const text = readFileSync(envFile, 'utf8');
  assert.ok(text.includes('"collectionMode":"privacy"'));
  assert.ok(text.includes('"schemaVersion":2'));
}

async function assertReportPrivacyRegression(): Promise<void> {
  const reportDir = join(tmpRoot, 'report');
  resetDir(reportDir);
  const postedBodies: string[] = [];
  const server = createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on('data', chunk => chunks.push(Buffer.from(chunk)));
    req.on('end', () => {
      postedBodies.push(Buffer.concat(chunks).toString('utf8'));
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end('{}');
    });
  });

  try {
    await new Promise<void>((resolve, reject) => {
      server.once('error', reject);
      server.listen(0, '127.0.0.1', () => resolve());
    });

    const address = server.address() as { port: number } | null;
    assert.ok(address?.port, 'local report test server should have a port');

    const configPath = join(reportDir, 'report.config.toml');
    writeFileSync(configPath, `host = "http://127.0.0.1:${address.port}"\nsecret_key = "must-not-load"\n`);

    const tsxBin = process.execPath;
    const env: NodeJS.ProcessEnv = {
      ...process.env,
      OTEL_EXPORTER_OTLP_ENDPOINT: `http://127.0.0.1:${address.port}`,
      OTEL_EXPORTER_OTLP_HEADERS: 'Authorization=must-not-load',
    };
    const child = spawn(tsxBin, ['--import', 'tsx',
      'src/report.ts',
      join(transcriptDir, 'session_privacy_generated_session.jsonl'),
      '--config', configPath,
      '--shift',
    ], { cwd: root, env });

    let stdout = '';
    let stderr = '';
    child.stdout.on('data', chunk => { stdout += String(chunk); });
    child.stderr.on('data', chunk => { stderr += String(chunk); });

    const exitCode = await new Promise<number | null>((resolve, reject) => {
      child.once('error', reject);
      child.once('exit', code => resolve(code));
    });

    assert.notEqual(exitCode, 0, `legacy remote report options must fail\nstdout:\n${stdout}\nstderr:\n${stderr}`);
    assert.match(`${stdout}\n${stderr}`, /--config was removed.*127\.0\.0\.1:4319/su);
    assert.equal(postedBodies.length, 0, 'replay must not contact a configured production/remote endpoint');
  } finally {
    await new Promise<void>(resolve => server.close(() => resolve()));
  }
}

async function assertGeneratedTranscriptReplayable(): Promise<void> {
  const transcriptFile = join(transcriptDir, 'session_privacy_generated_session.jsonl');
  const replayedSpans = join(replayDir, 'spans_replayed_generated_privacy.jsonl');
  const replayedEvents = join(replayDir, 'events_replayed_generated_privacy.jsonl');
  writeFileSync(replayedSpans, 'STALE_PRIVATE_USER_SENTINEL\n');
  writeFileSync(replayedEvents, 'STALE_PRIVATE_USER_SENTINEL\n');

  const tsxBin = process.execPath;
  const env = { ...process.env };
  delete env.OBSERV_OPENCODE_COLLECTION_MODE;
  delete env.AIVISION_COLLECTION_MODE;

  const child = spawn(tsxBin, ['--import', 'tsx',
    'src/replay.ts',
    transcriptFile,
    '--otel',
    replayedSpans,
  ], { cwd: root, env });

  let stdout = '';
  let stderr = '';
  child.stdout.on('data', chunk => { stdout += String(chunk); });
  child.stderr.on('data', chunk => { stderr += String(chunk); });

  const exitCode = await new Promise<number | null>((resolve, reject) => {
    child.once('error', reject);
    child.once('exit', code => resolve(code));
  });

  assert.equal(exitCode, 0, `generated privacy transcript replay should exit 0\nstdout:\n${stdout}\nstderr:\n${stderr}`);
  assertNoSentinels(replayedSpans);
  assertNoSentinels(replayedEvents);
  assertResourceCollectionMode(replayedSpans, 'spans');
  assertResourceCollectionMode(replayedEvents, 'logs');
  assertLogRecordsCorrelated(replayedEvents);
  assert.ok(parseJsonl(replayedSpans).length >= 3, 'replayed generated transcript should produce meaningful spans');
  assert.ok(parseJsonl(replayedEvents).length >= 3, 'replayed generated transcript should produce meaningful events');
  assertNormalizedLiveReplayEquality(replayedSpans, replayedEvents);
}

async function main(): Promise<void> {
  await generatePrivacyRun();
  assertGeneratedTranscript();
  assertEnvResolvedTranscriptPrivacy();
  assertNoSentinels(spansFile);
  assertNoSentinels(eventsFile);
  assertResourceCollectionMode(spansFile, 'spans');
  assertResourceCollectionMode(eventsFile, 'logs');
  assertLogRecordsCorrelated(eventsFile);
  await assertGeneratedTranscriptReplayable();
  await assertReportPrivacyRegression();
  console.log('privacy-mode regression checks passed');
}

main().catch(error => {
  console.error(error);
  process.exit(1);
});
