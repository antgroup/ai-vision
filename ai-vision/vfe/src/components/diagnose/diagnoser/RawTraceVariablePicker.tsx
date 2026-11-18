import { writeClipboardText } from "@/lib/clipboard"
import { useTranslation } from "react-i18next"
import type { TFunction } from "i18next"
import { useEffect, useMemo, useRef, useState } from "react"
import {
  Check,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Copy,
  FileJson,
  ListTree,
  Loader2,
  Play,
  Search,
  X,
} from "lucide-react"

import {
  Dialog,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { DialogContent } from "@/components/dialogs/DialogContent"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group"
import { Textarea } from "@/components/ui/textarea"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Popover,
  PopoverAnchor,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import { cn } from "@/lib/utils"

type JsonRecord = Record<string, unknown>
type JsonPath = Array<string | number>

interface RawTraceVariablePickerProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  sample: unknown
  samples?: Array<{ trace_id: string; test_input: Record<string, unknown> }>
  onActiveTraceChange?: (traceId: string) => void
  onRequestTrace?: (traceId: string) => void
  traceLoading?: boolean
  traceLoadError?: string | null
  expressionMode?: "template" | "python"
}

interface SpanRow {
  span: JsonRecord
  index: number
  depth: number
  startMs: number | null
  durationMs: number | null
}

function asRecord(value: unknown): JsonRecord | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonRecord)
    : null
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function normalizeExpressionValue(value: unknown): unknown {
  if (
    typeof value !== "string" ||
    (!value.startsWith("[") && !value.startsWith("{"))
  ) {
    return value
  }
  try {
    return JSON.parse(value)
  } catch {
    return value
  }
}

function expressionSpans(spans: JsonRecord[]): JsonRecord[] {
  return spans.map((span) => {
    const normalized: JsonRecord = { ...span }
    const attributes = asRecord(span.attributes)
    if (attributes) {
      normalized.attributes = Object.fromEntries(
        Object.entries(attributes).map(([key, value]) => [
          key,
          normalizeExpressionValue(value),
        ])
      )
    }
    for (const [key, value] of Object.entries(span)) {
      if (key.startsWith("attributes."))
        normalized[key] = normalizeExpressionValue(value)
    }
    return normalized
  })
}
function pathKey(path: JsonPath): string {
  return path.map(String).join(".")
}

function pathSuffix(path: JsonPath): string {
  return path
    .map((item) => (typeof item === "number" ? `[${item}]` : `.${item}`))
    .join("")
}

function numericTime(value: unknown): number | null {
  const numeric =
    typeof value === "number"
      ? value
      : typeof value === "string" && value.trim() !== ""
        ? Number(value)
        : Number.NaN
  if (Number.isFinite(numeric)) {
    const magnitude = Math.abs(numeric)
    if (magnitude >= 1e17) return numeric / 1_000_000
    if (magnitude >= 1e14) return numeric / 1_000
    if (magnitude >= 1e11) return numeric
    return numeric * 1_000
  }
  if (typeof value !== "string") return null
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : null
}

function spanStartMs(span: JsonRecord): number | null {
  return numericTime(
    span.start_time_unix_nano ??
      span.start_time ??
      span.startTime ??
      span.period
  )
}

function spanDuration(span: JsonRecord): number | null {
  const start = spanStartMs(span)
  const end = numericTime(
    span.end_time_unix_nano ?? span.end_time ?? span.endTime
  )
  if (start !== null && end !== null && end >= start) return end - start

  const duration =
    span.duration_ms ?? span.durationMs ?? span.latency_ms ?? span.latencyMs
  if (
    typeof duration === "number" &&
    Number.isFinite(duration) &&
    duration >= 0
  )
    return duration
  if (
    typeof duration === "string" &&
    duration.trim() !== "" &&
    Number.isFinite(Number(duration)) &&
    Number(duration) >= 0
  ) {
    return Number(duration)
  }
  return null
}

function spanKind(span: JsonRecord): string {
  const attributes = asRecord(span.attributes)
  return String(
    span["attributes.gen_ai.span.kind"] ??
      attributes?.["gen_ai.span.kind"] ??
      span.kind ??
      "SPAN"
  )
}

function spanTone(kind: string): string {
  const normalized = kind.toUpperCase()
  if (normalized.includes("AGENT") || normalized.includes("CHAT"))
    return "var(--color-success)"
  if (normalized.includes("LLM") || normalized.includes("GENERATION"))
    return "var(--color-auxiliary)"
  if (normalized.includes("TOOL")) return "var(--color-warning)"
  if (normalized.includes("RETRIEV")) return "var(--color-warning)"
  return "var(--color-text-secondary)"
}

function formatTimelineMs(value: number): string {
  return value >= 10_000
    ? `${Math.round(value / 1_000)}s`
    : `${(value / 1_000).toFixed(1)}s`
}

function spanRows(spans: JsonRecord[]): SpanRow[] {
  const byParent = new Map<string, number[]>()
  const ids = new Set<string>()
  const starts = spans.map(spanStartMs)
  spans.forEach((span, index) => {
    ids.add(String(span.span_id ?? span.spanId ?? span.id ?? index))
  })
  const roots: number[] = []
  spans.forEach((span, index) => {
    const parent = String(
      span.parent_span_id ?? span.parentSpanId ?? span.parent_id ?? ""
    )
    if (!parent || !ids.has(parent)) {
      roots.push(index)
      return
    }
    byParent.set(parent, [...(byParent.get(parent) ?? []), index])
  })

  const sortByStart = (left: number, right: number) => {
    const leftStart = starts[left]
    const rightStart = starts[right]
    if (leftStart === null && rightStart === null) return left - right
    if (leftStart === null) return 1
    if (rightStart === null) return -1
    return leftStart === rightStart ? left - right : leftStart - rightStart
  }

  const rows: SpanRow[] = []
  const visited = new Set<number>()
  const visit = (index: number, depth: number) => {
    if (visited.has(index)) return
    visited.add(index)
    const span = spans[index]
    if (!span) return
    rows.push({
      span,
      index,
      depth,
      startMs: spanStartMs(span),
      durationMs: spanDuration(span),
    })
    const id = String(span.span_id ?? span.spanId ?? span.id ?? index)
    for (const childIndex of (byParent.get(id) ?? []).sort(sortByStart))
      visit(childIndex, depth + 1)
  }
  for (const index of roots.sort(sortByStart)) visit(index, 0)

  // Cyclic or malformed parent links are still inspectable as root rows.
  for (const index of spans.map((_, index) => index).sort(sortByStart)) {
    if (!visited.has(index)) visit(index, 0)
  }
  return rows
}

interface ExpressionSegment {
  key?: string
  index?: number
  wildcard?: boolean
  slice?: [number | null, number | null]
  range?: [number, number]
  filter?: [string, string]
}

type SpanMatchMode = "all" | "first" | "last"

function parseExpression(expression: string): ExpressionSegment[] {
  return (expression.match(/[^.[\]]+|\[[^\]]*\]/g) ?? []).map((token) => {
    if (!token.startsWith("[")) return { key: token }
    const content = token.slice(1, -1).trim()
    if (content === "*") return { wildcard: true }
    if (content.includes(":")) {
      const [left, right] = content.split(":", 2).map((value) => value.trim())
      const isSlice =
        (left === "" || /^-?\d+$/.test(left)) &&
        (right === "" || /^-?\d+$/.test(right))
      if (isSlice) {
        return {
          slice: [
            left === "" ? null : Number(left),
            right === "" ? null : Number(right),
          ],
        }
      }
      return { filter: [left, right] }
    }
    if (content.includes(",")) {
      const [start, end] = content
        .split(",", 2)
        .map((value) => Number(value.trim()))
      return { range: [start, end] }
    }
    return { index: Number(content) }
  })
}

