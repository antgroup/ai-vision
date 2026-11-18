import { useTranslation } from "react-i18next"
import type { ReactNode } from "react"
import { CircleHelp, TrendingDown, TrendingUp } from "lucide-react"

import { Card } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { formatTokenAuto, formatDurationFromSeconds } from "@/lib/formatters"

export type StatsCardTone =
  | "info"
  | "success"
  | "warning"
  | "error"
  | "auxiliary"
  | "disabled"

export type StatsCardUnitType = "plain" | "token" | "duration"
export type StatsCardMetricRole = "primary" | "detail"

export interface StatsCardMetaItem {
  label: ReactNode
  value?: ReactNode
}

export interface StatsCardProps {
  title: ReactNode
  headerMeta?: ReactNode
  value?: ReactNode
  unit?: ReactNode
  unitType?: StatsCardUnitType
  unitOptions?: { decimals?: number }
  tooltip?: ReactNode
  description?: ReactNode
  helpText?: ReactNode
  meta?: StatsCardMetaItem[]
  tone?: StatsCardTone
  size?: "default" | "large"
  metricRole?: StatsCardMetricRole
  loading?: boolean
  error?: ReactNode
  emptyText?: ReactNode
  scoreDelta?: number | null
  className?: string
}

const toneClassName: Record<StatsCardTone, string> = {
  info: "bg-[var(--color-bg-info)]",
  success: "bg-[var(--color-bg-success)]",
  warning: "bg-[var(--color-bg-warning)]",
  error: "bg-[var(--color-bg-error)]",
  auxiliary: "bg-[var(--color-bg-auxiliary)]",
  disabled: "bg-muted/50 opacity-60",
}

function autoFormat(
  value: ReactNode,
  unitType: StatsCardUnitType | undefined,
  unitOptions: StatsCardProps["unitOptions"],
  fallbackUnit: ReactNode
): { displayValue: ReactNode; displayUnit: ReactNode } {
  if (!unitType || unitType === "plain") {
    return { displayValue: value, displayUnit: fallbackUnit }
  }
  if (value === null || value === undefined || value === "") {
    return { displayValue: undefined, displayUnit: undefined }
  }
  const num = Number(value)
  if (!Number.isFinite(num)) {
    return { displayValue: value, displayUnit: fallbackUnit }
  }
  if (unitType === "token") {
    const result = formatTokenAuto(num)
    return {
      displayValue: result.displayValue,
      displayUnit: result.unit || fallbackUnit,
    }
  }
  const result = formatDurationFromSeconds(num, unitOptions)
  return {
    displayValue: result.displayValue,
    displayUnit: result.unit || fallbackUnit,
  }
}

