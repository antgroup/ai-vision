import { useTranslation } from "react-i18next"

import { LabsStatStrip } from "@/components/labs/shared/LabsStatStrip"
import { formatMs, formatPercent } from "@/components/labs/shared/labsFormat"

import type { MemoryOverviewData, MemoryServer } from "./types"
import { memoryCallEntity } from "./memoryMcpPresentation"

export function MemoryOverview({
  overview,
  servers,
}: {
  overview: MemoryOverviewData
  servers: MemoryServer[]
}) {
  const { t } = useTranslation()
  const callEntity = memoryCallEntity(servers)
  const latency = (percentile: "p50Ms" | "p95Ms") => {
    const value = overview[percentile]
    const owner =
      value === null
        ? undefined
        : servers.find(
            (server) => server.calls > 0 && server[percentile] === value
          )
    const isP95 = percentile === "p95Ms"
    return {
      label: t(
        isP95 ? "labs.mcp.overview.slowestP95" : "labs.mcp.overview.slowestP50"
      ),
      value: overview.calls > 0 ? formatMs(value) : "—",
      hint: overview.calls > 0 ? owner?.serverId : undefined,
      help: [
        t(
          isP95
            ? "labs.memory.overview.p95Help"
            : "labs.memory.overview.p50Help"
        ),
        t(isP95 ? "labs.common.percentile.p95" : "labs.common.percentile.p50"),
        t("labs.common.percentile.samples"),
      ].join("\n\n"),
    }
  }
  return (
    <LabsStatStrip
      items={[
        {
          label: t(
            callEntity
              ? `labs.memory.${callEntity}.calledCount`
              : "labs.mcp.overview.calledTools"
          ),
          value: overview.calledToolCount.toLocaleString(),
          help: t(
            callEntity
              ? `labs.memory.${callEntity}.calledCountHelp`
              : "labs.memory.overview.calledToolsHelp"
          ),
        },
        {
          label: t("labs.mcp.overview.calls"),
          value: overview.calls.toLocaleString(),
          help: t(
            callEntity
              ? `labs.memory.${callEntity}.scopeNote`
              : "labs.memory.overview.callsHelp"
          ),
        },
        {
          label: t("labs.memory.overview.hitRate"),
          value:
            typeof overview.searchHitRate === "number"
              ? formatPercent(overview.searchHitRate)
              : "—",
          help: [
            t("labs.memory.overview.hitRateHelp"),
            t("labs.memory.overview.hitCalculation", {
              hits: overview.searchHitCalls,
              total: overview.searchCalls,
            }),
          ].join("\n\n"),
        },
        latency("p95Ms"),
        latency("p50Ms"),
      ]}
    />
  )
}
