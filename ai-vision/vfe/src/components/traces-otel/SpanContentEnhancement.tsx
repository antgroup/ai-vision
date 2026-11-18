import {
  useContext,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react"
import { useTranslation } from "react-i18next"
import { ChevronDown, FileDiff, MessageSquareText, Wrench } from "lucide-react"
import { Button } from "@/components/ui/button"
import { TraceRecordControls } from "@/components/traces/shared/TraceRecordControls"
import { TraceDocumentPreviews } from "@/components/traces/shared/TraceDocumentPreviews"
import { TraceCopyButton } from "@/components/traces/shared/TraceTextContent"
import { LabsSection } from "@/components/labs/shared/LabsSection"
import { traceMediaParts } from "@/components/traces/shared/traceMediaProjection"
import {
  traceCodeTextParts,
  traceRecordedTextPart,
} from "@/components/traces/shared/traceCodeTextParts"
import { SpanTypedContent } from "./SpanTypedContent"
import { FileChangeView } from "./FileChangeView"
import { TraceBadge } from "./shared/TraceBadge"
import { TraceEnhancedVisibility } from "@/components/traces/shared/traceEnhancedVisibility"
import { PowerContextMemoryContent } from "./PowerContextMemoryContent"
import { parsePowerContextContent } from "./powerContextContent"
import {
  contentPreview,
  messageContent,
  messageRole,
  resolveToolOutcome,
  type ContentField,
  type ContentMessage,
  type SpanContentModel,
} from "./spanContent"

export const SpanContentExcerpt = SpanTypedContent

/** Read known message text blocks as prose; retain every other block and raw copy. */
export function ConversationContent({
  value,
  renderFrame,
  previewLines = 8,
}: {
  value: unknown
  renderFrame: (controls: ReactNode, content: ReactNode) => ReactNode
  previewLines?: number
}) {
  const { t } = useTranslation()
  const [visible, setVisible] = useState(5)
  const parts = useMemo(
    () =>
      Array.isArray(value)
        ? traceCodeTextParts(value.map(traceRecordedTextPart))
        : [],
    [value]
  )
  if (!Array.isArray(value) || value.length === 0)
    return (
      <SpanContentExcerpt
        value={value}
        showCopy={false}
        previewLines={previewLines}
        treeLayout="outline"
        renderFrame={renderFrame}
      />
    )
  const projected = parts.filter(({ start }) => start < visible)
  const shown = projected.at(-1)?.end ?? 0
  const blocks = (
    <div className="min-w-0 space-y-2">
      {projected.map(({ start, end, text }) => {
        const details =
          text !== null &&
          value.slice(start, end).map((block, index) => {
            const textKey = typeof block.text === "string" ? "text" : "content"
            const fields = Object.entries(block)
              .filter(([key]) => key !== "type" && key !== textKey)
              .map(([key, value]) => ({ key, value }))
            if (!fields.length) return null
            return (
              <OtherFields
                key={index}
                treeLayout="outline"
                fields={fields}
                label={t("spanContent.blockMetadata", { count: fields.length })}
              />
            )
          })
        return (
          <div key={start} className="min-w-0 space-y-2">
            <SpanContentExcerpt
              value={text ?? value[start]}
              showCopy={false}
              showDocumentPreviews={value.length === 1}
              previewLines={previewLines}
              treeLayout="outline"
              renderFrame={
                value.length === 1
                  ? (controls, content) =>
                      renderFrame(
                        controls,
                        <>
                          {content}
                          {details}
                        </>
                      )
                  : undefined
              }
            />
            {value.length > 1 && details}
          </div>
        )
      })}
      <TraceRecordControls
        shown={shown}
        total={value.length}
        onMore={() => setVisible(shown + 5)}
        onAll={() => setVisible(value.length)}
        onCollapse={() => setVisible(5)}
      />
    </div>
  )
  return value.length === 1
    ? blocks
    : renderFrame(
        null,
        <TraceDocumentPreviews value={value}>{blocks}</TraceDocumentPreviews>
      )
}

function Message({
  message,
  heading,
  description,
  initiallyExpanded = false,
  children,
}: {
  message: ContentMessage
  heading?: string
  description?: string
  initiallyExpanded?: boolean
  children?: ReactNode
}) {
  const { t } = useTranslation()
  const active = useContext(TraceEnhancedVisibility) !== false
  const [fullMessage, setFullMessage] = useState(false)
  const fullMessageId = useId()
  const contentId = useId()
  const [expanded, setExpanded] = useState(!heading || initiallyExpanded)
  const disclosureRef = useRef<HTMLButtonElement>(null)
  const restoreDisclosureFocus = useRef(initiallyExpanded)
  useLayoutEffect(() => {
    if (restoreDisclosureFocus.current) {
      disclosureRef.current?.focus({ preventScroll: true })
      restoreDisclosureFocus.current = false
    }
  }, [expanded])
  const role = messageRole(message.value)
  const memory =
    active && role === "system"
      ? parsePowerContextContent(messageContent(message.value))
      : null
  const roleLabel =
    role && ["system", "developer", "user", "assistant", "tool"].includes(role)
      ? t(`traceLabels.role.${role}`)
      : role
  const identity = (
    <>
      {heading && <span className="font-semibold">{heading}</span>}
      <span
        data-message-number
        className="inline-flex min-w-9 shrink-0 items-center justify-center rounded-md border border-border bg-background px-2 py-1 font-mono text-sm font-semibold text-foreground tabular-nums"
        aria-label={t("spanContent.messageIndex", {
          index: message.index + 1,
        })}
      >
        #{message.index + 1}
      </span>
      {roleLabel && (
        <span className="font-medium text-foreground">{roleLabel}</span>
      )}
    </>
  )
  const header = (controls: ReactNode = null) => (
    <div
      data-message-header
      className={`flex flex-wrap items-center justify-between gap-x-3 gap-y-2 bg-muted/30 px-3 py-2 ${expanded ? "border-b border-border" : ""}`}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-2 text-sm">
        {heading ? (
          <button
            type="button"
            data-message-disclosure
            ref={disclosureRef}
            aria-expanded={expanded}
            aria-controls={contentId}
            aria-label={t(
              expanded ? "spanContent.collapseIO" : "spanContent.expandIO",
              { direction: heading }
            )}
            onClick={() => {
              restoreDisclosureFocus.current = true
              setExpanded(!expanded)
              setFullMessage(false)
            }}
            className="flex min-w-0 items-center gap-2 rounded-sm hover:text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          >
            <ChevronDown
              aria-hidden
              className={`size-3.5 shrink-0 ${expanded ? "" : "-rotate-90"}`}
            />
            {identity}
          </button>
        ) : (
          identity
        )}
        {description && (
          <span className="text-xs text-muted-foreground">{description}</span>
        )}
      </div>
      <div className="flex max-w-full flex-wrap items-center gap-1">
        {controls}
        <TraceCopyButton
          value={contentPreview(messageContent(message.value), Infinity).text}
          label={t("spanContent.copyContent")}
          className="size-6 shrink-0"
        />
        {expanded && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-6 gap-1 px-1 text-xs text-muted-foreground"
            aria-controls={fullMessageId}
            aria-expanded={fullMessage}
            aria-label={`${t("spanContent.fullMessage")}: ${t("spanContent.messageIndex", { index: message.index + 1 })}`}
            onClick={() => setFullMessage(!fullMessage)}
          >
            {t("spanContent.fullMessage")}
            <ChevronDown
              className={`size-3 ${fullMessage ? "rotate-180" : ""}`}
              aria-hidden="true"
            />
          </Button>
        )}
      </div>
    </div>
  )
  const renderFrame = (controls: ReactNode, content: ReactNode) => (
    <>
      {header(controls)}
      <div id={contentId} className="min-w-0 p-3">
        {content}
      </div>
    </>
  )
  return (
    <>
      <div
        className="min-w-0 overflow-hidden rounded-lg border border-border bg-[var(--color-bg-page)]"
        data-content-message-index={message.index}
      >
        {expanded ? (
          memory ? (
            renderFrame(null, <PowerContextMemoryContent content={memory} />)
          ) : (
            <ConversationContent
              value={messageContent(message.value)}
              renderFrame={renderFrame}
            />
          )
        ) : (
          header()
        )}
        {expanded && fullMessage && (
          <div
            id={fullMessageId}
            className="min-w-0 border-t border-border bg-muted/30 p-3"
          >
            <SpanContentExcerpt value={message.value} treeLayout="outline" />
          </div>
        )}
      </div>
      {expanded && children && <div className="mt-2 space-y-2">{children}</div>}
    </>
  )
}

