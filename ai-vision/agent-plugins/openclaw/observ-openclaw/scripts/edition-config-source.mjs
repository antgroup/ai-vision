import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = dirname(fileURLToPath(import.meta.url));
const packageDir = resolve(scriptDir, '..');

export const AIVISION_CONFIG_SOURCE = resolve(
  packageDir,
  '..',
  '..',
  'docs',
  'config',
  'openclaw.example.jsonc',
);

function stripJsonComments(source) {
  let result = '';
  let inString = false;
  let escaped = false;
  let lineComment = false;
  let blockComment = false;

  for (let index = 0; index < source.length; index += 1) {
    const current = source[index];
    const next = source[index + 1];

    if (lineComment) {
      if (current === '\n' || current === '\r') {
        lineComment = false;
        result += current;
      }
      continue;
    }

    if (blockComment) {
      if (current === '*' && next === '/') {
        blockComment = false;
        index += 1;
      } else if (current === '\n' || current === '\r') {
        result += current;
      }
      continue;
    }

    if (inString) {
      result += current;
      if (escaped) {
        escaped = false;
      } else if (current === '\\') {
        escaped = true;
      } else if (current === '"') {
        inString = false;
      }
      continue;
    }

    if (current === '"') {
      inString = true;
      result += current;
    } else if (current === '/' && next === '/') {
      lineComment = true;
      index += 1;
    } else if (current === '/' && next === '*') {
      blockComment = true;
      index += 1;
    } else {
      result += current;
    }
  }

  if (inString || blockComment) {
    throw new Error('unterminated JSONC string or block comment');
  }
  return result;
}

export function parseJsonc(source, sourcePath = 'JSONC input') {
  try {
    return JSON.parse(stripJsonComments(source));
  } catch (error) {
    throw new Error(`Invalid managed config source ${sourcePath}: ${error.message}`, {
      cause: error,
    });
  }
}

export function loadEditionConfig(edition) {
  if (edition === 'aivision') {
    return {
      config: parseJsonc(readFileSync(AIVISION_CONFIG_SOURCE, 'utf8'), AIVISION_CONFIG_SOURCE),
      sourcePath: AIVISION_CONFIG_SOURCE,
    };
  }

  if (edition === 'common') {
    const sourcePath = join(packageDir, 'configs', 'common.config.json');
    return {
      config: JSON.parse(readFileSync(sourcePath, 'utf8')),
      sourcePath,
    };
  }

  throw new Error(`Unsupported EDITION "${edition}". Expected one of: common, aivision.`);
}
