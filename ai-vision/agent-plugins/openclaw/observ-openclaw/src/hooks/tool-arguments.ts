import { softTruncate, SOFT_TRUNCATE_LIMIT } from './truncate.js';

export interface ToolArgumentSnapshot {
  value: string;
  truncated: boolean;
  originalLength: number;
}

/** Serialize and bound arguments once, before projecting them onto each signal. */
export function snapshotToolArguments(
  params: unknown,
  captureRequested: boolean,
): ToolArgumentSnapshot {
  if (!captureRequested) {
    return { value: '[REDACTED]', truncated: false, originalLength: '[REDACTED]'.length };
  }
  if (params === undefined || params === null) {
    return { value: '{}', truncated: false, originalLength: 2 };
  }
  try {
    const serialized = JSON.stringify(params, null, 2);
    return {
      value: softTruncate(serialized),
      truncated: serialized.length > SOFT_TRUNCATE_LIMIT,
      originalLength: serialized.length,
    };
  } catch {
    return { value: '[REDACTED]', truncated: false, originalLength: '[REDACTED]'.length };
  }
}

export function toolArgumentValueFor(
  snapshot: ToolArgumentSnapshot,
  enabled: boolean,
): string {
  return enabled ? snapshot.value : '[REDACTED]';
}
