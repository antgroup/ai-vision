import { useTranslation } from "react-i18next"
import { useEffect, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Skeleton } from "@/components/ui/skeleton"
import { Badge } from "@/components/ui/badge"
import { DecisionSpanCard } from "@/components/labs/decision/DecisionSpanCard"
import { resolveDecisionSpanInfo } from "@/components/labs/decision/decisionDisplay"
import { MemorySpanCard } from "@/labs/memory/MemorySpanCard"
import { McpSpanCard } from "@/labs/mcp/McpSpanCard"
import { SkillSpanCard } from "@/labs/skill/SkillSpanCard"
import { ContextCompactionCard } from "@/labs/context-compaction/ContextCompactionCard"
import { ReportedFieldOmissions } from "@/labs/privacy/ReportedFieldOmissions"
import {
  resolveSkillSpanInfo,
  resolveSkillDetailFields,
} from "@/labs/skill/skillDisplay"
import {
  resolveSpanContent,
  resolveToolSpanOutcome,
  sameContentValue,
} from "./spanContent"
import { TraceRootIO } from "./TraceRootIO"
import { spanIO } from "./spanIO"
import {
  SpanContentEnhancement,
  SpanContentExcerpt,
} from "./SpanContentEnhancement"
import {
  ARGUMENT_KEYS,
  isErrorSpan,
  resolveMcpSpanInfo,
} from "@/labs/mcp/mcpDisplay"
import { getModelSpanPresentation } from "@/components/traces/shared/modelSpanPresentation"
import { IOSection, DetailSection, MetadataTree } from "./shared/MetadataTree"
import { formatJsonHighlighted } from "./shared/JsonDetailWidgets"
import { deepParseJson } from "./shared/utils"
import { TraceEnhancedContent } from "./shared/TraceEnhancedContent"
import { fetchNativeSpanDetail, type NativeTraceSourceSystem } from "./api"
import { formatNanoTimestamp, formatNanoDuration } from "./format"
import type { OTelSpan } from "./types"
import { ErrorBadge, ModelBadge } from "./shared/TraceBadge"
import { resolveModelCallRole } from "./spanModelContext"
import { ModelCallRoleBadge } from "./ModelCallRoleBadge"
import { SpanAvailableTools } from "./SpanAvailableTools"
import { CostSummary } from "@/features/model-pricing/CostSummary"
import { PowerContextRunCard } from "@/labs/memory/PowerContextRunCard"
import { resolvePowerContextSpan } from "@/labs/memory/powerContextSpans"
import { PowerContextSpanIcon } from "@/labs/memory/powerContextSpanIcons"

const IO_KEYS = new Set([
  "gen_ai.input.messages",
  "gen_ai.output.messages",
  "langfuse.observation.input",
  "langfuse.observation.output",
  "input.value",
  "output.value",
  "gen_ai.tool.call.arguments",
  "gen_ai.tool.call.result",
  "aivision.request.input",
  "aivision.request.output",
])

interface SpanDetailPanelProps {
  spaceId: string
  traceId: string
  spanId: string
  agentProduct?: string
  agentUuid?: string | null
  demoCost?: boolean
  sourceSystem?: NativeTraceSourceSystem
  fromTimestamp?: string
  toTimestamp?: string
  contentMode?: "enhanced" | "raw"
  formatMode?: "formatted" | "json"
  spanData?: OTelSpan | null
  rootSpan?: boolean
  focusIO?: "input" | "output"
  /** Labs MCP/decision APIs are reachable; public trace links must pass false. */
  labsApi?: boolean
  traceSpans?: OTelSpan[]
  focusEventIndex?: number
  onSelectSpan?: (spanId: string) => void
  onNavigateToTrace?: (traceId: string, spanId?: string) => void
  /** Lets the MCP / decision cards offer a "back to trace" exit that
   * deselects the span (back to the trace preview). */
  onBackToTrace?: () => void
}

