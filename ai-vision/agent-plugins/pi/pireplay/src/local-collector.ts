export const REPLAY_OTLP_HTTP_BASE = 'http://127.0.0.1:4319' as const;

export type ReplayOtlpSignal = 'traces' | 'logs' | 'metrics';

export function replayOtlpUrl(signal: ReplayOtlpSignal): string {
  return `${REPLAY_OTLP_HTTP_BASE}/v1/${signal}`;
}
