/**
 * Combined token-usage receipt.
 *
 * One paper-like receipt that rolls up 1..N agents into 5 headline metrics
 * plus a per-agent share breakdown. Styled as a thermal-printer receipt
 * in both light and dark themes via Tailwind `dark:` classes.
 */

import { useTranslation } from "react-i18next"
import { useCallback, useEffect, useState } from "react"
import { Loader2, Printer } from "lucide-react"

import { cn } from "@/lib/utils"

import {
  buildLongCode,
  formatDateOnly,
  formatNumber,
  formatPercent,
} from "./utils/format"
import type { AgentShareRow, ReceiptTotals } from "./utils/normalize"

function ReceiptLoadingSkeleton({ onTimeout }: { onTimeout: () => void }) {
  const { t } = useTranslation()

  useEffect(() => {
    const timer = setTimeout(onTimeout, 8_000)
    return () => clearTimeout(timer)
  }, [onTimeout])

  return (
    <div className="flex min-h-[420px] flex-col items-center justify-center gap-5 py-12">
      <Loader2 className="size-8 animate-spin text-zinc-400 dark:text-zinc-500" />
      <p className="text-center text-xs text-zinc-500 dark:text-zinc-400">
        {t("receipt.generating")}
      </p>
    </div>
  )
}

interface ReceiptUser {
  name: string
  userUuid?: string
}

interface ReceiptProps {
  totals: ReceiptTotals
  shares: AgentShareRow[]
  rangeStart: Date
  rangeEnd: Date
  user?: ReceiptUser | null
  hasError?: boolean
  loading?: boolean
}

function hashString(value: string): number {
  let hash = 0
  for (let i = 0; i < value.length; i++) {
    hash = (hash * 31 + value.charCodeAt(i)) >>> 0
  }
  return hash
}

function buildHex(seed: string): string {
  return hashString(seed)
    .toString(16)
    .toUpperCase()
    .padStart(6, "0")
    .slice(0, 6)
}

function Divider() {
  return (
    <div className="border-t border-dashed border-zinc-300 dark:border-zinc-600" />
  )
}

function MetricRow({
  label,
  value,
  emphasize,
}: {
  label: string
  value: string
  emphasize?: boolean
}) {
  return (
    <div
      className={cn(
        "flex items-baseline justify-between text-sm",
        emphasize && "font-semibold"
      )}
    >
      <span className="text-zinc-600 dark:text-zinc-300">{label}</span>
      <span className="tabular-nums">{value}</span>
    </div>
  )
}

const TALLY_CELLS = 100

function useDarkMode(): boolean {
  const [dark, setDark] = useState(() =>
    document.documentElement.classList.contains("dark")
  )
  useEffect(() => {
    const el = document.documentElement
    const observer = new MutationObserver(() => {
      setDark(el.classList.contains("dark"))
    })
    observer.observe(el, { attributes: true, attributeFilter: ["class"] })
    return () => observer.disconnect()
  }, [])
  return dark
}

function TallyBar({ percent }: { percent: number }) {
  const dark = useDarkMode()
  const filledColor = dark ? "#f4f4f5" : "#18181b"
  const emptyColor = dark ? "#27272a" : "#e4e4e7"
  const ratio = Math.max(0, Math.min(1, percent))
  const filled =
    ratio === 0
      ? 0
      : Math.max(1, Math.min(TALLY_CELLS, Math.round(ratio * TALLY_CELLS)))
  return (
    <div
      aria-hidden="true"
      style={{
        display: "grid",
        gridTemplateColumns: `repeat(${TALLY_CELLS}, 1fr)`,
        gap: "1px",
        width: "100%",
        height: "14px",
      }}
    >
      {Array.from({ length: TALLY_CELLS }, (_, i) => (
        <div
          key={i}
          style={{
            borderRadius: "1px",
            backgroundColor: i < filled ? filledColor : emptyColor,
          }}
        />
      ))}
    </div>
  )
}

