import type { SpanProcessor } from '@opentelemetry/sdk-trace-base';
import type { LogRecordProcessor } from '@opentelemetry/sdk-logs';

export interface TelemetryProcessorContribution {
  spanProcessors?: SpanProcessor[];
  logRecordProcessors?: LogRecordProcessor[];
}

const registryKey = Symbol.for('observ-openclaw.telemetry-processor-contributions');

function registry(): Map<string, TelemetryProcessorContribution> {
  const globalValue = globalThis as typeof globalThis & {
    [registryKey]?: Map<string, TelemetryProcessorContribution>;
  };
  globalValue[registryKey] ??= new Map<string, TelemetryProcessorContribution>();
  return globalValue[registryKey];
}

export function registerTelemetryProcessorContribution(
  id: string,
  contribution: TelemetryProcessorContribution,
): () => void {
  registry().set(id, contribution);
  return () => registry().delete(id);
}

export function getTelemetryProcessorContributions(): TelemetryProcessorContribution[] {
  return [...registry().values()];
}
