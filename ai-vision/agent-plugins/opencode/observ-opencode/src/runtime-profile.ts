export type RuntimeProfile = 'production' | 'replay-local';

export interface RuntimeProfileLease {
  readonly profile: RuntimeProfile;
  readonly token: symbol;
}

interface RuntimeProfileSlot {
  profile: RuntimeProfile;
  token: symbol;
}

const SLOT = Symbol.for('observ-opencode.runtime-profile.v1');

function runtimeGlobal(): typeof globalThis & { [SLOT]?: RuntimeProfileSlot } {
  return globalThis as typeof globalThis & { [SLOT]?: RuntimeProfileSlot };
}

export function claimRuntimeProfile(profile: RuntimeProfile): RuntimeProfileLease {
  const host = runtimeGlobal();
  const existing = host[SLOT];
  if (existing) {
    throw new Error(
      `OpenCode telemetry runtime profile already claimed by "${existing.profile}"; `
      + `cannot initialize "${profile}" in the same process`,
    );
  }

  const lease = { profile, token: Symbol(profile) } as const;
  host[SLOT] = lease;
  return lease;
}

export function releaseRuntimeProfile(lease: RuntimeProfileLease): void {
  const host = runtimeGlobal();
  const existing = host[SLOT];
  if (!existing || existing.profile !== lease.profile || existing.token !== lease.token) {
    throw new Error(`Cannot release OpenCode telemetry runtime profile "${lease.profile}": owner token mismatch`);
  }
  delete host[SLOT];
}

/**
 * Bind shutdown ordering to a claimed runtime profile.
 *
 * OpenTelemetry global providers cannot be safely rebound after shutdown, so a
 * profile that reached runtime registration owns the process for its lifetime.
 * Only callers that fail before a runtime is returned may release their lease.
 */
export function bindRuntimeProfileLease<T extends {
  forceFlush: () => Promise<void>;
  shutdown: () => Promise<void>;
}>(
  runtime: T,
  _lease: RuntimeProfileLease,
): T {
  return {
    ...runtime,
    shutdown: async () => {
      await runtime.forceFlush();
      await runtime.shutdown();
    },
  };
}
