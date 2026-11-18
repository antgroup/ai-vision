/**
 * Receipt data hook.
 *
 * Runs one dashboard.summary query per selected agent (in parallel through
 * the V2 query adapter), normalizes both Claude-family and Codex-family
 * payloads, and exposes aggregated totals plus per-agent shares.
 *
 * Time range and refresh ticks come straight from TimeFilterContext, so the
 * receipt re-fetches in lock-step with the global toolbar.
 */

import { startTransition, useEffect, useMemo, useRef, useState } from "react"

import { useSpace } from "@/contexts/SpaceContext"
import type { Agent } from "@/contexts/AgentContext"
import { useTimeFilter } from "@/contexts/TimeFilterContext"
import {
  clearResultCache,
  executeSectionQuery,
} from "@/agents/lib/dashboardQueryAdapter"
import type { QueryTimeRange, DashboardScope } from "@/agents/types"
import { getPresetTimeWindow } from "@/lib/timeFilter"
import { resolveAgentDisplayName } from "@/agents/lib/agent-display"
import { dashboardSummaryQuery as claudeSummaryQuery } from "@/agents/dashboards/claudecode/queries/summaryQueries"
import { dashboardSummaryQuery as codexSummaryQuery } from "@/agents/dashboards/codex/queries/summaryQueries"

import {
  aggregateTotals,
  getReceiptFamily,
  isReceiptRuntime,
  normalizeRow,
  withShares,
  type AgentShareRow,
  type NormalizedAgentRow,
  type ReceiptRuntimeFamily,
  type ReceiptTotals,
} from "../utils/normalize"

const EMPTY_TOTALS: ReceiptTotals = {
  nativeInput: 0,
  cacheRead: 0,
  cacheWrite: 0,
  output: 0,
  totalTokens: 0,
  hitRate: 0,
}

const SUMMARY_BY_FAMILY: Record<ReceiptRuntimeFamily, { queryKey: string }> = {
  claude: claudeSummaryQuery,
  codex: codexSummaryQuery,
}

export interface ReceiptAgentDescriptor {
  agentProduct: string
  agentLabel: string
  family: ReceiptRuntimeFamily
}

export interface UseReceiptDataInput {
  /** Agents the user has selected (filtered to supported runtimes by the caller). */
  selectedAgents: ReceiptAgentDescriptor[]
}

export interface UseReceiptDataReturn {
  rows: NormalizedAgentRow[]
  shares: AgentShareRow[]
  totals: ReceiptTotals
  loading: boolean
  errors: Record<string, string>
  spaceId: string
  timeRange: QueryTimeRange
  scope: DashboardScope
}

function buildQueryTimeRange(input: {
  isCustom: boolean
  fromTimestamp: string
  toTimestamp: string
  timeRange: string
}): QueryTimeRange {
  if (input.isCustom) {
    return {
      type: "absolute",
      from: new Date(input.fromTimestamp).getTime(),
      to: new Date(input.toTimestamp).getTime(),
    }
  }
  const window = getPresetTimeWindow(input.timeRange)
  return {
    type: "absolute",
    from: new Date(window.from).getTime(),
    to: new Date(window.to).getTime(),
  }
}

function timeRangeKey(range: QueryTimeRange): string {
  return range.type === "absolute" ? `${range.from}-${range.to}` : range.range
}

/**
 * Build receipt descriptors from the AgentContext: keep only agents whose
 * runtime is supported (claudecode / codex) and dedupe by agent_product so
 * the same backend product is never queried twice.
 */
export function buildReceiptAgents(
  agents: Agent[],
  agentTypes: {
    agent_product?: string
    agent_name?: string
    runtime_type?: string
    display_name?: string | null
  }[]
): ReceiptAgentDescriptor[] {
  const seen = new Set<string>()
  const result: ReceiptAgentDescriptor[] = []
  for (const agent of agents) {
    const runtimeType = agent.runtime_type
    if (!isReceiptRuntime(runtimeType)) continue

    const product = agent.agent_product || agent.agent_name
    if (!product || seen.has(product)) continue
    seen.add(product)

    const family = getReceiptFamily(runtimeType)
    if (!family) continue

    result.push({
      agentProduct: product,
      agentLabel: resolveAgentDisplayName(
        product,
        agentTypes as Parameters<typeof resolveAgentDisplayName>[1]
      ),
      family,
    })
  }
  return result
}

export function useReceiptData({
  selectedAgents,
}: UseReceiptDataInput): UseReceiptDataReturn {
  const { currentSpace } = useSpace()
  const { isCustom, fromTimestamp, toTimestamp, timeRange, refreshTick } =
    useTimeFilter()

  const spaceId = currentSpace?.id ?? ""
  const scope: DashboardScope = "personal"

  const queryTimeRange = useMemo<QueryTimeRange>(
    () =>
      buildQueryTimeRange({
        isCustom,
        fromTimestamp,
        toTimestamp,
        timeRange,
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [isCustom, fromTimestamp, toTimestamp, timeRange, refreshTick]
  )

  const [rows, setRows] = useState<NormalizedAgentRow[]>([])
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(false)
  const seqRef = useRef(0)
  const prevRefreshTickRef = useRef(refreshTick)

  const selectionKey = selectedAgents
    .map((a) => `${a.agentProduct}@${a.family}`)
    .join("|")
  const timeKey = timeRangeKey(queryTimeRange)

  useEffect(() => {
    if (refreshTick !== prevRefreshTickRef.current) {
      prevRefreshTickRef.current = refreshTick
      clearResultCache()
    }

    if (!spaceId || selectedAgents.length === 0) {
      startTransition(() => {
        setRows([])
        setErrors({})
        setLoading(false)
      })
      return
    }

    const seq = ++seqRef.current
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true)

    const requests = selectedAgents.map(async (agent) => {
      const spec = SUMMARY_BY_FAMILY[agent.family]
      try {
        const result = await executeSectionQuery({
          spaceId,
          agentProduct: agent.agentProduct,
          spec,
          scope,
          timeRange: queryTimeRange,
        })
        return {
          agent,
          row: normalizeRow({
            agentProduct: agent.agentProduct,
            agentLabel: agent.agentLabel,
            family: agent.family,
            row: result.rows[0],
          }),
          error: null as string | null,
        }
      } catch (err) {
        return {
          agent,
          row: normalizeRow({
            agentProduct: agent.agentProduct,
            agentLabel: agent.agentLabel,
            family: agent.family,
            row: undefined,
          }),
          error: err instanceof Error ? err.message : "Query failed",
        }
      }
    })

    void Promise.all(requests).then((settled) => {
      if (seq !== seqRef.current) return
      const nextRows: NormalizedAgentRow[] = []
      const nextErrors: Record<string, string> = {}
      for (const item of settled) {
        nextRows.push(item.row)
        if (item.error) nextErrors[item.agent.agentProduct] = item.error
      }
      startTransition(() => {
        setRows(nextRows)
        setErrors(nextErrors)
        setLoading(false)
      })
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spaceId, selectionKey, timeKey, refreshTick])

  const totals = useMemo(
    () => (rows.length ? aggregateTotals(rows) : EMPTY_TOTALS),
    [rows]
  )
  const shares = useMemo(() => withShares(rows, totals), [rows, totals])

  return {
    rows,
    shares,
    totals,
    loading,
    errors,
    spaceId,
    timeRange: queryTimeRange,
    scope,
  }
}