function readNested(value: unknown, dottedKey: string): unknown {
  const source = asRecord(value)
  if (!source) return undefined
  if (dottedKey in source) return source[dottedKey]

  const dottedPrefix = dottedKey + "."
  const inferred: JsonRecord = {}
  for (const [key, child] of Object.entries(source)) {
    if (key.startsWith(dottedPrefix)) {
      assignNested(inferred, key.slice(dottedPrefix.length).split("."), child)
    }
  }
  if (Object.keys(inferred).length > 0) return inferred

  const parts = dottedKey.split(".")
  for (let index = 1; index < parts.length; index += 1) {
    const prefix = parts.slice(0, index).join(".")
    if (prefix in source)
      return readNested(source[prefix], parts.slice(index).join("."))
  }
  return undefined
}

function flattenResolved(values: unknown[]): unknown[] {
  return values.flatMap((value) => {
    if (Array.isArray(value)) return value
    return value === undefined || value === null ? [] : [value]
  })
}

function isArrayOperation(segment: ExpressionSegment): boolean {
  return Boolean(
    segment.index !== undefined ||
    segment.slice ||
    segment.range ||
    segment.wildcard
  )
}

function resolveExpression(
  value: unknown,
  segments: ExpressionSegment[]
): unknown {
  if (segments.length === 0) return value
  if (value === undefined || value === null) return undefined

  const [segment, ...rest] = segments
  if (segment.filter) {
    const [key, expected] = segment.filter
    const matches = asArray(value).filter(
      (item) => String(readNested(item, key)) === expected
    )
    if (rest[0] && isArrayOperation(rest[0]))
      return resolveExpression(matches, rest)
    return flattenResolved(matches.map((item) => resolveExpression(item, rest)))
  }
  if (segment.wildcard) {
    return flattenResolved(
      asArray(value).map((item) => resolveExpression(item, rest))
    )
  }
  if (segment.range) {
    const [start, end] = segment.range
    return flattenResolved(
      asArray(value)
        .slice(start, end + 1)
        .map((item) => resolveExpression(item, rest))
    )
  }
  if (segment.slice) {
    const [start, end] = segment.slice
    return flattenResolved(
      asArray(value)
        .slice(start ?? undefined, end ?? undefined)
        .map((item) => resolveExpression(item, rest))
    )
  }
  if (segment.index !== undefined) {
    const items = asArray(value)
    const index =
      segment.index < 0 ? items.length + segment.index : segment.index
    return index >= 0 && index < items.length
      ? resolveExpression(items[index], rest)
      : undefined
  }
  if (!segment.key) return undefined

  if (Array.isArray(value)) {
    return flattenResolved(
      value.map((item) => resolveExpression(item, segments))
    )
  }
  const direct = readNested(value, segment.key)
  if (direct !== undefined) return resolveExpression(direct, rest)
  if (rest.length > 0) {
    let combined = segment.key
    for (let index = 0; index < rest.length; index += 1) {
      if (!rest[index].key) break
      combined = `${combined}.${rest[index].key}`
      const combinedValue = readNested(value, combined)
      if (combinedValue !== undefined)
        return resolveExpression(combinedValue, rest.slice(index + 1))
    }
  }
  return undefined
}

function runTraceExpression(expression: string, spans: JsonRecord[]): unknown {
  const trimmed = expression.trim()
  if (!trimmed) return undefined
  if (trimmed === "*") return { spans }
  return resolveExpression({ spans }, parseExpression(trimmed))
}

function buildSpanExpression(
  span: JsonRecord,
  index: number,
  path: JsonPath,
  matchMode: SpanMatchMode
): string {
  const name = typeof span.name === "string" ? span.name.trim() : ""
  if (!name) return `spans[${index}]${pathSuffix(path)}`
  const matchIndex =
    matchMode === "first" ? "[0]" : matchMode === "last" ? "[-1]" : ""
  return `spans[name:${name}]${matchIndex}${pathSuffix(path)}`
}

function pythonPathExpression(base: string, path: JsonPath): string {
  return path.reduce<string>((expression, segment, index) => {
    if (typeof segment === "number") return `(${expression})[${segment}]`
    const hasNext = index < path.length - 1
    const fallback = hasNext
      ? typeof path[index + 1] === "number"
        ? ", []"
        : ", {}"
      : ""
    return `(${expression}).get(${JSON.stringify(segment)}${fallback})`
  }, base)
}

function buildPythonSpanExpression(
  span: JsonRecord,
  index: number,
  path: JsonPath,
  matchMode: SpanMatchMode
): string {
  const valueExpression = pythonPathExpression("span", path)
  const spanName = typeof span.name === "string" ? span.name.trim() : ""
  const source = '(trace.get("spans") or [])'
  const matches = spanName
    ? `[${valueExpression} for span in ${source} if span.get("name") == ${JSON.stringify(spanName)}]`
    : `[${valueExpression} for span_index, span in enumerate(${source}) if span_index == ${index}]`

  if (spanName && matchMode === "all") return matches
  return `(${matches} or [None])[${matchMode === "last" ? -1 : 0}]`
}

const DISPLAY_GROUPS = new Set(["attributes", "resource", "scope"])

function assignNested(target: JsonRecord, path: string[], value: unknown) {
  const [head, ...rest] = path
  if (!head) return
  if (rest.length === 0) {
    target[head] = value
    return
  }
  const child = asRecord(target[head]) ?? {}
  target[head] = child
  assignNested(child, rest, value)
}

function displaySpan(span: JsonRecord): JsonRecord {
  const display: JsonRecord = {}
  for (const [key, value] of Object.entries(span)) {
    const [group, ...rest] = key.split(".")
    if (group && DISPLAY_GROUPS.has(group) && rest.length > 0) {
      const groupValue = asRecord(display[group]) ?? {}
      display[group] = groupValue
      assignNested(groupValue, rest, value)
      continue
    }
    display[key] = value
  }
  return display
}

function sourcePathForDisplay(span: JsonRecord, path: JsonPath): JsonPath {
  const [first, ...rest] = path
  if (typeof first !== "string" || !DISPLAY_GROUPS.has(first)) return path
  if (rest.length === 0) return [first]

  const dotted = [first, ...rest].join(".")
  const dottedPrefix = dotted + "."
  if (
    dotted in span ||
    Object.keys(span).some((key) => key.startsWith(dottedPrefix))
  ) {
    return [dotted]
  }
  return first in span ? path : []
}

function displayPathForSource(path: JsonPath): JsonPath {
  const [first] = path
  if (typeof first !== "string") return path
  const [group, ...rest] = first.split(".")
  return group && DISPLAY_GROUPS.has(group) && rest.length > 0
    ? [group, ...rest]
    : path
}

function rawJsonPrimitive(value: unknown): { text: string; color: string } {
  if (value === null) {
    return { text: "null", color: "var(--color-text-description)" }
  }
  if (typeof value === "string") {
    return { text: JSON.stringify(value), color: "var(--color-success)" }
  }
  if (typeof value === "number") {
    return { text: String(value), color: "var(--color-auxiliary)" }
  }
  if (typeof value === "boolean") {
    return { text: String(value), color: "var(--color-warning)" }
  }
  return { text: String(value), color: "var(--color-text-description)" }
}

function rawSpanItemLabel(path: JsonPath, value: unknown): string | null {
  if (path.length !== 2 || path[0] !== "spans" || typeof path[1] !== "number") {
    return null
  }
  const name = asRecord(value)?.name
  return typeof name === "string" && name.trim() ? name : null
}

