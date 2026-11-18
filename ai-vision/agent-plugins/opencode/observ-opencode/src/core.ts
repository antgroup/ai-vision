/** Supported semantic-engine boundary shared by live and replay adapters. */
export const OBSERV_OPENCODE_CORE_API_VERSION = 1 as const;

export { createHooks } from './hooks/index.js';
export { resetHookState } from './hooks/state.js';
export {
  bindRuntimeProfileLease,
  claimRuntimeProfile,
  releaseRuntimeProfile,
  type RuntimeProfile,
  type RuntimeProfileLease,
} from './runtime-profile.js';
export { parseAdapterProfileConfig, parseConfig } from './config.js';
export {
  loadStandaloneConfigFile,
  mergeConfigOptions,
  resolveStandaloneConfigFilePath,
} from './standalone-config.js';
export { isPrivacyMode, redactStructureInPrivacy, REDACTED } from './privacy.js';
export type { ObservOpencodeConfig, CollectionMode } from './config.js';
export type { TelemetryRuntime } from './telemetry.js';
