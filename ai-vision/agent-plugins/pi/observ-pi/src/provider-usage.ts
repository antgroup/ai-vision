/** Only measurement fields from native provider events cross this boundary. */
export type ReportedUsage = Partial<Record<'input' | 'output' | 'total' | 'cacheRead' | 'cacheWrite' | 'reasoning', number | null>>;

function count(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : undefined;
}

function object(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? value as Record<string, unknown> : {};
}

export function projectProviderUsage(raw: Record<string, unknown>): ReportedUsage | undefined {
  if (raw.api !== 'openai-completions') return undefined;
  const data = object(raw.data);
  if (!data.usage || typeof data.usage !== 'object') return undefined;
  const usage = object(data.usage);
  const projected: ReportedUsage = {};
  const assign = (target: keyof ReportedUsage, source: Record<string, unknown>, key: string) => {
    if (Object.hasOwn(source, key)) projected[target] = count(source[key]) ?? null;
  };
  assign('input', usage, 'prompt_tokens');
  assign('output', usage, 'completion_tokens');
  assign('total', usage, 'total_tokens');
  const prompt = object(usage.prompt_tokens_details);
  const completion = object(usage.completion_tokens_details);
  if (Object.hasOwn(prompt, 'cached_tokens')) assign('cacheRead', prompt, 'cached_tokens');
  else if (Object.hasOwn(usage, 'prompt_cache_hit_tokens')) assign('cacheRead', usage, 'prompt_cache_hit_tokens');
  else assign('cacheRead', usage, 'cached_tokens');
  assign('cacheWrite', prompt, 'cache_write_tokens');
  assign('reasoning', completion, 'reasoning_tokens');
  return projected;
}

export function usageAttributes(message: Record<string, unknown>, reported?: ReportedUsage): Record<string, unknown> {
  const usage = object(message.usage);
  const cacheRead = reported ? count(reported.cacheRead) : count(usage.cacheRead ?? usage.cachedTokens ?? usage.cached_tokens);
  const cacheWrite = reported ? count(reported.cacheWrite) : count(usage.cacheWrite);
  const uncached = count(usage.input ?? usage.inputTokens ?? usage.input_tokens);
  const inclusive = uncached === undefined ? undefined : uncached + (cacheRead ?? 0) + (cacheWrite ?? 0);
  // Pi initializes all normalized buckets to zero. Without a native provider
  // measurement, a zero cannot prove reportedness (notably Pi <= 0.80.6).
  const input = reported ? count(reported.input) : inclusive !== undefined && inclusive > 0 ? count(inclusive) : undefined;
  const normalizedOutput = count(usage.output ?? usage.outputTokens ?? usage.output_tokens);
  const output = reported ? count(reported.output) : normalizedOutput || undefined;
  const total = reported && count(reported.total) !== undefined
    ? count(reported.total)
    : input !== undefined && output !== undefined ? input + output : undefined;
  const attrs: Record<string, unknown> = {};
  if (input !== undefined) attrs['gen_ai.usage.input_tokens'] = input;
  else attrs['agentic.usage.input_tokens.missing'] = 'true';
  if (output !== undefined) attrs['gen_ai.usage.output_tokens'] = output;
  else attrs['agentic.usage.output_tokens.missing'] = 'true';
  if (total !== undefined) attrs['gen_ai.usage.total_tokens'] = total;
  if (cacheRead !== undefined && (reported || cacheRead > 0)) {
    attrs['gen_ai.usage.cache_read.input_tokens'] = cacheRead;
    attrs['agentic.usage.cache_read_tokens'] = cacheRead;
  }
  if (cacheWrite !== undefined && (reported || cacheWrite > 0)) {
    attrs['gen_ai.usage.cache_creation.input_tokens'] = cacheWrite;
    attrs['agentic.usage.cache_write_tokens'] = cacheWrite;
  }
  const reasoning = reported ? count(reported.reasoning) : count(usage.reasoning);
  if (reasoning !== undefined && (reported || reasoning > 0)) attrs['gen_ai.usage.reasoning.output_tokens'] = reasoning;
  return attrs;
}
