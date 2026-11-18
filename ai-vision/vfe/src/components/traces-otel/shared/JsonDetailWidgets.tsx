import { toast } from "sonner"
import { writeClipboardText } from "@/lib/clipboard"
import { longJsonString } from "../../traces/shared/longJsonString"
import { useTranslation } from "react-i18next"
/* eslint-disable react-refresh/only-export-components */
import React, { useState, useCallback } from "react"
import { ChevronRight, ChevronDown, FileJson2, Copy } from "lucide-react"
import { parseStructuredTraceJson } from "../../traces/shared/traceJson"
import { PrivacyMarkedText } from "@/labs/privacy/PrivacyMarkedText"
import { privacyPreviewBoundary } from "@/labs/privacy/privacyMarkers"

import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"

/**
 * Convert literal \n \r \t sequences (from JSON serialization) to actual
 * whitespace characters. Only applies when the text doesn't already have
 * enough real newlines (i.e. content is still "escaped").
 */
export function unescapeContent(text: string): string {
  if (text.includes("\n") && text.split("\n").length > 3) {
    return text
  }
  return text.replace(/\\n/g, "\n").replace(/\\r/g, "\r").replace(/\\t/g, "\t")
}

/**
 * Detect if content looks like a script or code (bash, python, etc.)
 * rather than prose/markdown text.
 */
