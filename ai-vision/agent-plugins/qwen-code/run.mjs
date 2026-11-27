/**
 * Hook entry point — invoked by qwen-code as a command hook.
 * Stays separate from telemetry.mjs so importing the library never
 * starts reading stdin. Failures are always non-disruptive by
 * contract: log to stderr and exit 0; the host's own behavior is
 * never altered. Output on stdout is plain text (not JSON) so it is
 * never added to the model context.
 */

import { join, dirname } from 'node:path';

import { recordEvent, sessionRoot, finalizeSession } from './telemetry.mjs';

const terminalEvent = 'SessionEnd';

async function readStdin() {
  return new Promise(resolve => {
    let data = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', chunk => { data += chunk; });
    process.stdin.on('end', () => resolve(data));
    process.stdin.on('error', () => resolve(data));
  });
}

async function main() {
  const event = JSON.parse(await readStdin());
  const name = await recordEvent(event);
  if (name === terminalEvent) {
    const ingestConfigPath = process.env.AIVISION_QWEN_INGEST_CONFIG
      ?? join(dirname(sessionRoot()), 'ingest-config.json');
    const requestedMode = process.env.AIVISION_COLLECTION_MODE ?? 'privacy';
    // Write nothing to stdout: plain JSON on stdout could be interpreted as
    // hook output. stderr is for directed diagnostics only.
    const trace = await finalizeSession(event.session_id, ingestConfigPath, requestedMode);
    // One trace per turn: report each turn trace as it was shipped.
    for (const turnTrace of trace) {
      console.error(`AI Vision Trace ${turnTrace.traceId} turn=${turnTrace.number} (${turnTrace.status})`);
    }
  }
}

main().catch(error => {
  console.error('AI Vision qwen-code telemetry: ' + (error?.stack ?? error));
});
