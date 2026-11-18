import type { TFunction } from "i18next"
import { useTranslation } from "react-i18next"
import type { ReactNode } from "react"

import { cn } from "@/lib/utils"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { formatDateTime } from "@/lib/formatters"

export interface EventTableOption {
  label: string
  value: string
}

export interface EventTableRow {
  source?: Record<string, unknown>
  id: string
  time: ReactNode
  event: ReactNode
  cost: ReactNode
  costRatio?: number
  model: ReactNode
  inputTokens: ReactNode
  cacheReadTokens: ReactNode
  outputTokens: ReactNode
}

export interface EventTableColumn {
  key: Exclude<keyof EventTableRow, "source">
  header: ReactNode
  className?: string
  cell?: (row: EventTableRow) => ReactNode
}

export interface EventTableProps {
  rows: EventTableRow[]
  columns?: EventTableColumn[]
  title?: ReactNode
  total?: number
  page?: string
  pageOptions?: EventTableOption[]
  pageSize?: string
  pageSizeOptions?: EventTableOption[]
  eventType?: string
  eventTypeOptions?: EventTableOption[]
  eventColorMap?: Record<string, string>
  showAll?: boolean
  showAllLabel?: ReactNode
  toolbarActions?: ReactNode
  rowActions?: (row: EventTableRow) => ReactNode
  maxHeight?: number
  emptyText?: ReactNode
  loading?: boolean
  error?: ReactNode
  className?: string
  onPageChange?: (value: string) => void
  onPageSizeChange?: (value: string) => void
  onEventTypeChange?: (value: string) => void
  onShowAllToggle?: () => void
}

const defaultEventColorRules: [RegExp, string][] = [
  [/error|fail|exception/i, "text-destructive"],
  [/tool/i, "text-purple-600 dark:text-purple-400"],
  [/api|request|response/i, "text-blue-600 dark:text-blue-400"],
]

function getEventColorClass(
  event: string,
  customMap?: Record<string, string>
): string {
  if (customMap) {
    // When the caller encodes the semantic type as a "name · type" suffix, match
    // on the type token alone so a name that merely contains a keyword (e.g.
    // "tools · workflow") isn't shadowed by an earlier key like "tool".
    const suffix = event.includes(" · ") ? event.split(" · ").pop() : event
    const lower = (suffix ?? event).toLowerCase()
    for (const [keyword, cls] of Object.entries(customMap)) {
      if (lower.includes(keyword.toLowerCase())) return cls
    }
  }
  for (const [pattern, cls] of defaultEventColorRules) {
    if (pattern.test(event)) return cls
  }
  return ""
}

function TimeCell({ value }: { value: ReactNode }) {
  const raw = String(value)
  const formatted = formatDateTime(raw)
  return <span>{formatted}</span>
}

function EventCell({
  value,
  colorClass,
}: {
  value: ReactNode
  colorClass: string
}) {
  return (
    <span className={cn("inline-flex items-center gap-1.5", colorClass)}>
      {colorClass && (
        <span
          className={cn(
            "inline-block size-1.5 shrink-0 rounded-full bg-current"
          )}
        />
      )}
      {value}
    </span>
  )
}

function buildDefaultColumns(
  t: TFunction,
  eventColorMap?: Record<string, string>
): EventTableColumn[] {
  return [
    {
      key: "time",
      header: t("dashboard.columns.time"),
      className: "min-w-[176px]",
      cell: (row) => <TimeCell value={row.time} />,
    },
    {
      key: "event",
      header: t("dashboard.columns.event"),
      className: "min-w-[180px]",
      cell: (row) => (
        <EventCell
          value={row.event}
          colorClass={getEventColorClass(String(row.event), eventColorMap)}
        />
      ),
    },
    {
      key: "cost",
      header: t("dashboard.columns.cost_ms"),
      className: "min-w-[220px]",
      cell: (row) => (
        <div className="flex items-center gap-3">
          <CostBar value={row.costRatio ?? 0} />
          <span className="tabular-nums">{row.cost}</span>
        </div>
      ),
    },
    {
      key: "model",
      header: t("dashboard.columns.model"),
      className: "min-w-[220px]",
    },
    {
      key: "inputTokens",
      header: t("dashboard.columns.input_tokens"),
      className: "min-w-[160px]",
    },
    {
      key: "cacheReadTokens",
      header: t("dashboard.columns.cache_read_tokens"),
      className: "min-w-[180px]",
    },
    {
      key: "outputTokens",
      header: t("dashboard.columns.output_tokens"),
      className: "min-w-[160px]",
    },
  ]
}

