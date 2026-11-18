import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, isAbsolute, join, resolve } from 'node:path';

export function mergeConfigOptions(
  base: Record<string, unknown>,
  override: Record<string, unknown>,
): Record<string, unknown> {
  const result: Record<string, unknown> = { ...base };
  for (const [key, overrideValue] of Object.entries(override)) {
    const baseValue = base[key];
    if (
      baseValue && overrideValue
      && typeof baseValue === 'object' && !Array.isArray(baseValue)
      && typeof overrideValue === 'object' && !Array.isArray(overrideValue)
    ) {
      result[key] = mergeConfigOptions(
        baseValue as Record<string, unknown>,
        overrideValue as Record<string, unknown>,
      );
    } else {
      result[key] = overrideValue;
    }
  }
  return result;
}

export function resolveStandaloneConfigFilePath(
  filename: string,
  env: NodeJS.ProcessEnv = process.env,
  homeDir: string = homedir(),
  cwd: string = process.cwd(),
  configSubdir = 'opencode',
): string {
  const customConfigPath = env.OPENCODE_CONFIG?.trim();
  if (customConfigPath) {
    const resolvedConfigPath = isAbsolute(customConfigPath)
      ? customConfigPath
      : resolve(cwd, customConfigPath);
    return join(dirname(resolvedConfigPath), filename);
  }

  const customConfigDir = env.OPENCODE_CONFIG_DIR?.trim();
  if (customConfigDir) {
    const resolvedConfigDir = isAbsolute(customConfigDir)
      ? customConfigDir
      : resolve(cwd, customConfigDir);
    return join(resolvedConfigDir, filename);
  }

  return join(homeDir, '.config', configSubdir, filename);
}

export function loadStandaloneConfigFile(path: string): Record<string, unknown> | undefined {
  try {
    const parsed: unknown = JSON.parse(readFileSync(path, 'utf8'));
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error('configuration root must be a JSON object');
    }
    return parsed as Record<string, unknown>;
  } catch (error) {
    if (
      error && typeof error === 'object' && 'code' in error
      && (error as NodeJS.ErrnoException).code === 'ENOENT'
    ) {
      return undefined;
    }
    throw error;
  }
}
