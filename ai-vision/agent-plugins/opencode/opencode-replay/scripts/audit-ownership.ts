import assert from 'node:assert/strict';
import { mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const packageRoot = join(dirname(fileURLToPath(import.meta.url)), '..');
const workspaceRoot = join(packageRoot, '..');
const repoRoot = join(workspaceRoot, '..');
const sourceRoot = join(packageRoot, 'src');
const sourceFiles = readdirSync(sourceRoot).filter(file => file.endsWith('.ts')).sort();
const allowedOwners = ['observ-opencode/core', 'observ-opencode/http-runtime'];
const resolvedEdges: Array<{ from: string; to: string }> = [];
const violations: string[] = [];

for (const file of sourceFiles) {
  const path = join(sourceRoot, file);
  const text = readFileSync(path, 'utf8');
  const from = relative(repoRoot, path);
  for (const match of text.matchAll(/from ['"](observ-opencode(?:\/[^'"]+)?)['"]/gu)) {
    resolvedEdges.push({ from, to: match[1] });
    if (!allowedOwners.includes(match[1])) violations.push(`${from}: unsupported import ${match[1]}`);
  }
  if (/from ['"]observ-opencode\/src\//u.test(text)) violations.push(`${from}: imports private production source`);
  if (/\bparseAdapterProfileConfig\b/u.test(text)) violations.push(`${from}: imports production runtime config parser`);
  for (const forbidden of [
    /\.startSpan\(/u,
    /\.startActiveSpan\(/u,
    /\.setAttribute\(/u,
    /\.setStatus\(/u,
    /\.recordException\(/u,
    /\.create(?:Counter|Histogram|UpDownCounter)\(/u,
  ]) {
    if (forbidden.test(text)) violations.push(`${from}: owns forbidden semantic construction ${forbidden.source}`);
  }
}

const productionManifest = readFileSync(join(workspaceRoot, 'observ-opencode/package.json'), 'utf8');
const productionEntry = readFileSync(join(workspaceRoot, 'observ-opencode/dist/index.js'), 'utf8');
for (const forbidden of ['FileSpanExporter', 'FileLogExporter', 'FileMetricExporter', 'createTranscriptRecorder', 'decorateHooksWithTranscript']) {
  if (productionEntry.includes(forbidden)) violations.push(`production dist/index.js contains ${forbidden}`);
}
const manifest = JSON.parse(productionManifest) as Record<string, unknown>;
if ('bin' in manifest) violations.push('production manifest exports CLI bins');
if (JSON.stringify(manifest).includes('opencode-replay')) violations.push('production manifest references opencode-replay');

resolvedEdges.sort((a, b) => `${a.from}:${a.to}`.localeCompare(`${b.from}:${b.to}`));
const evidence = {
  schemaVersion: 1,
  semanticOwner: 'observ-opencode/core',
  transportOwner: 'observ-opencode/http-runtime',
  allowedOwners,
  entryPoints: sourceFiles.map(file => relative(repoRoot, join(sourceRoot, file))),
  resolvedEdges,
  forbiddenSemanticConstructors: [
    'startSpan', 'startActiveSpan', 'setAttribute', 'setStatus', 'recordException',
    'createCounter', 'createHistogram', 'createUpDownCounter',
  ],
  violations,
  pass: violations.length === 0,
};
const output = join(repoRoot, '.omx/evidence/opencode-replay/import-ownership.v1.json');
mkdirSync(dirname(output), { recursive: true });
writeFileSync(output, `${JSON.stringify(evidence, null, 2)}\n`);
assert.deepEqual(violations, [], `semantic ownership violations:\n${violations.join('\n')}`);
console.log(`semantic ownership audit passed (${resolvedEdges.length} supported package edges)`);