export function Receipt({
  totals,
  shares,
  rangeStart,
  rangeEnd,
  user,
  hasError = false,
  loading = false,
}: ReceiptProps) {
  const { t } = useTranslation()
  const [skeletonTimedOut, setSkeletonTimedOut] = useState(false)
  const handleTimeout = useCallback(() => setSkeletonTimedOut(true), [])
  const noData = totals.totalTokens === 0 && shares.length === 0
  const isFirstLoad = noData && !skeletonTimedOut

  const seed = `${rangeStart.getTime()}-${rangeEnd.getTime()}-${shares
    .map((s) => s.agentProduct)
    .join(",")}`
  const hex = buildHex(seed)
  const longCode = buildLongCode("RCP", rangeEnd, hex)

  return (
    <div
      className={cn(
        "relative mx-auto w-[420px] max-w-full rounded-sm bg-white p-7 font-mono text-zinc-900 ring-1 ring-zinc-200/70",
        "shadow-[0_30px_60px_-15px_rgba(15,23,42,0.35),0_10px_24px_-12px_rgba(15,23,42,0.25),inset_0_1px_0_rgba(255,255,255,0.8)]",
        "dark:bg-zinc-950 dark:text-zinc-100 dark:ring-zinc-800",
        "dark:shadow-[0_30px_60px_-15px_rgba(0,0,0,0.65),0_10px_24px_-12px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.04)]",
        "before:absolute before:inset-x-2 before:top-1.5 before:-z-10 before:h-3 before:rounded-sm before:bg-white/70 before:shadow-md before:dark:bg-zinc-900/70",
        "after:absolute after:inset-x-4 after:top-3 after:-z-20 after:h-2 after:rounded-sm after:bg-white/50 after:shadow-sm after:dark:bg-zinc-900/50",
        loading && !noData && "opacity-60"
      )}
    >
      {isFirstLoad ? (
        <ReceiptLoadingSkeleton onTimeout={handleTimeout} />
      ) : (
        <>
          {/* Header */}
          <div className="flex flex-col items-center gap-1.5 py-1">
            <Printer className="size-8" strokeWidth={1.5} />
            <h2 className="text-base font-semibold tracking-[0.25em]">
              AI VISION
            </h2>
            <p className="text-[10px] tracking-[0.3em] text-zinc-500 dark:text-zinc-400">
              {t("receipt.title")}
            </p>
          </div>

          <Divider />

          {/* User */}
          {user ? (
            <>
              <div className="space-y-1 py-3 text-xs">
                <div className="flex justify-between">
                  <span className="text-zinc-500 dark:text-zinc-400">
                    {t("receipt.customer")}
                  </span>
                  <span className="font-medium">
                    {user.name}
                    {user.userUuid ? ` (${user.userUuid})` : ""}
                  </span>
                </div>
              </div>
              <Divider />
            </>
          ) : null}

          {/* Meta */}
          <div className="space-y-1 py-3 text-xs">
            <div className="flex justify-between">
              <span className="text-zinc-500 dark:text-zinc-400">
                {t("receipt.period")}
              </span>
              <span className="tabular-nums">
                {formatDateOnly(rangeStart)} ~ {formatDateOnly(rangeEnd)}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-zinc-500 dark:text-zinc-400">
                {t("receipt.agentCount")}
              </span>
              <span>
                {shares.length === 0
                  ? "—"
                  : t("receipt.agentCountValue", { count: shares.length })}
              </span>
            </div>
          </div>

          <Divider />

          {/* Summary header */}
          <div className="flex justify-between py-3 text-[11px] tracking-widest text-zinc-500 uppercase dark:text-zinc-400">
            <span>{t("receipt.metric")}</span>
            <span>{t("receipt.value")}</span>
          </div>

          <Divider />

          <div className="space-y-2 py-3">
            <MetricRow
              label={t("receipt.inputTokens")}
              value={formatNumber(totals.nativeInput)}
            />
            <MetricRow
              label={t("receipt.outputTokens")}
              value={formatNumber(totals.output)}
            />
            <MetricRow
              label={t("receipt.cacheTokens")}
              value={formatNumber(totals.cacheRead)}
            />
            <MetricRow
              label={t("receipt.cacheHitRate")}
              value={formatPercent(totals.hitRate)}
              emphasize
            />
          </div>

          <Divider />

          {/* Total */}
          <div className="py-3 text-center">
            <p className="text-[11px] tracking-[0.3em] text-zinc-500 uppercase dark:text-zinc-400">
              {t("receipt.totalTokens")}
            </p>
            <p className="mt-1 text-2xl font-bold tabular-nums">
              {formatNumber(totals.totalTokens)}
            </p>
          </div>

          <Divider />

          {/* Per-agent breakdown header */}
          <div className="flex justify-between py-3 text-[11px] tracking-widest text-zinc-500 uppercase dark:text-zinc-400">
            <span>{t("receipt.agentShare")}</span>
            <span>{t("receipt.tokens")}</span>
          </div>

          <Divider />

          <div className="space-y-3 py-3">
            {shares.length === 0 ? (
              <p className="py-4 text-center text-xs text-zinc-500 dark:text-zinc-400">
                {t("common.noData")}
              </p>
            ) : (
              shares.map((row) => (
                <div key={row.agentProduct} className="space-y-1.5">
                  <div className="flex items-baseline justify-between text-sm">
                    <span className="truncate pr-2 font-medium tracking-wide uppercase">
                      {row.agentLabel}
                    </span>
                    <span className="tabular-nums">
                      {formatPercent(row.share, 1)}
                    </span>
                  </div>
                  <TallyBar percent={row.share} />
                  <div className="flex justify-between text-[11px] text-zinc-500 dark:text-zinc-400">
                    <span>
                      {t("receipt.cacheHitRate")}{" "}
                      {formatPercent(row.hitRate, 1)}
                    </span>
                    <span className="tabular-nums">
                      {formatNumber(row.totalTokens)}
                    </span>
                  </div>
                </div>
              ))
            )}
          </div>

          <Divider />

          {/* Footer */}
          <div className="space-y-2 py-3 text-center">
            {hasError && (
              <p className="text-[11px] leading-relaxed text-zinc-600 dark:text-zinc-300">
                {t("receipt.partialFailure")}
              </p>
            )}
            <p className="text-[11px] tracking-wide text-zinc-500 tabular-nums dark:text-zinc-400">
              {longCode}
            </p>
          </div>
        </>
      )}
    </div>
  )
}
