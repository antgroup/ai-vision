#!/usr/bin/env node
import { createHash } from 'node:crypto';
import { closeSync, existsSync, mkdirSync, openSync, readFileSync, readdirSync, rmSync, unlinkSync, writeFileSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { basename, join, resolve } from 'node:path';
import { spawn, spawnSync, type ChildProcess } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { GOLDEN_SCENARIOS } from './scenarios.js';
import { probeCapability } from './capability-probe.js';
import { diagnose, type Finding } from './diagnosis.js';
import { canonicalChecksum, type CanonicalEvent, type CollectionIntegrity } from '@observ/pi';
import { compareSemanticParity } from './parity.js';
import {
  lastOtlpMetrics,
  otlpAttributes,
  otlpLogRecords,
  otlpSpans,
  readJsonLines,
  type OtlpMetric,
} from './otlp.js';

const packageRoot = fileURLToPath(new URL('../../..', import.meta.url));
const piRoot = resolve(packageRoot, '..');
const repoRoot = resolve(piRoot, '..');
const cli = join(packageRoot, 'node_modules', '@earendil-works', 'pi-coding-agent', 'dist', 'cli.js');
const demo = join(packageRoot, 'dist', 'src', 'demo', 'run-demo.js');
const replayCli = join(packageRoot, 'dist', 'src', 'replay.js');
const baseUrl = 'http://127.0.0.1:4319';

type ResultClass = 'pass' | 'semantic-fail' | 'external-blocker' | 'harness-error';
interface ScenarioResult { id: string; status: 'passed' | 'failed' | 'skipped'; start: string; end: string; exitCode?: number; evidence?: string[]; transcriptFiles: string[]; replayFiles: string[]; log: string; integrity?: CollectionIntegrity; }
interface Manifest { runId: string; result: ResultClass; startedAt: string; endedAt?: string; baseUrl: string; integrity: 'complete' | 'incomplete'; scenarios: ScenarioResult[]; replayFiles: string[]; diagnosis?: string; blocker?: string; }

class RunError extends Error { constructor(readonly result: ResultClass, message: string) { super(message); } }

function writeJson(path: string, value: unknown): void { writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 }); }
async function ready(): Promise<boolean> { try { const response = await fetch(`${baseUrl}/api/stats`, { signal: AbortSignal.timeout(2000) }); return response.ok; } catch { return false; } }
async function requirePost(path: string): Promise<unknown> { const response = await fetch(`${baseUrl}${path}`, { method: 'POST', signal: AbortSignal.timeout(10_000) }); if (!response.ok) throw new RunError('external-blocker', `${path} returned ${response.status}`); const text = await response.text(); return text ? JSON.parse(text) : {}; }
async function getJson(path: string): Promise<Record<string, unknown>> { const response = await fetch(`${baseUrl}${path}`, { signal: AbortSignal.timeout(10_000) }); if (!response.ok) throw new RunError('external-blocker', `${path} returned ${response.status}`); return await response.json() as Record<string, unknown>; }

async function ensureWeaver(logDir: string): Promise<ChildProcess | undefined> {
  if (await ready()) return undefined;
  const fd = openSync(join(logDir, 'weaver.log'), 'a', 0o600);
  const noProxy = [process.env.NO_PROXY, '127.0.0.1', 'localhost'].filter(Boolean).join(',');
  const child = spawn('make', ['-C', join(repoRoot, 'weaver'), 'serve'], { detached: true, env: { ...process.env, NO_PROXY: noProxy }, stdio: ['ignore', fd, fd] });
  closeSync(fd);
  for (let attempt = 0; attempt < 60; attempt++) { await new Promise(resolveWait => setTimeout(resolveWait, 1000)); if (await ready()) return child; if (child.exitCode !== null) break; }
  if (child.pid) { try { process.kill(-child.pid, 'SIGTERM'); } catch {} }
  throw new RunError('external-blocker', 'AiVision did not become ready');
}

function runNode(script: string, env: NodeJS.ProcessEnv, log: string, timeoutMs = 180_000): number {
  const fd = openSync(log, 'a', 0o600);
  const result = spawnSync(process.execPath, [script], { cwd: packageRoot, env, stdio: ['ignore', fd, fd], timeout: timeoutMs, killSignal: 'SIGTERM' });
  closeSync(fd);
  if (result.error?.message.includes('ETIMEDOUT')) return 124;
  if (result.error) throw new RunError('harness-error', result.error.message);
  return result.status ?? 1;
}