function InjectedMemoryMessages({ messages }: { messages: ContentMessage[] }) {
  const [visible, setVisible] = useState(5)
  return (
    <div className="space-y-3" data-powercontext-messages>
      {messages.slice(0, visible).map((message) => (
        <Message key={message.index} message={message} />
      ))}
      <TraceRecordControls
        shown={Math.min(visible, messages.length)}
        total={messages.length}
        onMore={() => setVisible((count) => count + 5)}
        onAll={() => setVisible(messages.length)}
        onCollapse={() => setVisible(5)}
      />
    </div>
  )
}

function History({
  messages,
  label,
}: {
  messages: ContentMessage[]
  label: string
}) {
  const [expanded, setExpanded] = useState(false)
  const [visible, setVisible] = useState(5)
  if (!messages.length) return null
  return (
    <details
      className="rounded-md border border-border bg-muted/20 p-3 text-xs"
      data-content-history
      onToggle={(event) => setExpanded(event.currentTarget.open)}
    >
      <summary className="cursor-pointer rounded-sm font-medium text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
        {label}
      </summary>
      {expanded && (
        <div className="mt-3 space-y-4">
          {messages.slice(0, visible).map((message) => (
            <Message key={message.index} message={message} />
          ))}
          <TraceRecordControls
            shown={Math.min(visible, messages.length)}
            total={messages.length}
            onMore={() => setVisible((value) => value + 5)}
            onAll={() => setVisible(messages.length)}
            onCollapse={() => setVisible(5)}
          />
        </div>
      )}
    </details>
  )
}

