import type { TableColumnDataType } from "@/lib/schemas/observability"
import {
  formatNumber,
  formatDuration,
  formatToken,
  formatDateTime,
} from "@/lib/formatters"

type CellRenderer = (
  value: unknown,
  row: Record<string, unknown>
) => React.ReactNode

function renderDefault(value: unknown) {
  return (
    <span>{value !== null && value !== undefined ? String(value) : "-"}</span>
  )
}

function measurement(value: unknown): number | null {
  if (
    value === null ||
    value === undefined ||
    (typeof value === "string" && !value.trim())
  )
    return null
  const num = Number(value)
  return Number.isFinite(num) ? num : null
}

function renderNumber(value: unknown) {
  const num = measurement(value)
  if (num === null) return <span>—</span>
  return <span>{formatNumber(num)}</span>
}

function renderDateTime(value: unknown) {
  if (!value) return <span>-</span>
  const str = String(value)
  const date = /^\d{13,}$/.test(str) ? new Date(Number(str)) : new Date(str)
  if (isNaN(date.getTime())) return <span>{str}</span>
  return <span className="text-sm">{formatDateTime(date)}</span>
}

function renderDuration(value: unknown) {
  const ms = measurement(value)
  if (ms === null) return <span>—</span>
  return <span>{formatDuration(ms)}</span>
}

function renderToken(value: unknown) {
  const num = measurement(value)
  if (num === null) return <span>—</span>
  return <span>{formatToken(num)}</span>
}

function renderStatus(value: unknown) {
  return <span>{String(value ?? "-")}</span>
}

const registry: Record<TableColumnDataType, CellRenderer> = {
  string: (v) => renderDefault(v),
  number: (v) => renderNumber(v),
  datetime: (v) => renderDateTime(v),
  duration: (v) => renderDuration(v),
  token: (v) => renderToken(v),
  status: (v) => renderStatus(v),
}

export function getCellRenderer(
  dataType: TableColumnDataType | string
): CellRenderer {
  return (registry as Record<string, CellRenderer>)[dataType] ?? registry.string
}