function RawJsonNode({
  name,
  value,
  path,
  depth,
  isLast,
  expanded,
  selectedPath,
  onToggle,
  onSelect,
}: {
  name?: string
  value: unknown
  path: JsonPath
  depth: number
  isLast: boolean
  expanded: Set<string>
  selectedPath: string
  onToggle: (path: JsonPath) => void
  onSelect: (path: JsonPath, value: unknown) => void
}) {
  const { t } = useTranslation()
  const key = pathKey(path)
  const isArray = Array.isArray(value)
  const isObject = value !== null && typeof value === "object" && !isArray
  const expandable = isArray || isObject
  const entries = isArray
    ? value.map((item, index) => [String(index), item] as const)
    : isObject
      ? Object.entries(asRecord(value) ?? {})
      : []
  const open = expanded.has(key)
  const selected = key !== "" && selectedPath === key
  const opening = isArray ? "[" : "{"
  const closing = isArray ? "]" : "}"
  const spanItemLabel = rawSpanItemLabel(path, value)

  if (!expandable) {
    const primitive = rawJsonPrimitive(value)
    return (
      <button
        type="button"
        onClick={() => onSelect(path, value)}
        className={cn(
          "block min-h-6 w-full rounded-sm pr-3 text-left font-mono text-xs leading-5 transition-colors hover:bg-muted/40",
          selected && "bg-muted/60"
        )}
        style={{ paddingLeft: `${depth * 16 + 24}px` }}
      >
        {name !== undefined ? (
          <>
            <span style={{ color: "var(--color-warning)" }}>
              {JSON.stringify(name)}
            </span>
            <span className="mr-1 text-muted-foreground">:</span>
          </>
        ) : null}
        <span
          className="break-all whitespace-pre-wrap"
          style={{ color: primitive.color }}
        >
          {primitive.text}
        </span>
        {!isLast ? <span className="text-muted-foreground">,</span> : null}
      </button>
    )
  }

  return (
    <div>
      <div
        className={cn(
          "flex min-h-6 w-full items-start rounded-sm pr-3 font-mono text-xs leading-5 transition-colors hover:bg-muted/40",
          selected && "bg-muted/60"
        )}
        style={{ paddingLeft: `${depth * 16 + 4}px` }}
      >
        <button
          type="button"
          aria-label={t(
            open ? "rawTracePicker.collapseJson" : "rawTracePicker.expandJson"
          )}
          onClick={() => onToggle(path)}
          className="flex size-5 shrink-0 items-center justify-center text-muted-foreground"
        >
          {open ? (
            <ChevronDown className="size-3.5" />
          ) : (
            <ChevronRight className="size-3.5" />
          )}
        </button>
        <button
          type="button"
          onClick={() => {
            if (path.length > 0) onSelect(path, value)
            else onToggle(path)
          }}
          className="min-w-0 text-left"
        >
          {name !== undefined ? (
            <>
              <span style={{ color: "var(--color-warning)" }}>
                {JSON.stringify(name)}
              </span>
              <span className="mr-1 text-muted-foreground">:</span>
            </>
          ) : null}
          {spanItemLabel ? (
            <span className="mr-1 text-muted-foreground">{spanItemLabel}</span>
          ) : null}
          <span className="text-muted-foreground">{opening}</span>
          {!open ? (
            <>
              <span className="mx-1 text-muted-foreground">
                {t(isArray ? "rawTracePicker.items" : "rawTracePicker.fields", {
                  count: entries.length,
                })}
              </span>
              <span className="text-muted-foreground">{closing}</span>
              {!isLast ? (
                <span className="text-muted-foreground">,</span>
              ) : null}
            </>
          ) : null}
        </button>
      </div>

      {open ? (
        <>
          {entries.map(([childName, childValue], index) => (
            <RawJsonNode
              key={`${key}.${childName}`}
              name={isArray ? undefined : childName}
              value={childValue}
              path={[...path, isArray ? Number(childName) : childName]}
              depth={depth + 1}
              isLast={index === entries.length - 1}
              expanded={expanded}
              selectedPath={selectedPath}
              onToggle={onToggle}
              onSelect={onSelect}
            />
          ))}
          <div
            className="min-h-6 pr-3 font-mono text-xs leading-5 text-muted-foreground"
            style={{ paddingLeft: `${depth * 16 + 24}px` }}
          >
            {closing}
            {!isLast ? "," : ""}
          </div>
        </>
      ) : null}
    </div>
  )
}

function RawJsonTree({
  data,
  selectedPath,
  onSelect,
}: {
  data: JsonRecord
  selectedPath: JsonPath
  onSelect: (path: JsonPath, value: unknown) => void
}) {
  const initialExpanded = () =>
    new Set<string>([
      "",
      "spans",
      "spans.0",
      "spans.0.attributes",
      "spans.0.resource",
    ])
  const [expanded, setExpanded] = useState(initialExpanded)

  return (
    <div className="w-full py-2">
      <RawJsonNode
        value={data}
        path={[]}
        depth={0}
        isLast
        expanded={expanded}
        selectedPath={pathKey(selectedPath)}
        onToggle={(path) =>
          setExpanded((current) => {
            const next = new Set(current)
            const key = pathKey(path)
            if (next.has(key)) next.delete(key)
            else next.add(key)
            return next
          })
        }
        onSelect={onSelect}
      />
    </div>
  )
}

function valueColor(value: unknown): string {
  if (value === null || value === undefined)
    return "var(--color-text-description)"
  if (Array.isArray(value)) return "var(--color-success)"
  if (typeof value === "object") return "var(--color-auxiliary)"
  if (typeof value === "number" || typeof value === "boolean")
    return "var(--color-warning)"
  return "var(--color-auxiliary)"
}

function flattenJsonFields(
  value: unknown,
  path: JsonPath = []
): Array<{ path: JsonPath; value: unknown }> {
  const current = [{ path, value }]
  if (Array.isArray(value)) {
    return current.concat(
      value.flatMap((item, index) => flattenJsonFields(item, [...path, index]))
    )
  }
  if (value !== null && typeof value === "object") {
    return current.concat(
      Object.entries(asRecord(value) ?? {}).flatMap(([key, item]) =>
        flattenJsonFields(item, [...path, key])
      )
    )
  }
  return current
}

function fieldValueText(value: unknown): string {
  if (typeof value === "string") return value
  if (value === undefined) return "undefined"
  if (
    typeof value === "number" ||
    typeof value === "boolean" ||
    value === null
  ) {
    return String(value)
  }
  try {
    return JSON.stringify(value) ?? String(value)
  } catch {
    return String(value)
  }
}

