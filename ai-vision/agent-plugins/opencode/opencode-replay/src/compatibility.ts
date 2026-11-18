import { OBSERV_OPENCODE_CORE_API_VERSION } from 'observ-opencode/core';

const EXPECTED_CORE_API_VERSION = 1;

export function assertCoreApiCompatible(): void {
  if (OBSERV_OPENCODE_CORE_API_VERSION !== EXPECTED_CORE_API_VERSION) {
    throw new Error(
      `Incompatible observ-opencode/core API: expected ${EXPECTED_CORE_API_VERSION}, got ${OBSERV_OPENCODE_CORE_API_VERSION}`,
    );
  }
}
