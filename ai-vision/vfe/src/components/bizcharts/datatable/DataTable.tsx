import { writeClipboardText } from "@/lib/clipboard"
import { useTranslation } from "react-i18next"
import { useEffect, useRef, useState, type ReactNode } from "react"
import { Link, useLocation } from "react-router-dom"
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  ChevronLeft,
  ChevronRight,
  FileText,
  Copy,
  Check,
  Search,
  X,
} from "lucide-react"

import { cn } from "@/lib/utils"
import {
  buildAgentScopedUrl,
  getAgentProductFromDashboardPath,
  getAgentProductFromSearch,
  getAgentUuidFromDashboardPath,
  getAgentUuidFromSearch,
} from "@/agents/lib/agentDashboardRoute"
import { useSessionDrawer } from "@/contexts/SessionDrawerContext"
import type { TraceQueryTimeRange } from "@/agents/types/langfuse"
import { Input } from "@/components/ui/input"
import {
  Dialog,
  DialogClose,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import { DialogContent } from "@/components/dialogs/DialogContent"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import type {
  TableColumn,
  TableColumnDataType,
} from "@/lib/schemas/observability"
import { getCellRenderer } from "./cells"

function escapeRegex(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}

function HighlightText({ text, keyword }: { text: string; keyword: string }) {
  if (!keyword) return <>{text}</>
  const parts = text.split(new RegExp(`(${escapeRegex(keyword)})`, "gi"))
  return (
    <>
      {parts.map((part, i) =>
        part.toLowerCase() === keyword.toLowerCase() ? (
          <mark
            key={i}
            className="rounded-sm bg-yellow-200 dark:bg-yellow-800/50"
          >
            {part}
          </mark>
        ) : (
          part
        )
      )}
    </>
  )
}

export interface DataTableProps {
  columns: TableColumn[]
  rows: Record<string, unknown>[]
  title?: ReactNode
  total?: number
  page?: string
  pageOptions?: { label: string; value: string }[]
  pageSize?: string
  pageSizeOptions?: { label: string; value: string }[]
  showAll?: boolean
  showAllLabel?: ReactNode
  toolbarActions?: ReactNode
  rowActions?: (row: Record<string, unknown>) => ReactNode
  rowKey?: (row: Record<string, unknown>) => string
  sortKey?: string | null
  sortDirection?: "asc" | "desc"
  sortableKeys?: Set<string>
  maxHeight?: number
  emptyText?: ReactNode
  loading?: boolean
  error?: ReactNode
  className?: string
  searchText?: string
  onSearchChange?: (text: string) => void
  onPageChange?: (value: string) => void
  onPageSizeChange?: (value: string) => void
  onShowAllToggle?: () => void
  onSort?: (key: string) => void
}

const columnWidthMap: Record<string, string> = {
  session_id: "whitespace-nowrap",
  SessionId: "whitespace-nowrap",
  prompt: "min-w-[120px] max-w-[400px] truncate",
  time: "whitespace-nowrap",
  timestamp: "whitespace-nowrap",
  start_time: "whitespace-nowrap",
  end_time: "whitespace-nowrap",
  os_version: "whitespace-nowrap",
  os_type: "whitespace-nowrap",
  host_arch: "whitespace-nowrap",
  model: "whitespace-nowrap",
  cost_ms: "whitespace-nowrap",
  size: "whitespace-nowrap",
  tool: "whitespace-nowrap",
  tool_call_id: "whitespace-nowrap",
  app_version: "whitespace-nowrap",
  terminal_type: "whitespace-nowrap",
  input_tokens: "whitespace-nowrap",
  output_tokens: "whitespace-nowrap",
  cache_read_tokens: "whitespace-nowrap",
}

function getColumnWidth(key: string, dataType: TableColumnDataType): string {
  if (columnWidthMap[key]) return columnWidthMap[key]
  if (dataType === "datetime") return "min-w-[132px]"
  if (dataType === "duration") return "min-w-[90px]"
  return ""
}

function parseRowTimestampMs(value: unknown): number | null {
  if (value === null || value === undefined) return null
  if (typeof value === "number" && Number.isFinite(value)) return value
  const str = String(value).trim()
  if (!str) return null
  // 13+ digit numeric string → epoch ms; otherwise parse as a date string.
  if (/^\d{13,}$/.test(str)) {
    const n = Number(str)
    return Number.isFinite(n) ? n : null
  }
  const ms = new Date(str).getTime()
  return Number.isNaN(ms) ? null : ms
}

const SESSION_WINDOW_PADDING_MS = 10 * 60 * 1000

// Derive a tight query window from the session row's own start/end, padded by
// 10 minutes on each side. Returns undefined when the row lacks usable
// timestamps so callers can fall back to the dashboard's global window.
function deriveSessionTimeRange(
  row: Record<string, unknown>
): TraceQueryTimeRange | undefined {
  const startMs = parseRowTimestampMs(row.start_time ?? row.StartTime)
  const endMs = parseRowTimestampMs(row.end_time ?? row.EndTime)
  if (startMs === null && endMs === null) return undefined
  const lo = startMs ?? endMs!
  const hi = endMs ?? startMs!
  return {
    from: new Date(lo - SESSION_WINDOW_PADDING_MS).toISOString(),
    to: new Date(hi + SESSION_WINDOW_PADDING_MS).toISOString(),
  }
}

function SessionIdCell({
  value,
  row,
}: {
  value: unknown
  row: Record<string, unknown>
}) {
  const { onSessionClick } = useSessionDrawer()
  const location = useLocation()
  const id = String(value ?? "-")
  const className =
    "cursor-pointer font-mono text-sm whitespace-nowrap text-blue-600 hover:underline dark:text-blue-400"

  if (onSessionClick) {
    return (
      <button
        type="button"
        onClick={() => onSessionClick(id, deriveSessionTimeRange(row))}
        className={className}
        title={id}
      >
        {id}
      </button>
    )
  }

  const agentProduct =
    getAgentProductFromDashboardPath(location.pathname, location.search) ??
    getAgentProductFromSearch(location.search)
  const agentUuid =
    getAgentUuidFromDashboardPath(location.pathname, location.search) ??
    getAgentUuidFromSearch(location.search)
  const traceSearch = `${location.search}${
    location.search ? "&" : "?"
  }view=session&sessionId=${encodeURIComponent(id)}`
  const traceUrl = agentProduct
    ? buildAgentScopedUrl("/trace", agentProduct, traceSearch, agentUuid)
    : `/trace?view=session&sessionId=${encodeURIComponent(id)}`

  return (
    <Link to={traceUrl} className={className} title={id}>
      {id}
    </Link>
  )
}

function TruncatedCell({
  value,
  maxLen = 16,
}: {
  value: unknown
  maxLen?: number
}) {
  const text = String(value ?? "-")
  const truncated = text.length > maxLen ? `${text.slice(0, maxLen)}...` : text
  return <span title={text}>{truncated}</span>
}

function PromptCell({ value }: { value: unknown }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [copyState, setCopyState] = useState<"copied" | "failed" | null>(null)
  const text =
    typeof value === "string"
      ? value
      : value === undefined
        ? ""
        : JSON.stringify(value, null, 2)
  const copy = async () => {
    try {
      await writeClipboardText(text)
      setCopyState("copied")
    } catch {
      setCopyState("failed")
    }
  }
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (!next) setCopyState(null)
      }}
    >
      <div className="flex min-w-0 items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-sm">{text}</span>
        <button
          type="button"
          aria-label={t("dashboard.viewFullText")}
          onClick={(event) => {
            event.stopPropagation()
            setOpen(true)
          }}
          className="inline-flex shrink-0 items-center gap-1 rounded px-1 py-0.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-2"
        >
          <FileText className="size-3.5" />
          {t("dashboard.viewFullText")}
        </button>
      </div>
      <DialogContent
        size="detail"
        showCloseButton={false}
        className="flex flex-col gap-3 rounded-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <DialogHeader className="flex-row items-start justify-between gap-3">
          <div>
            <DialogTitle>{t("dashboard.fullTextTitle")}</DialogTitle>
            <DialogDescription>
              {t("dashboard.fullTextDescription")}
            </DialogDescription>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              aria-label={t("dashboard.copyFullText")}
              onClick={copy}
              className="inline-flex items-center gap-1 rounded border px-2 py-1 text-xs hover:bg-muted focus-visible:outline-2"
            >
              {copyState === "copied" ? (
                <Check className="size-3.5" />
              ) : (
                <Copy className="size-3.5" />
              )}
              {t(
                copyState === "copied"
                  ? "common.copied"
                  : "dashboard.copyFullText"
              )}
            </button>
            <DialogClose asChild>
              <button
                type="button"
                aria-label={t("labs.common.close")}
                className="rounded p-1 hover:bg-muted focus-visible:outline-2"
              >
                <X className="size-4" />
              </button>
            </DialogClose>
          </div>
        </DialogHeader>
        {copyState === "failed" ? (
          <p role="alert" className="text-xs text-destructive">
            {t("dashboard.copyFullTextFailed")}
          </p>
        ) : null}
        <pre
          tabIndex={0}
          className="min-h-0 flex-1 overflow-auto rounded-lg border bg-muted/30 p-3 font-mono text-xs leading-5 break-words whitespace-pre-wrap focus-visible:outline-2"
        >
          {text}
        </pre>
      </DialogContent>
    </Dialog>
  )
}

