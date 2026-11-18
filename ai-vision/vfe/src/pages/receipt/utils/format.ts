const pad = (n: number) => String(n).padStart(2, "0")

export const formatNumber = (n: number): string => n.toLocaleString("en-US")

export const formatDateTime = (d: Date): string =>
  `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`

export const buildShortCode = (prefix: string, d: Date, hex: string): string =>
  `${prefix}_${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}_${hex}`

export const buildLongCode = (prefix: string, d: Date, hex: string): string =>
  `${prefix}_${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}_${hex}`

export const formatPercent = (ratio: number, fractionDigits = 2): string => {
  if (!Number.isFinite(ratio)) return "0.00%"
  return `${(ratio * 100).toFixed(fractionDigits)}%`
}

export const formatDateOnly = (d: Date): string =>
  `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
