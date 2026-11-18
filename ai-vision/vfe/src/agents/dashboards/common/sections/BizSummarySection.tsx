import { useMemo } from "react"

import type { BizSummaryQuerySpecs } from "../../claudecode/sections/BizSummarySection"
import { BizSummarySection as BaseBizSummarySection } from "../../claudecode/sections/BizSummarySection"
import type { BizSummarySectionProps as BaseProps } from "../../claudecode/sections/BizSummarySection"
import { useSourceSystem } from "@/contexts/SourceSystemContext"
import { selectQuery } from "../queries-select"

export type BizSummarySectionProps = Omit<BaseProps, "queries">

export function BizSummarySection(props: BizSummarySectionProps) {
  const sourceSystem = useSourceSystem()

  const queries: BizSummaryQuerySpecs = useMemo(
    () => ({
      summary: selectQuery("teamSummaryQuery", sourceSystem),
      ranking: selectQuery("memberRankingQuery", sourceSystem),
      distribution: selectQuery("modelDistributionQuery", sourceSystem),
      trend: selectQuery("memberTrendQuery", sourceSystem),
      trendQueryKeyPrefix: "common.team.member_trend",
    }),
    [sourceSystem]
  )

  return <BaseBizSummarySection {...props} queries={queries} />
}
