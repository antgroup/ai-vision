import { useTranslation } from "react-i18next"

import { formatPercent } from "@/components/labs/shared/labsFormat"
import { McpServerCard } from "../mcp/McpServerCard"
import type { McpServerConfig } from "../mcp/types"
import { memoryCallEntity, memoryMcpDetail } from "./memoryMcpPresentation"
import type { MemoryServer } from "./types"

export function MemoryServerCard({
  server,
  config,
  onOpen,
  onImport,
  statsAvailable,
  configStatus,
}: {
  server: MemoryServer
  config?: McpServerConfig
  onOpen: (serverId: string) => void
  onImport?: () => void
  statsAvailable: boolean
  configStatus?: "loading" | "error" | "ready"
}) {
  const { t } = useTranslation()
  const callEntity = memoryCallEntity([server])
  return (
    <McpServerCard
      feature="memory"
      server={memoryMcpDetail(server, statsAvailable, t)}
      config={config}
      configStatus={configStatus}
      onOpen={onOpen}
      onImport={onImport}
      statsKnown={statsAvailable}
      p95Help={t(
        callEntity
          ? `labs.memory.${callEntity}.p95Help`
          : "labs.memory.card.p95Help"
      )}
      rateMetric={{
        label: t("labs.memory.card.hitRate"),
        value:
          statsAvailable && typeof server.searchHitRate === "number"
            ? formatPercent(server.searchHitRate)
            : "—",
        help: [
          t("labs.memory.overview.hitRateHelp"),
          t("labs.memory.overview.hitCalculation", {
            hits: server.searchHitCalls,
            total: server.searchCalls,
          }),
        ].join("\n\n"),
      }}
    />
  )
}