function OtherFields({
  fields,
  label,
  treeLayout,
}: {
  fields: ContentField[]
  label: string
  treeLayout?: "table" | "outline"
}) {
  if (!fields.length) return null
  return (
    <details className="text-xs" data-content-other-fields>
      <summary className="cursor-pointer rounded-sm text-muted-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
        {label}
      </summary>
      <dl className="mt-2 space-y-2">
        {fields.map((field) => (
          <div key={field.key} className="min-w-0">
            <dt className="font-mono text-muted-foreground">{field.key}</dt>
            <dd>
              <SpanContentExcerpt
                value={field.value}
                field={field.key}
                treeLayout={treeLayout}
              />
            </dd>
          </div>
        ))}
      </dl>
    </details>
  )
}

function textResult(value: unknown, allowUntyped = false) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null
  const block = value as Record<string, unknown>
  return typeof block.text === "string" &&
    (block.type === "text" || (allowUntyped && block.type === undefined))
    ? block
    : null
}

/** Known text envelopes expose their body without reinterpreting other blocks. */
function ToolResultContent({
  value,
  field,
}: {
  value: unknown
  field?: string
}) {
  const { t } = useTranslation()
  const [visible, setVisible] = useState(5)
  const text = textResult(value, field === undefined)
  if (text) {
    const metadata = Object.entries(text)
      .filter(([key]) => key !== "text")
      .map(([key, value]) => ({ key, value }))
    return (
      <div className="min-w-0 space-y-2" data-tool-result-text>
        <SpanContentExcerpt value={text.text} />
        <OtherFields
          fields={metadata}
          label={t(
            text.type === "text"
              ? "spanContent.blockMetadata"
              : "spanContent.otherResults",
            { count: metadata.length }
          )}
        />
      </div>
    )
  }
  if (
    field === "content" &&
    Array.isArray(value) &&
    value.some((block) => textResult(block) !== null)
  ) {
    return (
      <div className="space-y-3" data-tool-result-blocks>
        {value.slice(0, visible).map((block, index) => (
          <div
            key={index}
            className={`min-w-0 ${index > 0 ? "border-t border-border pt-3" : ""}`}
            data-tool-result-block={index}
          >
            {textResult(block) ? (
              <ToolResultContent value={block} />
            ) : (
              <SpanContentExcerpt value={block} />
            )}
          </div>
        ))}
        <TraceRecordControls
          shown={Math.min(visible, value.length)}
          total={value.length}
          onMore={() => setVisible((count) => count + 5)}
          onAll={() => setVisible(value.length)}
          onCollapse={() => setVisible(5)}
        />
      </div>
    )
  }
  return <SpanContentExcerpt value={value} field={field} />
}

