import { useTranslation } from "react-i18next"
import { PrivacyMarkedText } from "@/labs/privacy/PrivacyMarkedText"
import { privacyPreviewBoundary } from "@/labs/privacy/privacyMarkers"
import { TraceFencedText } from "@/components/traces/shared/TraceCodeSnippet"
import { traceCodeFences } from "@/components/traces/shared/traceCodeFences"
import { traceRecordedTextPart } from "@/components/traces/shared/traceCodeTextParts"
import {
  isRecordedMessageSequence,
  messageContent,
  messageRole,
} from "./spanContent"

const EXCERPT_LENGTH = 220
const FIELD_LIMIT = 3

type AssistantReply = { text: string; multipart: boolean }

/** Keep the two most recent readable replies in recorded order. */
function assistantReplies(messages: readonly unknown[]): AssistantReply[] {
  const replies: AssistantReply[] = []
  for (
    let index = messages.length - 1;
    index >= 0 && replies.length < 2;
    index--
  ) {
    const message = messages[index]
    if (messageRole(message) !== "assistant") continue
    const content = messageContent(message, { includeToolCalls: false })
    if (typeof content === "string") {
      if (content.trim()) replies.push({ text: content, multipart: false })
      continue
    }
    let text = ""
    for (const part of Array.isArray(content) ? content : [content]) {
      const fragment =
        typeof part === "string" ? part : traceRecordedTextPart(part)
      if (!fragment?.trim()) continue
      text = text ? text + "\n" + fragment : fragment
      if (text.length >= EXCERPT_LENGTH) break
    }
    if (text) replies.push({ text, multipart: true })
  }
  return replies.reverse()
}

/** A bounded excerpt of recorded values. The original payload owns full details and copy. */
export function TraceOverviewExcerpt({
  value,
  previewOutput = false,
}: {
  value: unknown
  previewOutput?: boolean
}) {
  const { t } = useTranslation()
  const scalar = (item: unknown): string => {
    if (Array.isArray(item))
      return `${t("spanContent.jsonArray")} · ${item.length}`
    if (item && typeof item === "object")
      return `${t("spanContent.jsonObject")} · ${Object.keys(item).length}`
    return typeof item === "string"
      ? item || t("spanContent.emptyValue")
      : item === undefined
        ? t("traceProcess.unreportedContent")
        : String(item)
  }
  const excerpt = (text: string) => {
    const end = privacyPreviewBoundary(text, EXCERPT_LENGTH)
    return (
      <PrivacyMarkedText
        text={text.slice(0, end) + (end < text.length ? "…" : "")}
      />
    )
  }
  const renderText = (text: string) => (
    <span className="line-clamp-3 leading-6 [overflow-wrap:anywhere] whitespace-pre-wrap">
      {excerpt(text)}
    </span>
  )
  const renderWithCode = (text: string) => {
    const fences = traceCodeFences(text)
    return fences.length ? (
      <TraceFencedText
        value={text}
        fences={fences}
        previewLines={3}
        renderText={renderText}
      />
    ) : (
      renderText(text)
    )
  }

  if (
    previewOutput &&
    Array.isArray(value) &&
    isRecordedMessageSequence(value)
  ) {
    const replies = assistantReplies(value)
    const latest = replies.at(-1)
    if (latest === undefined) return renderText(scalar(value))
    // A short excerpt of separate parts cannot serve as an exact code snippet.
    const latestContent = latest.multipart
      ? renderText(latest.text)
      : renderWithCode(latest.text)
    if (replies.length === 1) return latestContent
    return (
      <div className="space-y-1">
        <span className="line-clamp-1 leading-6 [overflow-wrap:anywhere] whitespace-pre-wrap text-muted-foreground">
          {excerpt(replies[0].text)}
        </span>
        {latestContent}
      </div>
    )
  }
  if (typeof value === "string" && value) return renderWithCode(value)
  if (!value || typeof value !== "object" || Array.isArray(value))
    return renderText(scalar(value))

  const entries = Object.entries(value).slice(0, FIELD_LIMIT)
  const fields = entries.flatMap(([key, item]) => {
    const children =
      item && typeof item === "object" && !Array.isArray(item)
        ? Object.entries(item).slice(0, FIELD_LIMIT)
        : []
    return children.length
      ? children.map(([child, leaf]) => ({
          path: `${key}.${child}`,
          value: leaf,
        }))
      : [{ path: key, value: item }]
  })
  if (!fields.length) return <span>{scalar(value)}</span>
  return (
    <span className="block min-w-0 text-sm leading-6">
      {fields.slice(0, FIELD_LIMIT).map((field) => (
        <span
          key={field.path}
          className="line-clamp-1 [overflow-wrap:anywhere]"
        >
          <span className="mr-2 font-mono text-xs text-muted-foreground">
            {field.path}
          </span>
          {excerpt(scalar(field.value))}
        </span>
      ))}
    </span>
  )
}
