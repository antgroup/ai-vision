#!/usr/bin/env node
/**
 * pireplay demo runner — drives a real pi session with BOTH the piagent
 * (real-time OTLP telemetry) and pireplay (canonical transcript recording)
 * extensions loaded, so one run produces durable transcripts (pireplay) AND
 * realtime OTLP files (piagent DEBUG) for golden parity comparison.
 *
 * Real model + API key are read from ~/.openclaw/openclaw.json (same model
 * bridge as the original piagent demo). The credential is passed to pi only
 * via the child env; the agent dir's models.json contains only a
 * `$OBSERV_PI_DEMO_API_KEY` reference.
 *
 * Output (set by the caller via env):
 *   PIREPLAY_SESSION_DIR    canonical transcript output
 *   OBSERV_PI_REPLAY_DIR      piagent DEBUG realtime OTLP files (parity reference)
 *   OBSERV_PI_SESSION_NAME / PIREPLAY_SESSION_NAME  artifact labels
 */
import { mkdtempSync, rmSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createPiModelConfig } from './model-bridge.js';

class DemoRunError extends Error {}

function run(command: string, args: string[], env: NodeJS.ProcessEnv, cwd: string, timeoutMs: number): Promise<number> {
  return new Promise((resolveExit, reject) => {
    const child = spawn(command, args, { cwd, env, stdio: 'inherit' });
    const onSignal = (signal: NodeJS.Signals) => { child.kill(signal); reject(new DemoRunError(`demo interrupted by ${signal}`)); };
    const cleanup = () => { process.off('SIGINT', onSignal); process.off('SIGTERM', onSignal); };
    process.once('SIGINT', onSignal); process.once('SIGTERM', onSignal);
    const timer = setTimeout(() => { child.kill('SIGTERM'); reject(new DemoRunError(`demo timed out after ${timeoutMs}ms`)); }, timeoutMs);
    child.once('error', () => { clearTimeout(timer); cleanup(); reject(new DemoRunError('could not start Pi')); });
    child.once('exit', (code) => { clearTimeout(timer); cleanup(); resolveExit(code ?? 1); });
  });
}

export async function runDemo(): Promise<void> {
  const root = mkdtempSync(join(tmpdir(), 'pireplay-demo-'));
  const agentDir = join(root, 'agent');
  const sessionDir = join(root, 'sessions');
  const cwd = join(root, 'workspace');
  mkdirSync(sessionDir, { recursive: true }); mkdirSync(cwd, { recursive: true });
  if (process.env.OBSERV_PI_DEMO_SEED_README === '1') {
    const { writeFileSync } = await import('node:fs');
    writeFileSync(join(cwd, 'README.md'), '# Piagent Golden Fixture\n\nThis file has three lines.\n', { mode: 0o600 });
  }
  const here = fileURLToPath(new URL('../../..', import.meta.url)); // pireplay root (dist/src/demo/ -> pireplay/)
  // observ-pi is a file dependency of pireplay; load its self-contained package entry.
  const piagentPath = join(here, 'node_modules', '@observ', 'pi', 'index.js');
  const pireplayPath = join(here, 'dist', 'src', 'index.js');
  const cli = join(here, 'node_modules', '@earendil-works', 'pi-coding-agent', 'dist', 'cli.js');
  try {
    const configPath = resolve(process.env.OPENCLAW_CONFIG ?? join(process.env.HOME ?? '', '.openclaw', 'openclaw.json'));
    const bridge = createPiModelConfig(configPath, agentDir);
    const noProxy = [process.env.NO_PROXY, bridge.baseUrl ? new URL(bridge.baseUrl).hostname : undefined].filter(Boolean).join(',');
    const code = await run(process.execPath, [
      cli, '-p', '--model', bridge.modelRef,
      '-e', piagentPath, '-e', pireplayPath,
      '--session-dir', sessionDir,
      process.env.OBSERV_PI_DEMO_PROMPT ?? '你好，用一句话介绍你自己。'
    ], {
      ...process.env,
      AGENT_ENABLE_TELEMETRY: process.env.AGENT_ENABLE_TELEMETRY ?? 'true',
      OTEL_EXPORTER_OTLP_PROTOCOL: undefined,
      OTEL_EXPORTER_OTLP_HEADERS: undefined,
      OTEL_EXPORTER_OTLP_TRACES_ENDPOINT: undefined,
      OTEL_EXPORTER_OTLP_METRICS_ENDPOINT: undefined,
      OTEL_EXPORTER_OTLP_LOGS_ENDPOINT: undefined,
      NO_PROXY: noProxy,
      PI_CODING_AGENT_DIR: agentDir,
      OBSERV_PI_DEMO_API_KEY: bridge.apiKey,
      OBSERV_PI_SESSION_NAME: process.env.OBSERV_PI_SESSION_NAME ?? 'demo',
      PIREPLAY_SESSION_NAME: process.env.PIREPLAY_SESSION_NAME ?? process.env.OBSERV_PI_SESSION_NAME ?? 'demo',
      PIREPLAY_CAPTURE_ENABLED: process.env.PIREPLAY_CAPTURE_ENABLED ?? 'true',
      OBSERV_PI_REPLAY_DIR: process.env.OBSERV_PI_REPLAY_DIR ?? join(root, 'replays'),
      OBSERV_PI_DEBUG: process.env.OBSERV_PI_DEBUG ?? 'true',
      PIREPLAY_SESSION_DIR: process.env.PIREPLAY_SESSION_DIR ?? join(root, 'transcripts'),
      PIREPLAY_CAPABILITY_PROBE: process.env.PIREPLAY_CAPABILITY_PROBE ?? '',
    }, cwd, Number(process.env.OBSERV_PI_DEMO_TIMEOUT_MS ?? 120_000));
    if (code !== 0) throw new DemoRunError(`pi exited with code ${code}`);
  } finally {
    if (process.env.KEEP_DEMO_ARTIFACTS !== '1') rmSync(root, { recursive: true, force: true });
    else console.error(`[pireplay] retained redacted demo root: ${root}`);
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  runDemo().catch((error) => {
    // Configuration and provider errors can contain excerpts of credential-bearing input.
    const detail = error instanceof DemoRunError ? error.message : 'check the demo model configuration and Pi setup';
    console.error(`[pireplay] demo failed: ${detail}`);
    process.exit(1);
  });
}
