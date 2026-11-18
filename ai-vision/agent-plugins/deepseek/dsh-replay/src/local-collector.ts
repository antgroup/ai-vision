/**
 * The only network destination a replay may write to.
 *
 * Replay never reads production endpoint/credential configuration. `--emit`
 * uploads the generated OTLP JSONL file to the fixed local collector with
 * standard OTLP signal paths.
 */

export const REPLAY_OTLP_HTTP_BASE = 'http://127.0.0.1:4319';

export function localCollectorUrl(signal: 'traces' | 'metrics' | 'logs'): string {
  return `${REPLAY_OTLP_HTTP_BASE}/v1/${signal}`;
}

export async function emitToLocalCollector(filePath: string, signal: 'traces' | 'metrics' | 'logs'): Promise<void> {
  const { readFileSync } = await import('node:fs');
  const lines = readFileSync(filePath, 'utf8').split('\n').filter(line => line.trim() !== '');
  for (const line of lines) {
    const response = await fetch(localCollectorUrl(signal), {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: line,
    });
    if (!response.ok) {
      throw new Error(`local collector ${signal} upload failed: HTTP ${response.status}`);
    }
  }
}