export function SpanDetailPanel({
  spaceId,
  traceId,
  spanId,
  agentProduct,
  agentUuid,
  demoCost,
  sourceSystem = "signoz",
  fromTimestamp,
  toTimestamp,
  formatMode = "formatted",
  contentMode,
  spanData,
  rootSpan = false,
  focusIO,
  labsApi = true,
  traceSpans,
  focusEventIndex,
  onSelectSpan,
  onNavigateToTrace,
  onBackToTrace,
}: SpanDetailPanelProps) {
  const { t, i18n } = useTranslation()
  const [localEventSource, setLocalEventSource] = useState<{
    spanId: string
    eventIndex: number
  } | null>(null)
  const [eventCollapse, setEventCollapse] = useState<{
    spanId: string
    collapsed: boolean
  } | null>(null)
  const eventContainer = useRef<HTMLDivElement>(null)
  const selectedEventIndex =
    localEventSource?.spanId === spanId
      ? localEventSource.eventIndex
      : focusEventIndex
  const eventsCollapsed =
    eventCollapse?.spanId === spanId
      ? eventCollapse.collapsed
      : selectedEventIndex === undefined
  const { data, isLoading } = useQuery({
    queryKey: [
      "nativeSpanDetail",
      spaceId,
      traceId,
      spanId,
      agentProduct,
      agentUuid,
      contentMode,
      formatMode,
      sourceSystem,
    ],
    queryFn: () =>
      fetchNativeSpanDetail(spaceId, traceId, spanId, {
        agentProduct,
        agentUuid,
        mediaView: contentMode ? contentMode === "enhanced" : formatMode !== "json",
        from: fromTimestamp,
        to: toTimestamp,
        sourceSystem,
      }),
    enabled: !spanData && !!spaceId && !!traceId && !!spanId,
    refetchOnWindowFocus: false,
  })
  useEffect(() => {
    if (eventsCollapsed || selectedEventIndex === undefined) return
    const event = eventContainer.current?.querySelector<HTMLElement>(
      `[data-trace-event-index="${selectedEventIndex}"]`
    )
    event?.scrollIntoView?.({ block: "nearest" })
    event?.focus({ preventScroll: true })
  }, [spanId, selectedEventIndex, eventsCollapsed, data, spanData])

  if (isLoading) {
    return (
      <div className="flex flex-col gap-3 p-4">
        <Skeleton className="h-6 w-1/2" />
        <Skeleton className="h-4 w-1/3" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    )
  }

  const span = spanData ?? (data?.code === 0 ? data.data : null)
  if (!span) {
    return (
      <div className="p-6 text-center text-sm text-muted-foreground">
        {t("traceLabels.spanLoadFailed")}
      </div>
    )
  }

  const enhanced = contentMode
    ? contentMode === "enhanced"
    : formatMode !== "json"
  // Legacy JSON-format callers still expect one SDK-envelope decode. Only
  // the explicit Original content mode bypasses that transport decoding.
  const original = contentMode === "raw"
  const attrs = span.attributes ?? {}
  const decodedInput = spanIO(attrs, "input")
  const decodedOutput = spanIO(attrs, "output")
  const inputData = original
    ? spanIO(attrs, "input", { preserveValue: true })
    : decodedInput
  const outputData = original
    ? spanIO(attrs, "output", { preserveValue: true })
    : decodedOutput

  const remainingAttrs: Record<string, unknown> = {}
  for (const [key, val] of Object.entries(attrs)) {
    if (!enhanced || !IO_KEYS.has(key)) {
      remainingAttrs[key] = val
    }
  }
  const hasRemainingAttrs = Object.keys(remainingAttrs).length > 0

  const resource = span.resource ?? {}
  const spanEnv =
    (resource["ant.agent.env"] as string) ||
    (attrs["ant.agent.env"] as string) ||
    null
  const presentation = getModelSpanPresentation(span)
  const decisionInfo = resolveDecisionSpanInfo(attrs)
  const mcpInfo = resolveMcpSpanInfo(attrs)
  const skillInfo = resolveSkillSpanInfo(attrs)
  const powerContextInfo =
    enhanced && !decisionInfo && !mcpInfo && !skillInfo
      ? resolvePowerContextSpan(span)
      : null
  const modelLabelKey = !decisionInfo && !mcpInfo ? presentation.labelKey : null
  const displayName = powerContextInfo
    ? t(powerContextInfo.labelKey)
    : modelLabelKey
      ? t(modelLabelKey)
      : span.name || t("traceLabels.unnamedSpan")
  const originalName =
    (modelLabelKey || powerContextInfo) && span.name
      ? t("modelSpan.originalName", { name: span.name })
      : null

  const hasError = isErrorSpan(span)

  const skillInput =
    skillInfo && Object.hasOwn(attrs, "agentic.skill.input")
      ? attrs["agentic.skill.input"]
      : undefined
  const skillOutput =
    skillInfo && Object.hasOwn(attrs, "agentic.skill.output")
      ? attrs["agentic.skill.output"]
      : undefined
  // Skill readers also report legacy argument aliases. Keep the selected
  // source value intact rather than joining or trimming multiple attributes.
  const skillArgumentKey = skillInfo
    ? ARGUMENT_KEYS.find(
        (key) => Object.hasOwn(attrs, key) && attrs[key] !== undefined
      )
    : undefined
  const skillFallbackInput =
    skillInput !== undefined
      ? skillInput
      : skillArgumentKey !== undefined
        ? attrs[skillArgumentKey]
        : undefined
  const displayedInput =
    decodedInput !== undefined ? decodedInput.value : skillFallbackInput
  const displayedOutput =
    decodedOutput !== undefined ? decodedOutput.value : skillOutput
  const hasSpecializedContent = Boolean(
    decisionInfo || mcpInfo || skillInfo || powerContextInfo
  )
  const missingInput = original
    ? inputData === undefined
    : displayedInput === undefined
  const missingOutput = original
    ? outputData === undefined
    : displayedOutput === undefined
  let contentModel = !decisionInfo
    ? resolveSpanContent(span, displayedInput, displayedOutput, {
        tool: Boolean(mcpInfo || skillInfo),
      })
    : null
  if (contentModel?.kind === "tool") {
    const outcome = resolveToolSpanOutcome(span, displayedOutput)
    // Preserve output-only parser semantics; native cards also report explicit
    // span status, and a recorded span error always wins in the presentation.
    if (outcome === "error" || mcpInfo || skillInfo) {
      contentModel = { ...contentModel, outcome }
    }
  }
  const statusInContent =
    contentModel?.kind === "tool" && contentModel.output !== undefined
  const skillSource = skillInfo ? resolveSkillDetailFields(attrs)?.source : null
  const sourceInContent = Boolean(
    skillSource &&
    contentModel?.kind === "tool" &&
    contentModel.fields.some(
      (field) => field.key === "file_path" && field.value === skillSource
    )
  )
  const additionalSkillMaterials = [
    { key: "agentic.skill.input", value: skillInput, primary: displayedInput },
    {
      key: "agentic.skill.output",
      value: skillOutput,
      primary: displayedOutput,
    },
  ].filter(
    (field) =>
      field.value !== undefined && !sameContentValue(field.value, field.primary)
  )
  const toolContent =
    contentModel && (mcpInfo || skillInfo) ? (
      <>
        <SpanContentEnhancement
          key={span.span_id}
          model={contentModel}
          embedded
          argumentNote={<ReportedFieldOmissions resource={span.resource} />}
        />
        {additionalSkillMaterials.length > 0 && (
          <details className="border-t border-border pt-3 text-xs">
            <summary className="cursor-pointer rounded-sm text-muted-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
              {t("spanContent.additionalEvidence")}
            </summary>
            <dl className="mt-3 space-y-3">
              {additionalSkillMaterials.map((field) => (
                <div key={field.key}>
                  <dt className="font-mono text-muted-foreground">
                    {field.key}
                  </dt>
                  <dd>
                    <SpanContentExcerpt value={field.value} />
                  </dd>
                </div>
              ))}
            </dl>
          </details>
        )}
      </>
    ) : undefined
  const rawIO = (
    <>
      {inputData !== undefined && (
        <>
          {contentModel?.kind === "tool" && (
            <ReportedFieldOmissions resource={span.resource} />
          )}
          {rootSpan && enhanced ? (
            <TraceRootIO
              key={`${span.span_id}/input`}
              value={inputData.value}
              variant="input"
              initiallyOpen={focusIO === "input"}
            />
          ) : (
            <IOSection
              title={t("nativeSpanUsage.input")}
              data={inputData.value}
              variant="input"
              viewMode={formatMode}
              rawText={original}
              preserveValue
              valuePresent
            />
          )}
        </>
      )}
      {outputData !== undefined &&
        (rootSpan && enhanced ? (
          <TraceRootIO
            key={`${span.span_id}/output`}
            value={outputData.value}
            variant="output"
            initiallyOpen={focusIO === "output"}
          />
        ) : (
          <IOSection
            title={t("nativeSpanUsage.output")}
            data={outputData.value}
            variant="output"
            viewMode={formatMode}
            rawText={original}
            preserveValue
            valuePresent
          />
        ))}
    </>
  )

  return (
    <div
      className="flex h-full flex-col gap-3 overflow-auto p-3"
      data-testid="native-span-detail"
      data-span-id={span.span_id}
    >
      {/* Header */}
      <div>
        <h4
          className="flex items-start gap-2 text-base font-medium [overflow-wrap:anywhere] text-foreground"
          title={originalName ?? undefined}
        >
          {powerContextInfo && (
            <PowerContextSpanIcon
              info={powerContextInfo}
              className="mt-0.5 size-4 shrink-0 text-muted-foreground"
              aria-hidden="true"
            />
          )}
          {displayName}
        </h4>
      </div>

      {/* Badges */}
      <div className="flex flex-wrap gap-2">
        <Badge variant="secondary" className="font-normal">
          <span>{t("traceLabels.latency")}:</span>
          <span className="font-mono">
            {formatNanoDuration(
              span.start_time_unix_nano,
              span.end_time_unix_nano
            )}
          </span>
        </Badge>
        {spanEnv && (
          <Badge variant="secondary" className="font-normal">
            <span>{t("traceLabels.environment")}:</span>
            <span>{spanEnv}</span>
          </Badge>
        )}
        {hasError && <ErrorBadge />}
        {presentation.model && <ModelBadge model={presentation.model} />}
        {enhanced && (
          <ModelCallRoleBadge role={resolveModelCallRole(span, traceSpans)} />
        )}
      </div>

      {span.costCoverage && (
        <CostSummary
          total={span.costCoverage.additive ? span.usage?.cost?.total : null}
          coverage={span.costCoverage}
          simulated={demoCost}
          compact
        />
      )}

      {span.status?.message && (
        <div
          className="min-w-0 text-xs font-medium text-destructive"
          data-testid="span-status-message"
        >
          {enhanced ? (
            <SpanContentExcerpt
              key={span.span_id}
              value={span.status.message}
              format="code"
            />
          ) : (
            <span className="break-all whitespace-pre-wrap">
              {span.status.message}
            </span>
          )}
        </div>
      )}

      <DetailSection
        title={t("traceLabels.callInfo")}
        collapsible
        defaultCollapsed
      >
        <dl className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
          <dt className="text-muted-foreground">Span ID</dt>
          <dd className="font-mono [overflow-wrap:anywhere]">{span.span_id}</dd>
          {span.parent_span_id && (
            <>
              <dt className="text-muted-foreground">
                {t("traceLabels.parent")}
              </dt>
              <dd className="font-mono [overflow-wrap:anywhere]">
                {span.parent_span_id}
              </dd>
            </>
          )}
        </dl>
        {originalName && (
          <p className="mt-2 text-xs [overflow-wrap:anywhere]">
            {originalName}
          </p>
        )}
        <div className="mt-2 flex flex-wrap gap-2">
          {span.start_time_unix_nano && (
            <Badge variant="secondary" className="font-normal">
              <span>{t("nativeSpanUsage.start")}:</span>
              <span className="font-mono">
                {formatNanoTimestamp(
                  span.start_time_unix_nano,
                  i18n?.resolvedLanguage
                )}
              </span>
            </Badge>
          )}
          {span.end_time_unix_nano && (
            <Badge variant="secondary" className="font-normal">
              <span>{t("nativeSpanUsage.end")}:</span>
              <span className="font-mono">
                {formatNanoTimestamp(
                  span.end_time_unix_nano,
                  i18n?.resolvedLanguage
                )}
              </span>
            </Badge>
          )}
        </div>
      </DetailSection>

      {original && (
        <p
          data-testid="privacy-original-note"
          className="text-xs leading-5 text-muted-foreground"
        >
          {t("labs.privacy.originalHelp")}
        </p>
      )}
      <TraceEnhancedContent active={enhanced}>
        {powerContextInfo && (
          <PowerContextRunCard
            key={span.span_id}
            span={span}
            traceSpans={traceSpans}
            onSelectSpan={onSelectSpan}
          />
        )}
        <ContextCompactionCard
          spans={[span]}
          variant="detail"
          onViewSource={(source) => {
            setLocalEventSource({ spanId, eventIndex: source.eventIndex })
            setEventCollapse({ spanId, collapsed: false })
          }}
        />
        <DecisionSpanCard
          span={span}
          traceSpans={traceSpans}
          onSelectSpan={onSelectSpan}
          onBackToTrace={onBackToTrace}
        />
        <McpSpanCard
          statusInContent={statusInContent}
          span={span}
          spaceId={spaceId}
          traceId={traceId}
          onSelectSpan={onSelectSpan}
          onNavigateToTrace={onNavigateToTrace}
          onBackToTrace={onBackToTrace}
          overviewLinks={labsApi}
          labsApi={labsApi}
        >
          {mcpInfo ? toolContent : undefined}
        </McpSpanCard>
        <MemorySpanCard
          statusInContent={statusInContent}
          contentModel={mcpInfo ? contentModel : undefined}
          output={
            decodedOutput ? attrs[decodedOutput.attributeKey] : displayedOutput
          }
          span={span}
          spaceId={spaceId}
          traceId={traceId}
          onSelectSpan={onSelectSpan}
          onNavigateToTrace={onNavigateToTrace}
          onBackToTrace={onBackToTrace}
          overviewLinks={labsApi}
          labsApi={labsApi}
        />
        <SkillSpanCard
          detailHeader
          statusInContent={statusInContent}
          sourceInContent={sourceInContent}
          span={span}
          spaceId={spaceId}
          traceId={traceId}
          onSelectSpan={onSelectSpan}
          onNavigateToTrace={onNavigateToTrace}
          onBackToTrace={onBackToTrace}
          overviewLinks={labsApi}
          labsApi={labsApi}
        >
          {/* A span can report both identities. MCP owns shared IO in that
              case; explicit empty children suppress the Skill IO fallback. */}
          {skillInfo ? mcpInfo ? <></> : toolContent : undefined}
        </SkillSpanCard>
      </TraceEnhancedContent>

      {/* Enhancements use only the selected span; original IO stays reversible. */}
      {contentModel && !hasSpecializedContent && (
        <TraceEnhancedContent active={enhanced}>
          <SpanContentEnhancement
            key={span.span_id}
            model={contentModel}
            focusIO={focusIO}
            argumentNote={<ReportedFieldOmissions resource={span.resource} />}
          />
        </TraceEnhancedContent>
      )}
      <TraceEnhancedContent active={enhanced}>
        <SpanAvailableTools span={span} masked={!enhanced} />
      </TraceEnhancedContent>
      {!enhanced ? (
        rawIO
      ) : hasSpecializedContent &&
        enhanced &&
        (inputData !== undefined || outputData !== undefined) ? (
        <DetailSection
          title={t("traceLabels.originalIO")}
          collapsible
          defaultCollapsed
          count={
            Number(inputData !== undefined) + Number(outputData !== undefined)
          }
        >
          <div className="flex flex-col gap-3">{rawIO}</div>
        </DetailSection>
      ) : !contentModel ? (
        rawIO
      ) : null}

      {!powerContextInfo && (missingInput || missingOutput) && (
        <section
          key={span.span_id}
          data-testid="span-missing-content"
          className="rounded-lg border border-border bg-muted/30 p-3 text-sm"
        >
          <h4 className="font-medium text-foreground">
            {t(
              missingInput && missingOutput
                ? "traceLabels.inputOutputNotRecorded"
                : missingInput
                  ? "traceLabels.inputNotRecorded"
                  : "traceLabels.outputNotRecorded"
            )}
          </h4>
          <p className="mt-1 leading-6 text-muted-foreground">
            {t("traceLabels.contentNotRecordedHint")}
          </p>
          <details className="mt-2">
            <summary className="cursor-pointer rounded-sm text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
              {t("traceLabels.contentNotRecordedHelp")}
            </summary>
            <ul className="mt-3 list-disc space-y-3 pl-5 leading-6 text-muted-foreground">
              <li>{t("traceLabels.contentInChildCalls")}</li>
              <li>{t("traceLabels.contentCaptureDisabled")}</li>
              {missingOutput && (
                <li>{t("traceLabels.contentNotReportedYet")}</li>
              )}
              {hasRemainingAttrs && (
                <li>{t("traceLabels.contentUnrecognizedFields")}</li>
              )}
            </ul>
          </details>
        </section>
      )}

      {/* Attributes */}
      {hasRemainingAttrs && (
        <DetailSection
          title={t("traceLabels.attributes")}
          collapsible
          defaultCollapsed
          count={Object.keys(remainingAttrs).length}
        >
          {formatMode === "json" ? (
            <pre className="overflow-x-auto rounded-lg bg-muted/50 p-3 font-mono text-xs leading-relaxed dark:bg-muted/30">
              <code>
                {formatJsonHighlighted(
                  JSON.stringify(
                    enhanced ? deepParseJson(remainingAttrs) : remainingAttrs,
                    null,
                    2
                  )
                )}
              </code>
            </pre>
          ) : (
            <MetadataTree data={remainingAttrs} floatToolbar />
          )}
        </DetailSection>
      )}

      {/* Resource */}
      {span.resource && Object.keys(span.resource).length > 0 && (
        <DetailSection
          title={t("traceLabels.resource")}
          collapsible
          defaultCollapsed
          count={Object.keys(span.resource).length}
        >
          {formatMode === "json" ? (
            <pre className="overflow-x-auto rounded-lg bg-muted/50 p-3 font-mono text-xs leading-relaxed dark:bg-muted/30">
              <code>
                {formatJsonHighlighted(
                  JSON.stringify(
                    enhanced ? deepParseJson(span.resource) : span.resource,
                    null,
                    2
                  )
                )}
              </code>
            </pre>
          ) : (
            <MetadataTree data={span.resource} floatToolbar />
          )}
        </DetailSection>
      )}

      {/* Events */}
      {span.events && span.events.length > 0 && (
        <DetailSection
          title={t("traceLabels.events")}
          collapsible
          defaultCollapsed
          collapsed={eventsCollapsed}
          onToggleCollapse={() =>
            setEventCollapse({ spanId, collapsed: !eventsCollapsed })
          }
          count={span.events.length}
        >
          <div ref={eventContainer} className="space-y-2">
            {span.events.map((event, idx) => (
              <div
                key={idx}
                data-trace-event-index={idx}
                tabIndex={-1}
                className="rounded-md border p-2 text-xs focus:outline-2 focus:outline-offset-2 focus:outline-ring"
              >
                <div className="font-medium">{event.name}</div>
                {event.time_unix_nano && (
                  <div className="mt-0.5 font-mono text-muted-foreground">
                    {formatNanoTimestamp(
                      event.time_unix_nano,
                      i18n?.resolvedLanguage
                    )}
                  </div>
                )}
                {event.attributes &&
                  Object.keys(event.attributes).length > 0 && (
                    <div className="mt-1">
                      <MetadataTree data={event.attributes} />
                    </div>
                  )}
              </div>
            ))}
          </div>
        </DetailSection>
      )}
    </div>
  )
}
