import {
  extractLiteralSkillName,
  isAcceptableSkillPath as sharedAcceptableSkillPath,
  parseSkillCatalog,
} from '../../../shared/skill-catalog-core.mjs';

export const MAX_SYSTEM_PROMPT_CHARS = 1_048_576;
export const MAX_SKILL_SECTION_CHARS = 262_144;
export const MAX_SKILL_ENTRIES = 256;

const SKILL_NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$/;
const CONTROL_CHARACTER_PATTERN = /[\u0000-\u001f\u007f]/;

export interface SkillCatalogEntry {
  name: string;
  location: string;
}

export interface PiSkillInvocation {
  name: string;
  location: string;
}

export type SkillEvidenceSource = 'native' | 'command' | 'catalog_read';

export interface SkillEvidence {
  name: string;
  source: SkillEvidenceSource;
  occurrenceId: string;
  sourceCallId?: string;
}

export function isAcceptableSkillPath(value: string): boolean {
  return sharedAcceptableSkillPath(value);
}

/** Parse Pi's Agent Skills catalog, rejecting malformed or ambiguous entries. */
export function parseAvailableSkills(systemPrompt: unknown): Map<string, SkillCatalogEntry> | undefined {
  const result = parseSkillCatalog(systemPrompt, { requireDescription: true });
  if (result.status !== 'valid') return undefined;
  return new Map([...result.entries].map(([location, entry]) => [
    location,
    { name: entry.name, location: entry.location },
  ]));
}

/** Parse the exact structured block produced by Pi for `/skill:<name>`. */
export function parsePiSkillInvocation(prompt: unknown): PiSkillInvocation | undefined {
  if (typeof prompt !== 'string') return undefined;
  const match = /^<skill name="([^"]+)" location="([^"]+)">\n[\s\S]*?\n<\/skill>(?:\n\n[\s\S]*)?$/.exec(prompt);
  if (!match) return undefined;
  const [, name, location] = match;
  if (!SKILL_NAME_PATTERN.test(name) || !isAcceptableSkillPath(location)) return undefined;
  return { name, location };
}

export function literalSkillName(args: unknown): string | undefined {
  return extractLiteralSkillName(args, true);
}

function acceptableOpaqueId(value: unknown): value is string {
  return typeof value === 'string'
    && value.length > 0
    && value.length <= 256
    && value === value.trim()
    && !CONTROL_CHARACTER_PATTERN.test(value);
}

/**
 * Validate an explicit upstream causal envelope. Never synthesize occurrence
 * identity from a Skill name, timestamp, or event proximity.
 */
export function parseSkillEvidence(
  value: unknown,
  expected: { name: string; source: SkillEvidenceSource; sourceCallId?: string },
): SkillEvidence | undefined {
  if (!value || typeof value !== 'object') return undefined;
  const source = value as Record<string, unknown>;
  if (source.name !== expected.name || source.source !== expected.source) return undefined;
  if (!acceptableOpaqueId(source.occurrenceId)) return undefined;
  if (expected.source === 'catalog_read') {
    if (!acceptableOpaqueId(expected.sourceCallId) || source.sourceCallId !== expected.sourceCallId) return undefined;
    return {
      name: expected.name,
      source: expected.source,
      occurrenceId: source.occurrenceId,
      sourceCallId: expected.sourceCallId,
    };
  }
  if (source.sourceCallId !== undefined) return undefined;
  return { name: expected.name, source: expected.source, occurrenceId: source.occurrenceId };
}
