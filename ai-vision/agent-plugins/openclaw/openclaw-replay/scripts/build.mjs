import { build } from 'esbuild';
import { copyFileSync, mkdirSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = dirname(fileURLToPath(import.meta.url));
const packageDir = resolve(scriptDir, '..');
const packageJson = JSON.parse(readFileSync(resolve(packageDir, 'package.json'), 'utf8'));

await build({
  entryPoints: [
    resolve(packageDir, 'index.ts'),
    resolve(packageDir, 'src/report.ts'),
  ],
  outdir: resolve(packageDir, 'dist'),
  outbase: packageDir,
  bundle: true,
  platform: 'node',
  format: 'esm',
  target: 'node18',
  external: ['openclaw'],
  banner: {
    js: "import { createRequire as __createRequire } from 'node:module'; const require = __createRequire(import.meta.url);",
  },
  define: {
    __PLUGIN_VERSION__: JSON.stringify(packageJson.version),
  },
});

mkdirSync(resolve(packageDir, 'dist'), { recursive: true });
copyFileSync(resolve(packageDir, 'openclaw.plugin.json'), resolve(packageDir, 'dist', 'openclaw.plugin.json'));
