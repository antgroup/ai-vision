import { Blocks } from "lucide-react"
import { useTranslation } from "react-i18next"
import { LabsMetricLabel } from "@/components/labs/shared/LabsMetricLabel"

import {
  TONE_TEXT_CLASS,
  formatMs,
  formatPercent,
  formatRelativeTime,
} from "@/components/labs/shared/labsFormat"
import {
  HEALTH_TAG_CLASS,
  errorRateTone,
  mcpStatusCoverage,
  healthFromErrorRate,
} from "@/labs/mcp/mcpDisplay"
import { cn } from "@/lib/utils"

import { skillProvenanceCounts } from "@/labs/skill/skillDisplay"
import type {
  SkillConfig,
  SkillLoadState,
  SkillStat,
  SkillStatsState,
} from "./types"

interface SkillCardProps {
  /** Aggregate from the current window, when available. */
  stat: SkillStat | null
  statState?: SkillStatsState
  configState?: SkillLoadState
  config?: SkillConfig
  onOpen: (name: string) => void
  onImport?: () => void
}

export function SkillCard({
  stat,
  config,
  onOpen,
  onImport,
  statState = "ready",
  configState = "ready",
}: SkillCardProps) {
  const { t } = useTranslation()
  const name = stat?.name ?? config?.name ?? ""
  const statsKnown = !!stat || statState === "ready"
  const hasCalls = (stat?.calls ?? 0) > 0
  const provenance = stat ? skillProvenanceCounts(stat) : null
  const description =
    config?.description?.trim() || t("labs.skill.card.noDescription")
  // Same four-state health as MCP server cards; imported-only skills with
  // no observed calls stay "unknown" instead of claiming healthy.
  const health =
    !stat || stat.calls === 0
      ? "unknown"
      : healthFromErrorRate(stat.errorRate, stat.errors)

  return (
    <article
      role="button"
      tabIndex={0}
      onClick={() => onOpen(name)}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault()
          onOpen(name)
        }
      }}
      className="flex cursor-pointer flex-col gap-3 rounded-[12px] border border-[var(--color-border)] bg-[var(--color-bg-page)] p-4 transition-colors hover:border-[var(--color-labs)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-labs)]"
    >
      <header className="-mx-4 -mt-4 flex items-start justify-between gap-3 rounded-t-[11px] border-b border-[var(--color-border-labs)] bg-[var(--color-bg-labs)] px-4 py-3">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-[var(--color-bg-labs-tag)] text-[var(--color-labs)]">
          <Blocks className="size-4" aria-hidden="true" />
        </span>
        <div className="flex min-w-0 flex-1 flex-col">
          <h3 className="text-base leading-6 font-medium [overflow-wrap:anywhere] text-[var(--color-text-primary)]">
            {name}
          </h3>
          {statState === "idle" ? null : stat?.sourceAgents.length ? (
            <p className="truncate text-xs leading-5 text-[var(--color-text-description)]">
              {t("labs.skill.card.usage", {
                agents: stat.sourceAgents.join(", "),
                time: stat.lastSeen
                  ? formatRelativeTime(stat.lastSeen, t)
                  : t("labs.skill.topic.timeUnknown"),
              })}
            </p>
          ) : (
            <p className="truncate text-xs leading-5 text-[var(--color-text-description)]">
              {statState !== "ready"
                ? t(
                    statState === "loading"
                      ? "labs.common.loading"
                      : "metrics.loadFailed"
                  )
                : stat
                  ? t("labs.skill.card.usageEmpty")
                  : t("labs.skill.card.usageImported")}
            </p>
          )}
        </div>
        <span
          className={cn(
            "inline-flex h-6 shrink-0 items-center rounded-[6px] px-2 text-xs leading-5 whitespace-nowrap",
            HEALTH_TAG_CLASS[health]
          )}
        >
          {t(`labs.skill.health.${health}`)}
        </span>
      </header>

      {hasCalls ? (
        <p className="text-xs leading-5 text-[var(--color-text-description)]">
          {provenance
            ? t("labs.skill.provenance.counts", provenance)
            : t("labs.skill.provenance.unknownTotals")}
        </p>
      ) : null}

      <p className="line-clamp-2 min-h-10 text-xs leading-5 text-[var(--color-text-secondary)]">
        {description}
      </p>

      {!config ? (
        <div className="flex h-8 items-center justify-between gap-2 rounded-[8px] border border-dashed border-[var(--color-border)] px-3 text-xs leading-5 text-[var(--color-text-description)]">
          <span className="truncate">
            {t(
              configState === "ready"
                ? "labs.skill.card.noConfig"
                : configState === "loading"
                  ? "labs.common.loading"
                  : "metrics.loadFailed"
            )}
          </span>
          {onImport && configState === "ready" ? (
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation()
                onImport()
              }}
              className="shrink-0 rounded-[6px] text-[var(--color-labs)] underline decoration-[var(--color-border-labs)] underline-offset-4 hover:decoration-current focus-visible:outline-2 focus-visible:outline-[var(--color-labs)]"
            >
              {t("labs.skill.card.importConfig")}
            </button>
          ) : null}
        </div>
      ) : null}

      <dl className="mt-auto grid grid-cols-4 gap-2 border-t border-[var(--color-border)] pt-3">
        <Metric
          label={t("labs.skill.provenance.records")}
          value={statsKnown ? (stat?.calls ?? 0).toLocaleString() : "—"}
        />
        <Metric
          label={t("labs.skill.provenance.errors")}
          value={statsKnown ? (stat?.errors ?? 0).toLocaleString() : "—"}
          className={
            !hasCalls
              ? undefined
              : (stat?.errors ?? 0) > 0
                ? TONE_TEXT_CLASS.error
                : stat?.errorRate === null || stat?.errorRate === undefined
                  ? undefined
                  : TONE_TEXT_CLASS.success
          }
        />
        <Metric
          label={t("labs.skill.provenance.errorRate")}
          value={hasCalls ? formatPercent(stat?.errorRate) : "—"}
          help={stat ? mcpStatusCoverage(stat, t) : undefined}
          className={
            hasCalls
              ? TONE_TEXT_CLASS[errorRateTone(stat?.errorRate)]
              : undefined
          }
        />
        <Metric
          label="P95"
          value={
            hasCalls && typeof stat?.p95Ms === "number"
              ? formatMs(stat.p95Ms)
              : "—"
          }
          help={[
            t("labs.skill.card.p95Help"),
            t("labs.common.percentile.p95"),
            t("labs.skill.durationHelp"),
            t("labs.common.percentile.samples"),
          ].join("\n\n")}
        />
      </dl>
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
