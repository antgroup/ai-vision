/** Developer-only OpenClaw raw callback recorder. */

import { parseReplayConfig } from './src/config.js';
import { registerAllHooks } from './src/hooks.js';
import { SessionTranscriptWriter } from './src/transcript-writer.js';
import type { OpenClawPluginApi } from 'openclaw/plugin-sdk/plugin-entry';

let registerCount = 0;

export default {
  id: 'openclaw-replay',
  name: 'OpenClaw Replay',
  description: 'Developer-only raw callback recorder for offline replay.',

  register(api: OpenClawPluginApi) {
    const config = parseReplayConfig(api.pluginConfig, {
      warn: message => api.logger.warn?.(message),
    });
    if (!config.captureEnabled) {
      api.logger.debug?.('[openclaw-replay] capture disabled');
      return;
    }

    registerCount += 1;
    const instanceId = 'replay-' + registerCount + '-' + Date.now().toString(36);
    const writer = new SessionTranscriptWriter({
      sessionDir: config.sessionDir,
      sessionName: config.sessionName,
      collectionMode: config.collectionMode,
      stdout: config.stdout,
      warn: (message) => api.logger.warn?.(message),
    });
    if (config.debug) {
      api.logger.debug?.('[openclaw-replay] replay diagnostics enabled');
    }
    registerAllHooks(api, writer, instanceId);

    api.registerService({
      id: 'openclaw-replay',
      start: () => {
        api.logger.info?.('[openclaw-replay] raw capture active'
          + (config.sessionDir ? ': ' + config.sessionDir : ' (stdout only)'));
      },
      stop: () => undefined,
    });
  },
};

export { parseReplayConfig, type ReplayPluginConfig } from './src/config.js';
export { SessionTranscriptWriter, type TranscriptRecord } from './src/transcript-writer.js';
