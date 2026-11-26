import { execFile, spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdir, mkdtemp, readFile, rm } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { createInterface } from 'node:readline';
import { promisify } from 'node:util';

import { readRows, readSessionRows } from './history.mjs';
import { buildTrace, exportTrace } from './telemetry.mjs';

const CANCELLATION_GRACE_MS = 65_000;
const SUPPORTED_VERSION = '0.7.2';
const signals = ['SIGINT', 'SIGTERM'];
const modelDiagnostics = new Set([
  'assistant_completed', 'model_phase_started', 'model_phase_finished', 'operation_finished',
]);

function settings(argv, env) {
  let ingestConfig = env.AIVISION_MINIMAX_INGEST_CONFIG;
  let mcode = env.AIVISION_MINIMAX_MCODE || 'mcode';
  let collectionMode;
  const args = [...argv];
  while (['--ingest-config', '--mcode', '--collection-mode'].includes(args[0])) {
    const flag = args.shift();
    const value = args.shift();
    if (!value || value.startsWith('--')) throw new Error(`Missing value for ${flag}`);
    if (flag === '--ingest-config') ingestConfig = value;
    if (flag === '--mcode') mcode = value;
    if (flag === '--collection-mode') collectionMode = value;
  }
  if (args[0] === '--') args.shift();
  if (args[0] === 'exec') args.shift();
  if (!ingestConfig) throw new Error('Use the installed MiniMax Code launcher or provide --ingest-config');
  const profile = env.MINIMAX_DATA_DIR || env.MAVIS_DATA_DIR;
  if (!profile) throw new Error('The MiniMax Code data directory must be selected by the installer');
  const userOptions = args.slice(0, args.includes('--') ? args.indexOf('--') : args.length);
  if (userOptions.some(arg => /^--(?:output-format|diagnostics-dir|profile|lane)(?:=|$)/.test(arg))) {
    throw new Error('The launcher manages output format, diagnostics and the selected MiniMax Code profile');
  }
  return { ingestConfig: resolve(ingestConfig), mcode, profile: resolve(profile), collectionMode, args };
}

async function captureExec({ mcode, args, profile }, diagnosticsPath) {
  const observedStartTimeMs = Date.now();
  const child = spawn(mcode, [
    'exec', '--output-format', 'stream-json', '--diagnostics-dir', diagnosticsPath, ...args,
  ], {
    detached: true,
    stdio: ['pipe', 'pipe', 'inherit'],
    env: { ...process.env, MINIMAX_DATA_DIR: profile },
  });

  const inputFlag = args.some(arg => arg === '--input' || arg.startsWith('--input='));
  if (inputFlag) {
    child.stdin.on('error', error => {
      if (error.code !== 'EPIPE') console.error('Could not forward MiniMax Code stdin.');
    });
    process.stdin.pipe(child.stdin);
  } else {
    child.stdin.end();
  }

  let requestedSignal;
  let killTimer;
  function forwardSignal(signal) {
    if (requestedSignal || child.exitCode !== null || child.signalCode || !child.pid) return;
    requestedSignal = signal;
    // A single SIGTERM gives mcode time to commit the cancelled turn and emit exec.completed.
    child.kill('SIGTERM');
    killTimer = setTimeout(() => {
      try {
        process.kill(-child.pid, 'SIGKILL');
      } catch (error) {
        if (error.code !== 'ESRCH') console.error('Could not stop the MiniMax Code process group.');
      }
    }, CANCELLATION_GRACE_MS);
    killTimer.unref();
  }

  for (const signal of signals) process.on(signal, forwardSignal);
  let stdoutClosed = false;
  function closeStdout(error) {
    if (stdoutClosed) return;
    stdoutClosed = true;
    if (error.code !== 'EPIPE') {
      console.error(`MiniMax Code output: ${error.message}`);
      process.exitCode ||= 1;
    }
  }
  // A pending write can fail even after capture ends and while the trace exports.
  process.stdout.on('error', closeStdout);
  const events = [];
  const consume = async () => {
    for await (const line of createInterface({ input: child.stdout, crlfDelay: Infinity })) {
      const observedTimeMs = Date.now();
      if (!stdoutClosed) {
        try {
          if (!process.stdout.write(line + '\n')) await once(process.stdout, 'drain');
        } catch (error) {
          closeStdout(error);
        }
      }
      try {
        const event = JSON.parse(line);
        if (event?.schemaVersion === 1) events.push({ ...event, observedTimeMs });
      } catch (error) {
        if (!(error instanceof SyntaxError)) throw error;
      }
    }
  };

  try {
    const [[code, signal]] = await Promise.all([once(child, 'close'), consume()]);
    return {
      events, observedStartTimeMs, observedEndTimeMs: Date.now(),
      termination: { code, signal, requestedSignal },
    };
  } finally {
    clearTimeout(killTimer);
    if (inputFlag) {
      process.stdin.unpipe(child.stdin);
      process.stdin.pause();
    }
    for (const signal of signals) process.removeListener(signal, forwardSignal);
  }
}

async function run() {
  const options = settings(process.argv.slice(2), process.env);
  const { collection_mode: savedMode = 'privacy' } = JSON.parse(await readFile(options.ingestConfig, 'utf8'));
  const requestedMode = process.env.AIVISION_COLLECTION_MODE ?? process.env.MINIMAX_CODE_COLLECTION_MODE
    ?? options.collectionMode ?? savedMode;
  const collectionMode = typeof requestedMode === 'string' ? requestedMode.trim().toLowerCase() : undefined;
  if (!['full', 'privacy'].includes(collectionMode)) throw new Error('Invalid collection mode');

  const version = await promisify(execFile)(options.mcode, ['--version'], { timeout: 5_000 });
  if (version.stdout.trim() !== SUPPORTED_VERSION) {
    throw new Error(`This PoC requires MiniMax Code ${SUPPORTED_VERSION}`);
  }

  const instanceDir = join(options.profile, 'aivision', 'minimax-code');
  await mkdir(instanceDir, { recursive: true, mode: 0o700 });
  const diagnosticsPath = await mkdtemp(join(instanceDir, 'exec-'));
  try {
    const execution = await captureExec(options, diagnosticsPath);
    const { termination } = execution;
    const hostExitCode = termination.code ?? (termination.requestedSignal === 'SIGINT' ? 130 : 143);
    if (hostExitCode) process.exitCode = hostExitCode;
    const sessionId = execution.events.find(event => typeof event.sessionId === 'string')?.sessionId;
    const rows = sessionId ? await readSessionRows(options.profile, sessionId) : [];
    const diagnostics = (await readRows(join(diagnosticsPath, 'progress.jsonl')))
      .filter(event => modelDiagnostics.has(event.kind));
    const trace = buildTrace({ ...execution, rows, diagnostics, collectionMode });
    if (trace) {
      await exportTrace(trace, options.ingestConfig);
      console.error(`AI Vision Trace ${trace.traceId} (${trace.status})`);
    } else if (sessionId) {
      console.error('AI Vision did not find a started MiniMax Code turn to export.');
    }
  } finally {
    await rm(diagnosticsPath, { recursive: true, force: true });
  }
}

try {
  await run();
} catch (error) {
  console.error(`MiniMax Code telemetry: ${error.message}`);
  process.exitCode ||= 1;
}
