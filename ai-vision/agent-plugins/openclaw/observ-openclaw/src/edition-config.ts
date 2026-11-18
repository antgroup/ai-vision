/**
 * Edition configuration resolution.
 *
 * Edition config is embedded at build time by scripts/generate-keys.ts from
 * configs/{edition}.config.json. The supported editions are common and aivision,
 * built via `EDITION=xxx npm run build` (see openclaw/Makefile targets).
 *
 * The common edition embeds configs/common.config.json when available.
 */

import { EMBEDDED_CONFIG, EMBEDDED_EDITION } from './edition-config.generated.js';

// ─── EditionConfig interface ─────────────────────────────────────────────────

export interface SignalEditionConfig {
  endpoint?: string;
  headers?: Record<string, string>;
  enabled?: boolean;
}

export interface EditionConfig {
  enableTelemetry?: boolean;
  serviceName?: string;
  collectionMode?: string;
  otelExporter?: {
    protocol?: string;
    metricsTemporalityPreference?: string;
    attributes?: Record<string, string>;
    resourceAttributes?: Record<string, string>;
  };
  /** Default traces signal config */
  tracesConfig?: SignalEditionConfig;
  /** Default metrics signal config */
  metricsConfig?: SignalEditionConfig;
  /** Default events/logs signal config */
  eventsConfig?: SignalEditionConfig;
}

export type EditionName = 'common' | 'aivision';

// ─── Public API ──────────────────────────────────────────────────────────────

/** Returns the build-time embedded edition config. */
export function resolveEditionConfig(): EditionConfig {
  return EMBEDDED_CONFIG as EditionConfig;
}

/** Returns the explicit build-time edition instead of inferring it from endpoints. */
export function resolveEditionName(): EditionName {
  return EMBEDDED_EDITION as EditionName;
}

/** True only for the fully managed personal AIVision distribution. */
export function isAivisionEdition(): boolean {
  return resolveEditionName() === 'aivision';
}
