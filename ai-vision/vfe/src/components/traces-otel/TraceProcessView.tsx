import { useLayoutEffect, useRef, useState, type ReactNode } from "react"
import { useTranslation } from "react-i18next"
import {
  ArrowDown,
  ArrowUpRight,
  Bot,
  MessageSquare,
  Wrench,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { ConversationContent } from "./SpanContentEnhancement"
import { SpanTypedContent } from "./SpanTypedContent"
import { contentPreview } from "./spanContent"
import { TraceCopyButton } from "@/components/traces/shared/TraceTextContent"
import { formatDurationMs } from "./format"
import type { TraceProcessLocation } from "./traceProcessNavigation"
import { ModelBadge } from "./shared/TraceBadge"
import { ModelCallRoleBadge } from "./ModelCallRoleBadge"
import { TraceRecordControls } from "@/components/traces/shared/TraceRecordControls"
import { PrivacyMarkedText } from "@/labs/privacy/PrivacyMarkedText"
import { privacyPreviewBoundary } from "@/labs/privacy/privacyMarkers"
import { FieldOmissions } from "@/labs/privacy/ReportedFieldOmissions"
import type {
  TraceProcessEntry,
  TraceProcessMessage,
  TraceProcessModel,
  TraceProcessSource,
} from "./traceProcess"

function ProcessValue({
  value,
  message = false,
  renderFrame,
}: {
  value: unknown
  message?: boolean
  renderFrame?: (controls: ReactNode, content: ReactNode) => ReactNode
}) {
  const { t } = useTranslation()
  if (value === undefined) {
    const content = (
      <p className="text-xs text-muted-foreground">
        {t("traceProcess.unreportedContent")}
      </p>
    )
    return renderFrame ? renderFrame(null, content) : content
  }
  if (message)
    return (
      <ConversationContent
        value={value}
        previewLines={3}
        renderFrame={
          renderFrame ??
          ((controls, content) => (
            <>
              {controls}
              {content}
            </>
          ))
        }
      />
    )
  return (
    <SpanTypedContent
      value={value}
      treeLayout="outline"
      previewLines={3}
      renderFrame={renderFrame}
    />
  )
}

function Fold({
  label,
  children,
  className,
  revealKey,
  ...props
}: {
  label: ReactNode
  children: ReactNode
  className?: string
  revealKey?: string
  "data-process-payload"?: string
  "data-process-context"?: boolean
  "data-process-sources"?: boolean
  "data-process-unlinked-tools"?: boolean
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDetailsElement>(null)
  useLayoutEffect(() => {
    if (revealKey && ref.current) ref.current.open = true
  }, [revealKey])
  return (
    <details
      ref={ref}
      className={cn("min-w-0 text-xs", className)}
      onToggle={(event) => setOpen(event.currentTarget.open)}
      {...props}
    >
      <summary className="cursor-pointer rounded-sm text-muted-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
        {label}
      </summary>
      {open && <div className="mt-3 min-w-0">{children}</div>}
    </details>
  )
}

function SourceButton({
  spanId,
  label,
  onSelectSpan,
}: {
  spanId: string
  label: string
  onSelectSpan: (id: string) => void
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className="h-7 max-w-full gap-1 px-2 text-xs"
      data-process-source={spanId}
      onClick={() => onSelectSpan(spanId)}
    >
      <span className="min-w-0 [overflow-wrap:anywhere] whitespace-normal">
        {label}
      </span>
      <ArrowUpRight className="size-3 shrink-0" aria-hidden />
    </Button>
  )
}

function Sources({
  sources,
  callId,
  onSelectSpan,
}: {
  sources: TraceProcessSource[]
  callId?: string
  onSelectSpan: (id: string) => void
}) {
  const { t } = useTranslation()
  if (!sources.length && !callId) return null
  return (
    <Fold label={t("traceProcess.sources")} data-process-sources>
      <p className="mb-2 text-xs text-muted-foreground">
        {t("traceProcess.sourcesHint")}
      </p>
      {callId && (
        <p className="mb-2 font-mono text-xs [overflow-wrap:anywhere] break-words">
          <span className="mr-2 font-sans text-muted-foreground">
            {t("traceProcess.callId")}
          </span>
          {callId}
        </p>
      )}
      <ul className="space-y-2">
        {sources.map((source, index) => (
          <li
            key={`${source.spanId}-${source.field}-${source.messageIndex ?? ""}-${index}`}
            className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1"
          >
            <span className="text-muted-foreground">
              {t(`traceProcess.field.${source.field}`)}
              {source.messageIndex !== undefined &&
                ` · ${t("spanContent.messageIndex", { index: source.messageIndex + 1 })}`}
            </span>
            {source.attributeKey && (
              <code className="min-w-0 [overflow-wrap:anywhere] break-words">
                {source.attributeKey}
              </code>
            )}
            <code className="min-w-0 [overflow-wrap:anywhere] break-words">
              {source.spanId}
            </code>
            <SourceButton
              spanId={source.spanId}
              label={t("traceProcess.viewCall")}
              onSelectSpan={onSelectSpan}
            />
          </li>
        ))}
      </ul>
    </Fold>
  )
}

function Role({ role }: { role?: string }) {
  const { t } = useTranslation()
  const known = ["system", "developer", "user", "assistant", "tool"]
  return (
    <span className="text-xs font-medium">
      {role && known.includes(role)
        ? t(`traceLabels.role.${role}`)
        : role || t("traceProcess.message")}
    </span>
  )
}

function ContextMessage({
  message,
  onSelectSpan,
}: {
  message: TraceProcessMessage
  onSelectSpan: (id: string) => void
}) {
  const { t } = useTranslation()
  return (
    <div
      className="min-w-0 space-y-2 border-l border-border pl-3"
      data-process-context-kind={message.kind ?? "message"}
    >
      <div className="flex flex-wrap items-center justify-between gap-1">
        {message.kind === "tool-definition" ? (
          <span className="text-xs font-medium">
            {t("modelContext.processDefinition")}
            {message.definitionName && (
              <>
                : <span className="font-mono">{message.definitionName}</span>
              </>
            )}
          </span>
        ) : (
          <Role role={message.role} />
        )}
        <SourceButton
          spanId={message.source.spanId}
          label={t("traceProcess.viewCall")}
          onSelectSpan={onSelectSpan}
        />
      </div>
      <ProcessValue value={message.content} message />
    </div>
  )
}

function argumentPreview(value: unknown) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null
  const argumentsObject = value as Record<string, unknown>
  for (const key of [
    "command",
    "cmd",
    "path",
    "file_path",
    "query",
    "pattern",
  ]) {
    const argument = argumentsObject[key]
    if (typeof argument !== "string" || !argument.trim()) continue
    const text = argument.trim().replace(/\s+/g, " ")
    const previewEnd = privacyPreviewBoundary(text, 144)
    return {
      key,
      text: text.length > previewEnd ? text.slice(0, previewEnd) + "…" : text,
    }
  }
  return null
}

function entryMatches(entry: TraceProcessEntry, ids?: ReadonlySet<string>) {
  return (
    !!ids &&
    [
      entry.spanId,
      entry.linkedToolSpanId,
      ...entry.sources.map((source) => source.spanId),
    ].some((id) => id !== undefined && ids.has(id))
  )
}

function ProcessEntry({
  entry,
  index,
  matched,
  focused,
  onSelectSpan,
}: {
  entry: TraceProcessEntry
  index: number
  matched: boolean
  focused: boolean
  onSelectSpan: (id: string) => void
}) {
  const { t } = useTranslation()
  const tool = entry.kind === "tool"
  const user = !tool && entry.role === "user"
  const Icon = tool ? Wrench : entry.role === "user" ? MessageSquare : Bot
  const outcome = entry.outcome ?? "unknown"
  const preview = tool ? argumentPreview(entry.arguments) : null
  const primarySource = entry.sources[0]
  const header = (controls: ReactNode = null) => (
    <div className="mb-2 flex min-w-0 flex-wrap items-center justify-between gap-x-2 gap-y-1">
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <span
          data-process-step
          className="rounded-md border border-border bg-background px-2 py-0.5 text-xs font-semibold tabular-nums"
        >
          {t("traceProcess.step", { index: index + 1 })}
        </span>
        <Icon
          className={cn(
            "size-3.5 shrink-0",
            tool
              ? "text-chart-3"
              : user
                ? "text-chart-1"
                : "text-muted-foreground"
          )}
          aria-hidden
        />
        {tool ? (
          <h3 className="min-w-0 font-mono text-xs font-medium [overflow-wrap:anywhere] break-words">
            {entry.toolName || t("traceProcess.tool")}
          </h3>
        ) : (
          <Role role={entry.role} />
        )}
        {primarySource?.messageIndex !== undefined && (
          <span
            className="font-mono text-xs font-medium"
            data-process-message-number
          >
            {t(`traceProcess.field.${primarySource.field}`)} #
            {primarySource.messageIndex + 1}
          </span>
        )}
        {tool &&
          entry.durationMs !== null &&
          entry.durationMs !== undefined && (
            <span className="text-xs text-muted-foreground tabular-nums">
              {formatDurationMs(entry.durationMs)}
            </span>
          )}
        {entry.model && <ModelBadge model={entry.model} />}
        <ModelCallRoleBadge role={entry.callRole} />
        {tool && (
          <span
            className={cn(
              "text-xs",
              outcome === "error"
                ? "font-medium text-destructive"
                : "text-muted-foreground"
            )}
            data-process-outcome={outcome}
          >
            {t(
              outcome === "error"
                ? "traceProcess.reportedFailure"
                : outcome === "ok"
                  ? "traceProcess.reportedSuccess"
                  : "traceProcess.statusUnknown"
            )}
          </span>
        )}
        {matched && (
          <span className="rounded-sm bg-foreground/5 px-1.5 py-0.5 text-xs text-muted-foreground">
            {t("traceProcess.match")}
          </span>
        )}
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-1">
        {controls}
        {!tool && entry.content !== undefined && (
          <TraceCopyButton
            value={contentPreview(entry.content, Infinity).text}
            label={t("spanContent.copyContent")}
            className="size-6"
          />
        )}
        <SourceButton
          spanId={entry.linkedToolSpanId || entry.spanId}
          label={t(
            tool && !entry.linkedToolSpanId
              ? "traceProcess.viewModelRecord"
              : "traceProcess.viewCall"
          )}
          onSelectSpan={onSelectSpan}
        />
      </div>
    </div>
  )
  return (
    <article
      className={cn(
        "relative min-w-0 scroll-mt-24",
        tool
          ? "mx-2 rounded-r-lg border-l-2 border-chart-3/45 bg-muted/25 px-3 py-2.5 @min-[42rem]/process:mr-12 @min-[42rem]/process:ml-6"
          : "rounded-xl border p-3 @min-[42rem]/process:max-w-[88%]",
        !tool &&
          (user
            ? "ml-auto rounded-tr-sm border-chart-1/25 bg-chart-1/7"
            : "mr-auto rounded-tl-sm border-border bg-card"),
        matched && "border-foreground/40 ring-1 ring-foreground/15",
        focused && "ring-2 ring-primary/60"
      )}
      tabIndex={-1}
      data-process-focused={focused ? "true" : undefined}
      data-process-entry={entry.id}
      data-process-kind={entry.kind}
      data-process-role={tool ? undefined : entry.role}
      data-process-matched={matched ? "true" : undefined}
      aria-label={t("traceProcess.step", { index: index + 1 })}
    >
      {tool ? (
        <div className="space-y-2">
          {header()}
          {entry.linkedToolSpanId && (
            <FieldOmissions paths={entry.omittedPaths ?? []} />
          )}
          {preview && (
            <p
              className="line-clamp-2 font-mono text-xs leading-5 [overflow-wrap:anywhere] break-words"
              data-process-argument-preview={preview.key}
            >
              <span className="mr-2 text-muted-foreground">{preview.key}</span>
              <PrivacyMarkedText text={preview.text} />
            </p>
          )}
          <div className="flex min-w-0 flex-wrap gap-x-6 gap-y-2">
            {entry.arguments !== undefined && (
              <Fold
                label={t("traceProcess.arguments")}
                className="basis-20 open:basis-full"
                data-process-payload="arguments"
              >
                <ProcessValue value={entry.arguments} />
              </Fold>
            )}
            {entry.result !== undefined ? (
              <Fold
                label={t("traceProcess.result")}
                className="basis-20 open:basis-full"
                data-process-payload="result"
              >
                <ProcessValue value={entry.result} />
              </Fold>
            ) : (
              <p className="text-xs text-muted-foreground">
                {t("traceProcess.resultMissing")}
              </p>
            )}
          </div>
        </div>
      ) : (
        <ProcessValue
          value={entry.content}
          message
          renderFrame={(controls, content) => (
            <>
              {header(controls)}
              {content}
            </>
          )}
        />
      )}
      <div className="mt-1">
        <Sources
          sources={entry.sources}
          callId={entry.callId}
          onSelectSpan={onSelectSpan}
        />
      </div>
    </article>
  )
}

export function TraceProcessView({
  model,
  onSelectSpan,
  matchingSpanIds,
  filterActive = false,
  complete = true,
  location,
}: {
  model: TraceProcessModel
  onSelectSpan: (id: string) => void
  matchingSpanIds?: ReadonlySet<string>
  filterActive?: boolean
  complete?: boolean
  location?: TraceProcessLocation
}) {
  const { t } = useTranslation()
  const root = useRef<HTMLDivElement>(null)
  const [localLocation, setLocalLocation] = useState<{
    source?: TraceProcessLocation
    id: string
    request: number
  }>()
  const focusedId =
    localLocation?.source === location ? localLocation?.id : location?.entryId
  useLayoutEffect(() => {
    if (!focusedId) return
    // A target in the separate execution list is mounted after its disclosure opens.
    const focus = () => {
      const element = Array.from(
        root.current?.querySelectorAll<HTMLElement>("[data-process-entry]") ??
          []
      ).find((entry) => entry.dataset.processEntry === focusedId)
      if (!element) return false
      element.scrollIntoView?.({ block: "center" })
      element.focus({ preventScroll: true })
      return true
    }
    if (focus()) return
    const observer = new MutationObserver(() => {
      if (focus()) observer.disconnect()
    })
    if (root.current)
      observer.observe(root.current, { childList: true, subtree: true })
    return () => observer.disconnect()
  }, [focusedId, location, localLocation])
  const allEntries = [...model.entries, ...(model.unlinkedTools ?? [])]
  const failures = allEntries.filter((entry) => entry.outcome === "error")
  const matches = filterActive
    ? allEntries.filter((entry) => entryMatches(entry, matchingSpanIds))
    : []
  const next = (entries: TraceProcessEntry[]) => {
    const current = entries.findIndex((entry) => entry.id === focusedId)
    const entry = entries[(current + 1) % entries.length]
    if (entry)
      setLocalLocation((previous) => ({
        source: location,
        id: entry.id,
        request: (previous?.request ?? 0) + 1,
      }))
  }
  const [contextPaging, setContextPaging] = useState({ model, limit: 5 })
  const contextLimit = contextPaging.model === model ? contextPaging.limit : 5
  const unlinkedRequests = model.entries.filter(
    (entry) => entry.kind === "tool" && !entry.linkedToolSpanId
  ).length
  const unlinkedTools = model.unlinkedTools ?? []
  const unlinkedFailures = unlinkedTools.filter(
    (entry) => entry.outcome === "error"
  ).length
  return (
    <div
      ref={root}
      className="@container/process min-w-0 space-y-3"
      data-trace-process
    >
      <div
        className="sticky top-0 z-10 flex flex-wrap items-center gap-2 rounded-md border bg-background px-3 py-2"
        data-process-navigation
      >
        <span className="mr-auto text-xs text-muted-foreground">
          {t("traceProcess.stepCount", { count: model.entries.length })}
        </span>
        {failures.length > 0 && (
          <Button
            size="sm"
            variant="ghost"
            className="h-7 gap-1 px-2 text-xs"
            onClick={() => next(failures)}
            data-process-next="error"
          >
            <ArrowDown className="size-3" />
            {t("traceProcess.nextFailure", { count: failures.length })}
          </Button>
        )}
        {filterActive && (
          <Button
            size="sm"
            variant="ghost"
            className="h-7 gap-1 px-2 text-xs"
            disabled={!matches.length}
            onClick={() => next(matches)}
            data-process-next="match"
          >
            <ArrowDown className="size-3" />
            {t("traceProcess.nextMatch", { count: matches.length })}
          </Button>
        )}
      </div>
      <div className="space-y-1">
        <p className="text-xs leading-5 text-muted-foreground">
          {t("traceProcess.description")}
        </p>
        {unlinkedRequests > 0 && (
          <p
            className="text-xs leading-5 text-muted-foreground"
            data-process-unlinked-summary={unlinkedRequests}
          >
            {t("traceProcess.unlinkedSummary", { count: unlinkedRequests })}
          </p>
        )}
        {filterActive && (
          <p
            className="text-xs leading-5 text-muted-foreground"
            data-process-filter-note
          >
            {t("traceProcess.filterNote")}
          </p>
        )}
        {!complete && (
          <p className="text-xs leading-5 text-muted-foreground" role="status">
            {t("traceProcess.incomplete")}
          </p>
        )}
      </div>
      {model.context.length > 0 && (
        <div className="rounded-lg border border-border p-3">
          <Fold
            label={t("traceProcess.context", { count: model.context.length })}
            data-process-context
          >
            <div className="space-y-4">
              {model.context.some(
                (message) => message.kind === "tool-definition"
              ) && (
                <p className="text-xs leading-5 text-muted-foreground">
                  {t("modelContext.availabilityHint")}
                </p>
              )}
              {model.context.slice(0, contextLimit).map((message, index) => (
                <ContextMessage
                  key={`${message.source.spanId}-${message.source.field}-${message.source.messageIndex ?? index}`}
                  message={message}
                  onSelectSpan={onSelectSpan}
                />
              ))}
              <TraceRecordControls
                shown={Math.min(contextLimit, model.context.length)}
                total={model.context.length}
                onMore={() =>
                  setContextPaging({ model, limit: contextLimit + 5 })
                }
                onAll={() =>
                  setContextPaging({ model, limit: model.context.length })
                }
                onCollapse={() => setContextPaging({ model, limit: 5 })}
              />
            </div>
          </Fold>
        </div>
      )}
      {model.entries.length ? (
        <ol className="space-y-2">
          {model.entries.map((entry, index) => (
            <li key={entry.id}>
              <ProcessEntry
                entry={entry}
                index={index}
                focused={focusedId === entry.id}
                matched={filterActive && entryMatches(entry, matchingSpanIds)}
                onSelectSpan={onSelectSpan}
              />
            </li>
          ))}
        </ol>
      ) : (
        <div className="rounded-lg border border-dashed border-border p-6 text-center">
          <p className="text-sm">{t("traceProcess.empty")}</p>
          <p className="mt-2 text-xs leading-5 text-muted-foreground">
            {t("traceProcess.emptyHint")}
          </p>
        </div>
      )}
      {unlinkedTools.length > 0 && (
        <div className="rounded-lg border border-border p-3">
          <Fold
            label={
              <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
                <span>
                  {t("traceProcess.unlinkedRecords", {
                    count: unlinkedTools.length,
                  })}
                </span>
                <span
                  className={
                    unlinkedFailures > 0 ? "text-destructive" : undefined
                  }
                  data-process-unlinked-failures={unlinkedFailures}
                >
                  {t("traceProcess.unlinkedFailures", {
                    count: unlinkedFailures,
                  })}
                </span>
              </span>
            }
            revealKey={
              unlinkedTools.some((entry) => entry.id === focusedId)
                ? `${focusedId}/${location?.request ?? 0}/${localLocation?.request ?? 0}`
                : undefined
            }
            data-process-unlinked-tools
          >
            <p className="mb-3 text-xs leading-5 text-muted-foreground">
              {t("traceProcess.unlinkedRecordsHint")}
            </p>
            <ol className="space-y-3">
              {unlinkedTools.map((entry, index) => (
                <li key={entry.id}>
                  <ProcessEntry
                    entry={entry}
                    index={model.entries.length + index}
                    focused={focusedId === entry.id}
                    matched={
                      filterActive && entryMatches(entry, matchingSpanIds)
                    }
                    onSelectSpan={onSelectSpan}
                  />
                </li>
              ))}
            </ol>
          </Fold>
        </div>
      )}
      {model.unrepresentedSpanCount > 0 && (
        <p className="text-xs leading-5 text-muted-foreground">
          {t("traceProcess.unrepresented", {
            count: model.unrepresentedSpanCount,
          })}
        </p>
      )}
    </div>
  )
}