function renderCell(
  column: TableColumn,
  value: unknown,
  row: Record<string, unknown>,
  searchText?: string
): ReactNode {
  const key = column.key
  if (key === "session_id" || key === "SessionId") {
    return <SessionIdCell value={value} row={row} />
  }
  if (key === "user_id") {
    return <TruncatedCell value={value} />
  }
  if (key === "prompt") {
    return <PromptCell value={value} />
  }
  if (searchText && value !== null && value !== undefined) {
    const text = String(value)
    if (text.toLowerCase().includes(searchText.toLowerCase())) {
      return <HighlightText text={text} keyword={searchText} />
    }
  }
  return getCellRenderer(column.data_type)(value, row)
}

function ToolbarSelect({
  ariaLabel,
  options,
  value,
  onValueChange,
}: {
  ariaLabel: string
  options: { label: string; value: string }[]
  value?: string
  onValueChange?: (value: string) => void
}) {
  if (options.length === 0) return null
  return (
    <Select value={value ?? options[0]?.value} onValueChange={onValueChange}>
      <SelectTrigger size="sm" aria-label={ariaLabel}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          {options.map((opt) => (
            <SelectItem key={opt.value} value={opt.value}>
              {opt.label}
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  )
}

function PageJump({
  page,
  totalPages,
  onPageChange,
}: {
  page: number
  totalPages: number
  onPageChange: (value: string) => void
}) {
  const { t } = useTranslation()
  const inputRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (inputRef.current) inputRef.current.value = String(page)
  }, [page, totalPages])
  const commit = (input: HTMLInputElement) => {
    const draft = input.value.trim()
    const parsed = /^\d+$/.test(draft) ? Number(draft) : NaN
    const next = Number.isSafeInteger(parsed)
      ? Math.min(totalPages, Math.max(1, parsed))
      : page
    input.value = String(next)
    if (next !== page) onPageChange(String(next))
  }
  const buttonClass =
    "flex size-7 items-center justify-center rounded-full hover:bg-accent disabled:pointer-events-none disabled:opacity-40"
  return (
    <div
      role="group"
      aria-label={t("dashboard.selectPage")}
      className="flex h-8 items-center gap-1 rounded-3xl bg-input/50 px-1 text-sm"
    >
      <button
        type="button"
        className={buttonClass}
        aria-label={t("common.prev")}
        disabled={page <= 1}
        onClick={() => onPageChange(String(page - 1))}
      >
        <ChevronLeft className="size-4" />
      </button>
      <Input
        ref={inputRef}
        type="text"
        inputMode="numeric"
        pattern="[0-9]*"
        aria-label={t("dashboard.selectPage")}
        defaultValue={String(page)}
        className="h-7 w-14 rounded-md border-0 bg-transparent px-1 text-center text-sm shadow-none"
        onFocus={(event) => event.currentTarget.select()}
        onBlur={(event) => commit(event.currentTarget)}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault()
            commit(event.currentTarget)
          } else if (event.key === "Escape") {
            event.currentTarget.value = String(page)
          }
        }}
      />
      <span className="pr-1 text-muted-foreground">/ {totalPages}</span>
      <button
        type="button"
        className={buttonClass}
        aria-label={t("common.next")}
        disabled={page >= totalPages}
        onClick={() => onPageChange(String(page + 1))}
      >
        <ChevronRight className="size-4" />
      </button>
    </div>
  )
}

