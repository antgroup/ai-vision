#!/usr/bin/env tsx

import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { pathToFileURL } from 'node:url';
import { join, resolve } from 'node:path';

const bundle = resolve('dist/index.js');
const source = readFileSync(bundle, 'utf8');
assert.match(source, /createRequire as __observCreateRequire/,
  'production bundle must carry its own ESM createRequire compatibility binding');

const moduleUrl = pathToFileURL(bundle).href;
const isolatedConfig = mkdtempSync(join(tmpdir(), 'observ-opencode-loader-'));
try {
const output = execFileSync(process.execPath, [
  '--input-type=module',
  '--eval',
  `
    if (typeof require !== 'undefined') throw new Error('test process unexpectedly exposes require');
    const entry = await import(${JSON.stringify(moduleUrl)});
    const packageEntry = await import('observ-opencode');
    if (entry.default !== packageEntry.default) throw new Error('package root differs from production plugin file');
    if (Object.hasOwn(globalThis, 'require')) throw new Error('plugin polluted globalThis.require');
    // Official OpenCode loads every distinct function export as a Plugin. Run
    // that contract against the shipped bundle, not just its default export.
    const seen = new Set();
    const input = { directory: process.cwd(), worktree: process.cwd(),
      project: { id: 'official-loader', worktree: process.cwd() } };
    for (const value of Object.values(entry)) {
      if (typeof value !== 'function' || seen.has(value)) continue;
      seen.add(value);
      const hooks = await value(input, { enableTelemetry: false });
      if (!hooks || typeof hooks !== 'object' || Array.isArray(hooks)) {
        throw new Error('production entry exported a non-Plugin function');
      }
    }
    if (seen.size !== 1) throw new Error('production entry must expose one distinct Plugin factory');
    const config = await import('observ-opencode/config');
    const runtime = await import('observ-opencode/http-runtime');
    const core = await import('observ-opencode/core');
    if (config.parseConfig({ enableTelemetry: false }).enableTelemetry !== false
      || typeof config.resolveStandaloneConfigPath() !== 'string'
      || typeof runtime.initObservabilityTelemetry !== 'function'
      || typeof core.createHooks !== 'function') {
      throw new Error('supported programmatic helper subpaths are unavailable');
    }
    console.log('desktop esm import passed');
    console.log('official exported-function loader contract passed');
  `,
], {
  cwd: process.cwd(),
  encoding: 'utf8',
  env: {
    ...Object.fromEntries(['PATH', 'SystemRoot', 'SYSTEMROOT', 'COMSPEC', 'PATHEXT', 'WINDIR']
      .filter(key => process.env[key] !== undefined).map(key => [key, process.env[key]])),
    HOME: isolatedConfig, USERPROFILE: isolatedConfig, OPENCODE_CONFIG_DIR: isolatedConfig,
    AGENT_ENABLE_TELEMETRY: 'false', NODE_OPTIONS: '',
  },
});

assert.match(output, /desktop esm import passed/);
assert.match(output, /official exported-function loader contract passed/);
console.log('Desktop loader compatibility tests passed');
} finally {
  rmSync(isolatedConfig, { recursive: true, force: true });
}
