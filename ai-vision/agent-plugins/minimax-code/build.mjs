import { copyFile, mkdir } from 'node:fs/promises';

const source = new URL('./', import.meta.url);
const dist = new URL('dist/', source);
await mkdir(dist, { recursive: true });
await Promise.all([
  copyFile(new URL('run.mjs', source), new URL('index.js', dist)),
  copyFile(new URL('history.mjs', source), new URL('history.mjs', dist)),
  copyFile(new URL('telemetry.mjs', source), new URL('telemetry.mjs', dist)),
  copyFile(new URL('../shared/ingest-auth.mjs', source), new URL('ingest-auth.mjs', dist)),
]);
