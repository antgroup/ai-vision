import { toast } from "sonner"
import { writeClipboardText } from "@/lib/clipboard"
import { mediaOutputValue } from "@/components/traces/shared/traceMediaOutput"
import { parseStructuredTraceJson } from "@/components/traces/shared/traceJson"
import { TraceDocumentPreviews } from "@/components/traces/shared/TraceDocumentPreviews"
import { TraceMarkdownLink } from "@/components/traces/shared/TraceTextContent"
import { TraceMediaContent } from "@/components/traces/shared/TraceMedia"
import { traceMediaParts } from "@/components/traces/shared/traceMediaProjection"
import { useTranslation } from "react-i18next"
import React, { useState } from "react"
import {
  ChevronDown,
  ChevronsDownUp,
  ChevronsUpDown,
  Copy,
  Check,
} from "lucide-react"
import ReactMarkdown, { defaultUrlTransform } from "react-markdown"
import remarkGfm from "remark-gfm"

const dataUriUrlTransform = (url: string) =>
  url.startsWith("data:") ? url : defaultUrlTransform(url)

import {
  extractContent,
  stripMarkdownFence,
  containsAnsiCodes,
  ansiToHtml,
} from "./utils"
import { markdownMediaComponents } from "@/components/traces/shared/MarkdownMedia"
import {
  RawJsonHover,
  OutputFieldTags,
  unescapeContent,
  detectScriptLanguage,
  CollapsibleJsonTree,
  formatJsonHighlighted,
} from "./JsonDetailWidgets"

export function CopyButton({ text }: { text: string }) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)
  const handleCopy = async () => {
    try {
      await writeClipboardText(text)
    } catch {
      setCopied(false)
      toast.error(t("common.copyFailed"))
      return
    }
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }
  return (
    <button
      type="button"
      onClick={handleCopy}
      className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
      title={t("common.copy")}
    >
      {copied ? (
        <Check className="size-3.5 text-emerald-500" />
      ) : (
        <Copy className="size-3.5" />
      )}
    </button>
  )
}

const MD_HINT =
  /^#{1,6}\s|^\s*[-*+]\s|^\s*\d+\.\s|^\|.+\|$|\*\*.+\*\*|__[^_]+__|```|!\[/m

export function MetadataValue({
  value,
  markdown = false,
}: {
  value: unknown
  markdown?: boolean
}) {
  if (value === null || value === undefined)
    return <span className="text-muted-foreground italic">null</span>
  if (typeof value === "string") {
    if (markdown && !containsAnsiCodes(value) && MD_HINT.test(value)) {
      return (
        <div className="prose prose-sm dark:prose-invert max-w-none">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            urlTransform={dataUriUrlTransform}
            components={markdownMediaComponents}
          >
            {value}
          </ReactMarkdown>
        </div>
      )
    }
    return (
      <span className="font-mono whitespace-pre-wrap text-emerald-600 dark:text-emerald-400">{`"${value}"`}</span>
    )
  }
  if (typeof value === "number")
    return (
      <span className="font-mono text-blue-600 dark:text-blue-400">
        {value}
      </span>
    )
  if (typeof value === "boolean")
    return (
      <span className="font-mono text-blue-600 dark:text-blue-400">
        {String(value)}
      </span>
    )
  return <span className="text-foreground">{String(value)}</span>
}

