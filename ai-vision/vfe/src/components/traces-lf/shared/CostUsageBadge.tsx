import { useTranslation } from "react-i18next"
import { useEffect, useMemo, useRef, useState } from "react"
import { createPortal } from "react-dom"

import { Badge } from "@/components/ui/badge"
import { formatCost } from "./utils"

type FloatingPosition = { top: number; left: number }

function useHoverCardPosition(open: boolean, width: number) {
  const triggerRef = useRef<HTMLDivElement | null>(null)
  const [position, setPosition] = useState<FloatingPosition | null>(null)

  useEffect(() => {
    if (!open || !triggerRef.current) return

    const updatePosition = () => {
      if (!triggerRef.current) return
      const rect = triggerRef.current.getBoundingClientRect()
      const viewportWidth = window.innerWidth
      const viewportHeight = window.innerHeight
      const gap = 8
      const estimatedHeight = 260
      const placeAbove = rect.top - gap - estimatedHeight > 0

      const left = Math.min(
        Math.max(rect.left, gap),
        Math.max(viewportWidth - width - gap, gap)
      )
      const top = placeAbove
        ? Math.max(rect.top - gap - estimatedHeight, gap)
        : Math.min(
            rect.bottom + gap,
            Math.max(viewportHeight - estimatedHeight - gap, gap)
          )

      setPosition({ top, left })
    }

    updatePosition()
    window.addEventListener("scroll", updatePosition, true)
    window.addEventListener("resize", updatePosition)
    return () => {
      window.removeEventListener("scroll", updatePosition, true)
      window.removeEventListener("resize", updatePosition)
    }
  }, [open, width])

  return { triggerRef, position }
}

const CACHE_KEYS = [
  "cache_read_input_tokens",
  "cache_creation_input_tokens",
] as const
const USAGE_LABEL_KEYS: Record<string, string> = {
  input: "nativeSpanUsage.input",
  output: "nativeSpanUsage.output",
  total: "nativeSpanUsage.total",
  cache_read_input_tokens: "nativeSpanUsage.cacheRead",
  cache_creation_input_tokens: "nativeSpanUsage.cacheCreation",
}