export function ToolResultBody({
  model,
}: {
  model: Extract<SpanContentModel, { kind: "tool" }>
}) {
  const { t } = useTranslation()
  const hasTextResult = textResult(model.output, true) !== null
  const visibleResults = model.resultFields.filter(
    (field) =>
      field.key !== "status" ||
      model.outcome === "unknown" ||
      resolveToolOutcome({ status: field.value }) !== model.outcome
  )
  return (
    <div className="space-y-3">
      {traceMediaParts(model.output) ? (
        <SpanContentExcerpt value={model.output} />
      ) : hasTextResult ? (
        <ToolResultContent value={model.output} />
      ) : visibleResults.length ? (
        <dl className="space-y-2">
          {visibleResults.map((field) => (
            <div key={field.key} className="min-w-0">
              <dt
                className={
                  visibleResults.length === 1 && field.key === "content"
                    ? "sr-only"
                    : "mb-0.5 font-mono text-xs text-muted-foreground"
                }
              >
                {field.key}
              </dt>
              <dd>
                <ToolResultContent value={field.value} field={field.key} />
              </dd>
            </div>
          ))}
        </dl>
      ) : model.resultFields.length === 0 ? (
        <ToolResultContent value={model.output} />
      ) : null}
      {!hasTextResult && model.resultFields.length > 0 && (
        <OtherFields
          fields={model.otherResults}
          label={t("spanContent.otherResults", {
            count: model.otherResults.length,
          })}
        />
      )}
    </div>
  )
}

