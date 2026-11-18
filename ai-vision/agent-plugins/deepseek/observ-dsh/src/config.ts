import { resolveCollectionMode } from '../../../shared/collection-mode.mjs';
/**
 * Configuration parsing for observ-dsh.
 *
 * The production plugin owns DSH_OBSERV_* exclusively. It never reads
 * DSH_TELEMETRY_* (dsh's native logs-only telemetry) or DSH_REPLAY_*; the
 * replay CLI owns DSH_REPLAY_* and never reads DSH_OBSERV_*.
 */

import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { isAbsolute, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export type CollectionMode = 'full' | 'privacy';

export interface SignalEndpointConfig {
  enabled: boolean;
  endpoint?: string;
  headers?: Record<string, string>;
}

export interface ContentLoggingSwitches {
  userPrompts: boolean;
  systemPrompts: boolean;
  modelOutputs: boolean;
  toolDetails: boolean;
  toolOutputs: boolean;
}

export interface ContentLoggingConfig {
  events: ContentLoggingSwitches;
  spans: ContentLoggingSwitches;
}

export interface ObservDshConfig {
  ingestConfig?: string;
  enableTelemetry: boolean;
  serviceName: string;
  collectionMode: CollectionMode;
  debug: boolean;
  dynamicUsername: boolean;
  dynamicUid: boolean;
  log: ContentLoggingConfig;
  otelExporter: {
    endpoint?: string;
    protocol: string;
    headers: Record<string, string>;
    attributes: Record<string, string>;
    resourceAttributes: Record<string, string>;
    metricsTemporalityPreference: string;
  };
  tracesConfig: SignalEndpointConfig;
  metricsConfig: SignalEndpointConfig;
  eventsConfig: SignalEndpointConfig;
  metricExportInterval: number;
  logsExportInterval: number;
}

export function envBool(value: string | undefined, defaultValue: boolean): boolean {
  if (value === undefined || value === '') return defaultValue;
  return value === '1' || value === 'true' || value === 'yes' || value === 'on';
}

export function parseKeyValueString(value: string | undefined): Record<string, string> {
  const result: Record<string, string> = {};
  if (!value) return result;
  for (const pair of value.split(',')) {
    const [key, ...parts] = pair.split('=');
    if (key && parts.length > 0) {
      const normalizedKey = key.trim();
      if (normalizedKey) result[normalizedKey] = parts.join('=').trim();
    }
  }
  return result;
}

function parseStringRecord(input: unknown): Record<string, string> {
  const result: Record<string, string> = {};
  if (!input || typeof input !== 'object' || Array.isArray(input)) return result;
  for (const [key, val] of Object.entries(input as Record<string, unknown>)) {
    if (typeof val === 'string') result[key] = val;
    else if (typeof val === 'number' || typeof val === 'boolean') result[key] = String(val);
  }
  return result;
}

function parseHeadersObject(input: unknown): Record<string, string> {
  const result: Record<string, string> = {};
  if (!input || typeof input !== 'object' || Array.isArray(input)) return result;
  for (const [key, val] of Object.entries(input as Record<string, unknown>)) {
    if (typeof val === 'string') result[key] = val;
  }
  return result;
}



function contentLoggingFromMode(mode: CollectionMode): ContentLoggingConfig {
  const switches: ContentLoggingSwitches = {
    userPrompts: mode === 'full',
    systemPrompts: mode === 'full',
    modelOutputs: mode === 'full',
    toolDetails: mode === 'full',
    toolOutputs: mode === 'full',
  };
  return { events: { ...switches }, spans: { ...switches } };
}

function boolInput(input: Record<string, unknown>, key: string, envVar: string, defaultValue: boolean): boolean {
  if (typeof input[key] === 'boolean') return input[key] as boolean;
  return envBool(process.env[envVar], defaultValue);
}

function resolveEndpoint(
  input: Record<string, unknown>,
  key: string,
  envVar: string,
  baseEndpoint: string | undefined,
): SignalEndpointConfig {
  const signal = (input[key] && typeof input[key] === 'object' ? input[key] : {}) as Record<string, unknown>;
  return {
    enabled: typeof signal.enabled === 'boolean' ? signal.enabled : true,
    endpoint: (typeof signal.endpoint === 'string' && signal.endpoint.trim() ? signal.endpoint : undefined)
      || process.env[envVar]
      || baseEndpoint,
    headers: {
      ...parseKeyValueString(process.env.DSH_OBSERV_OTLP_HEADERS),
      ...(typeof signal.headers === 'object' ? parseHeadersObject(signal.headers) : {}),
    },
  };
}

/** Deep-merge two plain config objects; override scalars/arrays win. Mirrors opencode's standalone-config mergeConfigOptions. */
export function mergeConfigOptions(
  base: Record<string, unknown>,
  override: Record<string, unknown>,
): Record<string, unknown> {
  const result: Record<string, unknown> = { ...base };
  for (const [key, overrideValue] of Object.entries(override)) {
    const baseValue = base[key];
    if (
      baseValue && overrideValue
      && typeof baseValue === 'object' && !Array.isArray(baseValue)
      && typeof overrideValue === 'object' && !Array.isArray(overrideValue)
    ) {
      result[key] = mergeConfigOptions(
        baseValue as Record<string, unknown>,
        overrideValue as Record<string, unknown>,
      );
    } else {
      result[key] = overrideValue;
    }
  }
  return result;
}

function readJsonConfigObject(path: string): Record<string, unknown> | undefined {
  try {
    const parsed: unknown = JSON.parse(readFileSync(path, 'utf8'));
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error('configuration root must be a JSON object');
    }
    return parsed as Record<string, unknown>;
  } catch (error) {
    if (
      error && typeof error === 'object' && 'code' in error
      && (error as NodeJS.ErrnoException).code === 'ENOENT'
    ) {
      return undefined;
    }
    throw error instanceof Error
      ? new Error(`observ-dsh config load failed (${path}): ${error.message}`)
      : error;
  }
}

