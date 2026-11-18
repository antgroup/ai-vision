/** 数值格式化工具 */

import { isChineseLocale } from "@/i18n"

export function formatToken(value: number): string {
  if (isChineseLocale()) {
    if (value >= 1e8) {
      const text = (value / 1e8).toFixed(2).replace(/\.?0+$/, "")
      return `${text} 亿`
    }
    if (value >= 10000) {
      const text = (value / 10000).toFixed(2).replace(/\.?0+$/, "")
      return `${text} 万`
    }
  } else {
    if (value >= 1e9) {
      const text = (value / 1e9).toFixed(2).replace(/\.?0+$/, "")
      return `${text} B`
    }
    if (value >= 1e6) {
      const text = (value / 1e6).toFixed(2).replace(/\.?0+$/, "")
      return `${text} M`
    }
    if (value >= 1000) {
      const text = (value / 1000).toFixed(2).replace(/\.?0+$/, "")
      return `${text} K`
    }
  }
  return value.toLocaleString()
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms.toFixed(1)} ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)} s`
  if (ms < 3600000) return `${(ms / 60000).toFixed(1)} min`
  return `${(ms / 3600000).toFixed(1)} h`
}

export function formatNumber(
  value: number,
  options?: { divisor?: number; suffix?: string; decimals?: number }
): string {
  const { divisor = 1, suffix = "", decimals } = options ?? {}
  const scaled = value / divisor
  const formatted =
    decimals !== undefined
      ? scaled.toFixed(decimals)
      : scaled.toLocaleString(undefined, { maximumFractionDigits: 2 })
  return `${formatted}${suffix}`
}

interface FormattedValue {
  displayValue: string
  unit: string
}

export function formatTokenAuto(value: number): FormattedValue {
  if (isChineseLocale()) {
    if (value >= 1e8) {
      return { displayValue: (value / 1e8).toFixed(2), unit: "亿" }
    }
    if (value >= 1e4) {
      return { displayValue: (value / 1e4).toFixed(2), unit: "万" }
    }
  } else {
    if (value >= 1e9) {
      return { displayValue: (value / 1e9).toFixed(2), unit: "B" }
    }
    if (value >= 1e6) {
      return { displayValue: (value / 1e6).toFixed(2), unit: "M" }
    }
    if (value >= 1e3) {
      return { displayValue: (value / 1e3).toFixed(2), unit: "K" }
    }
  }
  return { displayValue: value.toLocaleString(), unit: "" }
}

export function formatDurationFromSeconds(
  seconds: number,
  options?: { decimals?: number }
): FormattedValue {
  const decimals = options?.decimals ?? 2

  if (isChineseLocale()) {
    if (seconds >= 86400) {
      return { displayValue: (seconds / 86400).toFixed(decimals), unit: "天" }
    }
    if (seconds >= 3600) {
      return { displayValue: (seconds / 3600).toFixed(decimals), unit: "时" }
    }
    if (seconds >= 60) {
      return { displayValue: (seconds / 60).toFixed(decimals), unit: "分" }
    }
    return { displayValue: seconds.toFixed(decimals), unit: "秒" }
  }
  if (seconds >= 86400) {
    return { displayValue: (seconds / 86400).toFixed(decimals), unit: "d" }
  }
  if (seconds >= 3600) {
    return { displayValue: (seconds / 3600).toFixed(decimals), unit: "h" }
  }
  if (seconds >= 60) {
    return { displayValue: (seconds / 60).toFixed(decimals), unit: "min" }
  }
  return { displayValue: seconds.toFixed(decimals), unit: "s" }
}

export function formatDateTime(input: string | number | Date): string {
  const numericInput =
    typeof input === "string" && /^\d+$/.test(input.trim())
      ? Number(input)
      : input
  const date = new Date(numericInput)
  if (Number.isNaN(date.getTime())) return String(input)
  const y = date.getFullYear()
  const M = String(date.getMonth() + 1).padStart(2, "0")
  const d = String(date.getDate()).padStart(2, "0")
  const h = String(date.getHours()).padStart(2, "0")
  const m = String(date.getMinutes()).padStart(2, "0")
  const s = String(date.getSeconds()).padStart(2, "0")
  return `${y}-${M}-${d} ${h}:${m}:${s}`
}

