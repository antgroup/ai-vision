import { Database, Plug } from "lucide-react"
import { useTranslation } from "react-i18next"

import { CopyableCode } from "@/components/labs/shared/CopyableCode"
import { LabsMetricLabel } from "@/components/labs/shared/LabsMetricLabel"
import {
  TONE_TEXT_CLASS,
  formatPercent,
  type StatTone,
} from "@/components/labs/shared/labsFormat"
import { cn } from "@/lib/utils"

import {
  HEALTH_TAG_CLASS,
  errorRateTone,
  launchLine,
  formatMcpDuration,
  formatMcpTime,
  mcpMeasurementCoverage,
  mcpStatusCoverage,
} from "./mcpDisplay"
import { McpServerDescription } from "./McpServerDescription"
import type { McpServer, McpConfigSummary } from "./types"

interface McpServerCardProps {
  feature?: "mcp" | "memory"
  server: McpServer
  config?: McpConfigSummary
  onOpen: (serverId: string) => void
  onImport?: () => void
  configStatus?: "loading" | "error" | "ready"
  statsKnown?: boolean
  rateMetric?: {
    label: string
    value: string
    help: string
    /** Tone for the substituted rate (e.g. memory hit rate); absent stays uncolored. */
    tone?: StatTone
  }
  p95Help?: string
}

export function McpServerCard({
  feature = "mcp",
  server,
  config,
  onOpen,
  onImport,
  configStatus = "ready",
  statsKnown = true,
  rateMetric,
  p95Help,
}: McpServerCardProps) {
  const { t } = useTranslation()
  const FeatureIcon = feature === "memory" ? Database : Plug
  const { stats } = server
  const hasCalls = stats.calls > 0
  const coverage = mcpMeasurementCoverage(stats.measuredCalls, stats.calls, t)

  return (
    <article
      role="button"
      tabIndex={0}
      onClick={() => onOpen(server.serverId)}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault()
          onOpen(server.serverId)
        }
      }}
      className="flex cursor-pointer flex-col gap-3 rounded-[12px] border border-[var(--color-border)] bg-[var(--color-bg-page)] p-4 transition-colors hover:border-[var(--color-labs)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-labs)]"
    >
      <header className="-mx-4 -mt-4 flex items-start justify-between gap-3 rounded-t-[11px] border-b border-[var(--color-border-labs)] bg-[var(--color-bg-labs)] px-4 py-3">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-[var(--color-bg-labs-tag)] text-[var(--color-labs)]">
          <FeatureIcon className="size-4" aria-hidden="true" />
        </span>
        <div className="flex min-w-0 flex-1 flex-col">
          <h3 className="line-clamp-2 text-base leading-6 font-medium break-all text-[var(--color-text-primary)]">
            {server.serverId}
          </h3>
          {server.sourceAgents.length ? (
            <p className="truncate text-xs leading-5 text-[var(--color-text-description)]">
              {server.lastSeen
                ? t("labs.mcp.card.usage", {
                    agents: server.sourceAgents.join(", "),
                    time: formatMcpTime(server.lastSeen, t),
                  })
                : t("labs.mcp.measurement.usageUnknownTime", {
                    agents: server.sourceAgents.join(", "),
                  })}
            </p>
          ) : (
            <p className="truncate text-xs leading-5 text-[var(--color-text-description)]">
              {t(
                statsKnown
                  ? "labs.mcp.card.usageImported"
                  : "labs.mcp.topic.statsUnknown"
              )}
            </p>
          )}
        </div>
        <span
          className={cn(
            "inline-flex h-6 shrink-0 items-center rounded-[6px] px-2 text-xs leading-5 whitespace-nowrap",
            HEALTH_TAG_CLASS[server.health]
          )}
        >
          {t(`labs.mcp.health.${statsKnown ? server.health : "unknown"}`)}
        </span>
      </header>

      <McpServerDescription server={server} config={config} feature={feature} />

      {config ? (
        <div className="flex items-center gap-2">
          <span className="inline-flex h-6 shrink-0 items-center rounded-[6px] border border-[var(--color-border)] px-2 font-mono text-xs leading-5 whitespace-nowrap text-[var(--color-text-secondary)]">
            {config.transport}
          </span>
          <CopyableCode value={launchLine(config)} className="flex-1" />
        </div>
      ) : (
        <div className="flex h-8 items-center justify-between gap-2 rounded-[8px] border border-dashed border-[var(--color-border)] px-3 text-xs leading-5 text-[var(--color-text-description)]">
          <span>
            {t(
              configStatus === "ready"
                ? "labs.mcp.card.noConfig"
                : configStatus === "error"
                  ? "labs.mcp.topic.configsFailed"
                  : "labs.common.loading"
            )}
          </span>
          {onImport && configStatus === "ready" ? (
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation()
                onImport()
              }}
              className="shrink-0 rounded-[6px] text-[var(--color-labs)] underline decoration-[var(--color-border-labs)] underline-offset-4 hover:decoration-current focus-visible:outline-2 focus-visible:outline-[var(--color-labs)]"
            >
              {t("labs.mcp.card.importConfig")}
            </button>
          ) : null}
        </div>
      )}

      <div className="mt-auto flex flex-col gap-1">
        <dl className="grid grid-cols-[1.5fr_1fr_1fr_1fr] gap-2 border-t border-[var(--color-border)] pt-3">
          <Metric
            label={t(
              server.callEntity
                ? `labs.memory.${server.callEntity}.calledCount`
                : "labs.mcp.card.calledTools"
            )}
            value={statsKnown ? String(stats.calledToolCount) : "—"}
          />
          <Metric
            label={t("labs.mcp.card.calls")}
            value={statsKnown ? stats.calls.toLocaleString() : "—"}
          />
          <Metric
            label={rateMetric?.label ?? t("labs.mcp.card.errorRate")}
            value={
              rateMetric?.value ??
              (statsKnown && hasCalls ? formatPercent(stats.errorRate) : "—")
            }
            help={rateMetric?.help ?? mcpStatusCoverage(stats, t)}
            className={
              rateMetric?.tone !== undefined
                ? TONE_TEXT_CLASS[rateMetric.tone]
                : hasCalls && !rateMetric
                  ? TONE_TEXT_CLASS[errorRateTone(stats.errorRate)]
                  : undefined
            }
          />
          <Metric
            label="P95"
            value={
              statsKnown && hasCalls ? formatMcpDuration(stats.p95Ms, t) : "—"
            }
            help={[
              p95Help ?? t("labs.mcp.card.p95Help"),
              t("labs.common.percentile.p95"),
              t("labs.common.percentile.samples"),
              t("labs.mcp.measurement.help"),
            ].join("\n\n")}
          />
        </dl>
        {coverage ? (
          <p className="text-xs leading-5 text-[var(--color-text-description)]">
            {coverage}
          </p>
        ) : null}
      </div>
    </article>
  )
}

function Metric({
  label,
  value,
  className,
  help,
}: {
  label: string
  value: string
  className?: string
  help?: string
}) {
  return (
    <div className="flex min-w-0 flex-col">
      <dt className="text-xs leading-5 text-[var(--color-text-description)]">
        <LabsMetricLabel label={label} help={help} />
      </dt>
      <dd
        className={cn(
          "truncate font-stats text-base leading-6 font-semibold text-[var(--color-text-primary)]",
          className
        )}
      >
        {value}
      </dd>
    </div>
  )
}