/**
 * Load the JSON config file (pi/opencode schema). Resolution order:
 *   cordis `config.configFile` → DSH_OBSERV_CONFIG → ~/.config/dsh/aivision.config.json
 *   → the bundled configs/aivision.config.json.
 * Missing files fall through silently; malformed JSON throws with the path.
 */
export function loadObservDshConfigFile(
  explicitPath?: string,
  env: NodeJS.ProcessEnv = process.env,
  homeDir: string = homedir(),
): Record<string, unknown> {
  const candidates: string[] = [];
  const cordisPath = explicitPath?.trim();
  if (cordisPath) candidates.push(isAbsolute(cordisPath) ? cordisPath : resolve(process.cwd(), cordisPath));
  const envPath = env.DSH_OBSERV_CONFIG?.trim();
  if (envPath) candidates.push(isAbsolute(envPath) ? envPath : resolve(process.cwd(), envPath));
  candidates.push(join(homeDir, '.config', 'dsh', 'aivision.config.json'));
  candidates.push(fileURLToPath(new URL('../configs/aivision.config.json', import.meta.url)));
  for (const candidate of candidates) {
    const parsed = readJsonConfigObject(candidate);
    if (parsed !== undefined) return parsed;
  }
  return {};
}

export function parseConfig(value: unknown): ObservDshConfig {
  const input = (value && typeof value === 'object' && !Array.isArray(value)
    ? value : {}) as Record<string, unknown>;

  const otelInput = (input.otelExporter && typeof input.otelExporter === 'object'
    ? input.otelExporter : {}) as Record<string, unknown>;

  const baseEndpoint = (typeof otelInput.endpoint === 'string' ? otelInput.endpoint : undefined)
    || (typeof input.endpoint === 'string' ? input.endpoint : undefined)
    || process.env.DSH_OBSERV_OTLP_ENDPOINT
    || process.env.OTEL_EXPORTER_OTLP_ENDPOINT
    || undefined;

  const headers: Record<string, string> = {
    ...parseKeyValueString(process.env.OTEL_EXPORTER_OTLP_HEADERS),
    ...parseKeyValueString(process.env.DSH_OBSERV_OTLP_HEADERS),
    ...(typeof input.headers === 'object' ? parseHeadersObject(input.headers) : {}),
    ...(typeof otelInput.headers === 'object' ? parseHeadersObject(otelInput.headers) : {}),
  };

  const modeResolution = resolveCollectionMode(input.collectionMode, 'DSH_OBSERV_COLLECTION_MODE', process.env);
  const collectionMode = modeResolution.mode;

  const resourceAttributes = {
    ...parseStringRecord(otelInput.resourceAttributes),
    ...parseKeyValueString(process.env.OTEL_RESOURCE_ATTRIBUTES),
    ...parseKeyValueString(process.env.AIVISION_OTEL_RESOURCE_ATTRIBUTES),
    ...parseKeyValueString(process.env.DSH_OBSERV_OTEL_RESOURCE_ATTRIBUTES),
  };
  // ant.username / ant.uid are signal-scoped and must never enter the Resource.
  const resourceUsername = resourceAttributes['ant.username'] || resourceAttributes.username;
  delete resourceAttributes.username;
  delete resourceAttributes['ant.username'];
  delete resourceAttributes['ant.uid'];

  const attributes = {
    ...parseStringRecord(otelInput.attributes),
    ...parseKeyValueString(process.env.DSH_OBSERV_OTEL_ATTRIBUTES),
  };
  const signalUsername = attributes['ant.username'] || attributes.username || resourceUsername;
  delete attributes.username;
  if (signalUsername) attributes['ant.username'] = signalUsername;

  const metricExportInterval = (() => {
    const raw = typeof input.metricExportInterval === 'number'
      ? input.metricExportInterval
      : Number.parseInt(process.env.DSH_OBSERV_METRIC_EXPORT_INTERVAL ?? '', 10);
    return Number.isFinite(raw) && raw > 0 ? raw : 60000;
  })();

  const logsExportInterval = (() => {
    const raw = typeof input.logsExportInterval === 'number'
      ? input.logsExportInterval
      : Number.parseInt(process.env.DSH_OBSERV_LOGS_EXPORT_INTERVAL ?? '', 10);
    return Number.isFinite(raw) && raw > 0 ? raw : 5000;
  })();

  return {
    enableTelemetry: modeResolution.valid && envBool(
      process.env.DSH_OBSERV_ENABLED,
      typeof input.enableTelemetry === 'boolean' ? input.enableTelemetry : false,
    ),
    serviceName: (typeof input.serviceName === 'string' ? input.serviceName : undefined)
      || process.env.DSH_OBSERV_SERVICE_NAME
      || process.env.OTEL_SERVICE_NAME
      || 'dsh',
    collectionMode,
    debug: collectionMode === 'full' && boolInput(input, 'debug', 'DSH_OBSERV_DEBUG', false),
    dynamicUsername: typeof input.dynamicUsername === 'boolean'
      ? input.dynamicUsername
      : envBool(process.env.DSH_OBSERV_DYNAMIC_USERNAME, false),
    dynamicUid: typeof input.dynamicUid === 'boolean'
      ? input.dynamicUid
      : envBool(process.env.DSH_OBSERV_DYNAMIC_UID, false),
    log: contentLoggingFromMode(collectionMode),
    ingestConfig: typeof input.ingestConfig === 'string' ? input.ingestConfig : undefined,
    otelExporter: {
      endpoint: baseEndpoint,
      protocol: (typeof otelInput.protocol === 'string' ? otelInput.protocol : undefined)
        || process.env.DSH_OBSERV_OTLP_PROTOCOL
        || process.env.OTEL_EXPORTER_OTLP_PROTOCOL
        || 'http/json',
      headers,
      attributes,
      resourceAttributes,
      metricsTemporalityPreference: (typeof otelInput.metricsTemporalityPreference === 'string'
        ? otelInput.metricsTemporalityPreference : undefined)
        || process.env.DSH_OBSERV_METRICS_TEMPORALITY
        || process.env.OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE
        || 'delta',
    },
    tracesConfig: resolveEndpoint(input, 'tracesConfig', 'DSH_OBSERV_OTLP_TRACES_ENDPOINT', baseEndpoint),
    metricsConfig: resolveEndpoint(input, 'metricsConfig', 'DSH_OBSERV_OTLP_METRICS_ENDPOINT', baseEndpoint),
    eventsConfig: resolveEndpoint(input, 'eventsConfig', 'DSH_OBSERV_OTLP_LOGS_ENDPOINT', baseEndpoint),
    metricExportInterval,
    logsExportInterval,
  };
}

export function telemetryConfigLogSummary(config: ObservDshConfig): Record<string, unknown> {
  const baseEndpoint = config.otelExporter.endpoint;
  return {
    enableTelemetry: config.enableTelemetry,
    serviceName: config.serviceName,
    collectionMode: config.collectionMode,
    debug: config.debug,
    baseEndpoint: baseEndpoint ?? null,
    traces: { enabled: config.tracesConfig.enabled, endpoint: config.tracesConfig.endpoint ?? null },
    metrics: { enabled: config.metricsConfig.enabled, endpoint: config.metricsConfig.endpoint ?? null },
    events: { enabled: config.eventsConfig.enabled, endpoint: config.eventsConfig.endpoint ?? null },
  };
}
