export const ANT_META_TRIGGER_KEY = 'ant.meta.trigger';
export const AIVISION_META_TRIGGER_ENV = 'AIVISION_META_TRIGGER';
export const DEFAULT_META_TRIGGER = '';
export const META_TRIGGER_VALUES = Object.freeze([
  'automation',
  'interactive',
  'unknown',
]);

const META_TRIGGER_VALUE_SET = new Set(META_TRIGGER_VALUES);

export function normalizeMetaTriggerValue(value) {
  if (value === undefined) return undefined;
  if (typeof value !== 'string') return 'unknown';
  const normalized = value.trim();
  return META_TRIGGER_VALUE_SET.has(normalized) ? normalized : 'unknown';
}

export function normalizeConfiguredMetaTrigger(resourceValue, dedicatedValue) {
  return normalizeMetaTriggerValue(
    dedicatedValue !== undefined ? dedicatedValue : resourceValue,
  );
}

export function resolveMetaTrigger(resourceValue, dedicatedValue) {
  return normalizeConfiguredMetaTrigger(resourceValue, dedicatedValue)
    ?? DEFAULT_META_TRIGGER;
}