function MetadataRows({
  data,
  level = 0,
  collapseVersion,
  markdown = false,
}: {
  data: Record<string, unknown>
  level?: number
  collapseVersion: number
  markdown?: boolean
}) {
  const { t } = useTranslation()
  const [localToggled, setLocalToggled] = useState<Record<string, boolean>>({})
  const [prevCollapseVersion, setPrevCollapseVersion] =
    useState(collapseVersion)

  if (prevCollapseVersion !== collapseVersion) {
    setPrevCollapseVersion(collapseVersion)
    if (Object.keys(localToggled).length > 0) setLocalToggled({})
  }

  const allCollapsed = collapseVersion > 0 && collapseVersion % 2 === 1
  const entries = Object.entries(data)

  const toggle = (key: string) =>
    setLocalToggled((prev) => ({ ...prev, [key]: !prev[key] }))

  return (
    <>
      {entries.map(([key, value]) => {
        const isObject =
          value !== null && typeof value === "object" && !Array.isArray(value)
        const isArray = Array.isArray(value)
        const isExpandable = isObject || isArray
        const childEntries = isExpandable
          ? Object.entries(value as Record<string, unknown>)
          : []
        const baseOpen = allCollapsed ? false : level < 2
        const isOpen =
          localToggled[key] !== undefined ? localToggled[key] : baseOpen

        return (
          <React.Fragment key={key}>
            <tr className="border-b border-border">
              <td
                className="px-3 py-2 align-top text-sm text-foreground"
                style={{ paddingLeft: `${12 + level * 24}px` }}
              >
                <span className="flex items-center gap-1">
                  {isExpandable ? (
                    <button
                      type="button"
                      onClick={() => toggle(key)}
                      className="shrink-0 p-0.5 text-muted-foreground hover:text-foreground"
                    >
                      <ChevronDown
                        className={`size-3 transition-transform ${isOpen ? "" : "-rotate-90"}`}
                      />
                    </button>
                  ) : (
                    <span className="inline-block w-4 shrink-0" />
                  )}
                  <span className="font-normal">{key}</span>
                </span>
              </td>
              <td className="px-3 py-2 align-top text-sm break-all">
                {isExpandable ? (
                  childEntries.length === 0 ? (
                    <span className="text-muted-foreground italic">
                      {t("traceLabels.emptyObject")}
                    </span>
                  ) : (
                    <span className="text-muted-foreground italic">
                      {childEntries.length} {t("traceLabels.items")}
                    </span>
                  )
                ) : (
                  <MetadataValue value={value} markdown={markdown} />
                )}
              </td>
            </tr>
            {isExpandable && isOpen && childEntries.length > 0 && (
              <MetadataRows
                data={value as Record<string, unknown>}
                level={level + 1}
                collapseVersion={collapseVersion}
                markdown={markdown}
              />
            )}
          </React.Fragment>
        )
      })}
    </>
  )
}