function JsonFieldList({
  fields,
  selectedPath,
  onSelect,
  query = "",
}: {
  fields: Array<{ path: JsonPath; value: unknown }>
  selectedPath: JsonPath
  onSelect: (path: JsonPath) => void
  query?: string
}) {
  const { t } = useTranslation()
  const selectedFieldRef = useRef<HTMLButtonElement>(null)
  const normalizedQuery = query.trim().toLowerCase()
  const filteredFields = fields.filter(({ path }) =>
    pathKey(path).toLowerCase().includes(normalizedQuery)
  )

  useEffect(() => {
    selectedFieldRef.current?.scrollIntoView({ block: "nearest" })
  }, [selectedPath])

  return (
    <div className="flex flex-col gap-1 p-2">
      {filteredFields.map((field) => {
        const fieldPath = pathKey(field.path)
        const fieldValue = fieldValueText(field.value)
        const selected = pathKey(selectedPath) === fieldPath
        return (
          <button
            key={fieldPath}
            ref={selected ? selectedFieldRef : undefined}
            type="button"
            onClick={() => onSelect(field.path)}
            className={cn(
              "flex min-w-0 items-center gap-2 border-l-2 px-2 py-2 text-left transition-colors hover:bg-muted/40",
              selected ? "font-medium" : "border-l-transparent"
            )}
            style={
              selected
                ? {
                    borderLeftColor: "var(--color-auxiliary)",
                    backgroundColor:
                      "color-mix(in srgb, var(--color-auxiliary) 8%, transparent)",
                    color: "var(--color-auxiliary)",
                  }
                : undefined
            }
          >
            <span className="grid min-w-0 flex-1 grid-cols-2 items-center gap-2">
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="truncate font-mono text-xs">
                    {fieldPath}
                  </span>
                </TooltipTrigger>
                <TooltipContent className="max-w-[480px] text-xs break-all whitespace-pre-wrap">
                  {fieldPath}
                </TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="truncate text-xs text-muted-foreground">
                    {fieldValue}
                  </span>
                </TooltipTrigger>
                <TooltipContent className="max-w-[480px] text-xs break-all whitespace-pre-wrap">
                  {fieldValue}
                </TooltipContent>
              </Tooltip>
            </span>
          </button>
        )
      })}
      {filteredFields.length === 0 ? (
        <p className="py-10 text-center text-xs text-muted-foreground">
          {t("rawTracePicker.noFields")}
        </p>
      ) : null}
    </div>
  )
}

function previewPrimitive(value: unknown): { text: string; color: string } {
  if (value === null)
    return { text: "null", color: "var(--color-text-description)" }
  if (value === undefined)
    return { text: "undefined", color: "var(--color-text-description)" }
  if (typeof value === "string")
    return { text: JSON.stringify(value), color: "var(--color-success)" }
  if (typeof value === "number")
    return { text: String(value), color: "var(--color-auxiliary)" }
  return { text: String(value), color: "var(--color-warning)" }
}

function JsonPreviewNode({
  name,
  value,
  depth,
  isLast,
}: {
  name?: string
  value: unknown
  depth: number
  isLast: boolean
}) {
  const isArray = Array.isArray(value)
  const isObject =
    value !== null && typeof value === "object" && !Array.isArray(value)
  const entries = isArray
    ? value.map((item, index) => [String(index), item] as const)
    : Object.entries(asRecord(value) ?? {})
  if (!isArray && !isObject) {
    const primitive = previewPrimitive(value)
    return (
      <div
        className="min-h-6 pr-3 font-mono text-xs leading-5"
        style={{ paddingLeft: String(depth * 16 + 12) + "px" }}
      >
        {name !== undefined ? (
          <>
            <span className="text-[var(--color-warning)]">
              {JSON.stringify(name)}
            </span>
            <span className="mr-1 text-muted-foreground">:</span>
          </>
        ) : null}
        <span
          className="break-all whitespace-pre-wrap"
          style={{ color: primitive.color }}
        >
          {primitive.text}
        </span>
        {!isLast ? <span className="text-muted-foreground">,</span> : null}
      </div>
    )
  }
  const opening = isArray ? "[" : "{"
  const closing = isArray ? "]" : "}"
  if (entries.length === 0) {
    return (
      <div
        className="min-h-6 pr-3 font-mono text-xs leading-5"
        style={{ paddingLeft: String(depth * 16 + 12) + "px" }}
      >
        {name !== undefined ? (
          <>
            <span className="text-[var(--color-warning)]">
              {JSON.stringify(name)}
            </span>
            <span className="mr-1 text-muted-foreground">:</span>
          </>
        ) : null}
        <span className="text-muted-foreground">{opening + closing}</span>
        {!isLast ? <span className="text-muted-foreground">,</span> : null}
      </div>
    )
  }
  return (
    <div>
      <div
        className="min-h-6 pr-3 font-mono text-xs leading-5"
        style={{ paddingLeft: String(depth * 16 + 12) + "px" }}
      >
        {name !== undefined ? (
          <>
            <span className="text-[var(--color-warning)]">
              {JSON.stringify(name)}
            </span>
            <span className="mr-1 text-muted-foreground">:</span>
          </>
        ) : null}
        <span className="text-muted-foreground">{opening}</span>
      </div>
      {entries.map(([childName, childValue], index) => (
        <JsonPreviewNode
          key={String(depth) + "." + childName}
          name={isArray ? undefined : childName}
          value={childValue}
          depth={depth + 1}
          isLast={index === entries.length - 1}
        />
      ))}
      <div
        className="min-h-6 pr-3 font-mono text-xs leading-5 text-muted-foreground"
        style={{ paddingLeft: String(depth * 16 + 12) + "px" }}
      >
        {closing}
        {!isLast ? "," : ""}
      </div>
    </div>
  )
}

function JsonPreview({ value }: { value: unknown }) {
  return (
    <div className="w-full py-2">
      <JsonPreviewNode value={value} depth={0} isLast />
    </div>
  )
}

function hasResolvedValue(value: unknown): boolean {
  if (value === undefined || value === null) return false
  if (Array.isArray(value)) return value.length > 0
  if (typeof value === "object")
    return Object.keys(asRecord(value) ?? {}).length > 0
  return true
}

interface ExpressionPair {
  expression: string
  pythonExpression: string
}

function spanExpressionAt(index: number, path: string): ExpressionPair {
  return {
    expression: `spans[${index}].${path}`,
    pythonExpression: buildPythonSpanExpression(
      { name: "" },
      index,
      [path],
      "first"
    ),
  }
}

function firstObservedSpanExpression(
  spans: JsonRecord[],
  paths: string[],
  options: { fromEnd?: boolean } = {}
): ExpressionPair | null {
  const { fromEnd = false } = options
  const indexes = spans.map((_, index) => index)
  if (fromEnd) indexes.reverse()
  for (const index of indexes) {
    const span = spans[index]
    if (!span) continue
    for (const path of paths) {
      if (hasResolvedValue(readNested(span, path))) {
        return spanExpressionAt(index, path)
      }
    }
  }
  return null
}

interface QuickExpression {
  label: string
  expression: string
  pythonExpression: string
  color: string
}

