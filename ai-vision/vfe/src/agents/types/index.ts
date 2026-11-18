/**
 * Agent Dashboard V2 — Shared type barrel
 *
 * Re-exports all V2 domain types and Zod schemas so consumers can import
 * from a single path:
 *
 *   import type { AgentQueryPayload } from "@/agents/types"
 *   import { AgentQueryResultSchema } from "@/agents/types"
 */

// --- API request & response ---
export {
  RelativeRangeSchema,
  QueryTimeRangeSchema,
  AgentQueryPayloadSchema,
  AgentQueryBatchSpecSchema,
  AgentQueryBatchPayloadSchema,
  AgentQueryColumnSchema,
  AgentQueryMetaSchema,
  AgentQueryResultSchema,
  AgentQueryBatchErrorSchema,
  AgentQueryBatchResultSchema,
} from "./api"
export type {
  RelativeRange,
  QueryTimeRange,
  AgentQueryPayload,
  AgentQueryBatchPayload,
  AgentQueryBatchSpec,
  AgentQueryColumn,
  AgentQueryMeta,
  AgentQueryResult,
  AgentQueryBatchError,
  AgentQueryBatchResult,
} from "./api"

// --- Query configuration ---
export {
  AgentQueryCapabilitiesSchema,
  AgentQueryConfigSchema,
} from "./config"
export type {
  AgentQueryCapabilities,
  AgentQueryConfig,
} from "./config"

// --- Encrypted query protocol ---
export { EncryptedPayloadSchema } from "./crypto"
export type { EncryptedPayload } from "./crypto"

// --- Agent metadata (pure TS, no Zod) ---
export type { AgentCategory, AgentFamily } from "./agent"

// --- Dashboard props ---
export type { DashboardScope, DashboardContext } from "./dashboard"

// --- Langfuse API types ---
export type {
  LangfuseObservation,
  LangfuseObservationDetail,
  TraceQueryTimeRange,
  LangfuseTrace,
  LangfuseTraceDetail,
  LangfuseSession,
  LangfuseSessionTrace,
  LangfuseSessionDetail,
  TraceListResponse,
  TraceIOBatchResponse,
  SessionListResponse,
} from "./langfuse"

// --- Trace registry types ---
export type {
  TracePageContext,
  AgentTraceConfig,
  TraceColumnDef,
  TraceFilterDef,
  TraceDetailSectionDef,
  TraceDetailSectionPosition,
} from "./trace"
