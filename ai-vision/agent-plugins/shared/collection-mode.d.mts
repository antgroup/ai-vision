export function resolveCollectionMode(configured: unknown, legacyName: string, env?: Record<string, string | undefined>): { mode: 'full' | 'privacy'; valid: boolean };
export function privacyAttributes<T extends Record<string, unknown>>(attributes?: T): Record<string, unknown>;
export function privacyProcessor<T extends object>(processor: T, mode: string, signal: 'traces' | 'logs'): T;
export function privacyMetricExporter<T extends object>(exporter: T, mode: string): T;
