import { useTranslation } from "react-i18next"

import { LabsStatStrip } from "@/components/labs/shared/LabsStatStrip"
import { formatPercent } from "@/components/labs/shared/labsFormat"

import {
  errorRateTone,
  formatMcpDuration,
  mcpMeasurementCoverage,
  mcpStatusCoverage,
} from "./mcpDisplay"
import type { McpOverview as McpOverviewData } from "./types"

export function McpOverview({
  overview,
  slowestServerId,
  slowestP50ServerId,
}: {
  overview: McpOverviewData
  slowestServerId?: string
  slowestP50ServerId?: string
}) {
  const { t } = useTranslation()
  const hasCalls = overview.calls > 0
  const coverage = mcpMeasurementCoverage(
    overview.measuredCalls,
    overview.calls,
    t
  )

  return (
    <div className="flex flex-col gap-1">
      <LabsStatStrip
        items={[
          {
            label: t("labs.mcp.overview.calledTools"),
            value: String(overview.calledToolCount),
          },
          {
            label: t("labs.mcp.overview.calls"),
            value: overview.calls.toLocaleString(),
          },
          {
            label: t("labs.mcp.overview.errorRate"),
            value: hasCalls ? formatPercent(overview.errorRate) : "—",
            hint: mcpStatusCoverage(overview, t),
            tone: hasCalls ? errorRateTone(overview.errorRate) : "default",
          },
          {
            label: t("labs.mcp.overview.slowestP95"),
            value: hasCalls ? formatMcpDuration(overview.p95Ms, t) : "—",
            hint: hasCalls
              ? slowestServerId || t("labs.mcp.overview.slowestP95Hint")
              : undefined,
            help: [
              t("labs.mcp.overview.p95Help"),
              t("labs.common.percentile.p95"),
              t("labs.common.percentile.samples"),
              t("labs.mcp.measurement.help"),
            ].join("\n\n"),
          },
          {
            label: t("labs.mcp.overview.slowestP50"),
            value: hasCalls ? formatMcpDuration(overview.p50Ms, t) : "—",
            hint: hasCalls
              ? slowestP50ServerId || t("labs.mcp.overview.slowestP50Hint")
              : undefined,
            help: [
              t("labs.mcp.overview.p50Help"),
              t("labs.common.percentile.p50"),
              t("labs.common.percentile.samples"),
              t("labs.mcp.measurement.help"),
            ].join("\n\n"),
          },
        ]}
      />
      {coverage ? (
        <p className="text-xs leading-5 text-[var(--color-text-description)]">
          {coverage}
        </p>
      ) : null}
    </div>
  )
}
