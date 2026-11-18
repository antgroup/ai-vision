import type { AgentSourceSystem } from "@/agents/lib/observabilityV2Api"
import * as queries from "./queries"

export type QueryName = keyof typeof queries
export type QueryLike = { queryKey: string }
export const QUERY_NAMES = Object.keys(queries) as QueryName[]

export function selectQuery(
  name: QueryName,
  _sourceSystem: AgentSourceSystem
): QueryLike {
  void _sourceSystem
  return queries[name]
}