export function detectScriptLanguage(text: string): string | null {
  const trimmed = text.trimStart()

  // Shebang line
  if (trimmed.startsWith("#!/")) {
    if (/python/i.test(trimmed.split("\n")[0])) return "python"
    if (/node|ts-node/i.test(trimmed.split("\n")[0])) return "javascript"
    return "bash"
  }

  // Common shell patterns
  if (
    /^(set\s+-[eoux]|export\s+\w|CDN_BASE=|BIN_NAME=|INSTALL_DIR=|function\s+\w+\s*\(\)|while\s+\[\[)/m.test(
      trimmed
    )
  )
    return "bash"

  // Python patterns
  if (
    /^(import\s+\w|from\s+\w+\s+import|def\s+\w+\s*\(|class\s+\w+[:(]|if\s+__name__)/m.test(
      trimmed
    )
  )
    return "python"

  // Numbered line pattern (like "1 #!/usr/bin/env bash 2 # comment...")
  if (/^\d+\s+#!\//.test(trimmed)) return "bash"

  // Code with line numbers (e.g. "148\t  }" or "1  function foo()")
  if (/^\d+[\t ]{1,4}\S/m.test(trimmed)) {
    const numberedLines = trimmed
      .split("\n")
      .filter((l) => /^\d+[\t ]\s*\S/.test(l))
    if (numberedLines.length >= 3) return "code"
  }

  // TypeScript / JavaScript patterns
  if (
    /^(const\s+\w|let\s+\w|var\s+\w|function\s+\w|interface\s+\w|type\s+\w|export\s+(default\s+)?(?:function|const|class|interface|type))/m.test(
      trimmed
    )
  )
    return "code"

  return null
}

const OUTPUT_TAG_FIELDS = [
  "reasoning_content",
  "tool_use",
  "tool_calls",
  "stop_reason",
  "finish_reason",
] as const

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

const COLLAPSE_THRESHOLD = 200

function ExpandableString({
  truncated,
  rest,
}: {
  truncated: string
  rest: string
}) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)
  if (rest.length <= 1)
    return (
      <span className="text-emerald-600 dark:text-emerald-400">
        <PrivacyMarkedText text={truncated + rest} />
      </span>
    )
  return (
    <span className="text-emerald-600 dark:text-emerald-400">
      <PrivacyMarkedText text={truncated} />
      {expanded ? (
        <PrivacyMarkedText text={rest} />
      ) : (
        <button
          type="button"
          className="mx-1 inline rounded border border-border bg-muted/60 px-1.5 py-0.5 font-sans text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
          onClick={() => setExpanded(true)}
        >
          {t("traceLabels.expandCharacters", { count: rest.length - 1 })}
        </button>
      )}
      {!expanded && '"'}
    </span>
  )
}

export function formatJsonHighlighted(json: string): React.ReactNode {
  const lines = json.split("\n")

  return lines.map((line, index) => {
    // Check if this line contains a long string value (after the key)
    // Pattern: optional key part, then a string value
    const longStringMatch = longJsonString(line)
    if (longStringMatch) {
      const [prefix, fullStr, suffix] = longStringMatch
      // Render key part with highlighting
      const keyParts: React.ReactNode[] = []
      let kIdx = 0
      const keyMatch = prefix.match(/^(\s*)"([^"]+)"(:)(\s*)$/)
      if (keyMatch) {
        const [, indent, keyName, colon, space] = keyMatch
        keyParts.push(<span key={kIdx++}>{indent}</span>)
        keyParts.push(
          <span key={kIdx++} className="text-purple-600 dark:text-purple-400">
            &quot;
            <PrivacyMarkedText text={keyName} />
            &quot;
          </span>
        )
        keyParts.push(
          <span key={kIdx++}>
            {colon}
            {space}
          </span>
        )
      } else {
        keyParts.push(<span key={kIdx++}>{prefix}</span>)
      }

      // Truncate the string value
      const boundary = privacyPreviewBoundary(fullStr, COLLAPSE_THRESHOLD + 1)
      const truncated = fullStr.slice(0, boundary)
      const rest = fullStr.slice(boundary)

      return (
        <React.Fragment key={index}>
          {keyParts}
          <ExpandableString truncated={truncated} rest={rest} />
          <span>{suffix}</span>
          {index < lines.length - 1 && "\n"}
        </React.Fragment>
      )
    }

    // Consume complete JSON tokens so strings, escapes, and source text that
    // looks like markup are never rewritten by a later highlighting pass.
    const parts: React.ReactNode[] = []
    const tokens =
      /"(?:[^"\\]|\\.)*"(?=\s*:)|"(?:[^"\\]|\\.)*"|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|\b(?:true|false|null)\b/g
    let offset = 0
    for (const match of line.matchAll(tokens)) {
      const start = match.index!
      if (start > offset)
        parts.push(
          <PrivacyMarkedText
            key={`gap-${offset}`}
            text={line.slice(offset, start)}
          />
        )
      const token = match[0]
      const color = token.startsWith('"')
        ? /^\s*:/.test(line.slice(start + token.length))
          ? "text-purple-600 dark:text-purple-400"
          : "text-emerald-600 dark:text-emerald-400"
        : /^(true|false|null)$/.test(token)
          ? "text-amber-600 dark:text-amber-400"
          : "text-blue-600 dark:text-blue-400"
      parts.push(
        <span key={start} className={color}>
          <PrivacyMarkedText text={token} />
        </span>
      )
      offset = start + token.length
    }
    if (offset < line.length)
      parts.push(
        <PrivacyMarkedText key={`tail-${offset}`} text={line.slice(offset)} />
      )

    return (
      <React.Fragment key={index}>
        {parts}
        {index < lines.length - 1 && "\n"}
      </React.Fragment>
    )
  })
}

function ScrollableHoverContent({
  children,
  className,
  ...props
}: React.ComponentProps<typeof HoverCardContent>) {
  const handleWheel = (event: React.WheelEvent<HTMLDivElement>) => {
    const target = event.currentTarget
    const isScrollable =
      target.scrollHeight > target.clientHeight ||
      target.scrollWidth > target.clientWidth
    if (isScrollable) {
      event.stopPropagation()
    }
  }

  return (
    <HoverCardContent className={className} {...props}>
      <div
        className="overflow-auto"
        style={{ maxHeight: "inherit", maxWidth: "inherit" }}
        onWheel={handleWheel}
      >
        {children}
      </div>
    </HoverCardContent>
  )
}

export function RawJsonHover({ data }: { data: unknown }) {
  const { t } = useTranslation()

  if (data === null) return null

  const jsonString =
    typeof data === "string" ? data : JSON.stringify(data, null, 2)
  if (!jsonString || jsonString === "{}") return null

  const formatted =
    typeof data === "object" ? JSON.stringify(data, null, 2) : jsonString
  const structuredString = parseStructuredTraceJson(data)

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="rounded p-0.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          title={t("traceLabels.rawJson")}
          aria-label={t("traceLabels.rawJson")}
        >
          <FileJson2 className="size-3.5" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        side="bottom"
        align="start"
        className="max-h-[400px] w-[min(480px,calc(100vw-2rem))] overflow-auto rounded-xl p-0"
        aria-label={t("traceLabels.rawJson")}
        onWheel={(e) => e.stopPropagation()}
      >
        {structuredString !== null ? (
          <CollapsibleJsonTree data={data} />
        ) : (
          <pre className="p-3 font-mono text-xs leading-relaxed">
            <code>{formatJsonHighlighted(formatted)}</code>
          </pre>
        )}
      </PopoverContent>
    </Popover>
  )
}

/**
 * Flatten output data into a flat record for tag detection.
 * When data is an array of message objects, use the **last** item
 * for tag rendering (most recent assistant response).
 * The full JSON remains available in the raw JSON detail.
 */
function collectOutputFields(data: unknown): Record<string, unknown> {
  const fields: Record<string, unknown> = {}

  if (isRecord(data)) {
    Object.assign(fields, data)
    if (isRecord(data.content)) {
      Object.assign(fields, data.content)
    }
  } else if (Array.isArray(data) && data.length > 0) {
    // Use the last item in the array for tag detection
    const lastItem = data[data.length - 1]
    if (isRecord(lastItem)) {
      Object.assign(fields, lastItem)
    }
  }

  return fields
}

const TAG_STYLES: Record<string, string> = {
  reasoning_content:
    "border-violet-500/30 bg-violet-500/5 text-violet-700 dark:border-violet-400/20 dark:bg-violet-400/10 dark:text-violet-300",
  tool_use:
    "border-sky-500/30 bg-sky-500/5 text-sky-700 dark:border-sky-400/20 dark:bg-sky-400/10 dark:text-sky-300",
  tool_calls:
    "border-blue-500/30 bg-blue-500/5 text-blue-700 dark:border-blue-400/20 dark:bg-blue-400/10 dark:text-blue-300",
  stop_reason:
    "border-amber-500/30 bg-amber-500/5 text-amber-700 dark:border-amber-400/20 dark:bg-amber-400/10 dark:text-amber-300",
  finish_reason:
    "border-teal-500/30 bg-teal-500/5 text-teal-700 dark:border-teal-400/20 dark:bg-teal-400/10 dark:text-teal-300",
}

export function OutputFieldTags({ data }: { data: unknown }) {
  const fieldsToCheck = collectOutputFields(data)
  if (Object.keys(fieldsToCheck).length === 0) return null

  const foundFields = OUTPUT_TAG_FIELDS.filter(
    (field) => field in fieldsToCheck && fieldsToCheck[field] !== null
  )

  if (foundFields.length === 0) return null

  return (
    <>
      {foundFields.map((field) => {
        const value = fieldsToCheck[field]
        const isShortString = typeof value === "string" && value.length <= 30
        const displayValue =
          typeof value === "string" ? value : JSON.stringify(value, null, 2)
        const style =
          TAG_STYLES[field] ??
          "border-muted-foreground/30 bg-muted/50 text-muted-foreground"

        return (
          <HoverCard key={field} openDelay={200}>
            <HoverCardTrigger asChild>
              <span
                className={`cursor-default rounded border px-2 py-0.5 text-xs ${style}`}
              >
                {field}
                {isShortString && (
                  <>
                    <span className="opacity-50">:</span>{" "}
                    <span className="font-normal">{value as string}</span>
                  </>
                )}
              </span>
            </HoverCardTrigger>
            <ScrollableHoverContent
              side="bottom"
              align="start"
              className="max-h-[300px] w-[440px] overflow-hidden rounded-xl p-0"
            >
              <div className="p-3">
                <div className="mb-1 text-xs font-medium text-muted-foreground">
                  {field}
                </div>
                {typeof value === "object" ? (
                  <pre className="font-mono text-xs leading-relaxed">
                    <code>
                      {formatJsonHighlighted(JSON.stringify(value, null, 2))}
                    </code>
                  </pre>
                ) : (
                  <pre className="font-mono text-xs leading-relaxed break-words whitespace-pre-wrap text-foreground">
                    <PrivacyMarkedText text={displayValue} />
                  </pre>
                )}
              </div>
            </ScrollableHoverContent>
          </HoverCard>
        )
      })}
    </>
  )
}

// ── Collapsible JSON Tree ────────────────────────────────────────────────────

function JsonCopyButton({ data }: { data: unknown }) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)
  const handleCopy = useCallback(async () => {
    try {
      await writeClipboardText(
        typeof data === "string" ? data : JSON.stringify(data, null, 2)
      )
    } catch {
      setCopied(false)
      toast.error(t("common.copyFailed"))
      return
    }
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }, [data, t])
  return (
    <button
      type="button"
      className="ml-1 inline-flex items-center rounded p-0.5 text-muted-foreground/60 hover:text-muted-foreground"
      onClick={(event) => {
        event.stopPropagation()
        handleCopy()
      }}
      title={t("common.copy")}
      aria-label={t("common.copy")}
    >
      <Copy className="size-3" />
      {copied && <span className="ml-1 text-[10px] text-emerald-500">✓</span>}
    </button>
  )
}

