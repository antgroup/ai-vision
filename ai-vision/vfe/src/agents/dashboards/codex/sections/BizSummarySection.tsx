import { useMemo } from "react"

import type { BizSummaryQuerySpecs } from "../../claudecode/sections/BizSummarySection"
import { BizSummarySection as BaseBizSummarySection } from "../../claudecode/sections/BizSummarySection"
import type { BizSummarySectionProps as BaseProps } from "../../claudecode/sections/BizSummarySection"
import { useSourceSystem } from "@/contexts/SourceSystemContext"
import { selectQuery } from "../queries-select"

export type BizSummarySectionProps = Omit<BaseProps, "queries">

export function BizSummarySection(props: BizSummarySectionProps) {
  const sourceSystem = useSourceSystem()
  const codexTeamSummaryQuery = selectQuery(
    "codexTeamSummaryQuery",
    sourceSystem
  )
  const codexMemberRankingQuery = selectQuery(
    "codexMemberRankingQuery",
    sourceSystem
  )
  const codexModelDistributionQuery = selectQuery(
    "codexModelDistributionQuery",
    sourceSystem
  )
  const codexMemberTrendQuery = selectQuery(
    "codexMemberTrendQuery",
    sourceSystem
  )

  const queries: BizSummaryQuerySpecs = useMemo(
    () => ({
      summary: codexTeamSummaryQuery,
      ranking: codexMemberRankingQuery,
      distribution: codexModelDistributionQuery,
      trend: codexMemberTrendQuery,
      trendQueryKeyPrefix: "codex.team.member_trend",
    }),
    [
      codexTeamSummaryQuery,
      codexMemberRankingQuery,
      codexModelDistributionQuery,
      codexMemberTrendQuery,
    ]
  )

  return <BaseBizSummarySection {...props} queries={queries} />
}