export function StatsCard({
  title,
  headerMeta,
  value,
  unit,
  unitType,
  unitOptions,
  tooltip,
  description,
  helpText,
  meta,
  metricRole = "primary",
  tone = "info",
  size = "default",
  loading = false,
  error,
  emptyText,
  scoreDelta,
  className,
}: StatsCardProps) {
  const { t } = useTranslation()
  const isLarge = size === "large"
  const isDetail = metricRole === "detail"
  const { displayValue, displayUnit } = autoFormat(
    value,
    unitType,
    unitOptions,
    unit
  )
  const hasValue = value !== null && value !== undefined && value !== ""
  const showLoading = loading && !hasValue
  const content = showLoading ? (
    <Skeleton className="h-7 w-20" />
  ) : !hasValue ? (
    error ? (
      t("metrics.loadFailed")
    ) : (
      (emptyText ?? t("metrics.notReported"))
    )
  ) : (
    displayValue
  )

  return (
    <Card
      className={cn(
        "relative grid min-h-[100px] grid-rows-[auto_1fr] gap-2 overflow-hidden rounded-xl border border-[var(--color-border)] bg-card p-3 py-3 shadow-none ring-0",
        isLarge && "min-h-[112px]",
        isDetail && "border-dashed",
        className
      )}
    >
      {isDetail && (
        <span className="absolute top-1 right-1.5 rounded bg-muted px-1 py-0.5 text-[9px] leading-3 text-muted-foreground">
          {t("metrics.metaNotCounted")}
        </span>
      )}
      <div
        className={cn(
          "flex min-w-0 items-center gap-2",
          isDetail && "pr-[4.5rem]"
        )}
      >
        <h3
          className={cn(
            "min-w-0 truncate [font-family:var(--font-family-base)] text-[length:var(--font-size-base)] leading-[var(--line-height-base)] font-[var(--font-weight-regular)] text-[var(--color-text-secondary)]",
            isLarge &&
              "text-[length:var(--font-size-md)] leading-[var(--line-height-md)]"
          )}
          title={typeof title === "string" ? title : undefined}
        >
          {title}
        </h3>
        {headerMeta ? (
          <span className="ml-auto shrink-0 [font-family:var(--font-family-base)] text-[length:var(--font-size-sm)] leading-[var(--line-height-sm)] text-[var(--color-text-description)]">
            {headerMeta}
          </span>
        ) : null}
        {helpText ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                className="inline-flex size-5 items-center justify-center rounded-md text-[var(--color-icon-normal)] hover:bg-[var(--color-bg-hover)]"
                aria-label={t("dashboard.viewHelp")}
              >
                <CircleHelp className="size-4" />
              </button>
            </TooltipTrigger>
            <TooltipContent>{helpText}</TooltipContent>
          </Tooltip>
        ) : null}
      </div>

      <div
        className={cn(
          "flex min-h-0 items-center rounded-lg px-3 py-2",
          toneClassName[tone],
          isDetail && "opacity-70"
        )}
      >
        {tooltip ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <div className="flex min-w-0 cursor-default flex-wrap items-baseline gap-x-2 gap-y-1">
                <strong
                  className={cn(
                    "[font-family:var(--font-family-number)] text-[length:var(--font-size-lg)] leading-[var(--line-height-lg)] font-[var(--font-weight-bold)] break-words text-[var(--color-text-primary)] tabular-nums",
                    !hasValue &&
                      "[font-family:var(--font-family-base)] text-sm font-normal text-muted-foreground",
                    isLarge &&
                      "text-[length:var(--font-size-xl)] leading-[var(--line-height-xl)]"
                  )}
                >
                  {content}
                </strong>
                {!showLoading && hasValue && displayUnit ? (
                  <span className="[font-family:var(--font-family-base)] text-[length:var(--font-size-base)] leading-[var(--line-height-base)] text-[var(--color-text-primary)]">
                    {displayUnit}
                  </span>
                ) : null}
                {!showLoading &&
                hasValue &&
                scoreDelta !== null &&
                scoreDelta !== undefined ? (
                  <ScoreDelta value={scoreDelta} />
                ) : null}
              </div>
            </TooltipTrigger>
            <TooltipContent>{tooltip}</TooltipContent>
          </Tooltip>
        ) : (
          <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1">
            <strong
              className={cn(
                "[font-family:var(--font-family-number)] text-[length:var(--font-size-lg)] leading-[var(--line-height-lg)] font-[var(--font-weight-bold)] break-words text-[var(--color-text-primary)] tabular-nums",
                !hasValue &&
                  "[font-family:var(--font-family-base)] text-sm font-normal text-muted-foreground",
                isLarge &&
                  "text-[length:var(--font-size-xl)] leading-[var(--line-height-xl)]"
              )}
            >
              {content}
            </strong>
            {!showLoading && hasValue && displayUnit ? (
              <span className="[font-family:var(--font-family-base)] text-[length:var(--font-size-base)] leading-[var(--line-height-base)] text-[var(--color-text-primary)]">
                {displayUnit}
              </span>
            ) : null}
            {!showLoading &&
            hasValue &&
            scoreDelta !== null &&
            scoreDelta !== undefined ? (
              <ScoreDelta value={scoreDelta} />
            ) : null}
          </div>
        )}
      </div>

      {description || meta?.length ? (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 [font-family:var(--font-family-base)] text-[length:var(--font-size-sm)] leading-[var(--line-height-sm)] text-[var(--color-text-description)]">
          {description ? <span>{description}</span> : null}
          {meta?.map((item, index) => (
            <span key={index} className="inline-flex items-center gap-1">
              <span>{item.label}</span>
              {item.value !== null && item.value !== undefined ? (
                <span className="font-[var(--font-weight-medium)] text-[var(--color-text-secondary)]">
                  {item.value}
                </span>
              ) : null}
            </span>
          ))}
        </div>
      ) : null}
    </Card>
  )
}

export function ScoreDelta({ value }: { value: number }) {
  if (!Number.isFinite(value)) return null

  const roundedValue = Number(value.toFixed(1))
  const normalizedValue = Object.is(roundedValue, -0) ? 0 : roundedValue
  const formatted = Math.abs(normalizedValue).toFixed(1)
  if (normalizedValue > 0) {
    return (
      <span className="inline-flex items-center gap-0.5 text-xs font-medium text-emerald-600">
        <TrendingUp className="size-3" aria-hidden="true" />
        <span className="tabular-nums">+{formatted}</span>
      </span>
    )
  }
  if (normalizedValue < 0) {
    return (
      <span className="inline-flex items-center gap-0.5 text-xs font-medium text-red-500">
        <TrendingDown className="size-3" aria-hidden="true" />
        <span className="tabular-nums">-{formatted}</span>
      </span>
    )
  }
  return null
}
