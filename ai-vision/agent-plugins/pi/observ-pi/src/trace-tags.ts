export const MAX_LANGFUSE_TRACE_TAG_CHARS = 200;

/** Add a Skill/MCP summary tag while preserving exact first-seen order. */
export function addToolTraceTag(
  turn: { traceTags: string[] },
  toolType: string,
  toolName: string,
): void {
  if (toolType !== 'skill' && toolType !== 'mcp') return;
  const tag = `${toolType}:${toolName}`;
  if (tag.length > MAX_LANGFUSE_TRACE_TAG_CHARS || turn.traceTags.includes(tag)) return;
  turn.traceTags.push(tag);
}