/** Check whether a scenario log indicates a transient provider rate limit. */
function isRateLimited(log: string): boolean {
  try { const text = readFileSync(log, 'utf8'); return /rate_limit|429|额度超限|并发限流|throttl/i.test(text); }
  catch { return false; }
}

/** Remove transcripts that appeared after `before` so a retry starts clean. */
function dropNewTranscripts(dir: string, before: Set<string>): void {
  for (const file of listJsonl(dir)) if (!before.has(file)) { unlinkSync(file); try { unlinkSync(`${file}.integrity.json`); } catch {} }
}

function listJsonl(dir: string): string[] { return existsSync(dir) ? readdirSync(dir).filter(name => name.endsWith('.jsonl')).map(name => join(dir, name)).sort() : []; }

function canonicalEvents(files: string[]): CanonicalEvent[] {
  return files.flatMap(file => readJsonLines<CanonicalEvent>(file));
}

function metricTotal(metrics: OtlpMetric[], name: string): number {
  return metrics.filter(metric => metric.name === name).reduce((total, metric) => total
    + (metric.sum?.dataPoints ?? []).reduce((sum, point) => sum + Number(point.asDouble ?? point.asInt ?? 0), 0)
    + (metric.histogram?.dataPoints ?? []).reduce((sum, point) => sum + Number(point.count ?? 0), 0), 0);
}

function inspectSkillReplay(scenario: ScenarioResult): { valid: boolean; evidence: string[] } {
  if (scenario.id !== 'skill-command' && scenario.id !== 'skill-conversation') return { valid: true, evidence: [] };
  const events = canonicalEvents(scenario.transcriptFiles);
  const physicalStarts = events.filter(event => event.hook === 'tool_execution_start').length;
  const physicalEnds = events.filter(event => event.hook === 'tool_execution_end').length;
  const records = otlpLogRecords(scenario.replayFiles);
  const toolCalls = records.filter(record => record.body?.stringValue === 'tool_call').length;
  const toolResults = records.filter(record => record.body?.stringValue === 'tool_result').length;
  const replayMetrics = lastOtlpMetrics(scenario.replayFiles);
  const countMetrics = metricTotal(replayMetrics, 'agent.tool_call.count');
  const durationMetrics = metricTotal(replayMetrics, 'agent.tool_call.duration');
  const spans = otlpSpans(scenario.replayFiles);
  const skillSpans = spans.filter(span => otlpAttributes(span)['agentic.tool.type'] === 'skill');
  const turn = spans.find(span => otlpAttributes(span)['gen_ai.span.kind'] === 'CHAT');
  const tags = otlpAttributes(turn ?? {})['langfuse.trace.tags'];
  const cardinalityOk = toolCalls === physicalStarts && toolResults === physicalEnds
    && countMetrics === physicalEnds && durationMetrics === physicalEnds;
  const shapeOk = scenario.id === 'skill-command'
    ? skillSpans.length === 1 && otlpAttributes(skillSpans[0])['agentic.tool.derived'] !== true
    : skillSpans.length === 1 && otlpAttributes(skillSpans[0])['agentic.tool.derived'] === true && (skillSpans[0].links?.length ?? 0) === 1;
  const tagOk = Array.isArray(tags) && tags.some(tag => typeof tag === 'string' && tag.startsWith('skill:'));
  return {
    valid: cardinalityOk && shapeOk && tagOk,
    evidence: [
      `physical_tools=${physicalStarts}/${physicalEnds}`,
      `replay_tool_events=${toolCalls}/${toolResults}`,
      `tool_count_metric_total=${countMetrics}`,
      `tool_duration_metric_total=${durationMetrics}`,
      `skill_spans=${skillSpans.length}`,
      `skill_shape=${shapeOk}`,
      `turn_skill_tag=${tagOk}`,
    ],
  };
}

function endpointIdentity(value: string): string {
  const endpoint = new URL(value);
  if (endpoint.hostname === 'localhost') endpoint.hostname = '127.0.0.1';
  endpoint.hash = ''; endpoint.search = '';
  return endpoint.toString().replace(/\/+$/, '');
}

