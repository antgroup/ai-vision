#!/usr/bin/env tsx
/**
 * esbuild bundling script for observ-opencode.
 *
 * Produces a self-contained ESM bundle with all @opentelemetry/* dependencies
 * inlined. Peer dependencies (@opencode-ai/*) remain external.
 *
 * Usage:
 *   tsx scripts/build.ts           # bundle + type declarations
 */

import { build } from 'esbuild';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { cpSync, readFileSync, rmSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..');
const pkg = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf-8'));

console.log(`[build] observ-opencode v${pkg.version}`);
rmSync(join(ROOT, 'dist'), { recursive: true, force: true });

// Step 1: esbuild — bundle src/index.ts → dist/index.js
await build({
  entryPoints: [join(ROOT, 'src/index.ts')],
  bundle: true,
  platform: 'node',
  target: 'es2022',
  format: 'esm',
  outfile: join(ROOT, 'dist/index.js'),
  sourcemap: true,
  // Some bundled OpenTelemetry dependencies still call CommonJS require()
  // for Node built-ins such as async_hooks. OpenCode Desktop imports plugins
  // as pure ESM, where require is otherwise undefined. Keep the compatibility
  // binding module-local instead of mutating globalThis from an external loader.
  banner: {
    js: 'import { createRequire as __observCreateRequire } from "node:module"; const require = __observCreateRequire(import.meta.url);',
  },
  external: [
    // Peer dependencies — provided by the host (opencode)
    '@opencode-ai/plugin',
    '@opencode-ai/sdk',
  ],
  define: {
    // Inject version at build time instead of reading package metadata at runtime.
    PLUGIN_VERSION: JSON.stringify(pkg.version),
  },
  // Suppress warnings about Node.js built-in modules
  logLevel: 'info',
});

console.log('[build] Bundle complete → dist/index.js');

// Configuration helpers are imported by developers through ./config, never
// enumerated by the host's exported-function Plugin loader.
await build({
  entryPoints: [join(ROOT, 'src/config-api.ts')],
  bundle: true,
  platform: 'node',
  target: 'es2022',
  format: 'esm',
  outfile: join(ROOT, 'dist/config-api.js'),
  sourcemap: true,
  define: { PLUGIN_VERSION: JSON.stringify(pkg.version) },
  logLevel: 'info',
});
console.log('[build] Bundle complete → dist/config-api.js');

// Supported cross-package entry points keep OpenTelemetry packages external so
// replay and production share the host process singleton APIs/providers.
const sharedExternal = [
  '@opencode-ai/plugin',
  '@opencode-ai/sdk',
  '@opentelemetry/*',
];

await build({
  entryPoints: [join(ROOT, 'src/core.ts')],
  bundle: true,
  platform: 'node',
  target: 'es2022',
  format: 'esm',
  outfile: join(ROOT, 'dist/core.js'),
  sourcemap: true,
  external: sharedExternal,
  define: { PLUGIN_VERSION: JSON.stringify(pkg.version) },
  logLevel: 'info',
});
console.log('[build] Bundle complete → dist/core.js');

await build({
  entryPoints: [join(ROOT, 'src/http-runtime.ts')],
  bundle: true,
  platform: 'node',
  target: 'es2022',
  format: 'esm',
  outfile: join(ROOT, 'dist/http-runtime.js'),
  sourcemap: true,
  external: sharedExternal,
  define: { PLUGIN_VERSION: JSON.stringify(pkg.version) },
  logLevel: 'info',
});
console.log('[build] Bundle complete → dist/http-runtime.js');

// Step 2: tsc — emit type declarations only
console.log('[build] Generating type declarations...');
execFileSync(process.execPath, [createRequire(import.meta.url).resolve('typescript/bin/tsc'), '--emitDeclarationOnly', '--declaration', '--outDir', 'dist'], {
  cwd: ROOT,
  stdio: 'inherit',
});

// Step 3: copy plugin manifest
cpSync(
  join(ROOT, 'opencode.plugin.json'),
  join(ROOT, 'dist/opencode.plugin.json'),
);
console.log('[build] Copied opencode.plugin.json → dist/');

console.log('[build] Done.');