export function DataTable({
  columns,
  rows,
  title,
  total,
  page,
  pageOptions = [],
  pageSize,
  pageSizeOptions = [],
  showAll = false,
  showAllLabel,
  toolbarActions,
  rowActions,
  rowKey,
  sortKey,
  sortDirection,
  sortableKeys,
  maxHeight = 440,
  emptyText,
  loading = false,
  error,
  className,
  searchText,
  onSearchChange,
  onPageChange,
  onPageSizeChange,
  onShowAllToggle,
  onSort,
}: DataTableProps) {
  const { t } = useTranslation()
  // Large Radix Select collections mount every option, even while closed.
  // Only contiguous numeric pages can use direct jumps; preserve other choices.
  const usePageJump =
    pageOptions.length > 50 &&
    onPageChange &&
    pageOptions.every((option, index) => option.value === String(index + 1))
  return (
    <section
      className={cn(
        "overflow-hidden rounded-xl border bg-card text-card-foreground",
        className
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
        {title !== undefined && title !== null ? (
          <h2 className="text-base font-medium text-foreground">{title}</h2>
        ) : null}
        <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
          {onSearchChange && (
            <div className="flex items-center gap-1 rounded-md border border-input px-2">
              <Search className="size-3.5 shrink-0 text-muted-foreground" />
              <Input
                placeholder={t("common.search")}
                value={searchText ?? ""}
                onChange={(e) => onSearchChange(e.target.value)}
                className="h-7 w-28 border-none bg-transparent px-1 text-sm shadow-none focus-visible:ring-0"
              />
              {searchText && (
                <button
                  type="button"
                  onClick={() => onSearchChange("")}
                  className="shrink-0 rounded p-0.5 text-muted-foreground hover:text-foreground"
                >
                  <X className="size-3" />
                </button>
              )}
            </div>
          )}
          {toolbarActions}
          {typeof total === "number" ? (
            <span className="text-sm text-muted-foreground">
              {t("dashboard.rowCount", { count: total })}
            </span>
          ) : null}
          {!showAll && (
            <>
              {usePageJump ? (
                <PageJump
                  page={Number(page ?? "1")}
                  totalPages={pageOptions.length}
                  onPageChange={onPageChange!}
                />
              ) : (
                <ToolbarSelect
                  ariaLabel={t("dashboard.selectPage")}
                  options={pageOptions}
                  value={page}
                  onValueChange={onPageChange}
                />
              )}
              <ToolbarSelect
                ariaLabel={t("dashboard.selectPageSize")}
                options={pageSizeOptions}
                value={pageSize}
                onValueChange={onPageSizeChange}
              />
            </>
          )}
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

      <div className="overflow-auto border-t" style={{ maxHeight }}>
        <Table>
          <TableHeader className="sticky top-0 z-10 bg-muted">
            <TableRow className="hover:bg-transparent">
              {columns.map((col) => {
                const isSortable = onSort && sortableKeys?.has(col.key)
                const isActive = sortKey === col.key
                return (
                  <TableHead
                    key={col.key}
                    className={cn(
                      "h-11 text-xs font-semibold text-muted-foreground",
                      getColumnWidth(col.key, col.data_type),
                      isSortable && "cursor-pointer select-none"
                    )}
                    aria-sort={
                      isSortable
                        ? isActive
                          ? sortDirection === "asc"
                            ? "ascending"
                            : "descending"
                          : "none"
                        : undefined
                    }
                  >
                    {isSortable ? (
                      <button
                        type="button"
                        onClick={() => onSort(col.key)}
                        className="inline-flex w-full items-center gap-1 rounded text-left focus-visible:outline-2"
                        aria-label={t("dashboard.sortColumn", {
                          column: col.label,
                        })}
                      >
                        {col.label}
                        {isSortable &&
                          (isActive ? (
                            sortDirection === "asc" ? (
                              <ArrowUp className="size-3.5" />
                            ) : (
                              <ArrowDown className="size-3.5" />
                            )
                          ) : (
                            <ArrowUpDown className="size-3.5 opacity-30" />
                          ))}
                      </button>
                    ) : (
                      <span>{col.label}</span>
                    )}
                  </TableHead>
                )
              })}
              {rowActions ? (
                <TableHead className="h-11 text-xs font-semibold whitespace-nowrap text-muted-foreground">
                  {t("dashboard.rowActions")}
                </TableHead>
              ) : null}
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading || error ? (
              <TableRow>
                <TableCell
                  colSpan={columns.length + (rowActions ? 1 : 0)}
                  className="h-24 text-center text-muted-foreground"
                >
                  {loading ? t("common.loading") : String(error)}
                </TableCell>
              </TableRow>
            ) : rows.length > 0 ? (
              rows.map((row, i) => (
                <TableRow
                  key={
                    rowKey?.(row) ??
                    `${String(row.session_id ?? row.id ?? "row")}-${i}`
                  }
                >
                  {columns.map((col) => (
                    <TableCell
                      key={col.key}
                      className={cn(
                        "h-11 py-2 text-sm font-normal",
                        getColumnWidth(col.key, col.data_type)
                      )}
                    >
                      {renderCell(col, row[col.key], row, searchText)}
                    </TableCell>
                  ))}
                  {rowActions ? (
                    <TableCell className="h-11 py-2 text-sm whitespace-nowrap">
                      {rowActions(row)}
                    </TableCell>
                  ) : null}
                </TableRow>
              ))
            ) : (
              <TableRow>
                <TableCell
                  colSpan={columns.length + (rowActions ? 1 : 0)}
                  className="h-24 text-center text-muted-foreground"
                >
                  {emptyText ?? t("common.noData")}
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </section>
  )
}