export function SpanContentEnhancement({
  model,
  embedded = false,
  resultView,
  argumentLabel,
  argumentNote,
  focusIO,
}: {
  model: SpanContentModel
  embedded?: boolean
  resultView?: ReactNode
  argumentLabel?: (key: string) => string
  argumentNote?: ReactNode
  focusIO?: "input" | "output"
}) {
  const { t } = useTranslation()
  const active = useContext(TraceEnhancedVisibility) !== false
  const isConversation = model.kind === "conversation"
  const memoryMessages =
    active && model.kind === "conversation"
      ? model.inputHistory.filter(
          (message) =>
            messageRole(message.value) === "system" &&
            parsePowerContextContent(messageContent(message.value)) !== null
        )
      : []
  const otherInputHistory =
    model.kind === "conversation"
      ? model.inputHistory.filter(
          (message) => !memoryMessages.includes(message)
        )
      : []
  const hasChanges = model.kind === "tool" && model.changes.length > 0
  const content = (
    <>
      {model.kind === "conversation" ? (
        <>
          <section
            className="min-w-0"
            data-conversation-io="input"
            aria-label={t("nativeSpanUsage.input")}
          >
            <Message
              message={model.user}
              initiallyExpanded={focusIO === "input"}
              heading={t("nativeSpanUsage.input")}
              description={t("spanContent.lastUser")}
            >
              {(memoryMessages.length > 0 || otherInputHistory.length > 0) && (
                <>
                  {memoryMessages.length > 0 && (
                    <InjectedMemoryMessages messages={memoryMessages} />
                  )}
                  <History
                    messages={otherInputHistory}
                    label={t("spanContent.inputHistory", {
                      count: otherInputHistory.length,
                    })}
                  />
                </>
              )}
            </Message>
          </section>
          <section
            className="min-w-0"
            data-conversation-io="output"
            aria-label={t("nativeSpanUsage.output")}
          >
            {model.latestOutput ? (
              <Message
                message={model.latestOutput}
                initiallyExpanded={focusIO === "output"}
                heading={t("nativeSpanUsage.output")}
                description={t("spanContent.latestOutput")}
              >
                {model.outputHistory.length > 0 && (
                  <History
                    messages={model.outputHistory}
                    label={t("spanContent.outputHistory", {
                      count: model.outputHistory.length,
                    })}
                  />
                )}
              </Message>
            ) : (
              <p className="rounded-lg border border-border px-3 py-2 text-sm text-muted-foreground">
                <span className="mr-3 font-semibold text-foreground">
                  {t("nativeSpanUsage.output")}
                </span>
                {t("traceLabels.ioState.unknown")}
              </p>
            )}
          </section>
        </>
      ) : (
        <>
          {model.input !== undefined && argumentNote}
          {model.input !== undefined &&
            (!hasChanges || model.fields.length > 0) && (
              <section className="@container min-w-0 space-y-2">
                <h4 className="text-xs font-medium">
                  {t("spanContent.arguments")}
                </h4>
                {model.fields.length ? (
                  <dl className="space-y-2">
                    {model.fields.map((field) => (
                      <div
                        key={field.key}
                        className="grid min-w-0 grid-cols-1 gap-x-3 gap-y-0.5 @sm:grid-cols-[minmax(5rem,8rem)_minmax(0,1fr)]"
                      >
                        <dt
                          title={field.key}
                          className={`${argumentLabel ? "" : "font-mono"}text-xs [overflow-wrap:anywhere] text-muted-foreground`}
                        >
                          {argumentLabel?.(field.key) ?? field.key}
                        </dt>
                        <dd className="min-w-0">
                          <SpanContentExcerpt
                            value={field.value}
                            field={field.key}
                            compact
                          />
                        </dd>
                      </div>
                    ))}
                  </dl>
                ) : (
                  !hasChanges && (
                    <SpanContentExcerpt
                      value={model.input}
                      compact
                      format={
                        typeof model.input === "string" ? "code" : undefined
                      }
                    />
                  )
                )}
              </section>
            )}
          {(model.fields.length > 0 || hasChanges) && (
            <OtherFields
              fields={model.otherArguments}
              label={t("spanContent.otherArguments", {
                count: model.otherArguments.length,
              })}
            />
          )}
          {model.changes.map((change, index) => (
            <FileChangeView key={index} change={change} />
          ))}
          {model.output !== undefined &&
            (resultView ?? (
              <section
                className="min-w-0 overflow-hidden rounded-lg border border-border bg-[var(--color-bg-page)]"
                data-content-result-outcome={model.outcome}
              >
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border bg-[var(--color-bg-labs)] px-3 py-2">
                  <h4 className="text-xs font-medium">
                    {t("spanContent.result")}
                  </h4>
                  <TraceBadge
                    tone={
                      model.outcome === "error"
                        ? "error"
                        : model.outcome === "ok"
                          ? "success"
                          : "default"
                    }
                  >
                    {t(
                      model.outcome === "error"
                        ? "spanContent.statusError"
                        : model.outcome === "ok"
                          ? "spanContent.statusOk"
                          : "spanContent.statusUnknown"
                    )}
                  </TraceBadge>
                </div>
                <div className="min-w-0 p-3">
                  <ToolResultBody model={model} />
                </div>
              </section>
            ))}
        </>
      )}
    </>
  )
  if (embedded)
    return (
      <div
        className="space-y-3 border-t border-border pt-3"
        data-testid="span-content-enhancement"
        data-content-kind={model.kind}
      >
        {content}
      </div>
    )
  return (
    <LabsSection
      feature="span-content"
      variant="detail"
      icon={isConversation ? MessageSquareText : hasChanges ? FileDiff : Wrench}
      title={t(
        isConversation
          ? "spanContent.conversationTitle"
          : hasChanges
            ? "spanContent.changesTitle"
            : "spanContent.toolTitle"
      )}
      description={t(
        isConversation
          ? "spanContent.conversationDescription"
          : "spanContent.toolDescription"
      )}
      data-testid="span-content-enhancement"
      data-content-kind={model.kind}
    >
      {content}
    </LabsSection>
  )
}
