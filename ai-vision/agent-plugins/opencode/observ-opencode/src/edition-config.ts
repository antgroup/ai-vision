/**
 * Runtime access to embedded edition configuration.
 *
 * The config data is generated at build time by scripts/generate-keys.ts
 * from configs/{edition}.config.json. Non-sensitive values are embedded
 * as plaintext (no XOR needed).
 */

import {
  HAS_EMBEDDED_CONFIG,
  EMBEDDED_CONFIG,
  EMBEDDED_METADATA,
} from './edition-config.generated.js';

export interface EditionConfig {
  enableTelemetry?: boolean;
  collectionMode?: string;
  serviceName?: string;
  otelExporter?: {
    protocol?: string;
    metricsTemporalityPreference?: string;
    attributes?: Record<string, string>;
    resourceAttributes?: Record<string, string>;
  };
  tracesConfig?: {
    endpoint?: string;
    headers?: Record<string, string>;
    enabled?: boolean;
  };
  eventsConfig?: {
    endpoint?: string;
    headers?: Record<string, string>;
    enabled?: boolean;
  };
  metricsConfig?: {
    endpoint?: string;
    headers?: Record<string, string>;
    enabled?: boolean;
  };
}

export interface EditionMetadata {
  /** Internal config-path metadata; never passed to parseConfig. */
  standaloneConfigDir?: string;
}

/**
 * Returns the embedded edition config, or null for common edition.
 */
export function resolveEditionConfig(): EditionConfig | null {
  if (!HAS_EMBEDDED_CONFIG || !EMBEDDED_CONFIG) return null;
  return EMBEDDED_CONFIG as EditionConfig;
}

export function resolveEditionMetadata(): EditionMetadata | null {
  return EMBEDDED_METADATA as EditionMetadata | null;
}