function CostBar({ value }: { value: number }) {
  const percent = Math.max(0, Math.min(100, value))

  return (
    <span className="relative h-1.5 w-16 shrink-0 overflow-hidden rounded-full bg-muted">
      <span
        className="absolute inset-y-0 left-0 rounded-full bg-foreground/70"
        style={{ width: `${percent}%` }}
      />
    </span>
  )
}

function ToolbarSelect({
  ariaLabel,
  options,
  value,
  onValueChange,
}: {
  ariaLabel: string
  options: EventTableOption[]
  value?: string
  onValueChange?: (value: string) => void
}) {
  if (options.length === 0) {
    return null
  }

  return (
    <Select value={value ?? options[0]?.value} onValueChange={onValueChange}>
      <SelectTrigger size="sm" aria-label={ariaLabel}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          {options.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  )
}

export function EventTable({
  rows,
  columns,
  title,
  total,
  page,
  pageOptions = [],
  pageSize,
  pageSizeOptions = [],
  eventType,
  eventTypeOptions = [],
  eventColorMap,
  showAll = false,
  showAllLabel,
  toolbarActions,
  rowActions,
  maxHeight = 360,
  emptyText,
  loading = false,
  error,
  className,
  onPageChange,
  onPageSizeChange,
  onEventTypeChange,
  onShowAllToggle,
}: EventTableProps) {
  const { t } = useTranslation()
  const resolvedColumns = columns ?? buildDefaultColumns(t, eventColorMap)
  const bodyState = loading || Boolean(error) || rows.length === 0
  const bodyStateContent = loading
    ? t("common.loading")
    : (error ?? emptyText ?? t("dashboard.emptyEvents"))

  return (
    <section
      className={cn(
        "overflow-hidden rounded-xl border bg-card text-card-foreground",
        className
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
        <h2 className="text-base font-medium text-foreground">
          {title ?? t("metrics.eventStream")}
        </h2>
        <div className="flex flex-wrap items-center justify-end gap-2">
          {toolbarActions}
          {typeof total === "number" ? (
            <span className="text-sm text-muted-foreground">
              {t("dashboard.rowCount", { count: total })}
            </span>
          ) : null}
          {!showAll && (
            <>
              <ToolbarSelect
                ariaLabel={t("dashboard.selectPage")}
                options={pageOptions}
                value={page}
                onValueChange={onPageChange}
              />
              <ToolbarSelect
                ariaLabel={t("dashboard.selectPageSize")}
                options={pageSizeOptions}
                value={pageSize}
                onValueChange={onPageSizeChange}
              />
            </>
          )}
          <ToolbarSelect
            ariaLabel={t("dashboard.selectEventType")}
            options={eventTypeOptions}
            value={eventType}
            onValueChange={onEventTypeChange}
          />
          {onShowAllToggle && (
            <button
              type="button"
              onClick={onShowAllToggle}
              className={cn(
                "rounded-md border px-3 py-1 text-sm transition-colors",
                showAll
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-input bg-background text-foreground hover:bg-accent"
              )}
            >
              {showAllLabel ?? t("common.all")}
            </button>
          )}
        </div>
      </div>

      <div className="border-t" style={{ height: maxHeight }}>
        {bodyState ? (
          <div className="flex h-full items-center justify-center px-4 text-center text-sm text-muted-foreground">
            {bodyStateContent}
          </div>
        ) : (
          <div className="h-full overflow-auto">
            <table className="w-full min-w-max caption-bottom text-sm">
              <TableHeader className="sticky top-0 z-10 bg-muted">
                <TableRow className="hover:bg-transparent">
                  {resolvedColumns.map((column) => (
                    <TableHead
                      key={String(column.key)}
                      className={cn(
                        "h-11 text-xs font-semibold text-muted-foreground",
                        column.className
                      )}
                    >
                      {column.header}
                    </TableHead>
                  ))}
                  {rowActions ? (
                    <TableHead className="h-11 text-xs font-semibold whitespace-nowrap text-muted-foreground">
                      {t("dashboard.rowActions")}
                    </TableHead>
                  ) : null}
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => (
                  <TableRow key={row.id}>
                    {resolvedColumns.map((column) => (
                      <TableCell
                        key={`${row.id}-${String(column.key)}`}
                        className={cn(
                          "h-11 py-2 text-sm font-normal",
                          column.className
                        )}
                      >
                        {column.cell ? column.cell(row) : row[column.key]}
                      </TableCell>
                    ))}
                    {rowActions ? (
                      <TableCell className="h-11 py-2 text-sm whitespace-nowrap">
                        {rowActions(row)}
                      </TableCell>
                    ) : null}
                  </TableRow>
                ))}
              </TableBody>
            </table>
          </div>
        )}
      </div>
    </section>
  )
}