export function MetadataTree({
  data,
  markdown = false,
  floatToolbar = false,
}: {
  data: unknown
  markdown?: boolean
  floatToolbar?: boolean
}) {
  const { t } = useTranslation()
  const [collapseVersion, setCollapseVersion] = useState(0)
  const [copied, setCopied] = useState(false)

  if (data === null || data === undefined)
    return <span className="text-muted-foreground">-</span>
  if (typeof data !== "object") return <MetadataValue value={data} />

  const entries = Object.entries(data as Record<string, unknown>)
  if (entries.length === 0)
    return (
      <span className="text-sm text-muted-foreground italic">
        {t("traceLabels.emptyObject")}
      </span>
    )

  const isCollapsed = collapseVersion > 0 && collapseVersion % 2 === 1

  const handleCopy = async () => {
    const text = JSON.stringify(data, null, 2)
    try {
      await writeClipboardText(text)
    } catch {
      setCopied(false)
      toast.error(t("common.copyFailed"))
      return
    }
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div>
      <div
        className={
          floatToolbar
            ? "absolute top-1.5 right-3 flex gap-1"
            : "flex justify-end gap-1"
        }
      >
        <button
          type="button"
          onClick={() => setCollapseVersion((v) => v + 1)}
          className="rounded p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground"
          title={
            isCollapsed
              ? t("traceLabels.expandAll")
              : t("traceLabels.collapseAll")
          }
        >
          <ChevronsDownUp
            className={`size-3.5 ${isCollapsed ? "rotate-180" : ""}`}
          />
        </button>
        <button
          type="button"
          onClick={handleCopy}
          className="rounded p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground"
          title={t("traceLabels.copyJson")}
        >
          {copied ? (
            <Check className="size-3.5 text-emerald-500" />
          ) : (
            <Copy className="size-3.5" />
          )}
        </button>
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border bg-muted/50">
            <th
              className="px-2 py-1 text-left text-xs font-semibold text-muted-foreground"
              style={{ width: "180px" }}
            >
              {t("traceLabels.path")}
            </th>
            <th className="px-2 py-1 text-left text-xs font-semibold text-muted-foreground">
              {t("traceLabels.value")}
            </th>
          </tr>
        </thead>
        <tbody>
          <MetadataRows
            data={data as Record<string, unknown>}
            collapseVersion={collapseVersion}
            markdown={markdown}
          />
        </tbody>
      </table>
    </div>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export function renderJson(data: unknown): React.ReactNode {
  if (data === null || data === undefined)
    return <span className="text-muted-foreground">-</span>
  if (typeof data === "string")
    return (
      <span className="text-sm break-all whitespace-pre-wrap text-foreground">
        {data}
      </span>
    )
  if (typeof data === "object") return <MetadataTree data={data} />
  return <MetadataValue value={data} />
}

export function FormatToggle({
  mode,
  onChange,
}: {
  mode: "formatted" | "json"
  onChange: (mode: "formatted" | "json") => void
}) {
  const { t } = useTranslation()
  return (
    <div className="flex overflow-hidden rounded-md border text-xs">
      <button
        type="button"
        className={`px-2 py-0.5 transition-colors ${mode === "formatted" ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground"}`}
        onClick={() => onChange("formatted")}
      >
        {t("traceLabels.formatted")}
      </button>
      <button
        type="button"
        className={`px-2 py-0.5 transition-colors ${mode === "json" ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground"}`}
        onClick={() => onChange("json")}
      >
        JSON
      </button>
    </div>
  )
}

// ── ChatML message detection & rendering ────────────────────────────────────

interface ChatMessage {
  role: string
  content: unknown
  [key: string]: unknown
}

const LANGCHAIN_TYPE_TO_ROLE: Record<string, string> = {
  system: "system",
  human: "user",
  ai: "assistant",
  tool: "tool",
  function: "tool",
  chat: "assistant",
  generic: "assistant",
  AIMessage: "assistant",
  HumanMessage: "user",
  SystemMessage: "system",
  ToolMessage: "tool",
  FunctionMessage: "tool",
}

function hasRole(item: Record<string, unknown>): boolean {
  return "role" in item && typeof item.role === "string"
}

function hasLangChainType(item: Record<string, unknown>): boolean {
  return (
    "type" in item &&
    typeof item.type === "string" &&
    item.type in LANGCHAIN_TYPE_TO_ROLE
  )
}

function isChatMessageLike(data: unknown): data is Record<string, unknown>[] {
  if (!Array.isArray(data)) return false
  if (data.length === 0) return false
  return data.every(
    (item) =>
      typeof item === "object" &&
      item !== null &&
      (hasRole(item as Record<string, unknown>) ||
        hasLangChainType(item as Record<string, unknown>))
  )
}

function normalizeToChatMessages(
  data: Record<string, unknown>[]
): ChatMessage[] {
  return data.map((item) => {
    if (hasRole(item)) return item as ChatMessage
    const role =
      LANGCHAIN_TYPE_TO_ROLE[item.type as string] ?? (item.type as string)
    return { ...item, role } as ChatMessage
  })
}

function tryParseJsonString(value: unknown): unknown | null {
  if (typeof value !== "string") return null
  const trimmed = value.trim()
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return null
  try {
    return JSON.parse(trimmed)
  } catch {
    return null
  }
}

function extractChatMessages(data: unknown): ChatMessage[] | null {
  if (isChatMessageLike(data)) return normalizeToChatMessages(data)

  const parsed = tryParseJsonString(data)
  if (parsed !== null) return extractChatMessages(parsed)

  if (Array.isArray(data) && data.length === 1 && isChatMessageLike(data[0])) {
    return normalizeToChatMessages(data[0])
  }

  if (typeof data === "object" && data !== null && !Array.isArray(data)) {
    const record = data as Record<string, unknown>
    if (record.messages !== undefined)
      return extractChatMessages(record.messages)
  }

  return null
}

function extractAdditionalInput(data: unknown): Record<string, unknown> | null {
  if (typeof data !== "object" || data === null || Array.isArray(data))
    return null
  const entries = Object.entries(data as Record<string, unknown>).filter(
    ([key]) => key !== "messages"
  )
  return entries.length > 0 ? Object.fromEntries(entries) : null
}

const ROLE_STYLES: Record<string, { bg: string; label: string }> = {
  system: {
    bg: "bg-purple-500/5 dark:bg-purple-400/10",
    label: "traceLabels.role.system",
  },
  developer: {
    bg: "bg-purple-500/5 dark:bg-purple-400/10",
    label: "traceLabels.role.developer",
  },
  user: {
    bg: "bg-blue-500/5 dark:bg-blue-400/10",
    label: "traceLabels.role.user",
  },
  assistant: {
    bg: "bg-green-500/5 dark:bg-green-400/10",
    label: "traceLabels.role.assistant",
  },
  tool: {
    bg: "bg-orange-500/5 dark:bg-orange-400/10",
    label: "traceLabels.role.tool",
  },
  model: {
    bg: "bg-green-500/5 dark:bg-green-400/10",
    label: "traceLabels.model",
  },
}

function extractMessageText(content: unknown): string {
  if (typeof content === "string") return content
  if (content === null || content === undefined) return ""
  if (Array.isArray(content)) {
    const parts: string[] = []
    for (const block of content) {
      if (typeof block === "string") {
        parts.push(block)
        continue
      }
      if (typeof block !== "object" || block === null) continue
      const obj = block as Record<string, unknown>
      if (obj.type === "text" && typeof obj.text === "string")
        parts.push(obj.text)
      else if (obj.type === "thinking" && typeof obj.thinking === "string")
        parts.push(obj.thinking)
      else if (obj.type === "tool_use")
        parts.push(
          `**Tool: ${obj.name ?? "unknown"}**\n\`\`\`json\n${JSON.stringify(obj.input, null, 2)}\n\`\`\``
        )
      else if (obj.type === "tool_result") {
        const inner = extractMessageText(obj.content)
        parts.push(inner || "[Tool Result]")
      } else if (typeof obj.text === "string") parts.push(obj.text)
      else parts.push(JSON.stringify(block, null, 2))
    }
    return parts.join("\n\n")
  }
  if (typeof content === "object") {
    const obj = content as Record<string, unknown>
    if (typeof obj.text === "string") return obj.text
    if (typeof obj.content === "string") return obj.content
  }
  return JSON.stringify(content, null, 2)
}

interface ToolCall {
  id?: string
  type?: string
  function?: { name?: string; arguments?: string }
  name?: string
  input?: unknown
}

function ToolCallBlock({ call }: { call: ToolCall }) {
  const { t } = useTranslation()
  const name = call.function?.name ?? call.name ?? "unknown"
  let args: string
  if (call.function?.arguments) {
    try {
      args = JSON.stringify(JSON.parse(call.function.arguments), null, 2)
    } catch {
      args = call.function.arguments
    }
  } else if (call.input !== null && call.input !== undefined) {
    args = JSON.stringify(call.input, null, 2)
  } else {
    args = ""
  }

  return (
    <div className="rounded-md border border-sky-500/20 bg-sky-500/5 p-2 dark:border-sky-400/15 dark:bg-sky-400/5">
      <div className="mb-1 flex items-center gap-1.5 text-xs font-medium text-sky-700 dark:text-sky-300">
        <span>{t("traceLabels.toolCall")}:</span>
        <span className="font-mono">{name}</span>
      </div>
      {args && (
        <pre className="overflow-x-auto rounded bg-muted/50 p-2 font-mono text-xs leading-relaxed dark:bg-muted/30">
          <code>{formatJsonHighlighted(args)}</code>
        </pre>
      )}
    </div>
  )
}

function ThinkingBlock({ content }: { content: string }) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)
  const isLong = content.length > 200

  return (
    <div className="rounded-md border border-violet-500/20 bg-violet-500/5 p-2 dark:border-violet-400/15 dark:bg-violet-400/5">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="mb-1 flex items-center gap-1 text-xs font-medium text-violet-700 dark:text-violet-300"
      >
        <ChevronDown
          className={`size-3 transition-transform ${expanded ? "" : "-rotate-90"}`}
        />
        {t("traceLabels.thinking")}
      </button>
      <div
        className={`prose prose-sm dark:prose-invert max-w-none text-xs opacity-80 [&_p]:my-1 ${!expanded && isLong ? "line-clamp-3" : ""}`}
      >
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          urlTransform={dataUriUrlTransform}
          components={{ a: TraceMarkdownLink }}
        >
          {!expanded && isLong ? content.slice(0, 200) + "..." : content}
        </ReactMarkdown>
      </div>
    </div>
  )
}

