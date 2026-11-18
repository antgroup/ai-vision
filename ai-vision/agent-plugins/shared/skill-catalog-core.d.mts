export const MAX_SYSTEM_PROMPT_CHARS: number;
export const MAX_SKILL_SECTION_CHARS: number;
export const MAX_SKILL_ENTRIES: number;
export const MAX_SKILL_NAME_CHARS: number;
export const MAX_SKILL_LOCATION_CHARS: number;
export const MAX_SKILL_VERSION_CHARS: number;

export interface SharedSkillCatalogEntry {
  name: string;
  location: string;
  version?: string;
}

export type SharedSkillCatalogParseResult =
  | { status: 'absent' }
  | { status: 'invalid'; reason: string }
  | { status: 'valid'; entries: Map<string, SharedSkillCatalogEntry> };

export function isAcceptableSkillPath(value: string): boolean;
export function extractLiteralSkillName(params: unknown, validate?: boolean): string | undefined;
export function parseSkillCatalog(
  systemPrompt: unknown,
  options?: { requireDescription?: boolean; allowVersion?: boolean },
): SharedSkillCatalogParseResult;