function acquireLock(path: string): number {
  const open = (): number => {
    const fd = openSync(path, 'wx', 0o600);
    writeFileSync(fd, `${JSON.stringify({ pid: process.pid, endpoint: endpointIdentity(baseUrl), startedAt: new Date().toISOString() })}\n`);
    return fd;
  };
  try { return open(); } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
    let owner = 0;
    try { owner = Number((JSON.parse(readFileSync(path, 'utf8')) as { pid?: number }).pid ?? 0); } catch { /* malformed lock is stale */ }
    if (owner > 0) {
      try { process.kill(owner, 0); throw new RunError('harness-error', `another Golden evaluator (pid ${owner}) holds ${path}`); }
      catch (probeError) {
        if (probeError instanceof RunError) throw probeError;
        if ((probeError as NodeJS.ErrnoException).code !== 'ESRCH') throw new RunError('harness-error', `cannot verify Golden lock owner ${owner}`);
      }
    }
    unlinkSync(path);
    return open();
  }
}

function inspectTranscript(files: string[], scenarioId: string): { valid: boolean; evidence: string[]; integrity: NonNullable<ScenarioResult['integrity']> } {
  const events = canonicalEvents(files);
  const errors: string[] = [];
  let previous = '0'.repeat(64);
  let sequence = 0;
  for (const event of events) {
    const { checksum, ...base } = event;
    const expected = canonicalChecksum(base);
    if (event.sequence !== sequence + 1) errors.push(`sequence gap before ${event.sequence}`);
    if (event.previous_checksum !== previous || checksum !== expected) errors.push(`checksum mismatch at ${event.sequence}`);
    sequence = event.sequence; previous = checksum;
  }
  if (!events.length || events[0]?.hook !== 'session_start') errors.push('transcript does not start with session_start');
  if (events.at(-1)?.hook !== 'session_shutdown') errors.push('transcript does not end with session_shutdown');
  for (const file of files) {
    try {
      const seal = JSON.parse(readFileSync(`${file}.integrity.json`, 'utf8')) as CollectionIntegrity;
      if (!seal.complete) errors.push(`writer marked ${basename(file)} incomplete: ${seal.errors.join('; ')}`);
      if (seal.lastSequence !== sequence || seal.lastChecksum !== previous || seal.expectedSequence !== sequence || seal.expectedChecksum !== previous) errors.push(`integrity seal mismatch for ${basename(file)}`);
    } catch { errors.push(`missing integrity seal for ${basename(file)}`); }
  }
  const toolEnds = events.filter(event => event.hook === 'tool_execution_end');
  const successfulRead = toolEnds.some(event => event.payload.toolName === 'read' && event.payload.isError !== true);
  const failedTool = toolEnds.some(event => event.payload.isError === true);
  const completedMessage = events.some(event => event.hook === 'message_end' && (event.payload.message as Record<string, unknown> | undefined)?.stopReason === 'stop');
  const semanticOk = scenarioId === 'basic-llm' ? completedMessage
    : scenarioId === 'tool-success' ? successfulRead
    : scenarioId === 'tool-error' ? failedTool
    : scenarioId === 'multi-tool' ? toolEnds.length >= 3
    : scenarioId === 'skill-command' ? events.some(event => event.hook === 'before_agent_start' && typeof (event.payload.skillInvocation as Record<string, unknown> | undefined)?.name === 'string')
    : scenarioId === 'skill-conversation' ? events.some(event => event.hook === 'tool_execution_start' && typeof (event.payload.skillCandidate as Record<string, unknown> | undefined)?.name === 'string')
    : true;
  const evidence = [`events=${events.length}`, `tool_ends=${toolEnds.length}`, `completed_message=${completedMessage}`, `semantic_assertion=${semanticOk}`];
  return { valid: errors.length === 0 && semanticOk, evidence, integrity: { complete: errors.length === 0, lastSequence: sequence, lastChecksum: previous, expectedSequence: sequence, expectedChecksum: previous, errors } };
}

