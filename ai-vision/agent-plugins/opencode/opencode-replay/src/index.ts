import type { Hooks, Plugin, PluginInput } from '@opencode-ai/plugin';
import { assertCoreApiCompatible } from './compatibility.js';
import { createTranscriptHooks } from './decorator.js';
import { transcriptRecorderFromEnvironment } from './transcript.js';
import { resolveReplayPluginConfig } from './local-config.js';
import { initLogger } from './logger.js';

export const OpencodeReplayPlugin: Plugin = async (
  _input: PluginInput,
  options?: Record<string, unknown>,
): Promise<Hooks> => {
  const resolved = resolveReplayPluginConfig(options);
  initLogger(resolved.local.debug);
  if (!resolved.local.captureEnabled) return {};
  assertCoreApiCompatible();

  return createTranscriptHooks(
    transcriptRecorderFromEnvironment(resolved.local.collectionMode, resolved.local.sessionDir),
  ) as Hooks;
};

export default OpencodeReplayPlugin;
