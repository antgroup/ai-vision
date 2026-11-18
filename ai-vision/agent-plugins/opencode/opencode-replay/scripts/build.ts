import { build } from 'esbuild';
import { execSync } from 'node:child_process';
import { cpSync, rmSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const external = [
  '@opencode-ai/plugin',
  '@opencode-ai/sdk',
  '@opentelemetry/*',
  'observ-opencode',
  'observ-opencode/*',
];

rmSync(join(root, 'dist'), { recursive: true, force: true });

await Promise.all([
  ['index', 'src/index.ts'],
  ['replay', 'src/replay.ts'],
  ['report', 'src/report.ts'],
].map(async ([name, source]) => {
  await build({
    entryPoints: [join(root, source)],
    bundle: true,
    platform: 'node',
    target: 'es2022',
    format: 'esm',
    outfile: join(root, `dist/${name}.js`),
    sourcemap: true,
    external,
  });
}));

execSync('npx tsc --project tsconfig.build.json', { cwd: root, stdio: 'inherit' });
cpSync(join(root, 'opencode.plugin.json'), join(root, 'dist/opencode.plugin.json'));
