import { spawnSync } from 'node:child_process';
import { builtinModules, createRequire } from 'node:module';
import { rmSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const distDir = resolve(packageRoot, 'dist');
const manifest = JSON.parse(readFileSync(resolve(packageRoot, 'package.json'), 'utf8'));
const require = createRequire(import.meta.url);
const typescriptBin = require.resolve('typescript/bin/tsc');
const allowedBuiltins = new Set([
  ...builtinModules,
  ...builtinModules.map(name => `node:${name}`),
]);

function fail(message) {
  throw new Error(`[build] ${message}`);
}

function portable(path) {
  return path.split(sep).join('/');
}

function otelIdentity(input) {
  const normalized = '/' + portable(input).replace(/^\/+/, '');
  const marker = '/node_modules/@opentelemetry/';
  const markerIndex = normalized.lastIndexOf(marker);
  if (markerIndex < 0) return undefined;
  return normalized.slice(markerIndex + '/node_modules/'.length);
}

rmSync(distDir, { recursive: true, force: true });

const declarations = spawnSync(process.execPath, [
  typescriptBin,
  '-p',
  resolve(packageRoot, 'tsconfig.json'),
  '--emitDeclarationOnly',
], {
  cwd: packageRoot,
  encoding: 'utf8',
});
if (declarations.status !== 0) {
  process.stderr.write(declarations.stderr || declarations.stdout);
  process.exit(declarations.status ?? 1);
}

const result = await build({
  absWorkingDir: packageRoot,
  entryPoints: {
    index: 'src/index.ts',
    core: 'src/core.ts',
    telemetry: 'src/telemetry.ts',
    config: 'src/config.ts',
    vcs: 'src/vcs.ts',
  },
  bundle: true,
  splitting: true,
  format: 'esm',
  platform: 'node',
  target: 'node18',
  outdir: 'dist',
  chunkNames: 'chunks/[name]-[hash]',
  banner: {
    js: "import { createRequire as __createRequire } from 'node:module'; const require = __createRequire(import.meta.url);",
  },
  define: {
    __OBSERV_DSH_VERSION__: JSON.stringify(manifest.version),
  },
  metafile: true,
  sourcemap: false,
  legalComments: 'none',
  logLevel: 'info',
});

for (const [output, metadata] of Object.entries(result.metafile.outputs)) {
  for (const imported of metadata.imports) {
    if (imported.external && !allowedBuiltins.has(imported.path)) {
      fail(`${output} retains non-Node external import ${imported.path}`);
    }
  }
}

const otelInputs = new Map();
for (const output of Object.values(result.metafile.outputs)) {
  for (const [input, contribution] of Object.entries(output.inputs ?? {})) {
    if (!contribution.bytesInOutput) continue;
    const identity = otelIdentity(input);
    if (!identity) continue;
    const carriers = otelInputs.get(identity) ?? [];
    carriers.push(output);
    otelInputs.set(identity, carriers);
  }
}
if (otelInputs.size === 0) fail('bundle metafile contains no OpenTelemetry runtime inputs');
for (const [identity, carriers] of otelInputs) {
  if (carriers.length !== 1) {
    fail(`OpenTelemetry runtime input ${identity} is duplicated across ${carriers.length} outputs`);
  }
}

const publicMetafile = {
  version: 1,
  entryPoints: ['dist/index.js', 'dist/core.js', 'dist/telemetry.js'],
  outputs: Object.fromEntries(Object.entries(result.metafile.outputs).map(([output, metadata]) => [
    portable(relative(packageRoot, resolve(packageRoot, output))),
    {
      entryPoint: metadata.entryPoint ? portable(metadata.entryPoint) : undefined,
      imports: metadata.imports,
      inputs: Object.fromEntries(Object.entries(metadata.inputs ?? {}).map(([input, contribution]) => [
        portable(input),
        contribution,
      ])),
    },
  ])),
};
writeFileSync(resolve(distDir, 'bundle-meta.json'), `${JSON.stringify(publicMetafile, null, 2)}\n`);
