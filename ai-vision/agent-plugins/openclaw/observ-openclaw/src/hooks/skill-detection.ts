import type { ToolClassification } from '../semconv.js';
import {
  extractLiteralSkillName as sharedLiteralSkillName,
  isAcceptableSkillPath,
  parseSkillCatalog,
} from '../../../../shared/skill-catalog-core.mjs';

export const MAX_SYSTEM_PROMPT_CHARS = 1_048_576;
export const MAX_SKILL_SECTION_CHARS = 262_144;
export const MAX_SKILL_ENTRIES = 256;
export const MAX_SKILL_NAME_CHARS = 128;
export const MAX_SKILL_LOCATION_CHARS = 4_096;
export const MAX_SKILL_VERSION_CHARS = 256;

const FALLBACK_NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

export interface SkillCatalogEntry {
  name: string;
  location: string;
  version?: string;
}

export type SkillCatalogParseResult =
  | { status: 'absent' }
  | { status: 'invalid'; reason: string }
  | { status: 'valid'; entries: Map<string, SkillCatalogEntry> };

export interface SkillCandidate {
  entry: SkillCatalogEntry;
  source: 'catalog' | 'path_fallback';
}

/** Match cchooks: publisher:plugin:name becomes plugin:name; shorter names are unchanged. */
export function extractLiteralSkillName(params: unknown): string | undefined {
  return sharedLiteralSkillName(params);
}

export function isAcceptableCatalogSkillPath(value: string): boolean {
  return isAcceptableSkillPath(value);
}

export function parseAvailableSkills(systemPrompt: unknown): SkillCatalogParseResult {
  return parseSkillCatalog(systemPrompt, { allowVersion: true });
}

export function detectSkillCandidate(
  toolName: string,
  params: unknown,
  catalog: Map<string, SkillCatalogEntry> | undefined,
): SkillCandidate | undefined {
  if (toolName !== 'read' || !params || typeof params !== 'object') return undefined;
  const rawPath = (params as Record<string, unknown>).path;
  if (typeof rawPath !== 'string') return undefined;
  const path = rawPath.trim();
  if (!isAcceptableCatalogSkillPath(path)) return undefined;

  if (catalog !== undefined) {
    const entry = catalog.get(path);
    return entry ? { entry, source: 'catalog' } : undefined;
  }
  if (!path.startsWith('/')) return undefined;

  const segments = path.slice(1).split('/');
  if (segments.length < 3) return undefined;
  const suffix = segments.slice(-3);
  if (suffix[0] !== 'skills' || suffix[2] !== 'SKILL.md' || !FALLBACK_NAME_PATTERN.test(suffix[1])) {
    return undefined;
  }
  return {
    entry: { name: suffix[1], location: path },
    source: 'path_fallback',
  };
}

/** Build cchooks-compatible logical Skill attributes. */
export function classifySkill(
  entry: SkillCatalogEntry,
  rawToolName: string,
  realToolCallId?: string,
): ToolClassification {
  return {
    'agentic.tool.type': 'skill',
    'agentic.tool.name': entry.name,
    'agentic.tool.raw_name': rawToolName,
    'agentic.tool.id': realToolCallId || `skill-${entry.name}`,
  };
}
