/** Programmatic configuration helpers. This is not an OpenCode Plugin entry. */
import { homedir } from 'node:os';
import { resolveEditionMetadata } from './edition-config.js';
import { resolveStandaloneConfigFilePath } from './standalone-config.js';

export { parseConfig } from './config.js';
export type { ObservOpencodeConfig } from './config.js';

const configSubdir = resolveEditionMetadata()?.standaloneConfigDir || 'opencode';

/** Resolve the same standalone file location used by the production Plugin. */
export function resolveStandaloneConfigPath(
  env: NodeJS.ProcessEnv = process.env,
  homeDir: string = homedir(),
  cwd: string = process.cwd(),
): string {
  return resolveStandaloneConfigFilePath('observ-opencode.json', env, homeDir, cwd, configSubdir);
}
