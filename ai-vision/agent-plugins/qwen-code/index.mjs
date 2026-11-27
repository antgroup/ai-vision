/**
 * Module surface used by build.mjs tests and the finalizeSession path.
 * Re-exported so dist/index.js matches the published entrypoint contract.
 */

export {
  buildTrace, ledgerPath, sessionRoot, recordEvent, requireEvent,
  readTranscriptUsage, pairToolEvents,
} from './telemetry.mjs';
