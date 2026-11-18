import { resolveCollectionMode } from '../../../shared/collection-mode.mjs';
import { existsSync, readFileSync } from 'node:fs';
import { homedir, hostname, userInfo } from 'node:os';
import { isAbsolute, join, resolve } from 'node:path';
import {
  CONFIG_DIR_NAME,
  getAgentDir,
} from '@earendil-works/pi-coding-agent';
import { resolveEditionConfig } from './edition-config.js';
import { InternalAttributes } from './internal-attributes.js';
import {
  AIVISION_META_TRIGGER_ENV,
  ANT_META_TRIGGER_KEY,
  normalizeConfiguredMetaTrigger,
} from '../../../shared/meta-trigger.mjs';

export const OBSERV_PI_CONFIG_FILE = 'observ-pi.json';
export const DEFAULT_OTLP_HEADERS = {} as const;

export type CollectionMode = 'full' | 'privacy';
export type OtlpProtocol = 'http/json' | 'http/protobuf';
export type MetricsTemporalityPreference = 'delta' | 'cumulative';

export interface SignalEndpointConfig {
  endpoint?: string;
  headers: Record<string, string>;
  enabled: boolean;
}

export interface OtelExporterConfig {
  endpoint?: string;
  protocol: OtlpProtocol;
  headers: Record<string, string>;
  attributes: Record<string, string>;
  /**
   * Resource attributes. OTEL_RESOURCE_ATTRIBUTES,
   * AIVISION_OTEL_RESOURCE_ATTRIBUTES, and OBSERV_PI_OTEL_RESOURCE_ATTRIBUTES
   * are merged in that order; AIVISION_META_TRIGGER has final priority for
   * the reserved ant.meta.trigger attribute.
   */
  resourceAttributes: Record<string, string>;
  metricsTemporalityPreference: MetricsTemporalityPreference;
}

export interface PiagentConfig {
  ingestConfig?: string;
  enableTelemetry: boolean;
  serviceName: string;
  collectionMode: CollectionMode;
  dynamicUsername: boolean;
  dynamicUid: boolean;
  otelExporter: OtelExporterConfig;
  tracesConfig: SignalEndpointConfig;
  metricsConfig: SignalEndpointConfig;
  eventsConfig: SignalEndpointConfig;
  metricExportInterval: number;
  logsExportInterval: number;
  logDir?: string;

  /** Verbose diagnostics; configurable from JSON and overridden by OBSERV_PI_DEBUG. */
  debug: boolean;

  /** Environment-only artifact controls, intentionally outside the shared production schema. */
  replayDir?: string;
  sessionName?: string;

  /** Projections consumed by telemetry and the shared semantic engine. */
  userId: string;
  resource: Record<string, string>;
}

export interface ConfigLoadContext {
  cwd?: string;
  agentDir?: string;
  configDirName?: string;
  projectTrusted?: boolean;
  env?: NodeJS.ProcessEnv;
  homeDir?: string;
}

export interface PiagentConfigPaths {
  global: string;
  project: string;
}

