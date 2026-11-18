import { resolveCollectionMode } from '../../../shared/collection-mode.mjs';
/**
 * Plugin configuration parsing and validation.
 *
 * Follows the AI Vision Agent Observability Specification v2.0.
 * Each config item supports both environment variables (UPPER_SNAKE_CASE)
 * and programmatic config (camelCase with dot-separated prefixes).
 *
 * @see docs/standard/ant/conventions.md
 */

import { isAivisionEdition, resolveEditionConfig } from './edition-config.js';
import { resolveLogDir } from './logger.js';
import { InternalAttributes } from './semconv.js';

// ─── Signal-specific endpoint configuration ──────────────────────────────────

export interface SignalEndpointConfig {
  /** OTLP endpoint URL for this signal */
  endpoint?: string;
  /** Custom headers for this signal */
  headers?: Record<string, string>;
  /** Enable/disable this signal */
  enabled: boolean;
}

// ─── Content logging controls ────────────────────────────────────────────────

export type CollectionMode = 'full' | 'privacy';

/** Per-signal content logging switches (5 switches × 2 signal types). */
export interface ContentLoggingSwitches {
  /** Record user prompt text. Default: false */
  userPrompts: boolean;
  /** Record system prompt / system instructions. Default: false */
  systemPrompts: boolean;
  /** Record model output text. Default: false */
  modelOutputs: boolean;
  /** Record tool call parameters and MCP/Skill names. Default: false */
  toolDetails: boolean;
  /** Record tool execution result text. Default: false */
  toolOutputs: boolean;
}

/**
 * Internal content logging switches derived from collectionMode.
 * These are intentionally not a public configuration surface.
 */
export interface ContentLoggingConfig {
  /** Controls content included in OTel event bodies */
  events: ContentLoggingSwitches;
  /** Controls content set as span attributes */
  spans: ContentLoggingSwitches;
}

// ─── OTel exporter configuration ─────────────────────────────────────────────

export interface OtelExporterConfig {
  /** Base OTLP endpoint (OTEL_EXPORTER_OTLP_ENDPOINT) */
  endpoint?: string;
  /** OTLP protocol: http/json | http/protobuf (OTEL_EXPORTER_OTLP_PROTOCOL). Default: http/json */
  protocol: string;
  /** Attached headers, comma-separated key=value (OTEL_EXPORTER_OTLP_HEADERS) */
  headers: Record<string, string>;
  /** Common span/event attributes. Env: OBSERV_OPENCLAW_OTEL_ATTRIBUTES */
  attributes: Record<string, string>;
  /** Resource attribute overrides. Env: OTEL_RESOURCE_ATTRIBUTES, AIVISION_OTEL_RESOURCE_ATTRIBUTES, OBSERV_OPENCLAW_OTEL_RESOURCE_ATTRIBUTES */
  resourceAttributes: Record<string, string>;
  /** Metrics temporality preference (OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE). Default: delta */
  metricsTemporalityPreference: string;
}

// ─── Main plugin configuration ───────────────────────────────────────────────

export interface ObservOpenClawConfig {
  ingestConfig?: string;
  /** Enable telemetry collection — master switch (AGENT_ENABLE_TELEMETRY). Default: false */
  enableTelemetry: boolean;

  /** OpenTelemetry service name */
  serviceName: string;

  /** Content collection mode. Env: AIVISION_COLLECTION_MODE. Default: full */
  collectionMode: CollectionMode;

  /** Enable verbose plugin diagnostics. Env: OBSERV_OPENCLAW_DEBUG. Default: false */
  debug: boolean;

  /** Read ant.username from AIVISION_ANT_USERNAME whenever a signal is emitted. Default: false */
  dynamicUsername: boolean;

  /** Read ant.uid from AIVISION_ANT_UID whenever a signal is emitted. Default: false */
  dynamicUid: boolean;

  /** Content logging controls */
  log: ContentLoggingConfig;

  /** OTel exporter configuration */
  otelExporter: OtelExporterConfig;

