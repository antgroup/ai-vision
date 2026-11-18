import { useId, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { ExternalLink, MessageSquareText, Shield } from "lucide-react"
import { LabsSection } from "@/components/labs/shared/LabsSection"
import { Button } from "@/components/ui/button"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { TraceMediaContent, TraceMediaPartsContent } from "./TraceMedia"
import { TraceMediaSummary } from "./TraceMediaSummary"
import { TraceDocumentPreviews } from "./TraceDocumentPreviews"
import { TraceFencedText } from "./TraceCodeSnippet"
import { traceCodeFences } from "./traceCodeFences"
import {
  traceCodeTextParts,
  traceMediaCodeParts,
  traceRecordedTextPart,
} from "./traceCodeTextParts"
import { traceMediaParts } from "./traceMediaProjection"
import { TraceCopyButton, TraceTextContent } from "./TraceTextContent"
import { TraceRecordControls } from "./TraceRecordControls"
import type { UserInputMessage, UserInputSource } from "./traceUserInput"
import { collectTraceUserInput } from "./traceUserInput"
import type { LangfuseTraceDetail } from "@/agents/types/langfuse"
import { TraceOverviewExcerpt } from "@/components/traces-otel/TraceOverviewExcerpt"
import { hasPrivacyMarker } from "@/labs/privacy/privacyMarkers"

function userInputCodeParts(content: unknown) {
  if (!Array.isArray(content)) return null
  const parts = traceCodeTextParts(content.map(traceRecordedTextPart)).flatMap(
    ({ start, text }) => {
      const value = text ?? content[start]
      return typeof value === "string"
        ? [{ start, value, fences: traceCodeFences(value) }]
        : []
    }
  )
  return parts.some(({ fences }) => fences.length) ? parts : null
}

function UserMessage({
  message,
  primary = false,
  omitMedia = false,
}: {
  message: UserInputMessage
  primary?: boolean
  omitMedia?: boolean
}) {
  const { t } = useTranslation()
  const structured = typeof message.content !== "string"
  const [rawOpen, setRawOpen] = useState(false)
  const media = traceMediaParts(message.content)
  const codeParts = media ? null : userInputCodeParts(message.content)
  const fences = useMemo(
    () => (message.text === null ? [] : traceCodeFences(message.text)),
    [message.text]
  )
  const renderText = (content: string, primaryText = false) => (
    <TraceTextContent
      content={content}
      previewLines={4}
      textClassName="font-sans text-sm leading-relaxed"
      data-testid={primaryText ? "trace-user-input-content" : undefined}
      expandLabel={t("traceUserInput.showMore")}
      collapseLabel={t("traceUserInput.showLess")}
    />
  )
  return (
    <div className="min-w-0 space-y-2">
      <TraceDocumentPreviews value={message.content} />
      {media ? (
        <div data-testid={primary ? "trace-user-input-content" : undefined}>
          {!omitMedia ? (
            <TraceMediaContent value={message.content} />
          ) : !primary ? (
            <TraceMediaPartsContent
              parts={traceMediaCodeParts(media).filter(
                (part) => part.kind !== "media" && part.kind !== "invalid"
              )}
            />
          ) : null}
        </div>
      ) : codeParts ? (
        <div
          className="min-w-0 space-y-2"
          data-testid={primary ? "trace-user-input-content" : undefined}
        >
          {codeParts.map(({ start, value, fences }) => (
            <TraceFencedText
              key={start}
              value={value}
              fences={fences}
              renderText={renderText}
            />
          ))}
        </div>
      ) : message.text !== null && message.text.length > 0 ? (
        fences.length ? (
          <div data-testid={primary ? "trace-user-input-content" : undefined}>
            <TraceFencedText
              value={message.text}
              fences={fences}
              renderText={renderText}
            />
          </div>
        ) : (
          renderText(message.text, primary)
        )
      ) : (
        <p
          className="text-sm text-muted-foreground"
          data-testid={primary ? "trace-user-input-content" : undefined}
        >
          {t(
            message.text === null
              ? "traceUserInput.nonText"
              : "traceUserInput.emptyMessage"
          )}
        </p>
      )}
      {structured && (
        <details
          className="text-xs"
          onToggle={(event) => setRawOpen(event.currentTarget.open)}
        >
          <summary
            className="cursor-pointer rounded-sm text-muted-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            title={t("traceUserInput.copyStructured")}
          >
            {t("traceUserInput.rawContent")}
          </summary>
          {rawOpen && (
            <TraceTextContent
              content={message.copyText}
              previewLines={6}
              className="mt-2 rounded-md bg-muted/50 p-3"
            />
          )}
        </details>
      )}
    </div>
  )
}

function sourceLabel(
  source: UserInputSource,
  number: number,
  fallback: string
): string {
  const text = source.messages.at(-1)?.text
  const preview = text?.trim().replace(/\s+/g, " ") || fallback
  const characters = Array.from(preview)
  return `${number}. ${characters.slice(0, 64).join("")}${characters.length > 64 ? "…" : ""}`
}

/** A display projection of explicit user-role messages, separate from raw trace IO. */
export function TraceUserInput({
  sources,
  masked = false,
  onViewSource,
  compact = false,
  onViewMessage,
}: {
  sources: UserInputSource[]
  masked?: boolean
  onViewSource?: (id: string) => void
  compact?: boolean
  onViewMessage?: (source: UserInputSource, message: UserInputMessage) => void
}) {
  const { t } = useTranslation()
  const sourceId = useId()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [historyLimit, setHistoryLimit] = useState(5)
  const source = masked
    ? undefined
    : (sources.find((item) => item.id === selectedId) ?? sources[0])
  const latest = source?.messages.at(-1)
  const latestMedia = latest ? traceMediaParts(latest.content) : null
  const compactCodeParts =
    compact && latest && !latestMedia
      ? userInputCodeParts(latest.content)
      : null
  const mediaSources = masked
    ? []
    : sources
        .map((item) => ({
          id: item.id,
          name: item.name,
          parts: item.messages.flatMap(
            (message) => traceMediaParts(message.content) ?? []
          ),
        }))
        .filter((item) =>
          item.parts.some(
            (part) => part.kind === "media" || part.kind === "invalid"
          )
        )
  return (
    <LabsSection
      icon={MessageSquareText}
      feature="user-input"
      variant="summary"
      beta
      title={t("traceUserInput.title")}
      data-testid="trace-user-input"
      aria-label={t("traceUserInput.title")}
    >
      {masked ? (
        <p className="text-sm text-muted-foreground">
          {t("traceUserInput.masked")}
        </p>
      ) : !source || !latest ? (
        <p className="text-sm text-muted-foreground">
          {t("traceUserInput.empty")}
        </p>
      ) : (
        <>
          <div
            className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs"
            data-user-input-source
          >
            {sources.length > 1 && (
              <>
                <select
                  id={sourceId}
                  aria-label={t("traceUserInput.source")}
                  value={source.id}
                  onChange={(event) => {
                    setSelectedId(event.target.value)
                    setHistoryLimit(5)
                  }}
                  className="h-7 max-w-full min-w-0 flex-1 rounded-md border bg-background px-2 text-xs focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {sources.map((item, index) => (
                    <option key={item.id} value={item.id}>
                      {sourceLabel(
                        item,
                        index + 1,
                        t(
                          item.messages.at(-1)?.text === null
                            ? compact
                              ? "traceProcess.structuredMessage"
                              : "traceUserInput.nonText"
                            : "traceUserInput.emptyMessage"
                        )
                      )}
                    </option>
                  ))}
                </select>
              </>
            )}
            {source.messages.length > 1 && (
              <span className="shrink-0 text-muted-foreground">
                {t("traceUserInput.messageCount", {
                  count: source.messages.length,
                })}
              </span>
            )}
            {source.messages.some((message) =>
              hasPrivacyMarker(message.copyText)
            ) && (
              <Popover key={source.id}>
                <PopoverTrigger asChild>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="size-7 shrink-0 p-0 text-muted-foreground"
                    aria-label={t("labs.privacy.markerHelp")}
                    title={t("labs.privacy.markerHelp")}
                    data-user-input-privacy-help
                  >
                    <Shield aria-hidden="true" className="size-3.5" />
                  </Button>
                </PopoverTrigger>
                <PopoverContent
                  align="start"
                  className="max-w-[calc(100vw_-_2rem)] rounded-md p-3 text-xs leading-5"
                  data-testid="user-input-privacy-note"
                >
                  <p>{t("labs.privacy.userMessageHelp")}</p>
                </PopoverContent>
              </Popover>
            )}
            <TraceCopyButton
              key={`${source.id}/${latest.index}`}
              value={latest.copyText}
              label={t("traceUserInput.copyLastMessage")}
            />
            {source.id && (onViewSource || onViewMessage) && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 shrink-0 gap-1 px-2 text-xs"
                onClick={() =>
                  onViewMessage
                    ? onViewMessage(source, latest)
                    : onViewSource?.(source.id)
                }
              >
                <ExternalLink className="size-3" />
                {t(
                  onViewMessage
                    ? "traceProcess.viewMessage"
                    : "traceUserInput.viewSource"
                )}
              </Button>
            )}
          </div>
          {mediaSources.length > 0 && (
            <div
              className="min-w-0 space-y-2 text-sm"
              data-user-input-media-sources
            >
              <TraceMediaSummary
                sources={mediaSources}
                primaryParts={latestMedia ?? undefined}
                onViewSource={onViewSource}
              />
            </div>
          )}
          {latestMedia ? (
            <UserMessage
              key={`${source.id}/${latest.index}`}
              message={latest}
              primary
              omitMedia
            />
          ) : compact ? (
            <div
              data-testid="trace-user-input-content"
              className="flex min-w-0 items-start gap-2 text-sm"
            >
              <span className="mt-0.5 shrink-0 font-mono text-xs font-semibold">
                #{latest.index + 1}
              </span>
              <div className="min-w-0 space-y-2">
                <TraceDocumentPreviews value={latest.content} />
                {compactCodeParts ? (
                  compactCodeParts.map(({ start, value }) => (
                    <TraceOverviewExcerpt key={start} value={value} />
                  ))
                ) : (
                  <TraceOverviewExcerpt value={latest.text ?? latest.content} />
                )}
              </div>
            </div>
          ) : (
            <UserMessage
              key={`${source.id}/${latest.index}`}
              message={latest}
              primary
            />
          )}
          {!compact && source.messages.length > 1 && (
            <details key={source.id} className="border-t pt-2">
              <summary className="cursor-pointer rounded-sm text-xs text-muted-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
                {t("traceUserInput.otherMessages")} (
                {source.messages.length - 1})
              </summary>
              <div className="mt-2 space-y-2">
                {source.messages
                  .slice(0, -1)
                  .slice(0, historyLimit)
                  .map((message, index) => (
                    <div
                      key={message.index}
                      data-user-input-history-message
                      className="space-y-1 rounded-md bg-background p-2"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <p className="text-xs font-medium">
                          {t("traceUserInput.messageNumber", {
                            number: index + 1,
                          })}
                        </p>
                        <TraceCopyButton
                          value={message.copyText}
                          label={t("traceUserInput.copyMessage", {
                            number: index + 1,
                          })}
                        />
                      </div>
                      <UserMessage message={message} omitMedia />
                    </div>
                  ))}
                <TraceRecordControls
                  shown={Math.min(historyLimit, source.messages.length - 1)}
                  total={source.messages.length - 1}
                  onMore={() => setHistoryLimit(historyLimit + 5)}
                  onAll={() => setHistoryLimit(source.messages.length - 1)}
                  onCollapse={() => setHistoryLimit(5)}
                />
              </div>
            </details>
          )}
        </>
      )}
    </LabsSection>
  )
}