export function CostBadge({
  totalCost,
  inputCost,
  outputCost,
  costDetails,
  variant = "inline",
}: {
  totalCost: number
  inputCost: number | null
  outputCost: number | null
  costDetails: Record<string, number> | null
  variant?: "inline" | "badge"
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const hasBreakdown =
    inputCost !== null ||
    outputCost !== null ||
    (costDetails && Object.keys(costDetails).length > 0)
  const { triggerRef, position } = useHoverCardPosition(open, 256)

  const formatPrecise = (value: number | null) => {
    if (value === null || value === undefined) return "-"
    if (value === 0) return "$0"
    return `$${value.toFixed(8).replace(/0+$/, "").replace(/\.$/, "")}`
  }

  return (
    <div
      ref={triggerRef}
      className="relative"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <Badge
        variant="secondary"
        className={`cursor-default gap-1 shadow-none ${
          variant === "badge" ? "" : "bg-transparent px-0 py-0"
        }`}
      >
        <span>{formatCost(totalCost)}</span>
        {hasBreakdown && (
          <span className="text-xs text-muted-foreground">ⓘ</span>
        )}
      </Badge>
      {open &&
        hasBreakdown &&
        position &&
        createPortal(
          <div
            className="fixed z-[100] w-64 rounded-lg border bg-popover text-popover-foreground shadow-md"
            style={{ top: position.top, left: position.left }}
          >
            <div className="px-4 py-3">
              <h4 className="mb-3 text-sm font-medium">
                {t("traceLabels.costBreakdown")}
              </h4>
              <div className="space-y-2">
                {inputCost !== null && (
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-normal">
                      {t("traceLabels.inputCost")}
                    </span>
                    <span className="font-mono text-sm">
                      {formatPrecise(inputCost)}
                    </span>
                  </div>
                )}
                {outputCost !== null && (
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-normal">
                      {t("traceLabels.outputCost")}
                    </span>
                    <span className="font-mono text-sm">
                      {formatPrecise(outputCost)}
                    </span>
                  </div>
                )}
                {costDetails &&
                  Object.entries(costDetails)
                    .filter(([k]) => k !== "input" && k !== "output")
                    .map(([k, v]) => (
                      <div
                        key={k}
                        className="flex items-center justify-between"
                      >
                        <span className="text-xs text-muted-foreground">
                          {USAGE_LABEL_KEYS[k] ? t(USAGE_LABEL_KEYS[k]) : k}
                        </span>
                        <span className="font-mono text-xs text-muted-foreground">
                          {formatPrecise(v)}
                        </span>
                      </div>
                    ))}
                <div className="mt-2 flex items-center justify-between border-t pt-2">
                  <span className="text-sm font-medium">
                    {t("traceLabels.totalCost")}
                  </span>
                  <span className="font-mono text-sm font-medium">
                    {formatPrecise(totalCost)}
                  </span>
                </div>
              </div>
            </div>
          </div>,
          document.body
        )}
    </div>
  )
}

export function UsageBadge({
  promptTokens,
  completionTokens,
  totalTokens,
  usageDetails,
  variant = "inline",
}: {
  promptTokens: number
  completionTokens: number
  totalTokens: number
  usageDetails: Record<string, number> | null
  variant?: "inline" | "badge"
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const hasBreakdown = usageDetails && Object.keys(usageDetails).length > 0
  const { triggerRef, position } = useHoverCardPosition(open, 288)

  const groups = useMemo(() => {
    if (!usageDetails)
      return {
        input: [] as [string, number][],
        output: [] as [string, number][],
        cache: [] as [string, number][],
        other: [] as [string, number][],
      }
    const input: [string, number][] = []
    const output: [string, number][] = []
    const cache: [string, number][] = []
    const other: [string, number][] = []
    const cacheKeySet = new Set<string>(CACHE_KEYS)
    for (const [k, v] of Object.entries(usageDetails)) {
      if (cacheKeySet.has(k)) cache.push([k, v])
      else if (k === "input" || k.includes("input")) input.push([k, v])
      else if (k === "output" || k.includes("output")) output.push([k, v])
      else other.push([k, v])
    }
    cache.sort(
      (a, b) =>
        CACHE_KEYS.indexOf(a[0] as (typeof CACHE_KEYS)[number]) -
        CACHE_KEYS.indexOf(b[0] as (typeof CACHE_KEYS)[number])
    )
    return { input, output, cache, other }
  }, [usageDetails])

  return (
    <div
      ref={triggerRef}
      className="relative"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <Badge
        variant="secondary"
        className={`cursor-default gap-1 shadow-none ${
          variant === "badge" ? "" : "bg-transparent px-0 py-0"
        }`}
      >
        <span>
          {t("nativeSpanUsage.input")} {promptTokens.toLocaleString()}
        </span>
        <span aria-hidden="true">·</span>
        <span>
          {t("nativeSpanUsage.output")} {completionTokens.toLocaleString()}
        </span>
        <span aria-hidden="true">·</span>
        <span>
          {t("nativeSpanUsage.total")} {totalTokens.toLocaleString()}
        </span>
        {hasBreakdown && (
          <span className="text-xs text-muted-foreground">ⓘ</span>
        )}
      </Badge>
      {open &&
        hasBreakdown &&
        position &&
        createPortal(
          <div
            className="fixed z-[100] w-72 rounded-lg border bg-popover text-popover-foreground shadow-md"
            style={{ top: position.top, left: position.left }}
          >
            <div className="px-4 py-3">
              <h4 className="mb-3 text-sm font-medium">
                {t("nativeSpanUsage.breakdown")}
              </h4>
              <div className="space-y-2">
                {(groups.input.length > 0 || groups.cache.length > 0) && (
                  <>
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-normal">
                        {t("traceLabels.inputUsage")}
                      </span>
                      <span className="font-mono text-sm font-normal">
                        {promptTokens.toLocaleString()}
                      </span>
                    </div>
                    {groups.cache
                      .filter(([, v]) => v > 0)
                      .map(([k, v]) => (
                        <div
                          key={k}
                          className="flex items-center justify-between"
                        >
                          <span className="text-xs text-muted-foreground">
                            {USAGE_LABEL_KEYS[k] ? t(USAGE_LABEL_KEYS[k]) : k}
                          </span>
                          <span className="font-mono text-xs text-muted-foreground">
                            {v.toLocaleString()}
                          </span>
                        </div>
                      ))}
                    {groups.input
                      .filter(([k, v]) => k !== "input" || v !== promptTokens)
                      .map(([k, v]) => (
                        <div
                          key={k}
                          className="flex items-center justify-between"
                        >
                          <span className="text-xs text-muted-foreground">
                            {USAGE_LABEL_KEYS[k] ? t(USAGE_LABEL_KEYS[k]) : k}
                          </span>
                          <span className="font-mono text-xs text-muted-foreground">
                            {v.toLocaleString()}
                          </span>
                        </div>
                      ))}
                  </>
                )}
                {groups.output.length > 0 && (
                  <>
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-normal">
                        {t("traceLabels.outputUsage")}
                      </span>
                      <span className="font-mono text-sm font-normal">
                        {completionTokens.toLocaleString()}
                      </span>
                    </div>
                    {groups.output
                      .filter(
                        ([k, v]) => k !== "output" || v !== completionTokens
                      )
                      .map(([k, v]) => (
                        <div
                          key={k}
                          className="flex items-center justify-between"
                        >
                          <span className="text-xs text-muted-foreground">
                            {USAGE_LABEL_KEYS[k] ? t(USAGE_LABEL_KEYS[k]) : k}
                          </span>
                          <span className="font-mono text-xs text-muted-foreground">
                            {v.toLocaleString()}
                          </span>
                        </div>
                      ))}
                  </>
                )}
                {groups.other.length > 0 &&
                  groups.other
                    .filter(([k, v]) => k !== "total" || v !== totalTokens)
                    .map(([k, v]) => (
                      <div
                        key={k}
                        className="flex items-center justify-between"
                      >
                        <span className="text-xs text-muted-foreground">
                          {USAGE_LABEL_KEYS[k] ? t(USAGE_LABEL_KEYS[k]) : k}
                        </span>
                        <span className="font-mono text-xs text-muted-foreground">
                          {v.toLocaleString()}
                        </span>
                      </div>
                    ))}
                <div className="mt-2 flex items-center justify-between border-t pt-2">
                  <span className="text-sm font-medium">
                    {t("nativeSpanUsage.total")}
                  </span>
                  <span className="font-mono text-sm font-medium">
                    {totalTokens.toLocaleString()}
                  </span>
                </div>
              </div>
            </div>
          </div>,
          document.body
        )}
    </div>
  )
}
