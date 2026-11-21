import type { DshAssistantStreamRecord } from './types.js';

function hasFragment(value: unknown): boolean {
  return typeof value === 'string' && value.length > 0;
}

/** Follow the host's Trajectory view when locating the first output delta. */
export function firstOutputTime(stream: readonly DshAssistantStreamRecord[]): number | undefined {
  for (const record of stream) {
    if (!record || typeof record !== 'object') return undefined;
    if (record.type === 'chunk') {
      const { chunk } = record;
      if (!chunk || typeof chunk.type !== 'string') return undefined;
      if ((chunk.type === 'text-delta' || chunk.type === 'reasoning-delta') && hasFragment(chunk.text)) {
        return record.time;
      }
      // DSH counts the presence of a tool name even when it is empty.
      if (chunk.type === 'tool-call-delta' && (chunk.name !== undefined || hasFragment(chunk.argumentsDelta))) {
        return record.time;
      }
      continue;
    }
    if (record.type === 'tool-call-chunks' && record.name !== undefined) return record.time0;

    const fragments = record.type === 'tool-call-chunks' ? record.args : record.texts;
    if (!Array.isArray(fragments) || !Array.isArray(record.dt)) return undefined;
    const first = fragments.findIndex(hasFragment);
    if (first === -1) continue;

    let time = record.time0;
    if (!Number.isSafeInteger(time)) return undefined;
    // DSH records wall-clock timestamps, which can move backwards between chunks.
    for (let index = 0; index < first; index++) {
      const delta = record.dt[index];
      if (delta === undefined || !Number.isSafeInteger(delta)) return undefined;
      time += delta;
      if (!Number.isSafeInteger(time)) return undefined;
    }
    return time;
  }
  return undefined;
}
