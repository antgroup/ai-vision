import { resolveCollectionMode } from '../../../shared/collection-mode.mjs';
/**
 * Plugin configuration parsing and validation.
 *
 * Follows the AI Vision Agent Observability Specification v2.0.
 * Each config item supports both environment variables (UPPER_SNAKE_CASE)
 * and programmatic config (camelCase with dot-separated prefixes).
 *
 * Aligned with observ-openclaw config format.
 *
 * @see docs/standard/ant/conventions.md
 */

import { resolveEmbeddedLangfuseConfig } from './keys.js';
import { resolveEditionConfig } from './edition-config.js';
import { log, logWarn, resolveLogDir } from './logger.js';
import { InternalAttributes } from './semconv.js';
import {
  AIVISION_META_TRIGGER_ENV,
  ANT_META_TRIGGER_KEY,
  normalizeConfiguredMetaTrigger,
} from '../../../shared/meta-trigger.mjs';

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

export interface AdapterProfileConfigOptions {
  profile: 'replay' | 'report';
  collectionMode?: CollectionMode;
  endpoint?: string;
  headers?: Record<string, string>;
  username?: string;
}

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
  /** OTLP protocol: grpc | http/json | http/protobuf (OTEL_EXPORTER_OTLP_PROTOCOL). Default: http/json */
  protocol: string;
  /** Attached headers, comma-separated key=value (OTEL_EXPORTER_OTLP_HEADERS) */
  headers: Record<string, string>;
  /** Common span/event/metric attributes. Env: OBSERV_OPENCODE_OTEL_ATTRIBUTES */
  attributes: Record<string, string>;
  /**
   * Resource overrides. Environment layers, from low to high priority:
   * OTEL_RESOURCE_ATTRIBUTES, AIVISION_OTEL_RESOURCE_ATTRIBUTES,
   * OBSERV_OPENCODE_OTEL_RESOURCE_ATTRIBUTES. AIVISION_META_TRIGGER has final
   * priority for the reserved ant.meta.trigger attribute.
   */
  resourceAttributes: Record<string, string>;
  /** Metrics temporality preference (OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE). Default: delta */
  metricsTemporalityPreference: string;
}

// ─── Main plugin configuration ───────────────────────────────────────────────

export interface ObservOpencodeConfig {
  ingestConfig?: string;
  /** Enable telemetry collection — master switch (AGENT_ENABLE_TELEMETRY). Default: false */
  enableTelemetry: boolean;

  /** OpenTelemetry service name */
  serviceName: string;

  /** Content collection mode. Env: AIVISION_COLLECTION_MODE. Default: full */
  collectionMode: CollectionMode;

  /** Enable verbose plugin diagnostics. Env: OBSERV_OPENCODE_DEBUG. Default: false */
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

  /** Directory for the plugin-owned observ-opencode.log file. */
  logDir: string;
}