async function main(): Promise<void> {
  const reuseRun = process.env.PIREPLAY_GOLDEN_REUSE_RUN;
  const generatedRunId = `${new Date().toISOString().replace(/[:.]/g, '-')}-${process.pid}`;
  const aivisionHome = process.env.AIVISION_HOME?.trim() || join(homedir(), '.aivision');
  const runDir = resolve(
    reuseRun
      ?? process.env.PIREPLAY_GOLDEN_RUN_DIR
      ?? join(aivisionHome, 'pi', 'golden', 'runs', generatedRunId),
  );
  const transcriptDir = join(runDir, 'transcripts'); const realtimeDir = join(runDir, 'realtime'); const replayDir = join(runDir, 'replays'); const logDir = join(runDir, 'logs');
  for (const dir of [transcriptDir, realtimeDir, replayDir, logDir]) mkdirSync(dir, { recursive: true, mode: 0o700 });
  const manifestPath = join(runDir, 'manifest.json');
  const manifest: Manifest = reuseRun
    ? JSON.parse(readFileSync(manifestPath, 'utf8')) as Manifest
    : { runId: generatedRunId, result: 'harness-error', startedAt: new Date().toISOString(), baseUrl, integrity: 'complete', scenarios: [], replayFiles: [] };
  const runId = manifest.runId;
  manifest.result = 'harness-error'; delete manifest.blocker; delete manifest.endedAt;
  writeJson(manifestPath, manifest);
  const lockHash = createHash('sha256').update(endpointIdentity(baseUrl)).digest('hex').slice(0, 20);
  const lock = join(tmpdir(), `aivision-golden-${lockHash}.lock`);
  let lockFd: number | undefined; let weaver: ChildProcess | undefined;
  try {
    lockFd = acquireLock(lock);
    if (!existsSync(cli)) throw new RunError('harness-error', 'Pi CLI dependency is not installed; run make build');
    weaver = await ensureWeaver(logDir);

    if (!reuseRun) {
      const preflightDir = join(runDir, 'preflight');
      const preflightTranscripts = join(preflightDir, 'transcripts');
      const preflightReplays = join(preflightDir, 'replays');
      const preflightRealtime = join(preflightDir, 'realtime');
      for (const dir of [preflightTranscripts, preflightReplays, preflightRealtime]) mkdirSync(dir, { recursive: true, mode: 0o700 });
      const nodeVersion = spawnSync(process.execPath, ['--version'], { encoding: 'utf8' });
      const piVersion = spawnSync(process.execPath, [cli, '--version'], { cwd: packageRoot, encoding: 'utf8', timeout: 30_000 });
      writeJson(join(preflightDir, 'environment.json'), { node: nodeVersion.stdout.trim(), pi: piVersion.stdout.trim(), weaverReady: await ready(), openclawConfig: resolve(process.env.OPENCLAW_CONFIG ?? join(process.env.HOME ?? '', '.openclaw', 'openclaw.json')) });
      if (nodeVersion.status !== 0 || piVersion.status !== 0) throw new RunError('harness-error', 'Node or Pi version preflight failed');
      const MAX_RATE_LIMIT_RETRIES = Number(process.env.PIREPLAY_GOLDEN_MAX_RETRIES ?? 4);
      const RETRY_DELAY_MS = Number(process.env.PIREPLAY_GOLDEN_RETRY_DELAY_MS ?? 15_000);
      // Preflight demo: retry on transient rate limits so a cold provider
      // throttle does not block the entire Golden run.
      let preflightCode = 1;
      for (let attempt = 0; attempt <= MAX_RATE_LIMIT_RETRIES; attempt++) {
        if (attempt > 0) { console.error(`[preflight] retry ${attempt}/${MAX_RATE_LIMIT_RETRIES} after ${RETRY_DELAY_MS}ms`); await new Promise(r => setTimeout(r, RETRY_DELAY_MS)); }
        const preflightLog = join(logDir, 'preflight-demo.log');
        const preflightBefore = new Set(listJsonl(preflightTranscripts));
        const preflightRealtimeBefore = new Set(listJsonl(preflightRealtime));
        preflightCode = runNode(demo, { ...process.env, OBSERV_PI_SESSION_NAME: 'preflight-demo', PIREPLAY_SESSION_NAME: 'preflight-demo', PIREPLAY_SESSION_DIR: preflightTranscripts, OBSERV_PI_REPLAY_DIR: preflightRealtime, OBSERV_PI_DEMO_SEED_README: '1', OBSERV_PI_DEBUG: 'true' }, preflightLog);
        if (preflightCode === 0 || !isRateLimited(preflightLog)) break;
        dropNewTranscripts(preflightTranscripts, preflightBefore);
        dropNewTranscripts(preflightRealtime, preflightRealtimeBefore);
      }
      if (preflightCode !== 0) throw new RunError('external-blocker', `real-model demo failed with exit ${preflightCode}`);
      for (const transcript of listJsonl(preflightTranscripts)) {
        const output = join(preflightReplays, `spans_${basename(transcript)}`);
        const replay = spawnSync(process.execPath, [replayCli, transcript, '--otel', output], { cwd: packageRoot, env: { ...process.env, OBSERV_PI_DEBUG: 'false' }, encoding: 'utf8', timeout: 60_000 });
        if (replay.status !== 0) throw new RunError('harness-error', 'preflight demo replay failed');
      }
      const preflightValidate = spawnSync('make', ['-C', piRoot, 'validate', `REPLAY_DIR=${preflightReplays}`, 'FORMAT=json'], { encoding: 'utf8', timeout: 120_000 });
      writeFileSync(join(logDir, 'preflight-validate.log'), `${preflightValidate.stdout ?? ''}${preflightValidate.stderr ?? ''}`, { mode: 0o600 });
      if (preflightValidate.status !== 0) throw new RunError('semantic-fail', 'preflight demo validation failed');
      await requirePost('/api/clear');

      const probeFile = join(runDir, 'capabilities.json');
      for (const scenario of GOLDEN_SCENARIOS) {
      const startedAt = new Date().toISOString();
      const before = new Set(listJsonl(transcriptDir));
      const log = join(logDir, `${scenario.id}.log`);
      try { unlinkSync(log); } catch {}  // truncate per attempt
      if (scenario.optional) {
        const probe = probeCapability(probeFile, scenario.optional);
        if (probe.state === 'unsupported') { manifest.scenarios.push({ id: scenario.id, status: 'skipped', start: startedAt, end: new Date().toISOString(), evidence: probe.evidence, transcriptFiles: [], replayFiles: [], log }); writeJson(manifestPath, manifest); continue; }
        if (probe.state === 'indeterminate') throw new RunError('harness-error', `${scenario.id} capability probe indeterminate: ${probe.evidence.join('; ')}`);
      }
      let code = 1; let attempt = 0; let transcripts: string[] = [];
      for (attempt = 0; attempt <= MAX_RATE_LIMIT_RETRIES; attempt++) {
        if (attempt > 0) { console.error(`[${scenario.id}] retry ${attempt}/${MAX_RATE_LIMIT_RETRIES} after ${RETRY_DELAY_MS}ms (previous attempt rate-limited)`); await new Promise(r => setTimeout(r, RETRY_DELAY_MS)); }
        const attemptBefore = new Set(listJsonl(transcriptDir));
        const attemptRealtimeBefore = new Set(listJsonl(realtimeDir));
        code = runNode(demo, {
          ...process.env, OBSERV_PI_DEMO_PROMPT: scenario.prompt, OBSERV_PI_SESSION_NAME: `${runId}-${scenario.id}`, PIREPLAY_SESSION_NAME: `${runId}-${scenario.id}`,
          PIREPLAY_SESSION_DIR: transcriptDir, OBSERV_PI_REPLAY_DIR: realtimeDir, PIREPLAY_CAPABILITY_PROBE: probeFile,
          OBSERV_PI_DEMO_SEED_README: '1', OBSERV_PI_DEBUG: 'true'
        }, log);
        transcripts = listJsonl(transcriptDir).filter(file => !attemptBefore.has(file));
        // Stop retrying if the scenario succeeded or failed for a non-transient reason.
        if (code === 0 || !isRateLimited(log)) break;
        // Rate-limited: drop the partial transcript AND realtime OTLP so the
        // next attempt starts clean (otherwise realtime/replay parity breaks).
        dropNewTranscripts(transcriptDir, attemptBefore);
        dropNewTranscripts(realtimeDir, attemptRealtimeBefore);
      }
      const inspection = inspectTranscript(transcripts, scenario.id);
      const result: ScenarioResult = { id: scenario.id, status: code === 0 && transcripts.length > 0 && inspection.valid ? 'passed' : 'failed', start: startedAt, end: new Date().toISOString(), exitCode: code, evidence: [...inspection.evidence, attempt > 0 ? `retries=${attempt}` : ''], transcriptFiles: transcripts, replayFiles: [], log, integrity: inspection.integrity };
        manifest.scenarios.push(result); writeJson(manifestPath, manifest);
      }
    }

    if (manifest.scenarios.some(item => item.status === 'failed')) throw new RunError('semantic-fail', 'one or more required Golden scenarios failed');
    writeJson(join(runDir, 'realtime-summary.json'), {
      generatedAt: new Date().toISOString(),
      files: listJsonl(realtimeDir),
      counts: Object.fromEntries(['events', 'metrics', 'spans'].map(prefix => [prefix, listJsonl(realtimeDir).filter(file => basename(file).startsWith(`${prefix}_`)).length]))
    });
    rmSync(replayDir, { recursive: true, force: true });
    mkdirSync(replayDir, { recursive: true, mode: 0o700 });
    for (const transcript of listJsonl(transcriptDir)) {
      const output = join(replayDir, `spans_${basename(transcript)}`);
      const fd = openSync(join(logDir, 'replay.log'), 'a', 0o600);
      const result = spawnSync(process.execPath, [replayCli, transcript, '--otel', output], { cwd: packageRoot, env: { ...process.env, OBSERV_PI_DEBUG: 'false', OTEL_EXPORTER_OTLP_ENDPOINT: baseUrl }, stdio: ['ignore', fd, fd], timeout: 60_000 });
      closeSync(fd);
      if (result.status !== 0) { manifest.integrity = 'incomplete'; throw new RunError('harness-error', `replay failed for ${basename(transcript)}`); }
    }
    manifest.replayFiles = listJsonl(replayDir);
    for (const scenario of manifest.scenarios) {
      scenario.replayFiles = manifest.replayFiles.filter(replay => scenario.transcriptFiles.some(transcript => replay.endsWith(basename(transcript))));
      if (scenario.status === 'passed') {
        const skillInspection = inspectSkillReplay(scenario);
        scenario.evidence = [...new Set([...(scenario.evidence ?? []).filter(Boolean), ...skillInspection.evidence])];
        if (!skillInspection.valid) scenario.status = 'failed';
      }
    }
    writeJson(manifestPath, manifest);
    if (manifest.scenarios.some(item => item.status === 'failed')) throw new RunError('semantic-fail', 'one or more sealed replay assertions failed');
    if (!manifest.replayFiles.length) throw new RunError('harness-error', 'no replay files were produced');
    const parity = compareSemanticParity(listJsonl(realtimeDir), manifest.replayFiles);
    writeJson(join(runDir, 'parity.json'), parity);
    if (parity.equal !== true) throw new RunError('semantic-fail', 'realtime/replay semantic parity failed');
    const validate = spawnSync('make', ['-C', piRoot, 'validate', `REPLAY_DIR=${replayDir}`, 'FORMAT=json'], { encoding: 'utf8', timeout: 120_000 });
    writeFileSync(join(logDir, 'validate.log'), `${validate.stdout ?? ''}${validate.stderr ?? ''}`, { mode: 0o600 });
    if (validate.status !== 0) throw new RunError('semantic-fail', 'local Weaver validation failed');

    await requirePost('/api/clear');
    const report = spawnSync('python3', [join(repoRoot, 'scripts', 'report_check.py'), '--base-url', baseUrl, ...manifest.replayFiles], { encoding: 'utf8', timeout: 120_000 });
    writeFileSync(join(logDir, 'report-check.log'), `${report.stdout ?? ''}${report.stderr ?? ''}`, { mode: 0o600 });
    if (report.status !== 0) throw new RunError('external-blocker', 'POST replay failed');
    const response = await getJson('/api/violations?levels=all&limit=500');
    const findings = (Array.isArray(response.findings) ? response.findings : Array.isArray(response.items) ? response.items : []) as Finding[];
    const diagnosisPath = join(runDir, 'diagnosis.json'); writeJson(diagnosisPath, diagnose(findings)); manifest.diagnosis = diagnosisPath;
    const violations = Number(response.total_recent_violations ?? findings.filter(item => item.level === 'violation').length);
    if (violations !== 0) throw new RunError('semantic-fail', `AiVision reported ${violations} violation(s)`);
    manifest.result = 'pass';
  } catch (error) {
    const failure = error instanceof RunError ? error : new RunError('harness-error', error instanceof Error ? error.message : String(error));
    manifest.result = failure.result; manifest.blocker = failure.message;
  } finally {
    manifest.endedAt = new Date().toISOString(); writeJson(manifestPath, manifest);
    if (weaver?.pid) { try { process.kill(-weaver.pid, 'SIGTERM'); } catch {} }
    if (lockFd !== undefined) closeSync(lockFd);
    try { unlinkSync(lock); } catch {}
  }
  console.log(JSON.stringify({ runId, result: manifest.result, manifest: manifestPath }));
  process.exitCode = manifest.result === 'pass' ? 0 : manifest.result === 'semantic-fail' ? 10 : manifest.result === 'external-blocker' ? 20 : 30;
}

main().catch(error => { console.error(error); process.exit(30); });
