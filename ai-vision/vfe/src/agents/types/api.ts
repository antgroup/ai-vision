/**
 * Agent Dashboard V2 — API request & response types
 *
 * All types that cross the network boundary between frontend and backend
 * query endpoints. Aligned with docs/v2/backend-v2-apis.md §5–§7.
 */

import { z } from "zod"

// ---------------------------------------------------------------------------
// Shared primitives
// ---------------------------------------------------------------------------

/** Supported relative time ranges matching backend's parse_time_range. */
export const RelativeRangeSchema = z.enum([
  "5m",
  "15m",
  "30m",
  "1h",
  "3h",
  "6h",
  "12h",
  "24h",
  "7d",
  "30d",
])
export type RelativeRange = z.infer<typeof RelativeRangeSchema>

/**
 * Time range for agent queries.
 *
 * - `relative`: backend computes start from the named range and current time.
 * - `absolute`: caller supplies explicit millisecond timestamps; backend MUST
 *   enforce both boundaries to prevent unbounded queries.
 */
export const QueryTimeRangeSchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("relative"), range: RelativeRangeSchema }),
  z.object({ type: z.literal("absolute"), from: z.number(), to: z.number() }),
])
export type QueryTimeRange = z.infer<typeof QueryTimeRangeSchema>

const scopeSchema = z.enum(["personal", "space"])

// ---------------------------------------------------------------------------
// Request payloads
// ---------------------------------------------------------------------------

/**
 * Payload sent to `POST /api/v2/spaces/{space_id}/agent-products/{agent_product}/query`.
 *
 * This structure is also the decrypted payload for encrypted queries
 * (`e=1`).
 */
export const AgentQueryPayloadSchema = z.object({
  scope: scopeSchema,
  queryKey: z.string(),
  timeRange: QueryTimeRangeSchema,
  params: z.record(z.string(), z.unknown()).optional(),
})
export type AgentQueryPayload = z.infer<typeof AgentQueryPayloadSchema>

/** One query item inside a batch dashboard query request. */
export const AgentQueryBatchSpecSchema = z.object({
  queryKey: z.string(),
  params: z.record(z.string(), z.unknown()).optional(),
})
export type AgentQueryBatchSpec = z.infer<typeof AgentQueryBatchSpecSchema>

/**
 * Payload sent to
 * `POST /api/v2/spaces/{space_id}/agent-products/{agent_product}/query_batch`.
 *
 * Scope and timeRange are intentionally shared so section/scope orchestration
 * decides which compatible query specs are grouped together.
 */
export const AgentQueryBatchPayloadSchema = z.object({
  scope: scopeSchema,
  timeRange: QueryTimeRangeSchema,
  queries: z.array(AgentQueryBatchSpecSchema),
})
export type AgentQueryBatchPayload = z.infer<
  typeof AgentQueryBatchPayloadSchema
>

// ---------------------------------------------------------------------------
// Response types
// ---------------------------------------------------------------------------

/** Column descriptor returned in query results. */
export const AgentQueryColumnSchema = z.object({
  key: z.string(),
  type: z.enum(["number", "string", "boolean"]),
})
export type AgentQueryColumn = z.infer<typeof AgentQueryColumnSchema>

/** Execution metadata returned alongside query data. */
export const AgentQueryMetaSchema = z.object({
  encrypted: z.boolean(),
  query_time_ms: z.number(),
})
export type AgentQueryMeta = z.infer<typeof AgentQueryMetaSchema>

/**
 * Successful response from `POST …/query`.
 *
 * Keeps the raw columnar shape from the data source so that sections can
 * perform their own display transforms.
 */
export const AgentQueryResultSchema = z.object({
  queryKey: z.string(),
  columns: z.array(AgentQueryColumnSchema),
  rows: z.array(z.record(z.string(), z.unknown())),
  meta: AgentQueryMetaSchema,
})
export type AgentQueryResult = z.infer<typeof AgentQueryResultSchema>

/** Per-query error returned by the batch query endpoint. */
export const AgentQueryBatchErrorSchema = z.object({
  type: z.string(),
  message: z.string(),
  details: z.record(z.string(), z.unknown()),
  status: z.number(),
})
export type AgentQueryBatchError = z.infer<typeof AgentQueryBatchErrorSchema>

/** Batch query response keyed by each requested queryKey. */
export const AgentQueryBatchResultSchema = z.object({
  results: z.record(z.string(), AgentQueryResultSchema),
  errors: z.record(z.string(), AgentQueryBatchErrorSchema),
  meta: z.object({
    encrypted: z.boolean(),
    query_count: z.number(),
    success_count: z.number(),
    error_count: z.number(),
    total_time_ms: z.number(),
  }),
})
export type AgentQueryBatchResult = z.infer<typeof AgentQueryBatchResultSchema>
