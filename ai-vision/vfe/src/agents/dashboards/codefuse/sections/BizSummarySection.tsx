import { useMemo } from "react"

import type { BizSummaryQuerySpecs } from "../../claudecode/sections/BizSummarySection"
import { BizSummarySection as BaseBizSummarySection } from "../../claudecode/sections/BizSummarySection"
import type { BizSummarySectionProps as BaseProps } from "../../claudecode/sections/BizSummarySection"
import { useSourceSystem } from "@/contexts/SourceSystemContext"
import { selectQuery } from "../queries-select"

export type BizSummarySectionProps = Omit<BaseProps, "queries">

export function BizSummarySection(props: BizSummarySectionProps) {
  const sourceSystem = useSourceSystem()
  const teamSummaryQuery = selectQuery("teamSummaryQuery", sourceSystem)
  const memberRankingQuery = selectQuery("memberRankingQuery", sourceSystem)
  const modelDistributionQuery = selectQuery(
    "modelDistributionQuery",
    sourceSystem
  )
  const memberTrendQuery = selectQuery("memberTrendQuery", sourceSystem)

  const queries: BizSummaryQuerySpecs = useMemo(
    () => ({
      summary: teamSummaryQuery,
      ranking: memberRankingQuery,
      distribution: modelDistributionQuery,
      trend: memberTrendQuery,
      trendQueryKeyPrefix: "codefuse.team.member_trend",
    }),
    [
      teamSummaryQuery,
      memberRankingQuery,
      modelDistributionQuery,
      memberTrendQuery,
    ]
  )

  return <BaseBizSummarySection {...props} queries={queries} />
}