function JsonStringValue({ value, depth }: { value: string; depth: number }) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)
  const displayStr = JSON.stringify(value)
  if (displayStr.length <= COLLAPSE_THRESHOLD + 2 || expanded) {
    return (
      <span className="break-all text-emerald-600 dark:text-emerald-400">
        <PrivacyMarkedText text={displayStr} />
      </span>
    )
  }
  const boundary = privacyPreviewBoundary(displayStr, COLLAPSE_THRESHOLD + 1)
  const truncated = displayStr.slice(0, boundary)
  const remaining = displayStr.length - boundary - 1
  if (remaining <= 0)
    return (
      <span className="break-all text-emerald-600 dark:text-emerald-400">
        <PrivacyMarkedText text={displayStr} />
      </span>
    )
  void depth
  return (
    <span className="break-all text-emerald-600 dark:text-emerald-400">
      <PrivacyMarkedText text={truncated} />
      <button
        type="button"
        className="mx-1 inline rounded border border-border bg-muted/60 px-1.5 py-0.5 font-sans text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
        onClick={() => setExpanded(true)}
      >
        {t("traceLabels.expandCharacters", { count: remaining })}
      </button>
      {'"'}
    </span>
  )
}

function JsonNode({
  keyName,
  value: sourceValue,
  depth,
  defaultCollapsed,
}: {
  keyName?: string
  value: unknown
  depth: number
  defaultCollapsed?: boolean
}) {
  const { t } = useTranslation()
  const structuredString =
    depth < 16 ? parseStructuredTraceJson(sourceValue) : null
  const value = structuredString ?? sourceValue
  const isObject =
    typeof value === "object" && value !== null && !Array.isArray(value)
  const isArray = Array.isArray(value)
  const isCollapsible = isObject || isArray
  const [collapsed, setCollapsed] = useState(defaultCollapsed ?? false)

  const indent = depth * 16

  if (!isCollapsible) {
    // Primitive value
    return (
      <div className="flex items-start" style={{ paddingLeft: indent }}>
        {keyName !== undefined && (
          <span className="shrink-0 text-purple-600 dark:text-purple-400">
            &quot;
            <PrivacyMarkedText text={keyName} />
            &quot;<span className="text-foreground">: </span>
          </span>
        )}
        {typeof value === "string" ? (
          <JsonStringValue value={value} depth={depth} />
        ) : typeof value === "number" ? (
          <span className="text-blue-600 dark:text-blue-400">
            {String(value)}
          </span>
        ) : typeof value === "boolean" ? (
          <span className="text-amber-600 dark:text-amber-400">
            {String(value)}
          </span>
        ) : (
          <span className="text-amber-600 dark:text-amber-400">null</span>
        )}
      </div>
    )
  }

  const count = isArray
    ? value.length
    : Object.keys(value as Record<string, unknown>).length
  const bracket = isArray ? ["[", "]"] : ["{", "}"]

  return (
    <div>
      <div
        className="group flex items-center rounded-sm hover:bg-accent/50"
        style={{ paddingLeft: indent }}
      >
        <button
          type="button"
          className="flex min-w-0 items-center rounded-sm text-left focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          aria-expanded={!collapsed}
          aria-label={`${t(collapsed ? "traceLabels.expandContent" : "traceLabels.collapseContent")}${keyName === undefined ? "" : ` ${keyName}`}`}
          onClick={() => setCollapsed((value) => !value)}
        >
          <span className="mr-0.5 text-muted-foreground">
            {collapsed ? (
              <ChevronRight className="size-3.5" />
            ) : (
              <ChevronDown className="size-3.5" />
            )}
          </span>
          {keyName !== undefined && (
            <span className="text-purple-600 dark:text-purple-400">
              &quot;
              <PrivacyMarkedText text={keyName} />
              &quot;<span className="text-foreground">: </span>
            </span>
          )}
          {collapsed ? (
            <span className="text-muted-foreground">
              {bracket[0]}{" "}
              <span className="text-[11px] text-blue-600 dark:text-blue-400">
                {t("traceLabels.itemCount", { count })}
              </span>{" "}
              {bracket[1]}
            </span>
          ) : (
            <span className="text-foreground">{bracket[0]}</span>
          )}
          {structuredString !== null && (
            <span className="ml-2 font-sans text-[11px] text-muted-foreground">
              {t("traceLabels.jsonString")}
            </span>
          )}
        </button>
        {depth === 0 && <JsonCopyButton data={sourceValue} />}
      </div>
      {!collapsed && (
        <div>
          {isArray
            ? (value as unknown[]).map((item, i) => (
                <JsonNode
                  key={i}
                  keyName={String(i)}
                  value={item}
                  depth={depth + 1}
                />
              ))
            : Object.entries(value as Record<string, unknown>).map(([k, v]) => (
                <JsonNode key={k} keyName={k} value={v} depth={depth + 1} />
              ))}
          <div style={{ paddingLeft: indent }} className="text-foreground">
            {bracket[1]}
          </div>
        </div>
      )}
    </div>
  )
}

export function CollapsibleJsonTree({ data }: { data: unknown }) {
  return (
    <div className="overflow-x-auto rounded-lg bg-muted/50 p-3 font-mono text-xs leading-relaxed dark:bg-muted/30">
      <JsonNode value={data} depth={0} defaultCollapsed={false} />
    </div>
  )
}