function trimmed(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

function envValue(env: NodeJS.ProcessEnv, ...names: string[]): string | undefined {
  for (const name of names) {
    const value = trimmed(env[name]);
    if (value) return value;
  }
  return undefined;
}

function envBool(env: NodeJS.ProcessEnv, names: string[], fallback: boolean): boolean {
  const value = envValue(env, ...names)?.toLowerCase();
  if (value === undefined) return fallback;
  if (value === '1' || value === 'true') return true;
  if (value === '0' || value === 'false') return false;
  return fallback;
}

function envInt(env: NodeJS.ProcessEnv, name: string, fallback: number): number {
  const raw = envValue(env, name);
  if (!raw) return fallback;
  const parsed = Number(raw);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

export function parseKeyValueString(value: string | undefined): Record<string, string> {
  const result: Record<string, string> = {};
  if (!value) return result;
  for (const pair of value.split(',')) {
    const [key, ...parts] = pair.split('=');
    const normalizedKey = key?.trim();
    if (normalizedKey && parts.length > 0) result[normalizedKey] = parts.join('=').trim();
  }
  return result;
}

function parseStringRecord(value: unknown): Record<string, string> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  const result: Record<string, string> = {};
  for (const [key, candidate] of Object.entries(value as Record<string, unknown>)) {
    if (typeof candidate === 'string') result[key] = candidate;
    else if (typeof candidate === 'number' || typeof candidate === 'boolean') result[key] = String(candidate);
  }
  return result;
}

function parseHeaders(value: unknown): Record<string, string> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  const result: Record<string, string> = {};
  for (const [key, candidate] of Object.entries(value as Record<string, unknown>)) {
    if (typeof candidate === 'string') result[key] = candidate;
  }
  return result;
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

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

function readConfigFile(path: string): Record<string, unknown> | undefined {
  if (!existsSync(path)) return undefined;
  try {
    const parsed: unknown = JSON.parse(readFileSync(path, 'utf8'));
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error('configuration root must be a JSON object');
    }
    return parsed as Record<string, unknown>;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const diagnostic = `[observ-pi] invalid config ${JSON.stringify(path)}: ${message}`;
    console.warn(diagnostic);
    throw new Error(diagnostic);
  }
}

export function resolvePiagentConfigPaths(context: ConfigLoadContext = {}): PiagentConfigPaths {
  const cwd = resolve(context.cwd ?? process.cwd());
  const agentDir = resolve(context.agentDir ?? getAgentDir());
  const configDirName = context.configDirName ?? CONFIG_DIR_NAME;
  return {
    global: join(agentDir, OBSERV_PI_CONFIG_FILE),
    project: join(cwd, configDirName, OBSERV_PI_CONFIG_FILE),
  };
}

export function loadPiagentConfigFiles(context: ConfigLoadContext = {}): Record<string, unknown> {
  const paths = resolvePiagentConfigPaths(context);
  const global = readConfigFile(paths.global) ?? {};
  if (!context.projectTrusted) return global;
  return mergeConfigOptions(global, readConfigFile(paths.project) ?? {});
}


function normalizeProtocol(value: unknown): OtlpProtocol {
  return trimmed(value)?.toLowerCase() === 'http/protobuf' ? 'http/protobuf' : 'http/json';
}

function normalizeTemporality(value: unknown): MetricsTemporalityPreference {
  return trimmed(value)?.toLowerCase() === 'cumulative' ? 'cumulative' : 'delta';
}

function pathValue(value: unknown, cwd: string): string | undefined {
  const path = trimmed(value);
  if (!path) return undefined;
  return isAbsolute(path) ? resolve(path) : resolve(cwd, path);
}

function parseSignalConfig(
  input: Record<string, unknown>,
  key: 'tracesConfig' | 'metricsConfig' | 'eventsConfig',
  baseEndpoint: string | undefined,
  globalHeaders: Record<string, string>,
  envEndpoint: string | undefined,
  envHeaders: string | undefined,
): SignalEndpointConfig {
  const signal = objectValue(input[key]);
  return {
    enabled: typeof signal.enabled === 'boolean' ? signal.enabled : true,
    endpoint: envEndpoint ?? trimmed(signal.endpoint) ?? baseEndpoint,
    headers: {
      ...globalHeaders,
      ...parseHeaders(signal.headers),
      ...parseKeyValueString(envHeaders),
    },
  };
}

