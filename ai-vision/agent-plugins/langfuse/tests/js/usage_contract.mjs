// Expected values for the bounded Chat Completions fixtures. These are built
// from the independent provider response, never from platform normalization.
import assert from "node:assert/strict";

export const rawUsageKey = "langfuse.observation.usage_details";
export const canonicalUsageKeys = {
  input: "gen_ai.usage.input_tokens", output: "gen_ai.usage.output_tokens", total: "gen_ai.usage.total_tokens",
  cacheReadInput: "gen_ai.usage.cache_read.input_tokens", cacheWriteInput: "gen_ai.usage.cache_creation.input_tokens",
  reasoning: "gen_ai.usage.reasoning.output_tokens",
};

export function generationUsageContract(providerUsage, sdkVersion) {
  assert.match(sdkVersion, /^(5|6)\.\d+\.\d+(?:[-+].+)?$/, "Usage fixture requires the tested JS SDK major");
  const values = Object.fromEntries(Object.keys(canonicalUsageKeys).map((field) => [field, null]));
  if (providerUsage === null) return { raw: null, values, sources: {}, v1: { input: 0, output: 0, total: 0 } };
  assert.ok(providerUsage && typeof providerUsage === "object", "Independent provider usage is required");
  const count = (value) => { assert.ok(Number.isSafeInteger(value) && value >= 0, "Provider usage must be a nonnegative safe integer"); return value; };
  const input = count(providerUsage.prompt_tokens), output = count(providerUsage.completion_tokens), total = count(providerUsage.total_tokens);
  assert.equal(input + output, total, "Provider usage total is inconsistent");
  const cached = providerUsage.prompt_tokens_details?.cached_tokens;
  const reasoning = providerUsage.completion_tokens_details?.reasoning_tokens;
  if (cached != null) count(cached);
  if (reasoning != null) count(reasoning);
  assert.ok((cached ?? 0) <= input && (reasoning ?? 0) <= output, "Provider details exceed inclusive usage");
  const raw = { input: input - (cached ?? 0), output: output - (reasoning ?? 0), total,
    ...(cached == null ? {} : { input_cached_tokens: cached }),
    ...(reasoning == null ? {} : { output_reasoning_tokens: reasoning }) };
  Object.assign(values, { input, output, total, cacheReadInput: cached ?? 0, cacheWriteInput: 0, reasoning: reasoning ?? 0 });
  // Missing buckets can be derived as zero only because this independently
  // known flat breakdown is complete. Their source differs from reported zero.
  const sources = { input: "langfuse.exclusive_input_plus_cache", output: "langfuse.exclusive_output_plus_reasoning",
    total: `${rawUsageKey}.total`, cacheReadInput: cached == null ? "langfuse.complete_usage_absent_bucket" : `${rawUsageKey}.input_cached_tokens`,
    cacheWriteInput: "langfuse.complete_usage_absent_bucket",
    reasoning: reasoning == null ? "langfuse.complete_usage_absent_bucket" : `${rawUsageKey}.output_reasoning_tokens` };
  return { raw, values, sources, v1: { input, output, total } };
}

export function generationExpectations(inventory, providerUsage) {
  assert.notEqual(providerUsage, undefined, "Every generation needs an independent known/unknown usage reference");
  return inventory.filter((span) => span.type === "generation").map((span) => ({ id: span.id, providerUsage }));
}