/** Both observation-based trace panels share the same masking and source rules. */
export function ObservationTraceUserInput({
  trace,
  masked,
  onViewSource,
}: {
  trace: LangfuseTraceDetail
  masked: boolean
  onViewSource: (id: string) => void
}) {
  const { t } = useTranslation()
  const sources = useMemo(() => {
    // Do not inspect child observations when the parent view is desensitized.
    if (masked) return []
    return collectTraceUserInput([
      {
        id: "",
        name: t("traceUserInput.traceInput"),
        input: trace.input,
        isRoot: true,
      },
      ...(trace.observations ?? []).map((observation) => {
        const metadata = observation.metadata
        const record =
          metadata && typeof metadata === "object" && !Array.isArray(metadata)
            ? (metadata as Record<string, unknown>)
            : undefined
        const nested = record?.attributes
        const attributes =
          nested && typeof nested === "object" && !Array.isArray(nested)
            ? (nested as Record<string, unknown>)
            : record
        return {
          id: observation.id,
          name: observation.name || observation.id,
          input: observation.input,
          attributes,
          startTime: observation.startTime,
          isTool: ["TOOL", "RETRIEVER", "EMBEDDING"].includes(
            observation.type.toUpperCase()
          ),
        }
      }),
    ])
  }, [trace, masked, t])
  return (
    <TraceUserInput
      key={`${trace.id}/${masked}`}
      sources={sources}
      masked={masked}
      onViewSource={onViewSource}
    />
  )
}
