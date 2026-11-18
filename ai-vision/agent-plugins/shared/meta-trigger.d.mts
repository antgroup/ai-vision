export const ANT_META_TRIGGER_KEY: 'ant.meta.trigger';
export const AIVISION_META_TRIGGER_ENV: 'AIVISION_META_TRIGGER';
export const DEFAULT_META_TRIGGER: string;
export const META_TRIGGER_VALUES: readonly string[];

export function normalizeMetaTriggerValue(value: unknown): string | undefined;
export function normalizeConfiguredMetaTrigger(
  resourceValue: unknown,
  dedicatedValue: unknown,
): string | undefined;
export function resolveMetaTrigger(
  resourceValue: unknown,
  dedicatedValue: unknown,
): string;
