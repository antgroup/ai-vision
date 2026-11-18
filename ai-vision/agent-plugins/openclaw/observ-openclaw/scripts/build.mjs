import { build } from 'esbuild';
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = dirname(fileURLToPath(import.meta.url));
const packageDir = resolve(scriptDir, '..');
const packageJson = JSON.parse(readFileSync(resolve(packageDir, 'package.json'), 'utf8'));

const result = await build({
  entryPoints: [
    resolve(packageDir, 'index.ts'),
    resolve(packageDir, 'src/semconv.ts'),
    resolve(packageDir, 'src/hooks/index.ts'),
    resolve(packageDir, 'src/telemetry-extensions.ts'),
  ],
  outdir: resolve(packageDir, 'dist'),
  outbase: packageDir,
  bundle: true,
  platform: 'node',
  format: 'esm',
  target: 'node18',
  external: ['openclaw'],
  metafile: true,
  sourcemap: false,
  logLevel: 'info',
  banner: {
    js: "import { createRequire as __createRequire } from 'node:module'; const require = __createRequire(import.meta.url);",
  },
  define: {
    __PLUGIN_VERSION__: JSON.stringify(packageJson.version),
  },
});

writeFileSync(
  resolve(packageDir, 'dist', 'esbuild-metafile.json'),
  `${JSON.stringify(result.metafile, null, 2)}\n`,
  'utf8',
);
