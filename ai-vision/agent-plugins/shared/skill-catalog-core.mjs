export const MAX_SYSTEM_PROMPT_CHARS = 1_048_576;
export const MAX_SKILL_SECTION_CHARS = 262_144;
export const MAX_SKILL_ENTRIES = 256;
export const MAX_SKILL_NAME_CHARS = 128;
export const MAX_SKILL_LOCATION_CHARS = 4_096;
export const MAX_SKILL_VERSION_CHARS = 256;

const AVAILABLE_SKILLS_OPEN = '<available_skills>';
const AVAILABLE_SKILLS_CLOSE = '</available_skills>';
const SKILL_NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$/;
const CONTROL_CHARACTER_PATTERN = /[\u0000-\u001f\u007f]/;
const URI_SCHEME_PATTERN = /^[A-Za-z][A-Za-z0-9+.-]*:\/\//;

function countOccurrences(value, needle) {
  let count = 0;
  let offset = 0;
  while (true) {
    const index = value.indexOf(needle, offset);
    if (index < 0) return count;
    count++;
    offset = index + needle.length;
  }
}

function decodeXmlText(value) {
  if (/&(?!amp;|lt;|gt;|quot;|apos;)/.test(value)) return undefined;
  const entities = {
    '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&apos;': "'",
  };
  return value.replace(/&(amp|lt|gt|quot|apos);/g, entity => entities[entity]).trim();
}

export function isAcceptableSkillPath(value) {
  if (value.length === 0 || value.length > MAX_SKILL_LOCATION_CHARS || value !== value.trim()) return false;
  if (CONTROL_CHARACTER_PATTERN.test(value) || value.includes('\\') || value.includes('%')) return false;
  if (URI_SCHEME_PATTERN.test(value) || !(value.startsWith('/') || value.startsWith('~/'))) return false;
  if (!value.endsWith('/SKILL.md')) return false;
  const segments = (value.startsWith('~/') ? value.slice(2) : value.slice(1)).split('/');
  return segments.every(segment => segment !== '' && segment !== '.' && segment !== '..');
}

export function extractLiteralSkillName(params, validate = false) {
  if (!params || typeof params !== 'object') return undefined;
  const value = params.skill;
  if (typeof value !== 'string' || value.trim() === '') return undefined;
  const parts = value.trim().split(':');
  const name = parts.length >= 3 ? parts.slice(1).join(':') : value.trim();
  return !validate || SKILL_NAME_PATTERN.test(name) ? name : undefined;
}

function parseFields(block, requireDescription, allowVersion) {
  const fields = { name: [], location: [], description: [], version: [] };
  const fieldPattern = /<(name|location|description|version)>([\s\S]*?)<\/\1>/g;
  let cursor = 0;
  let match;
  while ((match = fieldPattern.exec(block)) !== null) {
    if (block.slice(cursor, match.index).trim() !== '') return undefined;
    const key = match[1];
    if ((key === 'description' && !requireDescription) || (key === 'version' && !allowVersion)) return undefined;
    const decoded = key === 'description' ? match[2].trim() : decodeXmlText(match[2]);
    if (decoded === undefined) return undefined;
    fields[key].push(decoded);
    cursor = match.index + match[0].length;
  }
  if (block.slice(cursor).trim() !== '') return undefined;
  if (fields.name.length !== 1 || fields.location.length !== 1) return undefined;
  if (fields.description.length !== (requireDescription ? 1 : 0)) return undefined;
  if (fields.version.length > (allowVersion ? 1 : 0)) return undefined;
  return fields;
}

export function parseSkillCatalog(systemPrompt, options = {}) {
  const requireDescription = options.requireDescription === true;
  const allowVersion = options.allowVersion === true;
  if (typeof systemPrompt !== 'string') return { status: 'absent' };
  if (systemPrompt.length > MAX_SYSTEM_PROMPT_CHARS) {
    return { status: 'invalid', reason: 'system prompt exceeds limit' };
  }
  const openCount = countOccurrences(systemPrompt, AVAILABLE_SKILLS_OPEN);
  const closeCount = countOccurrences(systemPrompt, AVAILABLE_SKILLS_CLOSE);
  if (openCount === 0 && closeCount === 0) return { status: 'absent' };
  if (openCount !== 1 || closeCount !== 1) {
    return { status: 'invalid', reason: 'available_skills section cardinality is invalid' };
  }
  const openIndex = systemPrompt.indexOf(AVAILABLE_SKILLS_OPEN);
  const closeIndex = systemPrompt.indexOf(AVAILABLE_SKILLS_CLOSE);
  if (closeIndex < openIndex + AVAILABLE_SKILLS_OPEN.length) {
    return { status: 'invalid', reason: 'available_skills section is malformed' };
  }
  const section = systemPrompt.slice(openIndex + AVAILABLE_SKILLS_OPEN.length, closeIndex);
  if (section.length > MAX_SKILL_SECTION_CHARS) {
    return { status: 'invalid', reason: 'available_skills section exceeds limit' };
  }

  const entries = new Map();
  const ambiguousLocations = new Set();
  const skillPattern = /<skill>([\s\S]*?)<\/skill>/g;
  let cursor = 0;
  let count = 0;
  let match;
  while ((match = skillPattern.exec(section)) !== null) {
    if (section.slice(cursor, match.index).trim() !== '') {
      return { status: 'invalid', reason: 'available_skills contains malformed content' };
    }
    if (++count > MAX_SKILL_ENTRIES) {
      return { status: 'invalid', reason: 'available_skills has too many entries' };
    }
    const fields = parseFields(match[1], requireDescription, allowVersion);
    if (!fields) return { status: 'invalid', reason: 'skill field cardinality is invalid' };
    const [name] = fields.name;
    const [location] = fields.location;
    const version = fields.version[0] || undefined;
    if (name.length > MAX_SKILL_NAME_CHARS || !SKILL_NAME_PATTERN.test(name)) {
      return { status: 'invalid', reason: 'skill name is invalid' };
    }
    if (!isAcceptableSkillPath(location)) {
      return { status: 'invalid', reason: 'skill location is invalid' };
    }
    if (version !== undefined && (version.length > MAX_SKILL_VERSION_CHARS || CONTROL_CHARACTER_PATTERN.test(version))) {
      return { status: 'invalid', reason: 'skill version is invalid' };
    }
    if (!ambiguousLocations.has(location)) {
      const entry = { name, location, ...(version ? { version } : {}) };
      const existing = entries.get(location);
      if (!existing) entries.set(location, entry);
      else if (existing.name !== entry.name || existing.version !== entry.version) {
        entries.delete(location);
        ambiguousLocations.add(location);
      }
    }
    cursor = match.index + match[0].length;
  }
  if (section.slice(cursor).trim() !== '') {
    return { status: 'invalid', reason: 'available_skills contains malformed content' };
  }
  return { status: 'valid', entries };
}
