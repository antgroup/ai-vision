import type { DashboardScope, QueryTimeRange } from "@/agents/types"
import { measurementNumber } from "@/components/bizcharts/helpers/measurements"
import { useSectionQuery, type UseSectionQueryReturn } from "./useSectionQuery"

interface EmptyDataCheckInput {
  queryKey: string
  scope: DashboardScope
  spaceId: string
  agentProduct: string
  timeRange: QueryTimeRange
  refreshTick?: number
}

export function useEmptyDataCheck(input: EmptyDataCheckInput): {
  isEmpty: boolean
  summaryQuery: UseSectionQueryReturn
} {
  const summaryQuery = useSectionQuery(input)

  const isEmpty = (() => {
    if (summaryQuery.loading || summaryQuery.error || !summaryQuery.data)
      return false
    const row = summaryQuery.data.rows?.[0]
    if (!row) return true
    const tokens =
      Number(row.input_tokens ?? 0) + Number(row.output_tokens ?? 0)
    const requests = measurementNumber(row.request_count)
    const sessions = measurementNumber(row.total_sessions)
    return tokens === 0 && requests === 0 && sessions === 0
  })()

  return { isEmpty, summaryQuery }
}
