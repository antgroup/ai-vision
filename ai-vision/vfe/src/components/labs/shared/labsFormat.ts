import type { TFunction } from "i18next"

export type StatTone = "default" | "success" | "warning" | "error"

export const TONE_TEXT_CLASS: Record<StatTone, string> = {
  default: "text-[var(--color-text-primary)]",
  success: "text-[var(--color-success)]",
  warning: "text-[var(--color-alert)]",
  error: "text-[var(--color-error)]",
}

export const TONE_TAG_CLASS: Record<StatTone, string> = {
  default: "bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)]",
  success: "bg-[var(--color-bg-success)] text-[var(--color-success)]",
  warning: "bg-[var(--color-bg-alert)] text-[var(--color-alert)]",
  error: "bg-[var(--color-bg-error)] text-[var(--color-error)]",
}

export function formatPercent(
  value: number | null | undefined,
  decimals = 1
): string {
  if (value === null || value === undefined || !Number.isFinite(value))
    return "—"
  return `${(value * 100).toFixed(decimals)}%`
}

export function formatMs(ms: number | null): string {
  if (ms === null) return "—"
  if (ms < 1000) return `${Math.round(ms)} ms`
  return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`
}

export function formatRelativeTime(iso: string, t: TFunction): string {
  const diffMinutes = Math.max(
    0,
    Math.round((Date.now() - new Date(iso).getTime()) / 60_000)
  )
  if (diffMinutes < 1) return t("labs.common.justNow")
  if (diffMinutes < 60)
    return t("labs.common.minutesAgo", { count: diffMinutes })
  const hours = Math.round(diffMinutes / 60)
  if (hours < 24) return t("labs.common.hoursAgo", { count: hours })
  return t("labs.common.daysAgo", { count: Math.round(hours / 24) })
}