  /** Trace-specific configuration */
  tracesConfig: SignalEndpointConfig;
  /** Metrics-specific configuration */
  metricsConfig: SignalEndpointConfig;
  /** Events/Logs-specific configuration */
  eventsConfig: SignalEndpointConfig;

  /** Metrics export interval in milliseconds (OTEL_METRIC_EXPORT_INTERVAL). Default: 60000 */
  metricExportInterval: number;
  /** Logs/Events export interval in milliseconds (OTEL_LOGS_EXPORT_INTERVAL). Default: 5000 */
  logsExportInterval: number;

  /** Directory for the plugin-owned observ-openclaw.log file. */
  logDir: string;
}

/** Safe startup-log projection. Deliberately excludes headers and identity attributes. */
export function telemetryConfigLogSummary(config: ObservOpenClawConfig) {
  const baseEndpoint = config.otelExporter.endpoint;
  return {
    enableTelemetry: config.enableTelemetry,
    debug: config.debug,
    serviceName: config.serviceName,
    protocol: config.otelExporter.protocol,
    baseEndpoint: baseEndpoint ?? null,
    traces: {
      enabled: config.tracesConfig.enabled,
      endpoint: config.tracesConfig.endpoint || baseEndpoint || null,
    },
    metrics: {
      enabled: config.metricsConfig.enabled,
      endpoint: config.metricsConfig.endpoint || baseEndpoint || null,
    },
    events: {
      enabled: config.eventsConfig.enabled,
      endpoint: config.eventsConfig.endpoint || baseEndpoint || null,
    },
  };
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function envBool(envVar: string, defaultValue: boolean): boolean {
  const value = process.env[envVar];
  if (value === undefined || value === '') return defaultValue;
  return value === '1' || value === 'true';
}

function envString(envVar: string, defaultValue: string): string {
  return process.env[envVar] || defaultValue;
}

function envInt(envVar: string, defaultValue: number): number {
  const value = process.env[envVar];
  if (!value) return defaultValue;
  const parsed = parseInt(value, 10);
  return isNaN(parsed) ? defaultValue : parsed;
}

export function parseKeyValueString(value: string | undefined): Record<string, string> {
  const result: Record<string, string> = {};
  if (!value) return result;
  for (const pair of value.split(',')) {
    const [key, ...valueParts] = pair.split('=');
    if (key && valueParts.length > 0) {
      const normalizedKey = key.trim();
      if (normalizedKey) {
        result[normalizedKey] = valueParts.join('=').trim();
      }
    }
  }
  return result;
}

function parseHeadersString(headersStr: string | undefined): Record<string, string> {
  return parseKeyValueString(headersStr);
}

function parseStringRecord(input: unknown): Record<string, string> {
  const result: Record<string, string> = {};
  if (!input || typeof input !== 'object') return result;
  for (const [key, val] of Object.entries(input as Record<string, unknown>)) {
    if (typeof val === 'string') {
      result[key] = val;
    } else if (typeof val === 'number' || typeof val === 'boolean') {
      result[key] = String(val);
    }
  }
  return result;
}

function parseHeadersObject(input: unknown): Record<string, string> {
  const result: Record<string, string> = {};
  if (!input || typeof input !== 'object') return result;
  for (const [key, val] of Object.entries(input as Record<string, unknown>)) {
    if (typeof val === 'string') {
      result[key] = val;
    }
  }
  return result;
}

/**
 * Parse a signal-specific config (traces/metrics/events).
 * Priority: pluginConfig > env var > edition config defaults.
 */
function parseSignalConfig(
  input: Record<string, unknown> | undefined,
  key: string,
  envEndpoint?: string,
  envHeaders?: string,
  editionDefault?: { endpoint?: string; headers?: Record<string, string>; enabled?: boolean },
  managedHeadersOnly = false,
): SignalEndpointConfig {
  const signalInput = input?.[key] as Record<string, unknown> | undefined;

  // Resolve enabled: pluginConfig > edition default > true
  const enabled = signalInput?.enabled !== undefined
    ? signalInput.enabled !== false
    : editionDefault?.enabled ?? true;

  // Resolve endpoint: pluginConfig > env var > edition default
  const endpoint = (signalInput?.endpoint && typeof signalInput.endpoint === 'string'
    ? signalInput.endpoint : undefined)
    || envEndpoint
    || editionDefault?.endpoint;

  // Resolve headers: merge layers (low -> high): edition default -> env var -> pluginConfig.
  // Each layer adds/overrides header names from previous layers.
  let headers: Record<string, string> | undefined;
  if (managedHeadersOnly) {
    return { enabled, endpoint, headers: {} };
  }
  if (editionDefault?.headers) {
    headers = { ...editionDefault.headers };
  }
  if (envHeaders) {
    headers = { ...headers, ...parseHeadersString(envHeaders) };
  }
  if (signalInput?.headers && typeof signalInput.headers === 'object') {
    headers = { ...headers, ...parseHeadersObject(signalInput.headers) };
  }

  return { enabled, endpoint, headers };
}

function parseBoolField(input: Record<string, unknown>, key: string, envVar: string, defaultValue: boolean): boolean {
  if (typeof input[key] === 'boolean') return input[key] as boolean;
  return envBool(envVar, defaultValue);
}



function contentLoggingFromMode(mode: CollectionMode): ContentLoggingConfig {
  const enabled = mode === 'full';
  const switches: ContentLoggingSwitches = {
    userPrompts: enabled,
    systemPrompts: enabled,
    modelOutputs: enabled,
    toolDetails: enabled,
    toolOutputs: enabled,
  };
  return {
    events: { ...switches },
    spans: { ...switches },
  };
}

// ─── Main parse function ─────────────────────────────────────────────────────

/**
 * @param value  Raw plugin config object
 * @param _runtimeEnv  Kept for API compatibility with older embedded-key builds.
 */
export function parseConfig(value: unknown, _runtimeEnv?: string): ObservOpenClawConfig {
  const input = (value && typeof value === 'object' ? value : {}) as Record<string, unknown>;

  if (Object.hasOwn(input, 'ant')) {
    throw new Error(
      'Config "ant" is no longer supported; use otelExporter.attributes for ant.username and ant.uid, '
      + 'and otelExporter.resourceAttributes for ant.agent.*.',
    );
  }

  // Resolve edition config (build-time embedded or runtime environment detection)
  const editionConfig = resolveEditionConfig();
  const managedAivision = isAivisionEdition();

  const modeResolution = resolveCollectionMode(input.collectionMode ?? editionConfig?.collectionMode, 'OBSERV_OPENCLAW_COLLECTION_MODE', process.env);
  const collectionMode = modeResolution.mode;

  // Parse nested `otel` / `otelExporter` object if present
  const otelInput = (input.otelExporter && typeof input.otelExporter === 'object'
    ? input.otelExporter : {}) as Record<string, unknown>;

  // Parse headers from otel config or top-level fallback
  let otelHeaders: Record<string, string> = {};
  if (managedAivision) {
    // The personal edition does not use auth headers. Protocol-required
    // routing headers are added separately by the exporter layer.
    otelHeaders = {};
  } else if (otelInput.headers && typeof otelInput.headers === 'object') {
    otelHeaders = parseHeadersObject(otelInput.headers);
  } else if (input.headers && typeof input.headers === 'object') {
    otelHeaders = parseHeadersObject(input.headers);
  } else {
    otelHeaders = parseHeadersString(process.env.OTEL_EXPORTER_OTLP_HEADERS);
  }

  // Resolve base endpoint
  const baseEndpoint = (typeof otelInput.endpoint === 'string' ? otelInput.endpoint : undefined)
    || (typeof input.endpoint === 'string' ? input.endpoint : undefined)
    || process.env.OTEL_EXPORTER_OTLP_ENDPOINT
    || undefined;

  const resourceAttributes = {
    ...parseStringRecord(editionConfig?.otelExporter?.resourceAttributes),
    ...parseStringRecord(otelInput.resourceAttributes),
    ...parseKeyValueString(process.env.OTEL_RESOURCE_ATTRIBUTES),
    ...parseKeyValueString(process.env.AIVISION_OTEL_RESOURCE_ATTRIBUTES),
    ...parseKeyValueString(process.env.OBSERV_OPENCLAW_OTEL_RESOURCE_ATTRIBUTES),
  };
  delete resourceAttributes['ant.uid'];
  resourceAttributes['agentic.collection.mode'] = collectionMode;

  const attributes = {
    ...parseStringRecord(editionConfig?.otelExporter?.attributes),
    ...parseStringRecord(otelInput.attributes),
    ...parseKeyValueString(process.env.OBSERV_OPENCLAW_OTEL_ATTRIBUTES),
  };
  delete attributes[InternalAttributes.META_ROOT];

  const config: ObservOpenClawConfig = {
    // Master switch — edition config can default to true
    enableTelemetry: modeResolution.valid && parseBoolField(input, 'enableTelemetry', 'AGENT_ENABLE_TELEMETRY',
      editionConfig?.enableTelemetry ?? false),

    // Service name
    serviceName: (typeof input.serviceName === 'string' ? input.serviceName : undefined)
      || envString('OTEL_SERVICE_NAME', editionConfig?.serviceName ?? 'openclaw'),

    collectionMode,

    debug: collectionMode === 'full' && envBool('OBSERV_OPENCLAW_DEBUG',
      typeof input.debug === 'boolean' ? input.debug : false),

    dynamicUsername: typeof input.dynamicUsername === 'boolean' ? input.dynamicUsername : false,

    dynamicUid: typeof input.dynamicUid === 'boolean' ? input.dynamicUid : false,

    // Content logging is now derived from AIVISION_COLLECTION_MODE only.
    // `full` records content, `privacy` redacts/omits raw content.
    log: contentLoggingFromMode(collectionMode),

    // OTel exporter
    ingestConfig: typeof input.ingestConfig === 'string' ? input.ingestConfig : undefined,
    otelExporter: {
      endpoint: baseEndpoint,
      protocol: (typeof otelInput.protocol === 'string' ? otelInput.protocol : '')
        || envString('OTEL_EXPORTER_OTLP_PROTOCOL', editionConfig?.otelExporter?.protocol ?? 'http/json'),
      headers: otelHeaders,
      attributes,
      resourceAttributes,
      metricsTemporalityPreference: (typeof otelInput.metricsTemporalityPreference === 'string'
        ? otelInput.metricsTemporalityPreference : '')
        || envString('OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE',
          editionConfig?.otelExporter?.metricsTemporalityPreference ?? 'delta'),
    },

    // Signal-specific configs
    // Priority: pluginConfig > env var > edition config defaults
    tracesConfig: parseSignalConfig(
      input, 'tracesConfig',
      process.env.OTEL_EXPORTER_OTLP_TRACES_ENDPOINT,
      process.env.OTEL_EXPORTER_OTLP_TRACES_HEADERS,
      editionConfig?.tracesConfig,
      managedAivision,
    ),
    metricsConfig: parseSignalConfig(
      input, 'metricsConfig',
      process.env.OTEL_EXPORTER_OTLP_METRICS_ENDPOINT,
      process.env.OTEL_EXPORTER_OTLP_METRICS_HEADERS,
      editionConfig?.metricsConfig,
      managedAivision,
    ),
    eventsConfig: parseSignalConfig(
      input, 'eventsConfig',
      process.env.OTEL_EXPORTER_OTLP_LOGS_ENDPOINT,
      process.env.OTEL_EXPORTER_OTLP_LOGS_HEADERS,
      editionConfig?.eventsConfig,
      managedAivision,
    ),

    // Export intervals
    metricExportInterval: (typeof input.metricExportInterval === 'number' ? input.metricExportInterval : 0)
      || envInt('OTEL_METRIC_EXPORT_INTERVAL', 60000),
    logsExportInterval: (typeof input.logsExportInterval === 'number' ? input.logsExportInterval : 0)
      || envInt('OTEL_LOGS_EXPORT_INTERVAL', 5000),

    // Plugin-owned log directory: explicit config > env > edition default.
    logDir: resolveLogDir(typeof input.logDir === 'string' ? input.logDir : undefined),
  };

  return config;
}
