import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseAvailableSkills, parsePiSkillInvocation } from '../src/skill-detection.js';

interface ContractFixture {
  metadata: {
    package: string;
    version: string;
    integrity: string;
    shasum: string;
    generatorRevision: number;
    sourceHash: string;
  };
  command: { input: string; expanded: string; expectedName: string; expectedLocation: string };
  catalog: { systemPrompt: string; expectedName: string; expectedLocation: string };
}

const root = join(dirname(fileURLToPath(import.meta.url)), 'fixtures', 'pi-skill-contract');

for (const version of ['0.80.6', '0.80.10', '1.0.2']) {
  test(`Pi ${version} Skill command and catalog contracts remain recognizable`, () => {
    const fixture = JSON.parse(readFileSync(join(root, `${version}.json`), 'utf8')) as ContractFixture;
    assert.equal(fixture.metadata.package, '@earendil-works/pi-coding-agent');
    assert.equal(fixture.metadata.version, version);
    assert.match(fixture.metadata.integrity, /^sha512-/);
    assert.match(fixture.metadata.shasum, /^[a-f0-9]{40}$/);
    assert.match(fixture.metadata.sourceHash, /^[a-f0-9]{64}$/);
    assert.equal(fixture.metadata.generatorRevision, 1);
    assert.doesNotMatch(JSON.stringify(fixture), /observ-pi-contract-/);

    assert.deepEqual(parsePiSkillInvocation(fixture.command.expanded), {
      name: fixture.command.expectedName,
      location: fixture.command.expectedLocation,
    });
    const catalog = parseAvailableSkills(fixture.catalog.systemPrompt);
    assert.deepEqual(catalog?.get(fixture.catalog.expectedLocation), {
      name: fixture.catalog.expectedName,
      location: fixture.catalog.expectedLocation,
    });
  });
}
