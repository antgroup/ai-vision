#!/usr/bin/env node
import { createHash } from 'node:crypto';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const outputDir = join(packageRoot, 'test', 'fixtures', 'pi-skill-contract');
const versions = process.argv.slice(2).filter(value => /^\d+\.\d+\.\d+$/.test(value));
const requestedVersions = versions.length ? versions : ['0.80.6', '0.80.10', '1.0.2'];
const packageName = '@earendil-works/pi-coding-agent';

function run(command, args, cwd) {
  const result = spawnSync(command, args, { cwd, encoding: 'utf8' });
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(' ')} failed: ${result.stderr || result.stdout}`);
  }
  return result.stdout;
}

function stable(value, actualRoot) {
  return value.split(actualRoot).join('/opt/pi-contract/skills/weather');
}

async function generate(version) {
  const root = mkdtempSync(join(tmpdir(), `observ-pi-contract-${version}-`));
  try {
    const packOutput = run('npm', [
      'pack',
      `${packageName}@${version}`,
      '--ignore-scripts',
      '--json',
      '--pack-destination',
      root,
    ], root);
    const [pack] = JSON.parse(packOutput);
    writeFileSync(join(root, 'package.json'), '{"private":true,"type":"module"}\n');
    run('npm', [
      'install',
      '--ignore-scripts',
      '--no-audit',
      '--no-fund',
      '--package-lock=false',
      '--save-exact',
      `${packageName}@${version}`,
    ], root);

    const extracted = join(root, 'node_modules', '@earendil-works', 'pi-coding-agent');
    const agentSessionPath = join(extracted, 'dist', 'core', 'agent-session.js');
    const skillsPath = join(extracted, 'dist', 'core', 'skills.js');
    const [{ AgentSession, parseSkillBlock }, { formatSkillsForPrompt }] = await Promise.all([
      import(`${pathToFileURL(agentSessionPath).href}?version=${version}`),
      import(`${pathToFileURL(skillsPath).href}?version=${version}`),
    ]);

    const skillRoot = join(root, 'workspace', 'skills', 'weather');
    const skillPath = join(skillRoot, 'SKILL.md');
    mkdirSync(skillRoot, { recursive: true });
    writeFileSync(skillPath, [
      '---',
      'name: weather',
      'description: Weather workflow',
      '---',
      'Follow the weather workflow.',
      '',
    ].join('\n'));
    const skill = {
      name: 'weather',
      description: 'Weather workflow',
      filePath: skillPath,
      baseDir: skillRoot,
      disableModelInvocation: false,
    };
    const session = Object.create(AgentSession.prototype);
    session._resourceLoader = { getSkills: () => ({ skills: [skill] }) };
    session._extensionRunner = { emitError: error => { throw new Error(String(error?.error ?? error)); } };

    const input = '/skill:weather Hangzhou';
    const expanded = session._expandSkillCommand(input);
    const parsed = parseSkillBlock(expanded);
    if (!parsed || parsed.name !== 'weather' || parsed.location !== skillPath) {
      throw new Error(`Pi ${version} command expansion contract did not round-trip`);
    }
    const systemPrompt = formatSkillsForPrompt([skill]);
    const stableRoot = skillRoot;
    const fixture = {
      metadata: {
        package: packageName,
        version,
        integrity: pack.integrity,
        shasum: pack.shasum,
        generatorRevision: 1,
        sourceHash: createHash('sha256')
          .update(readFileSync(agentSessionPath))
          .update(readFileSync(skillsPath))
          .digest('hex'),
      },
      command: {
        input,
        expanded: stable(expanded, stableRoot),
        expectedName: 'weather',
        expectedLocation: '/opt/pi-contract/skills/weather/SKILL.md',
      },
      catalog: {
        systemPrompt: stable(systemPrompt, stableRoot),
        expectedName: 'weather',
        expectedLocation: '/opt/pi-contract/skills/weather/SKILL.md',
      },
    };
    mkdirSync(outputDir, { recursive: true });
    writeFileSync(join(outputDir, `${version}.json`), `${JSON.stringify(fixture, null, 2)}\n`);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

for (const version of requestedVersions) await generate(version);