/** Safe startup-log projection. Deliberately excludes headers and identity attributes. */
export function telemetryConfigLogSummary(config: ObservOpencodeConfig) {
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

function parseSignalConfig(
  input: Record<string, unknown> | undefined,
  key: string,
  envEndpoint?: string,
  envHeaders?: string,
  editionDefault?: { endpoint?: string; headers?: Record<string, string>; enabled?: boolean },
  fallbackEndpoint?: string,
  fallbackHeaders?: Record<string, string>,
): SignalEndpointConfig {
  const signalInput = input?.[key] as Record<string, unknown> | undefined;

  const enabled = typeof signalInput?.enabled === 'boolean'
    ? signalInput.enabled
    : editionDefault?.enabled ?? true;

  const endpoint = (signalInput?.endpoint && typeof signalInput.endpoint === 'string'
    ? signalInput.endpoint : undefined)
    || envEndpoint
    || fallbackEndpoint
    || editionDefault?.endpoint;

  let headers: Record<string, string> | undefined;
  if (editionDefault?.headers) {
    headers = { ...editionDefault.headers };
  }
  if (fallbackHeaders && Object.keys(fallbackHeaders).length > 0) {
    headers = { ...headers, ...fallbackHeaders };
  }
  if (envHeaders) {
    headers = { ...headers, ...parseKeyValueString(envHeaders) };
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

// ─── Deprecated env var migration warnings ───────────────────────────────────

const LOCAL_REPLAY_CONFIG_KEYS = ['replayDir', 'sessionDir', 'sessionName'] as const;
const LOCAL_REPLAY_ENV_VARS = [
  'OPENCODE_DEBUG',
  'OPENCODE_REPLAY_DIR',
  'OPENCODE_SESSION_DIR',
  'OPENCODE_SESSION_NAME',
  'AIVISION_DEBUG',
  'AIVISION_REPLAY_DIR',
  'OBSERV_OPENCODE_REPLAY_DIR',
  'OBSERV_OPENCODE_SESSION_DIR',
  'OBSERV_OPENCODE_SESSION_NAME',
] as const;

export function emitDeprecationWarnings(
  input: Record<string, unknown> = {},
  env: NodeJS.ProcessEnv = process.env,
  warn: (message: string, ...args: unknown[]) => void = logWarn,
): void {
  const deprecatedVars = [
    'AGENT_LOG_USER_PROMPTS', 'AGENT_LOG_SYSTEM_PROMPTS', 'AGENT_LOG_MODEL_OUTPUTS',
    'AGENT_LOG_TOOL_DETAILS', 'AGENT_LOG_TOOL_OUTPUTS',
    'AGENT_LOG_EVENTS_USER_PROMPTS', 'AGENT_LOG_EVENTS_SYSTEM_PROMPTS',
    'AGENT_LOG_EVENTS_MODEL_OUTPUTS', 'AGENT_LOG_EVENTS_TOOL_DETAILS',
    'AGENT_LOG_EVENTS_TOOL_OUTPUTS', 'AGENT_LOG_SPANS_USER_PROMPTS',
    'AGENT_LOG_SPANS_SYSTEM_PROMPTS', 'AGENT_LOG_SPANS_MODEL_OUTPUTS',
    'AGENT_LOG_SPANS_TOOL_DETAILS', 'AGENT_LOG_SPANS_TOOL_OUTPUTS',
  ];
  const found = deprecatedVars.filter(v => env[v] !== undefined && env[v] !== '');
  if (found.length > 0) {
    warn('[observ-opencode] Deprecated: AGENT_LOG_* env vars are ignored. Use AIVISION_COLLECTION_MODE=full|privacy instead. Found:', found.join(', '));
  }

  const localConfigKeys = LOCAL_REPLAY_CONFIG_KEYS.filter(key => Object.hasOwn(input, key));
  const localEnvVars = LOCAL_REPLAY_ENV_VARS.filter(key => env[key] !== undefined && env[key] !== '');
  if (localConfigKeys.length > 0 || localEnvVars.length > 0) {
    warn(
      '[observ-opencode] Deprecated local replay settings are ignored by observ-opencode. '
      + 'Install/configure opencode-replay instead; prefer OPENCODE_REPLAY_* environment variables. Found:',
      [...localConfigKeys, ...localEnvVars].join(', '),
    );
  }
}

// ─── Main parse function ─────────────────────────────────────────────────────

export function parseConfig(value: unknown): ObservOpencodeConfig {
  const input = (value && typeof value === 'object' ? value : {}) as Record<string, unknown>;

  if (Object.hasOwn(input, 'ant')) {
    throw new Error(
      'Config "ant" is no longer supported; use otelExporter.attributes for ant.username and ant.uid, '
      + 'and otelExporter.resourceAttributes for ant.agent.*.',
    );
  }

  const editionConfig = resolveEditionConfig();

  emitDeprecationWarnings(input);

  const modeResolution = resolveCollectionMode(input.collectionMode ?? editionConfig?.collectionMode, 'OBSERV_OPENCODE_COLLECTION_MODE', process.env);
  const collectionMode = modeResolution.mode;

  const otelInput = (input.otelExporter && typeof input.otelExporter === 'object'
    ? input.otelExporter : {}) as Record<string, unknown>;

  let otelHeaders: Record<string, string> = {
    ...parseKeyValueString(process.env.OTEL_EXPORTER_OTLP_HEADERS),
  };
  if (input.headers && typeof input.headers === 'object') {
    otelHeaders = { ...otelHeaders, ...parseHeadersObject(input.headers) };
  }
  if (otelInput.headers && typeof otelInput.headers === 'object') {
    otelHeaders = { ...otelHeaders, ...parseHeadersObject(otelInput.headers) };
  }

  const baseEndpoint = (typeof otelInput.endpoint === 'string' ? otelInput.endpoint : undefined)
    || (typeof input.endpoint === 'string' ? input.endpoint : undefined)
    || process.env.OTEL_EXPORTER_OTLP_ENDPOINT
    || undefined;

  // Generic Resource layers are merged in ascending priority. The dedicated
  // trigger variable is resolved separately below so it always wins.
  const resourceAttributes = {
    ...parseStringRecord(editionConfig?.otelExporter?.resourceAttributes),
    ...parseStringRecord(otelInput.resourceAttributes),
    ...parseKeyValueString(process.env.OTEL_RESOURCE_ATTRIBUTES),
    ...parseKeyValueString(process.env.AIVISION_OTEL_RESOURCE_ATTRIBUTES),
    ...parseKeyValueString(process.env.OBSERV_OPENCODE_OTEL_RESOURCE_ATTRIBUTES),
  };
  // User identity is signal-scoped. Accept legacy Resource aliases, then move
  // the value to the canonical signal attribute below.
  const resourceUsername = resourceAttributes['ant.username'] || resourceAttributes.username;
  delete resourceAttributes.username;
  delete resourceAttributes['ant.username'];
  delete resourceAttributes['ant.uid'];
  const metaTrigger = normalizeConfiguredMetaTrigger(
    resourceAttributes[ANT_META_TRIGGER_KEY],
    process.env[AIVISION_META_TRIGGER_ENV],
  );
  if (metaTrigger === undefined) delete resourceAttributes[ANT_META_TRIGGER_KEY];
  else resourceAttributes[ANT_META_TRIGGER_KEY] = metaTrigger;
  resourceAttributes['agentic.collection.mode'] = collectionMode;

  const attributes = {
    ...parseStringRecord(editionConfig?.otelExporter?.attributes),
    ...parseStringRecord(otelInput.attributes),
    ...parseKeyValueString(process.env.OBSERV_OPENCODE_OTEL_ATTRIBUTES),
  };
  const signalUsername = attributes['ant.username'] || attributes.username || resourceUsername;
  delete attributes.username;
  if (signalUsername) attributes['ant.username'] = signalUsername;
  delete attributes[InternalAttributes.META_ROOT];

  const config: ObservOpencodeConfig = {
    enableTelemetry: modeResolution.valid && parseBoolField(input, 'enableTelemetry', 'AGENT_ENABLE_TELEMETRY',
      editionConfig?.enableTelemetry ?? false),

    serviceName: (typeof input.serviceName === 'string' ? input.serviceName : undefined)
      || envString('OTEL_SERVICE_NAME', editionConfig?.serviceName ?? 'opencode'),

    collectionMode,

    debug: collectionMode === 'full' && envBool('OBSERV_OPENCODE_DEBUG',
      typeof input.debug === 'boolean' ? input.debug : false),

    dynamicUsername: typeof input.dynamicUsername === 'boolean' ? input.dynamicUsername : false,

    dynamicUid: typeof input.dynamicUid === 'boolean' ? input.dynamicUid : false,

    log: contentLoggingFromMode(collectionMode),

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

    tracesConfig: parseSignalConfig(
      input, 'tracesConfig',
      process.env.OTEL_EXPORTER_OTLP_TRACES_ENDPOINT,
      process.env.OTEL_EXPORTER_OTLP_TRACES_HEADERS,
      editionConfig?.tracesConfig,
      baseEndpoint,
      otelHeaders,
    ),
    metricsConfig: parseSignalConfig(
      input, 'metricsConfig',
      process.env.OTEL_EXPORTER_OTLP_METRICS_ENDPOINT,
      process.env.OTEL_EXPORTER_OTLP_METRICS_HEADERS,
      editionConfig?.metricsConfig,
      baseEndpoint,
      otelHeaders,
    ),
    eventsConfig: parseSignalConfig(
      input, 'eventsConfig',
      process.env.OTEL_EXPORTER_OTLP_LOGS_ENDPOINT,
      process.env.OTEL_EXPORTER_OTLP_LOGS_HEADERS,
      editionConfig?.eventsConfig,
      baseEndpoint,
      otelHeaders,
    ),

    metricExportInterval: typeof input.metricExportInterval === 'number'
      ? input.metricExportInterval
      : envInt('OTEL_METRIC_EXPORT_INTERVAL', 60000),
    logsExportInterval: typeof input.logsExportInterval === 'number'
      ? input.logsExportInterval
      : envInt('OTEL_LOGS_EXPORT_INTERVAL', 5000),

    logDir: resolveLogDir(typeof input.logDir === 'string' ? input.logDir : undefined),
  };

  // ── Embedded Langfuse keys (aivision/cowork editions) ──────────────────────
  const embedded = resolveEmbeddedLangfuseConfig();
  if (embedded) {
    const authHeader = `Basic ${Buffer.from(`${embedded.publicKey}:${embedded.secretKey}`).toString('base64')}`;
    const signalConfigs = [config.tracesConfig, config.metricsConfig, config.eventsConfig];
    for (const sc of signalConfigs) {
      if (embedded.host) {
        const OTLP_PATHS = ['/v1/traces', '/v1/metrics', '/v1/logs'];
        const isPerSignalEndpoint = OTLP_PATHS.some(p => sc.endpoint?.includes(p));
        if (!isPerSignalEndpoint) {
          sc.endpoint = embedded.host;
        }
      }
      if (!sc.headers) sc.headers = {};
      if (!sc.headers['Authorization']) {
        sc.headers['Authorization'] = authHeader;
      }
    }
  }

  log('Final config:', {
    enableTelemetry: config.enableTelemetry,
    serviceName: config.serviceName,
    collectionMode: config.collectionMode,
    endpoint: config.otelExporter.endpoint,
    tracesEnabled: config.tracesConfig.enabled,
    metricsEnabled: config.metricsConfig.enabled,
    eventsEnabled: config.eventsConfig.enabled,
  });

  return config;
}

/** Canonical semantic/resource defaults for non-production adapters. */
export function parseAdapterProfileConfig(options: AdapterProfileConfigOptions): ObservOpencodeConfig {
  const httpEnabled = Boolean(options.endpoint);
  return parseConfig({
    enableTelemetry: true,
    serviceName: `opencode-${options.profile}`,
    collectionMode: options.collectionMode,
    otelExporter: {
      endpoint: options.endpoint,
      headers: options.headers,
      attributes: { 'ant.username': options.username ?? options.profile },
      resourceAttributes: {
        'ant.agent.type': 'opencode',
        'ant.agent.product': 'OpenCode',
        'ant.agent.instance': options.profile,
        'ant.agent.env': 'Default',
        'ant.agent.scene': 'default',
      },
    },
    tracesConfig: { enabled: httpEnabled, endpoint: options.endpoint, headers: options.headers },
    metricsConfig: { enabled: false },
    eventsConfig: { enabled: false },
  });
}