export function parseConfig(
  value: unknown,
  context: Pick<ConfigLoadContext, 'cwd' | 'env' | 'homeDir'> = {},
): PiagentConfig {
  const input = objectValue(value);
  if (Object.hasOwn(input, 'ant')) {
    throw new Error(
      'Config "ant" is no longer supported; use otelExporter.attributes for ant.username and ant.uid, '
      + 'and otelExporter.resourceAttributes for ant.agent.*.',
    );
  }

  const env = context.env ?? process.env;
  const cwd = resolve(context.cwd ?? process.cwd());
  const otelInput = objectValue(input.otelExporter);
  const modeResolution = resolveCollectionMode(input.collectionMode, 'OBSERV_PI_COLLECTION_MODE', env);
  const collectionMode = modeResolution.mode;
  const serviceName = envValue(env, 'OTEL_SERVICE_NAME')
    ?? trimmed(input.serviceName)
    ?? 'pi';
  const baseEndpoint = envValue(env, 'OBSERV_PI_OTLP_ENDPOINT', 'OTEL_EXPORTER_OTLP_ENDPOINT')
    ?? trimmed(otelInput.endpoint)
    ?? trimmed(input.endpoint)
    ?? 'http://localhost:4319/';
  const globalHeaders = {
    ...DEFAULT_OTLP_HEADERS,
    ...parseHeaders(input.headers),
    ...parseHeaders(otelInput.headers),
    ...parseKeyValueString(envValue(env, 'OTEL_EXPORTER_OTLP_HEADERS')),
    ...parseKeyValueString(envValue(env, 'OBSERV_PI_OTLP_HEADERS')),
  };

  // Generic Resource layers are merged in ascending priority. The dedicated
  // trigger variable is resolved separately below so it always wins.
  const resourceAttributes: Record<string, string> = {
    'ant.agent.instance': envValue(env, 'AIVISION_AGENT_INSTANCE') ?? hostname(),
    'ant.agent.env': envValue(env, 'AIVISION_AGENT_ENV') ?? 'Default',
    'ant.agent.scene': envValue(env, 'AIVISION_AGENT_SCENE') ?? 'default',
    ...parseStringRecord(otelInput.resourceAttributes),
    ...parseKeyValueString(envValue(env, 'OTEL_RESOURCE_ATTRIBUTES')),
    ...parseKeyValueString(envValue(env, 'AIVISION_OTEL_RESOURCE_ATTRIBUTES')),
    ...parseKeyValueString(envValue(env, 'OBSERV_PI_OTEL_RESOURCE_ATTRIBUTES')),
    'ant.agent.type': 'pi',
    'ant.agent.product': 'Pi',
    'service.name': serviceName,
    'service.version': '0.2.1',
    'agentic.collection.mode': collectionMode,
  };
  delete resourceAttributes['ant.uid'];
  delete resourceAttributes['ant.username'];
  delete resourceAttributes.username;
  const metaTrigger = normalizeConfiguredMetaTrigger(
    resourceAttributes[ANT_META_TRIGGER_KEY],
    env[AIVISION_META_TRIGGER_ENV],
  );
  if (metaTrigger === undefined) delete resourceAttributes[ANT_META_TRIGGER_KEY];
  else resourceAttributes[ANT_META_TRIGGER_KEY] = metaTrigger;

  const attributes = {
    ...parseStringRecord(otelInput.attributes),
    ...parseKeyValueString(envValue(env, 'OBSERV_PI_OTEL_ATTRIBUTES')),
  };
  const configuredUsername = attributes['ant.username'] || attributes.username;
  delete attributes.username;
  attributes['ant.username'] = configuredUsername
    || envValue(env, 'AIVISION_ANT_USERNAME', 'USER')
    || userInfo().username
    || 'piagent';
  const configuredUid = attributes['ant.uid']
    || envValue(env, 'AIVISION_ANT_UID', 'AIVISION_USER_ID');
  if (configuredUid) attributes['ant.uid'] = configuredUid;
  delete attributes[InternalAttributes.META_ROOT];

  const tracesConfig = parseSignalConfig(
    input, 'tracesConfig', baseEndpoint, globalHeaders,
    envValue(env, 'OTEL_EXPORTER_OTLP_TRACES_ENDPOINT'),
    envValue(env, 'OTEL_EXPORTER_OTLP_TRACES_HEADERS'),
  );
  const metricsConfig = parseSignalConfig(
    input, 'metricsConfig', baseEndpoint, globalHeaders,
    envValue(env, 'OTEL_EXPORTER_OTLP_METRICS_ENDPOINT'),
    envValue(env, 'OTEL_EXPORTER_OTLP_METRICS_HEADERS'),
  );
  const eventsConfig = parseSignalConfig(
    input, 'eventsConfig', baseEndpoint, globalHeaders,
    envValue(env, 'OTEL_EXPORTER_OTLP_LOGS_ENDPOINT'),
    envValue(env, 'OTEL_EXPORTER_OTLP_LOGS_HEADERS'),
  );
  const debug = collectionMode === 'full' && envBool(
    env,
    ['OBSERV_PI_DEBUG'],
    typeof input.debug === 'boolean' ? input.debug : false,
  );
  const aivisionHome = envValue(env, 'AIVISION_HOME') ?? join(context.homeDir ?? homedir(), '.aivision');
  const replayDir = pathValue(envValue(env, 'OBSERV_PI_REPLAY_DIR'), cwd)
    ?? (debug ? resolve(aivisionHome, 'pi', 'replays') : undefined);
  const username = attributes['ant.username'];
  const userId = attributes['ant.uid']
    ?? envValue(env, 'AIVISION_USER_ID')
    ?? username;

  return {
    enableTelemetry: modeResolution.valid && envBool(
      env,
      ['OBSERV_PI_ENABLE_TELEMETRY', 'AGENT_ENABLE_TELEMETRY'],
      typeof input.enableTelemetry === 'boolean' ? input.enableTelemetry : false,
    ),
    serviceName,
    collectionMode,
    dynamicUsername: typeof input.dynamicUsername === 'boolean' ? input.dynamicUsername : false,
    dynamicUid: typeof input.dynamicUid === 'boolean' ? input.dynamicUid : false,
    ingestConfig: typeof input.ingestConfig === 'string' ? input.ingestConfig : undefined,
    otelExporter: {
      endpoint: baseEndpoint,
      protocol: normalizeProtocol(
        envValue(env, 'OTEL_EXPORTER_OTLP_PROTOCOL') ?? otelInput.protocol ?? 'http/json',
      ),
      headers: globalHeaders,
      attributes,
      resourceAttributes,
      metricsTemporalityPreference: normalizeTemporality(
        envValue(env, 'OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE')
          ?? otelInput.metricsTemporalityPreference
          ?? 'delta',
      ),
    },
    tracesConfig,
    metricsConfig,
    eventsConfig,
    metricExportInterval: envInt(
      env,
      'OTEL_METRIC_EXPORT_INTERVAL',
      typeof input.metricExportInterval === 'number'
        && Number.isInteger(input.metricExportInterval)
        && input.metricExportInterval > 0
        ? input.metricExportInterval
        : 60_000,
    ),
    logsExportInterval: envInt(
      env,
      'OTEL_LOGS_EXPORT_INTERVAL',
      typeof input.logsExportInterval === 'number'
        && Number.isInteger(input.logsExportInterval)
        && input.logsExportInterval > 0
        ? input.logsExportInterval
        : 5_000,
    ),
    logDir: pathValue(
      envValue(env, 'OBSERV_PI_LOG_DIR', 'AIVISION_LOG_DIR') ?? input.logDir,
      cwd,
    ),
    debug,
    replayDir,
    sessionName: envValue(env, 'OBSERV_PI_SESSION_NAME'),
    userId,
    resource: resourceAttributes,
  };
}

export function loadConfig(
  options?: Record<string, unknown>,
  context: ConfigLoadContext = {},
): PiagentConfig {
  const fileOptions = mergeConfigOptions(resolveEditionConfig(), loadPiagentConfigFiles(context));
  return parseConfig(
    options ? mergeConfigOptions(fileOptions, options) : fileOptions,
    context,
  );
}

/** Resolve common signal attributes at emission time for dynamic identity. */
export function resolveSignalAttributes(
  config: PiagentConfig,
  env: NodeJS.ProcessEnv = process.env,
): Record<string, string> {
  const attributes = { ...config.otelExporter.attributes };
  if (config.dynamicUsername) {
    const username = envValue(env, 'AIVISION_ANT_USERNAME');
    if (username) attributes['ant.username'] = username;
  }
  if (config.dynamicUid) {
    const uid = envValue(env, 'AIVISION_ANT_UID');
    if (uid) attributes['ant.uid'] = uid;
  }
  return attributes;
}
