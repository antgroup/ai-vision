import { readFileSync } from 'node:fs';
import { basename } from 'node:path';

export type OtlpJson = Record<string, unknown>;

interface OtlpAnyValue {
  stringValue?: string;
  intValue?: string | number;
  doubleValue?: number;
  boolValue?: boolean;
  bytesValue?: string;
  arrayValue?: { values?: OtlpAnyValue[] };
  kvlistValue?: { values?: Array<{ key?: string; value?: OtlpAnyValue }> };
}

export interface OtlpAttribute {
  key?: string;
  value?: OtlpAnyValue;
}

export interface OtlpLogRecord {
  body?: { stringValue?: string };
  attributes?: OtlpAttribute[];
  traceId?: string;
  spanId?: string;
}

export interface OtlpSpan {
  name?: string;
  traceId?: string;
  spanId?: string;
  links?: Array<{ traceId?: string; spanId?: string }>;
  attributes?: OtlpAttribute[];
}

export interface OtlpMetric {
  name?: string;
  sum?: { dataPoints?: Array<{ asDouble?: number; asInt?: string | number }> };
  histogram?: { dataPoints?: Array<{ count?: string | number }> };
}

export function readJsonLines<T = OtlpJson>(path: string): T[] {
  return readFileSync(path, 'utf8').trim().split('\n').filter(Boolean)
    .map(line => JSON.parse(line) as T);
}

export function readJsonLineFiles<T = OtlpJson>(files: string[]): T[] {
  return files.flatMap(file => readJsonLines<T>(file));
}

function decodeAnyValue(value: OtlpAnyValue | undefined): unknown {
  if (!value) return undefined;
  if (value.stringValue !== undefined) return value.stringValue;
  if (value.intValue !== undefined) return value.intValue;
  if (value.doubleValue !== undefined) return value.doubleValue;
  if (value.boolValue !== undefined) return value.boolValue;
  if (value.bytesValue !== undefined) return value.bytesValue;
  if (value.arrayValue !== undefined) return (value.arrayValue.values ?? []).map(decodeAnyValue);
  if (value.kvlistValue !== undefined) {
    return Object.fromEntries((value.kvlistValue.values ?? []).map(item => [
      String(item.key),
      decodeAnyValue(item.value),
    ]));
  }
  return undefined;
}

export function otlpAttributes(record: { attributes?: OtlpAttribute[] }): Record<string, unknown> {
  return Object.fromEntries((record.attributes ?? []).map(attribute => [
    String(attribute.key),
    decodeAnyValue(attribute.value),
  ]));
}

export function otlpLogRecords(files: string[]): OtlpLogRecord[] {
  return files.filter(file => basename(file).startsWith('events_')).flatMap(file => readJsonLines(file))
    .flatMap(request => (request.resourceLogs as OtlpJson[] | undefined) ?? [])
    .flatMap(resource => (resource.scopeLogs as OtlpJson[] | undefined) ?? [])
    .flatMap(scope => (scope.logRecords as OtlpLogRecord[] | undefined) ?? []);
}

export function otlpSpans(files: string[]): OtlpSpan[] {
  return files.filter(file => basename(file).startsWith('spans_')).flatMap(file => readJsonLines(file))
    .flatMap(request => (request.resourceSpans as OtlpJson[] | undefined) ?? [])
    .flatMap(resource => (resource.scopeSpans as OtlpJson[] | undefined) ?? [])
    .flatMap(scope => (scope.spans as OtlpSpan[] | undefined) ?? []);
}

export function lastOtlpMetrics(files: string[]): OtlpMetric[] {
  return files.filter(file => basename(file).startsWith('metrics_')).flatMap(file => {
    const last = readJsonLines(file).at(-1);
    return ((last?.resourceMetrics as OtlpJson[] | undefined) ?? [])
      .flatMap(resource => (resource.scopeMetrics as OtlpJson[] | undefined) ?? [])
      .flatMap(scope => (scope.metrics as OtlpMetric[] | undefined) ?? []);
  });
}