function buildQuickExpressions(
  spans: JsonRecord[],
  t: TFunction
): QuickExpression[] {
  const items: QuickExpression[] = []
  const add = (
    label: string,
    expressions: ExpressionPair | null,
    color: string
  ) => {
    if (
      !expressions ||
      !hasResolvedValue(runTraceExpression(expressions.expression, spans))
    ) {
      return
    }
    if (items.some((item) => item.expression === expressions.expression)) return
    items.push({ label, ...expressions, color })
  }

  add(
    t("rawTracePicker.allSpans"),
    { expression: "spans", pythonExpression: '(trace.get("spans") or [])' },
    "var(--color-text-secondary)"
  )
  add(
    t("rawTracePicker.spanNames"),
    {
      expression: "spans[*].name",
      pythonExpression:
        '[span.get("name") for span in (trace.get("spans") or [])]',
    },
    "var(--color-text-secondary)"
  )
  add(
    t("rawTracePicker.spanAttributes"),
    {
      expression: "spans[*].attributes",
      pythonExpression:
        '[span.get("attributes") or {key[11:]: value for key, value in span.items() if key.startswith("attributes.")} for span in (trace.get("spans") or [])]',
    },
    "var(--color-auxiliary)"
  )
  add(
    t("rawTracePicker.resourceAttributes"),
    {
      expression: "spans[*].resource",
      pythonExpression:
        '[span.get("resource") or {key[9:]: value for key, value in span.items() if key.startswith("resource.")} for span in (trace.get("spans") or [])]',
    },
    "var(--color-auxiliary)"
  )
  add(
    t("rawTracePicker.detectedInput"),
    firstObservedSpanExpression(spans, [
      "attributes.gen_ai.input.messages",
      "attributes.gen_ai.input",
      "input",
    ]),
    "var(--color-success)"
  )
  add(
    t("rawTracePicker.detectedOutput"),
    firstObservedSpanExpression(
      spans,
      [
        "attributes.gen_ai.output.messages",
        "attributes.gen_ai.output",
        "output",
      ],
      { fromEnd: true }
    ),
    "var(--color-auxiliary)"
  )
  add(
    t("rawTracePicker.detectedTool"),
    firstObservedSpanExpression(spans, [
      "attributes.gen_ai.tool.name",
      "attributes.gen_ai.tool.call.name",
      "attributes.agentic.tool.name",
      "attributes.tool.name",
      "attributes.code.tool.name",
    ]),
    "var(--color-warning)"
  )
  add(
    t("rawTracePicker.detectedTokens"),
    firstObservedSpanExpression(
      spans,
      [
        "attributes.gen_ai.usage.total_tokens",
        "attributes.gen_ai.usage.total",
        "usage.total_tokens",
      ],
      { fromEnd: true }
    ),
    "var(--color-warning)"
  )
  return items
}

export function TraceIdInput({
  value,
  samples,
  loading,
  onSelect,
  onRequest,
  onRequestRecent,
  popoverClassName,
}: {
  value: string
  samples: Array<{ trace_id: string }>
  loading?: boolean
  onSelect: (traceId: string) => void
  onRequest: (traceId: string) => void
  onRequestRecent?: () => void
  popoverClassName?: string
}) {
  const [open, setOpen] = useState(false)
  const { t } = useTranslation()
  const [draft, setDraft] = useState(value)
  const inputRef = useRef<HTMLInputElement>(null)

  const request = () => {
    const traceId = draft.trim()
    if (!traceId) return
    onRequest(traceId)
    setOpen(false)
  }

  return (
    <Popover
      open={open}
      onOpenChange={(nextOpen) => {
        setOpen(nextOpen)
        if (nextOpen && samples.length === 0) onRequestRecent?.()
      }}
    >
      <PopoverAnchor asChild>
        <InputGroup>
          <InputGroupInput
            ref={inputRef}
            value={draft}
            aria-label={t("traceSearch.types.traceId")}
            role="combobox"
            aria-expanded={open}
            placeholder={t("diagnose.diagnoser.form.traceInput")}
            className="font-mono text-xs"
            onClick={() => setOpen(true)}
            onChange={(event) => {
              setDraft(event.target.value)
              setOpen(true)
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") request()
              if (event.key === "ArrowDown") setOpen(true)
              if (event.key === "Escape") setOpen(false)
            }}
          />
          <InputGroupAddon align="inline-end" className="gap-0 pr-2">
            {draft ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <InputGroupButton
                    size="icon-xs"
                    aria-label={t("diagnose.diagnoser.form.clearTrace")}
                    className="group/clear rounded-full hover:bg-transparent"
                    onClick={() => {
                      setDraft("")
                      setOpen(false)
                      window.requestAnimationFrame(() =>
                        inputRef.current?.focus()
                      )
                    }}
                  >
                    <span className="flex size-3.5 items-center justify-center rounded-full bg-muted-foreground/45 text-background transition-colors group-hover/clear:bg-muted-foreground/65">
                      <X className="size-2.5" />
                    </span>
                  </InputGroupButton>
                </TooltipTrigger>
                <TooltipContent side="top">
                  {t("diagnose.diagnoser.form.clearInput")}
                </TooltipContent>
              </Tooltip>
            ) : null}
            <PopoverTrigger asChild>
              <InputGroupButton
                size="icon-xs"
                aria-label={t("diagnose.diagnoser.form.recentTrace")}
              >
                <ChevronDown />
              </InputGroupButton>
            </PopoverTrigger>
          </InputGroupAddon>
        </InputGroup>
      </PopoverAnchor>
      <PopoverContent
        align="start"
        className={cn(
          "w-[min(360px,calc(100vw-32px))] gap-0 p-1",
          popoverClassName
        )}
        onOpenAutoFocus={(event) => event.preventDefault()}
        onCloseAutoFocus={(event) => event.preventDefault()}
      >
        <Command shouldFilter={false}>
          <CommandList className="max-h-80">
            <CommandEmpty className="py-4 text-xs text-muted-foreground">
              {loading
                ? t("diagnose.diagnoser.form.loadingTrace")
                : t("diagnose.diagnoser.form.noTrace")}
            </CommandEmpty>
            <CommandGroup
              heading={t("diagnose.diagnoser.form.recentTraceHint")}
              className="**:[[cmdk-group-heading]]:whitespace-nowrap"
            >
              {samples.map((sample) => (
                <CommandItem
                  key={sample.trace_id}
                  value={sample.trace_id}
                  data-checked={sample.trace_id === value}
                  onSelect={() => {
                    onSelect(sample.trace_id)
                    setOpen(false)
                  }}
                  className="font-mono text-xs font-normal"
                >
                  <span className="min-w-0 flex-1 truncate">
                    {sample.trace_id}
                  </span>
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}

function ExpressionHelp({ mode }: { mode: "template" | "python" }) {
  const { t } = useTranslation()
  const [hovered, setHovered] = useState(false)
  const [pinned, setPinned] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!pinned) return
    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (!triggerRef.current?.contains(event.target as Node)) setPinned(false)
    }
    document.addEventListener("pointerdown", closeOnOutsidePointer)
    return () =>
      document.removeEventListener("pointerdown", closeOnOutsidePointer)
  }, [pinned])

  return (
    <Tooltip open={hovered || pinned}>
      <TooltipTrigger asChild>
        <Button
          ref={triggerRef}
          type="button"
          variant="ghost"
          size="icon"
          className="size-6"
          aria-label={t("rawTracePicker.expressionHelp")}
          aria-pressed={pinned}
          onPointerEnter={() => setHovered(true)}
          onPointerLeave={() => setHovered(false)}
          onClick={() => setPinned((current) => !current)}
        >
          <CircleHelp className="size-3.5" />
        </Button>
      </TooltipTrigger>
      <TooltipContent
        side="top"
        align="start"
        className="w-80 rounded-lg p-3 shadow-none"
      >
        <div className="space-y-2 text-xs leading-5 text-background">
          <p className="font-medium">
            {t(
              mode === "python"
                ? "rawTracePicker.pythonExpression"
                : "rawTracePicker.expression"
            )}
          </p>
          {mode === "python" ? (
            <>
              <p>{t("rawTracePicker.pythonPasteHelp")}</p>
              <p>{t("rawTracePicker.pythonMatchHelp")}</p>
              <p>{t("rawTracePicker.sourceHelp")}</p>
            </>
          ) : (
            <>
              <p>{t("rawTracePicker.nameHelp")}</p>
              <p>{t("rawTracePicker.indexHelp")}</p>
              <p>{t("rawTracePicker.filterHelp")}</p>
              <p>{t("rawTracePicker.pathHelp")}</p>
            </>
          )}
        </div>
      </TooltipContent>
    </Tooltip>
  )
}

