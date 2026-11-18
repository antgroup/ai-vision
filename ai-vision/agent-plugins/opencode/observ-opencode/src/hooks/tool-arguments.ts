import type { ObservOpencodeConfig } from '../config.js';
import { isPrivacyMode, REDACTED, stringifyForExport } from '../privacy.js';
import type { ToolArgumentSnapshot } from './types.js';
import { truncateWithMeta } from './state.js';

export function snapshotToolArguments(
  config: ObservOpencodeConfig,
  args: unknown,
): ToolArgumentSnapshot {
  if (args === undefined || args === null) {
    return { value: '{}', truncated: false, originalLength: 2 };
  }
  if (isPrivacyMode(config) || (!config.log.spans.toolDetails && !config.log.events.toolDetails)) {
    return { value: REDACTED, truncated: false, originalLength: REDACTED.length };
  }
  const result = truncateWithMeta(stringifyForExport(args));
  return { value: result.text, truncated: result.truncated, originalLength: result.originalLength };
}

export function toolArgumentValueFor(
  config: ObservOpencodeConfig,
  snapshot: ToolArgumentSnapshot,
  signal: 'span' | 'event',
): string {
  const enabled = signal === 'span'
    ? config.log.spans.toolDetails
    : config.log.events.toolDetails;
  return enabled ? snapshot.value : REDACTED;
}