function ToolResultContent({ content }: { content: string }) {
  const trimmed = content.trim()
  const looksLikeJson =
    (trimmed.startsWith("{") && trimmed.endsWith("}")) ||
    (trimmed.startsWith("[") && trimmed.endsWith("]"))
  if (looksLikeJson) {
    let formatted: string | null = null
    try {
      formatted = JSON.stringify(JSON.parse(trimmed), null, 2)
    } catch {
      /* not valid JSON, fall through */
    }
    if (formatted !== null) {
      return (
        <pre className="overflow-x-auto rounded bg-muted/50 p-2 font-mono text-xs leading-relaxed dark:bg-muted/30">
          <code>{formatJsonHighlighted(formatted)}</code>
        </pre>
      )
    }
  }
  return (
    <pre className="overflow-x-auto rounded bg-muted/50 p-2 font-mono text-xs leading-relaxed break-words whitespace-pre-wrap dark:bg-muted/30">
      <code>{content}</code>
    </pre>
  )
}

function ChatMessageBlock({ message }: { message: ChatMessage }) {
  const { t } = useTranslation()
  const style = ROLE_STYLES[message.role] ?? {
    bg: "bg-muted/50",
    label: message.role,
  }
  const contentStr = extractMessageText(message.content)
  const reasoningContent =
    typeof message.reasoning_content === "string"
      ? message.reasoning_content
      : null
  // LangChain stores tool_calls in additional_kwargs.tool_calls, standard ChatML uses top-level tool_calls
  const rawToolCalls = Array.isArray(message.tool_calls)
    ? (message.tool_calls as ToolCall[])
    : (() => {
        const ak = message.additional_kwargs as
          | Record<string, unknown>
          | undefined
        return Array.isArray(ak?.tool_calls)
          ? (ak!.tool_calls as ToolCall[])
          : null
      })()
  const toolCalls =
    rawToolCalls && rawToolCalls.length > 0 ? rawToolCalls : null
  const isToolRole = message.role === "tool"
  const toolName = typeof message.name === "string" ? message.name : null
  const toolCallId =
    typeof message.tool_call_id === "string" ? message.tool_call_id : null

  return (
    <div className={`rounded-lg p-3 ${style.bg}`}>
      <div className="mb-1 flex items-center gap-2 text-xs font-medium text-muted-foreground">
        <span>{ROLE_STYLES[message.role] ? t(style.label) : style.label}</span>
        {isToolRole && toolName && (
          <span className="font-mono text-orange-600 dark:text-orange-400">
            {toolName}
          </span>
        )}
      </div>
      <div className="flex flex-col gap-2">
        {reasoningContent && <ThinkingBlock content={reasoningContent} />}
        {traceMediaParts(message.content) ? (
          <TraceMediaContent value={message.content} />
        ) : contentStr ? (
          isToolRole ? (
            <ToolResultContent content={contentStr} />
          ) : (
            <div className="prose prose-sm dark:prose-invert max-w-none text-sm [&_p]:my-1 [&_pre]:my-2">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                urlTransform={dataUriUrlTransform}
                components={{ a: TraceMarkdownLink }}
              >
                {contentStr}
              </ReactMarkdown>
            </div>
          )
        ) : isToolRole ? (
          <div className="text-xs text-muted-foreground italic">
            {toolCallId ? `tool_call_id: ${toolCallId}` : "empty content"}
          </div>
        ) : null}
        {toolCalls && toolCalls.length > 0 && (
          <div className="flex flex-col gap-1.5">
            {toolCalls.map((call, i) => (
              <ToolCallBlock key={call.id ?? i} call={call} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function ChatMessageList({ messages }: { messages: ChatMessage[] }) {
  return (
    <div className="flex flex-col gap-2">
      {messages.map((msg, index) => (
        <ChatMessageBlock key={index} message={msg} />
      ))}
    </div>
  )
}

// ── IOSection ────────────────────────────────────────────────────────────────

const IO_COLLAPSED_MAX_LINES = 3
const IO_COLLAPSED_MAX_CHARS = 200

function collapsedJsonPreview(data: unknown): string {
  if (typeof data !== "object" || data === null) return JSON.stringify(data)
  const entries = Object.entries(data as Record<string, unknown>)
  const preview: Record<string, string> = {}
  for (const [k, v] of entries) {
    if (typeof v === "object" && v !== null) {
      preview[k] = Array.isArray(v) ? `[…${v.length}]` : "{…}"
    } else {
      preview[k] = JSON.stringify(v)
    }
  }
  return JSON.stringify(preview, null, 2)
}

function truncateText(text: string): string {
  const lines = text.split("\n")
  if (lines.length > IO_COLLAPSED_MAX_LINES) {
    return lines.slice(0, IO_COLLAPSED_MAX_LINES).join("\n") + "\n…"
  }
  if (text.length > IO_COLLAPSED_MAX_CHARS) {
    return text.slice(0, IO_COLLAPSED_MAX_CHARS) + "…"
  }
  return text
}

export function IOSection({
  title,
  data: rawData,
  variant,
  viewMode = "formatted",
  collapsed: controlledCollapsed,
  onToggleCollapse,
}: {
  title: string
  data: unknown
  variant: "input" | "output"
  viewMode?: "formatted" | "json"
  collapsed?: boolean
  onToggleCollapse?: () => void
}) {
  const { t } = useTranslation()
  const data =
    typeof rawData === "string"
      ? (parseStructuredTraceJson(rawData) ?? rawData)
      : rawData
  const [internalCollapsed, setInternalCollapsed] = useState(false)
  const isCollapsed = controlledCollapsed ?? internalCollapsed
  const handleToggle =
    onToggleCollapse ?? (() => setInternalCollapsed((v) => !v))

  const mediaOutput = variant === "output" ? mediaOutputValue(data) : null
  const isOutput = variant === "output"
  const isStructuredObject = typeof data === "object" && data !== null
  const rawContent = extractContent(data, variant)
  const content = unescapeContent(rawContent)
  const scriptLang = !isStructuredObject ? detectScriptLanguage(content) : null
  const hasAnsi =
    !isStructuredObject && !scriptLang && containsAnsiCodes(content)
  const markdownContent =
    !isStructuredObject && !scriptLang && !hasAnsi
      ? stripMarkdownFence(content)
      : content

  const jsonText =
    typeof rawData === "string" ? rawData : JSON.stringify(rawData, null, 2)
  const chatMessages =
    viewMode === "formatted" ? extractChatMessages(data) : null
  const additionalInput = chatMessages ? extractAdditionalInput(data) : null

  return (
    <div>
      <div className="mb-1 flex items-center gap-2">
        <h4 className="text-sm font-medium text-foreground">{title}</h4>
        <RawJsonHover data={rawData} />
        {isOutput && <OutputFieldTags data={data} />}
        <div className="flex-1" />
        <button
          type="button"
          onClick={handleToggle}
          className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
          title={
            isCollapsed
              ? t("traceLabels.expandContent")
              : t("traceLabels.collapseContent")
          }
        >
          {isCollapsed ? (
            <ChevronsUpDown className="size-3.5" />
          ) : (
            <ChevronsDownUp className="size-3.5" />
          )}
        </button>
        <CopyButton
          text={
            viewMode === "json"
              ? jsonText
              : isStructuredObject
                ? jsonText
                : content
          }
        />
      </div>
      <TraceDocumentPreviews
        value={data}
        enabled={!isCollapsed && viewMode === "formatted"}
      >
        {isCollapsed ? (
          <pre className="overflow-x-auto rounded-lg bg-muted/50 p-3 font-mono text-xs leading-relaxed dark:bg-muted/30">
            <code className="text-muted-foreground">
              {isStructuredObject
                ? collapsedJsonPreview(data)
                : truncateText(content)}
            </code>
          </pre>
        ) : viewMode === "json" ? (
          <CollapsibleJsonTree data={data} />
        ) : mediaOutput !== null || traceMediaParts(data) ? (
          <TraceMediaContent value={mediaOutput ?? data} />
        ) : chatMessages ? (
          <div className="flex flex-col gap-3">
            <ChatMessageList messages={chatMessages} />
            {additionalInput && (
              <>
                <h4 className="text-sm font-medium text-muted-foreground">
                  {t("traceLabels.additionalInput")}
                </h4>
                <MetadataTree data={additionalInput} />
              </>
            )}
          </div>
        ) : isStructuredObject ? (
          <MetadataTree data={data} />
        ) : hasAnsi ? (
          <div
            className="overflow-x-auto rounded-lg bg-muted/50 p-3 font-mono dark:bg-muted/30"
            style={{
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              fontSize: "12px",
              lineHeight: "1.6",
            }}
            dangerouslySetInnerHTML={{ __html: ansiToHtml(content) }}
          />
        ) : scriptLang ? (
          <pre className="overflow-x-auto rounded-lg bg-muted/50 p-3 font-mono text-xs leading-relaxed dark:bg-muted/30">
            <code className="break-words whitespace-pre-wrap">{content}</code>
          </pre>
        ) : (
          <div
            className={`rounded-lg p-3 text-sm ${isOutput ? "bg-green-500/5 dark:bg-green-400/10" : "bg-blue-500/5 dark:bg-blue-400/10"}`}
          >
            <div className="prose prose-sm dark:prose-invert max-w-none [&_p]:my-1 [&_strong]:font-semibold [&_table]:border-collapse [&_td]:border [&_td]:border-border [&_td]:px-3 [&_td]:py-2 [&_th]:border [&_th]:border-border [&_th]:px-3 [&_th]:py-2">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                urlTransform={dataUriUrlTransform}
                components={{
                  ...markdownMediaComponents,
                  a: TraceMarkdownLink,
                }}
              >
                {markdownContent}
              </ReactMarkdown>
            </div>
          </div>
        )}
      </TraceDocumentPreviews>
    </div>
  )
}

export function DetailSection({
  title,
  children,
  className,
  collapsible = false,
  collapsed: controlledCollapsed,
  onToggleCollapse,
}: {
  title: string
  children: React.ReactNode
  className?: string
  collapsible?: boolean
  collapsed?: boolean
  onToggleCollapse?: () => void
}) {
  const { t } = useTranslation()
  const [internalCollapsed, setInternalCollapsed] = useState(false)
  const isCollapsed = controlledCollapsed ?? internalCollapsed
  const handleToggle =
    onToggleCollapse ?? (() => setInternalCollapsed((v) => !v))

  return (
    <div
      className={`relative rounded-lg border border-border bg-card text-card-foreground ${className ?? ""}`}
    >
      <div className="flex items-center px-3 py-1.5">
        <h4 className="flex-1 text-sm font-medium">{title}</h4>
        {collapsible && (
          <button
            type="button"
            onClick={handleToggle}
            className="rounded p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground"
            title={
              isCollapsed
                ? t("traceLabels.expandContent")
                : t("traceLabels.collapseContent")
            }
          >
            {isCollapsed ? (
              <ChevronsUpDown className="size-3.5" />
            ) : (
              <ChevronsDownUp className="size-3.5" />
            )}
          </button>
        )}
      </div>
      {!isCollapsed && <div className="relative px-3 pb-2">{children}</div>}
    </div>
  )
}