export function RawTraceVariablePicker({
  open,
  onOpenChange,
  sample,
  samples,
  onActiveTraceChange,
  onRequestTrace,
  traceLoading = false,
  traceLoadError,
  expressionMode = "template",
}: RawTraceVariablePickerProps) {
  const { t } = useTranslation()
  const traceSamples = useMemo(() => {
    if (samples?.length) return samples
    return sample
      ? [
          {
            trace_id: String(asRecord(sample)?.trace_id ?? "current-trace"),
            test_input: asRecord(sample) ?? {},
          },
        ]
      : []
  }, [sample, samples])
  const [activeTraceId, setActiveTraceId] = useState("")
  const activeSample =
    traceSamples.find((item) => item.trace_id === activeTraceId) ??
    traceSamples[0]
  const raw = asRecord(activeSample?.test_input)
  const spans = useMemo(
    () =>
      asArray(raw?.spans).filter(
        (span): span is JsonRecord => asRecord(span) !== null
      ),
    [raw]
  )
  const rawTrace = useMemo<JsonRecord>(() => ({ spans }), [spans])
  const expressionSourceSpans = useMemo(() => expressionSpans(spans), [spans])
  const previewSourceSpans =
    expressionMode === "python" ? spans : expressionSourceSpans
  const rows = useMemo(() => spanRows(spans), [spans])
  const timeline = useMemo(() => {
    const timedRows = rows.filter(
      (row): row is SpanRow & { startMs: number } => row.startMs !== null
    )
    if (timedRows.length === 0) return null
    const startMs = Math.min(...timedRows.map((row) => row.startMs))
    const endMs = Math.max(
      ...timedRows.map((row) => row.startMs + (row.durationMs ?? 0))
    )
    return { startMs, durationMs: Math.max(endMs - startMs, 1) }
  }, [rows])
  const timelineTicks = [0, 1]
  const selectedTraceId = activeTraceId || activeSample?.trace_id || ""
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [selectedPath, setSelectedPath] = useState<JsonPath>([])
  const [selectedRawPath, setSelectedRawPath] = useState<JsonPath>([])
  const [spanQuery, setSpanQuery] = useState("")
  const [fieldQuery, setFieldQuery] = useState("")
  const [expression, setExpression] = useState("")
  const [pythonExpression, setPythonExpression] = useState("")
  const [preview, setPreview] = useState<unknown>(undefined)
  const [copied, setCopied] = useState(false)
  const [matchMode, setMatchMode] = useState<SpanMatchMode>("all")

  const selectedSpan = spans[selectedIndex]
  const hasTraceWorkspace = Boolean(raw && selectedSpan && spans.length > 0)
  const selectedSpanName =
    typeof selectedSpan?.name === "string" ? selectedSpan.name : ""
  const selectedDisplaySpan = selectedSpan ? displaySpan(selectedSpan) : {}
  const selectedFields = flattenJsonFields(selectedDisplaySpan).filter(
    ({ path }) => path.length > 0
  )
  const selectedFieldCount = selectedSpan ? Object.keys(selectedSpan).length : 0
  const duplicateCount = selectedSpanName
    ? spans.filter((span) => span.name === selectedSpanName).length
    : 0
  const previewCount = Array.isArray(preview)
    ? preview.length
    : preview === undefined || preview === null
      ? 0
      : 1
  const quickExpressions = useMemo(
    () => buildQuickExpressions(previewSourceSpans, t),
    [previewSourceSpans, t]
  )
  const displayedExpression =
    expressionMode === "python" ? pythonExpression : expression

  const filteredRows = spanQuery.trim()
    ? rows.filter(({ span }) =>
        `${span.name ?? ""} ${spanKind(span)}`
          .toLowerCase()
          .includes(spanQuery.trim().toLowerCase())
      )
    : rows

  /* eslint-disable react-hooks/set-state-in-effect -- reset local picker state when a new sample opens. */
  useEffect(() => {
    if (!open || traceSamples.length === 0) return
    if (!activeTraceId) {
      const nextTraceId = traceSamples[0]?.trace_id
      if (!nextTraceId) return
      setActiveTraceId(nextTraceId)
      onActiveTraceChange?.(nextTraceId)
      return
    }
    if (!traceSamples.some((item) => item.trace_id === activeTraceId)) return
    if (spans.length === 0) return
    setSelectedIndex(0)
    setSelectedPath([])
    setSelectedRawPath([])
    setSpanQuery("")
    setFieldQuery("")
    const firstSpan = spans[0]
    if (!firstSpan) return
    const initialMode: SpanMatchMode =
      expressionMode === "python" ||
      spans.filter((span) => span.name === firstSpan.name).length > 1
        ? "first"
        : "all"
    const initialExpression = buildSpanExpression(firstSpan, 0, [], initialMode)
    setMatchMode(initialMode)
    setExpression(initialExpression)
    setPythonExpression(
      buildPythonSpanExpression(firstSpan, 0, [], initialMode)
    )
    setPreview(runTraceExpression(initialExpression, previewSourceSpans))
    setCopied(false)
  }, [
    activeSample?.trace_id,
    activeTraceId,
    onActiveTraceChange,
    open,
    expressionMode,
    previewSourceSpans,
    spans,
    traceSamples,
  ])

  const selectTrace = (traceId: string) => {
    setActiveTraceId(traceId)
    onActiveTraceChange?.(traceId)
  }

  const requestTrace = (traceId: string) => {
    const normalized = traceId.trim()
    if (!normalized) return
    selectTrace(normalized)
    if (!traceSamples.some((sample) => sample.trace_id === normalized)) {
      onRequestTrace?.(normalized)
    }
  }

  const selectSpan = (index: number) => {
    const span = spans[index]
    if (!span) return
    const nextMode: SpanMatchMode =
      expressionMode === "python" ||
      spans.filter((item) => item.name === span.name).length > 1
        ? "first"
        : "all"
    const nextExpression = buildSpanExpression(span, index, [], nextMode)
    setSelectedIndex(index)
    setSelectedPath([])
    setMatchMode(nextMode)
    setExpression(nextExpression)
    setPythonExpression(buildPythonSpanExpression(span, index, [], nextMode))
    setPreview(runTraceExpression(nextExpression, previewSourceSpans))
  }

  const selectField = (path: JsonPath) => {
    const sourcePath = sourcePathForDisplay(selectedSpan, path)
    const nextExpression = buildSpanExpression(
      selectedSpan,
      selectedIndex,
      sourcePath,
      matchMode
    )
    setSelectedPath(path)
    setExpression(nextExpression)
    setPythonExpression(
      buildPythonSpanExpression(
        selectedSpan,
        selectedIndex,
        sourcePath,
        matchMode
      )
    )
    setPreview(runTraceExpression(nextExpression, previewSourceSpans))
  }

  const selectRawPath = (path: JsonPath) => {
    setSelectedRawPath(path)
    if (typeof path[0] !== "number") return
    const [index, ...fieldPath] = path
    const span = spans[index]
    if (!span) return
    const nextMode: SpanMatchMode =
      expressionMode === "python" ||
      spans.filter((item) => item.name === span.name).length > 1
        ? "first"
        : "all"
    const nextExpression = buildSpanExpression(span, index, fieldPath, nextMode)
    setSelectedIndex(index)
    setSelectedPath(displayPathForSource(fieldPath))
    setFieldQuery("")
    setMatchMode(nextMode)
    setExpression(nextExpression)
    setPythonExpression(
      buildPythonSpanExpression(span, index, fieldPath, nextMode)
    )
    setPreview(runTraceExpression(nextExpression, previewSourceSpans))
  }

  const applyQuickExpression = (item: QuickExpression) => {
    setSelectedPath([])
    setExpression(item.expression)
    setPythonExpression(item.pythonExpression)
    setPreview(runTraceExpression(item.expression, previewSourceSpans))
  }

  const updateMatchMode = (nextMode: SpanMatchMode) => {
    const sourcePath = sourcePathForDisplay(selectedSpan, selectedPath)
    const nextExpression = buildSpanExpression(
      selectedSpan,
      selectedIndex,
      sourcePath,
      nextMode
    )
    setMatchMode(nextMode)
    setExpression(nextExpression)
    setPythonExpression(
      buildPythonSpanExpression(
        selectedSpan,
        selectedIndex,
        sourcePath,
        nextMode
      )
    )
    setPreview(runTraceExpression(nextExpression, previewSourceSpans))
  }

  const copyExpression = async () => {
    const value = displayedExpression.trim()
    if (!value) return
    try {
      await writeClipboardText(
        expressionMode === "python" ? value : `{{${value}}}`
      )
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1200)
    } catch {
      setCopied(false)
      toast.error(t("rawTracePicker.copyFailed"))
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        size="fullscreen"
        className="flex flex-col gap-0 overflow-hidden p-0"
        onOpenAutoFocus={(event) => event.preventDefault()}
      >
        <DialogHeader className="shrink-0 border-b px-6 py-3 pr-14">
          <div className="flex min-h-10 flex-wrap items-center justify-between gap-3 pr-8">
            <div className="flex min-w-0 flex-1 flex-col justify-center">
              <div className="flex items-center gap-1">
                <DialogTitle>{t("rawTracePicker.title")}</DialogTitle>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-xs"
                      aria-label={t("rawTracePicker.structureHelp")}
                    >
                      <CircleHelp className="size-3.5" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent side="right">
                    {t("rawTracePicker.description")}
                  </TooltipContent>
                </Tooltip>
              </div>
              <DialogDescription className="sr-only">
                {t("rawTracePicker.description")}
              </DialogDescription>
            </div>
            <div className="w-full min-w-0 sm:w-80">
              <div className="space-y-1">
                <TraceIdInput
                  key={selectedTraceId}
                  value={selectedTraceId}
                  samples={traceSamples}
                  loading={traceLoading}
                  onSelect={selectTrace}
                  onRequest={requestTrace}
                  popoverClassName="w-80 max-w-[calc(100vw-2rem)]"
                />
                {traceLoadError ? (
                  <p className="text-xs text-[var(--color-error)]">
                    {traceLoadError}
                  </p>
                ) : null}
              </div>
            </div>
          </div>
        </DialogHeader>

        <div className="grid min-h-0 flex-1 auto-rows-[minmax(20rem,1fr)] grid-cols-1 overflow-y-auto lg:auto-rows-auto lg:grid-cols-3 lg:overflow-hidden">
          {hasTraceWorkspace ? (
            <>
              <section className="flex min-h-0 min-w-0 flex-col border-r">
                <div className="flex h-12 shrink-0 items-center justify-between border-b bg-muted/20 px-4">
                  <h3 className="text-sm font-medium">
                    {t("rawTracePicker.selectSpan")}
                  </h3>
                  <span className="text-xs text-muted-foreground">
                    {t("rawTracePicker.spans", { count: spans.length })}
                  </span>
                </div>
                <Tabs
                  defaultValue="waterfall"
                  className="flex min-h-0 flex-1 flex-col gap-0"
                >
                  <div className="shrink-0 p-2">
                    <TabsList className="h-8! rounded-lg p-0.5">
                      <TabsTrigger
                        value="waterfall"
                        className="gap-1.5 rounded-md px-2.5 text-xs"
                      >
                        <ListTree className="size-3.5" />{" "}
                        {t("rawTracePicker.waterfall")}
                      </TabsTrigger>
                      <TabsTrigger
                        value="raw"
                        className="gap-1.5 rounded-md px-2.5 text-xs"
                      >
                        <FileJson className="size-3.5" />{" "}
                        {t("rawTracePicker.rawJson")}
                      </TabsTrigger>
                    </TabsList>
                  </div>
                  <TabsContent
                    value="waterfall"
                    className="flex min-h-0 flex-1 flex-col"
                  >
                    <div className="shrink-0 p-3">
                      <div className="relative">
                        <Search className="absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
                        <Input
                          value={spanQuery}
                          onChange={(event) => setSpanQuery(event.target.value)}
                          placeholder={t("rawTracePicker.searchSpans")}
                          className="h-8 pl-8 text-xs"
                        />
                      </div>
                    </div>
                    <div className="min-h-0 flex-1 overflow-y-auto">
                      <div className="sticky top-0 z-10 grid grid-cols-[minmax(0,1fr)_64px_42px] items-end gap-2 border-l-2 border-l-transparent bg-background px-3 pb-1 text-[10px] leading-4 text-muted-foreground">
                        <span />
                        <div className="relative h-5 border-b">
                          {timelineTicks.map((ratio, index) => (
                            <span
                              key={ratio}
                              className={cn(
                                "absolute top-0 font-mono",
                                index === 0
                                  ? "left-0 -translate-x-1/2"
                                  : index === timelineTicks.length - 1
                                    ? "right-0 translate-x-1/2"
                                    : "-translate-x-1/2"
                              )}
                              style={
                                index > 0 && index < timelineTicks.length - 1
                                  ? { left: `${ratio * 100}%` }
                                  : undefined
                              }
                            >
                              {timeline
                                ? formatTimelineMs(timeline.durationMs * ratio)
                                : "-"}
                            </span>
                          ))}
                        </div>
                        <span />
                      </div>
                      <div className="py-1">
                        {filteredRows.map(
                          ({ span, index, depth, startMs, durationMs }) => {
                            const kind = spanKind(span)
                            return (
                              <button
                                key={`${String(span.span_id ?? index)}-${index}`}
                                type="button"
                                onClick={() => selectSpan(index)}
                                className={cn(
                                  "grid w-full grid-cols-[minmax(0,1fr)_64px_42px] items-center gap-2 border-l-2 px-3 py-2 text-left transition-colors",
                                  selectedIndex === index
                                    ? "font-medium"
                                    : "border-l-transparent hover:bg-muted/40"
                                )}
                                style={
                                  selectedIndex === index
                                    ? {
                                        borderLeftColor: spanTone(kind),
                                        backgroundColor:
                                          "color-mix(in srgb, " +
                                          spanTone(kind) +
                                          " 8%, transparent)",
                                      }
                                    : undefined
                                }
                              >
                                <span
                                  className="flex min-w-0 items-center gap-2"
                                  style={{ paddingLeft: `${depth * 12}px` }}
                                >
                                  <span
                                    className="size-2 shrink-0 rounded-full"
                                    style={{ backgroundColor: spanTone(kind) }}
                                  />
                                  <span
                                    className="truncate text-sm"
                                    title={String(
                                      span.name ?? `Span ${index + 1}`
                                    )}
                                  >
                                    {String(span.name ?? `Span ${index + 1}`)}
                                  </span>
                                </span>
                                <span className="relative h-2 rounded-full bg-muted">
                                  <span
                                    className="absolute top-0 h-2 rounded-full opacity-70"
                                    style={{
                                      backgroundColor: spanTone(kind),
                                      left: `${timeline && startMs !== null ? Math.min(((startMs - timeline.startMs) / timeline.durationMs) * 100, 98) : 0}%`,
                                      width: `${
                                        timeline && startMs !== null
                                          ? Math.max(
                                              Math.min(
                                                ((durationMs ?? 0) /
                                                  timeline.durationMs) *
                                                  100,
                                                100
                                              ),
                                              2
                                            )
                                          : Math.min(
                                              Math.max(
                                                (durationMs ?? 50) / 50,
                                                8
                                              ),
                                              100
                                            )
                                      }%`,
                                    }}
                                  />
                                </span>
                                <span className="text-right font-mono text-xs text-muted-foreground">
                                  {durationMs === null
                                    ? "-"
                                    : `${(durationMs / 1000).toFixed(1)}s`}
                                </span>
                              </button>
                            )
                          }
                        )}
                      </div>
                    </div>
                  </TabsContent>
                  <TabsContent
                    value="raw"
                    className="min-h-0 flex-1 overflow-auto bg-muted/15"
                  >
                    <RawJsonTree
                      key={selectedTraceId}
                      data={rawTrace}
                      selectedPath={["spans", ...selectedRawPath]}
                      onSelect={(path) => {
                        const [root, ...spanPath] = path
                        if (root === "spans") selectRawPath(spanPath)
                      }}
                    />
                  </TabsContent>
                </Tabs>
              </section>

              <section className="flex min-h-0 min-w-0 flex-col border-r">
                <div className="flex h-12 shrink-0 items-center justify-between border-b bg-muted/20 px-4">
                  <h3 className="text-sm font-medium">
                    {t("rawTracePicker.selectField")}
                  </h3>
                  <span className="text-xs text-muted-foreground">
                    {t("rawTracePicker.fields", { count: selectedFieldCount })}
                  </span>
                </div>
                <div className="flex h-12 shrink-0 items-center px-4">
                  <p className="truncate text-xs font-medium text-foreground">
                    {String(selectedSpan.name ?? `Span ${selectedIndex + 1}`)}
                  </p>
                </div>
                <div className="shrink-0 p-3">
                  <div className="relative">
                    <Search className="absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      value={fieldQuery}
                      onChange={(event) => setFieldQuery(event.target.value)}
                      placeholder={t("rawTracePicker.searchFields")}
                      className="h-8 pl-8 text-xs"
                    />
                  </div>
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto">
                  <JsonFieldList
                    key={String(selectedSpan.span_id ?? selectedIndex)}
                    fields={selectedFields}
                    selectedPath={selectedPath}
                    query={fieldQuery}
                    onSelect={selectField}
                  />
                </div>
              </section>

              <section className="flex min-h-0 min-w-0 flex-col">
                <div className="flex h-12 shrink-0 items-center border-b bg-muted/20 px-4">
                  <h3 className="text-sm font-medium">
                    {t("rawTracePicker.expressionValue")}
                  </h3>
                </div>
                <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3">
                  {duplicateCount > 1 ? (
                    <div className="space-y-1.5">
                      <label className="text-xs font-medium text-muted-foreground">
                        {t("rawTracePicker.matchMode")}
                      </label>
                      <Select
                        value={matchMode}
                        onValueChange={(value) =>
                          updateMatchMode(value as SpanMatchMode)
                        }
                      >
                        <SelectTrigger className="h-8 w-full text-xs">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectGroup>
                            <SelectItem value="all">
                              {t("rawTracePicker.matchAll", {
                                count: duplicateCount,
                              })}
                            </SelectItem>
                            <SelectItem value="first">
                              {t("rawTracePicker.first")}
                            </SelectItem>
                            <SelectItem value="last">
                              {t("rawTracePicker.last")}
                            </SelectItem>
                          </SelectGroup>
                        </SelectContent>
                      </Select>
                    </div>
                  ) : null}
                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex items-center gap-1">
                        <label className="text-xs font-medium text-muted-foreground">
                          {t(
                            expressionMode === "python"
                              ? "rawTracePicker.pythonExpression"
                              : "rawTracePicker.expression"
                          )}
                        </label>
                        <ExpressionHelp mode={expressionMode} />
                      </div>
                      <Button
                        type="button"
                        variant="outline"
                        size="xs"
                        className="h-7"
                        onClick={() =>
                          setPreview(
                            runTraceExpression(expression, previewSourceSpans)
                          )
                        }
                      >
                        <Play className="mr-1 size-3" />{" "}
                        {t("rawTracePicker.tryValue")}
                      </Button>
                    </div>
                    <div className="relative">
                      <Textarea
                        value={displayedExpression}
                        readOnly={expressionMode === "python"}
                        onChange={(event) => setExpression(event.target.value)}
                        spellCheck={false}
                        className={cn(
                          "resize-none rounded-md pr-24 pb-8 font-mono text-xs leading-5",
                          expressionMode === "python"
                            ? "h-[120px] min-h-[120px]"
                            : "h-[72px] min-h-[72px]"
                        )}
                      />
                      <Button
                        type="button"
                        variant="outline"
                        size="xs"
                        className="absolute right-2 bottom-2 h-6 bg-background/95 px-2 shadow-sm"
                        disabled={!displayedExpression.trim()}
                        onClick={() => void copyExpression()}
                      >
                        {copied ? (
                          <Check className="mr-1 size-3" />
                        ) : (
                          <Copy className="mr-1 size-3" />
                        )}
                        {t(copied ? "common.copied" : "common.copy")}
                      </Button>
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {quickExpressions.map((item) => (
                      <Button
                        key={item.label}
                        type="button"
                        variant="outline"
                        size="xs"
                        className={cn(
                          "rounded-md font-normal",
                          expression === item.expression && "font-medium"
                        )}
                        aria-pressed={expression === item.expression}
                        onClick={() => applyQuickExpression(item)}
                        style={
                          expression === item.expression
                            ? {
                                color: item.color,
                                borderColor: item.color,
                                backgroundColor:
                                  "color-mix(in srgb, " +
                                  item.color +
                                  " 10%, transparent)",
                              }
                            : undefined
                        }
                      >
                        <span
                          className="size-1.5 rounded-full"
                          style={{ backgroundColor: item.color }}
                        />
                        {item.label}
                        {expression === item.expression ? (
                          <Check className="size-3" />
                        ) : null}
                      </Button>
                    ))}
                  </div>
                  <div className="flex min-h-32 min-w-0 flex-1 flex-col overflow-hidden rounded-md border bg-background">
                    <div className="flex shrink-0 items-center justify-between gap-3 border-b bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
                      <span className="flex items-center gap-2 font-medium">
                        <span
                          className="size-1.5 rounded-full"
                          style={{ backgroundColor: valueColor(preview) }}
                        />
                        {t("rawTracePicker.valuePreview")}
                      </span>
                      <span>
                        {t("rawTracePicker.matchedValues", {
                          count: previewCount,
                        })}
                      </span>
                    </div>
                    <div className="min-h-0 flex-1 overflow-auto">
                      {preview === undefined ? (
                        <p className="p-3 text-xs text-muted-foreground">
                          {t("rawTracePicker.noMatch")}
                        </p>
                      ) : (
                        <JsonPreview value={preview} />
                      )}
                    </div>
                  </div>
                </div>
              </section>
            </>
          ) : (
            <div className="col-span-full flex min-h-0 items-center justify-center px-6 py-12">
              <div className="flex max-w-md flex-col items-center gap-3 text-center">
                {traceLoading ? (
                  <Loader2 className="size-5 animate-spin text-muted-foreground" />
                ) : null}
                <div className="space-y-1">
                  <p className="text-sm font-medium">
                    {t(
                      traceLoading
                        ? "rawTracePicker.loading"
                        : "rawTracePicker.noSpans"
                    )}
                  </p>
                  {!traceLoading ? (
                    <p className="text-xs leading-5 text-muted-foreground">
                      {traceLoadError || t("rawTracePicker.noSpansHelp")}
                    </p>
                  ) : null}
                </div>
              </div>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
