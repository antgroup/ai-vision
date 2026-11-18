import { useTranslation } from "react-i18next"

import { LabsStatStrip } from "@/components/labs/shared/LabsStatStrip"
import { formatMs, formatPercent } from "@/components/labs/shared/labsFormat"
import { errorRateTone, mcpStatusCoverage } from "@/labs/mcp/mcpDisplay"

import { skillProvenanceCounts } from "@/labs/skill/skillDisplay"

import type { SkillOverview as SkillOverviewData } from "./types"

export function SkillOverview({
  overview,
  slowestSkillName,
  slowestP50SkillName,
}: {
  overview: SkillOverviewData
  slowestSkillName?: string
  slowestP50SkillName?: string
}) {
  const { t } = useTranslation()
  const hasCalls = overview.calls > 0
  const provenance = skillProvenanceCounts(overview)

  return (
    <LabsStatStrip
      columns={4}
      items={[
        {
          label: t("labs.skill.provenance.records"),
          value: overview.calls.toLocaleString(),
          hint: hasCalls
            ? provenance
              ? t("labs.skill.provenance.counts", provenance)
              : t("labs.skill.provenance.unknownTotals")
            : undefined,
          help: t("labs.skill.provenance.help"),
        },
        {
          label: t("labs.skill.provenance.errorRate"),
          value: hasCalls ? formatPercent(overview.errorRate) : "—",
          hint: mcpStatusCoverage(overview, t),
          tone: hasCalls ? errorRateTone(overview.errorRate) : "default",
        },
        {
          label: t("labs.skill.overview.slowestP95"),
          value:
            hasCalls && overview.p95Ms !== null
              ? formatMs(overview.p95Ms)
              : "—",
          hint: hasCalls
            ? slowestSkillName || t("labs.skill.overview.slowestP95Hint")
            : undefined,
          help: [
            t("labs.skill.overview.p95Help"),
            t("labs.common.percentile.p95"),
            t("labs.skill.durationHelp"),
            t("labs.common.percentile.samples"),
          ].join("\n\n"),
        },
        {
          label: t("labs.skill.overview.slowestP50"),
          value:
            hasCalls && overview.p50Ms !== null
              ? formatMs(overview.p50Ms)
              : "—",
          hint: hasCalls
            ? slowestP50SkillName || t("labs.skill.overview.slowestP50Hint")
            : undefined,
          help: [
            t("labs.skill.overview.p50Help"),
            t("labs.common.percentile.p50"),
            t("labs.skill.durationHelp"),
            t("labs.common.percentile.samples"),
          ].join("\n\n"),
        },
      ]}
    />
  )
}
