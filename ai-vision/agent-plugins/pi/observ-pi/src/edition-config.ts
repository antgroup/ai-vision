import { EMBEDDED_CONFIG, EMBEDDED_EDITION } from './edition-config.generated.js';

export type EditionName = 'common' | 'aivision';

export function resolveEditionName(): EditionName {
  return EMBEDDED_EDITION;
}

export function resolveEditionConfig(): Record<string, unknown> {
  return EMBEDDED_CONFIG as Record<string, unknown>;
}

export function isAivisionEdition(): boolean {
  return resolveEditionName() === 'aivision';
}
