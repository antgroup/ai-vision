/**
 * Agent Dashboard V2 — Query configuration types
 *
 * Returned by `GET /api/v2/agent-products/{agent_product}/query-config`.
 * Aligned with docs/v2/backend-v2-apis.md §4.
 */

import { z } from "zod"

/** Capability flags advertised by a query-config response. */
export const AgentQueryCapabilitiesSchema = z.object({
  supports_personal_dashboard: z.boolean(),
  supports_space_dashboard: z.boolean(),
  supports_encrypted_query: z.boolean(),
  supports_plain_query: z.boolean(),
})
export type AgentQueryCapabilities = z.infer<
  typeof AgentQueryCapabilitiesSchema
>

/**
 * Non-sensitive query configuration.
 *
 * Deliberately omits table_name, pk, sk, query_endpoint, and default_filters.
 */
export const AgentQueryConfigSchema = z.object({
  agent_product: z.string(),
  source_system: z.string(),
  capabilities: AgentQueryCapabilitiesSchema,
  kid: z.string().nullable().optional(),
  keyB64: z.string().nullable().optional(),
})
export type AgentQueryConfig = z.infer<typeof AgentQueryConfigSchema>
