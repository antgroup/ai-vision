import { useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { Database } from "lucide-react"
import { LabsSection } from "@/components/labs/shared/LabsSection"
import { TraceRecordControls } from "@/components/traces/shared/TraceRecordControls"
import { SpanTypedContent } from "@/components/traces-otel/SpanTypedContent"
import { PowerContextMemoryContent } from "@/components/traces-otel/PowerContextMemoryContent"
import { parsePowerContextContent } from "@/components/traces-otel/powerContextContent"
import { TraceBadge } from "@/components/traces-otel/shared/TraceBadge"
import { spanIO } from "@/components/traces-otel/spanIO"
import type { OTelSpan } from "@/components/traces-otel/types"
import { PowerContextSpanIcon } from "./powerContextSpanIcons"
import {
  powerContextSpanStateKey,
  resolvePowerContextSpan,
} from "./powerContextSpans"
import { powerContextRun } from "./powerContextRun"

function readRecalledMemory(attrs: Record<string, unknown>, output: unknown) {
  const truncated = attrs["powercontext.content.output.truncated"]
  return truncated === undefined || truncated === false || truncated === "false"
    ? parsePowerContextContent(output)
    : null
}

export function PowerContextRunCard({
  span,
  traceSpans,
  onSelectSpan,
  comparisonSearchSources,
}: {
  span: OTelSpan
  traceSpans?: OTelSpan[]
  onSelectSpan?: (spanId: string) => void
  comparisonSearchSources?: ReadonlySet<string>
}) {
  const { t } = useTranslation()
  const [visible, setVisible] = useState(5)
  const run = useMemo(
    () =>
      powerContextRun(
        span,
        traceSpans,
        comparisonSearchSources
          ? { searchSources: comparisonSearchSources }
          : undefined
      ),
    [span, traceSpans, comparisonSearchSources]
  )
  if (!run) return null
  const compact = comparisonSearchSources !== undefined
  const { hits, sources, recall } = run
  const operation = run.info.operation
  const isRecall = operation === "recall"
  const isSearch = operation === "memory.search"
  const attrs = span.attributes ?? {}
  // Recall's query is already available in the original input/output panel.
  const input = compact || isRecall ? undefined : spanIO(attrs, "input")?.value
  const reportedOutput =
    compact && !isRecall ? undefined : spanIO(attrs, "output")?.value
  const output =
    isRecall && reportedOutput === null ? undefined : reportedOutput
  const sdkStep =
    !compact &&
    run.info.layer === "client" &&
    ["prepare_context", "resolve_scope_binding"].includes(operation)
  const reference = compact ? null : run.sdkReference
  const sourceAttrs = reference?.source.attributes ?? {}
  const referenceInput =
    reference && input === undefined
      ? spanIO(sourceAttrs, "input")?.value
      : undefined
  const referenceOutput =
    reference?.allowContext && output === undefined
      ? spanIO(sourceAttrs, "output")?.value
      : undefined
  const referenceScope =
    reference &&
    attrs["powercontext.scope.id"] === undefined &&
    typeof sourceAttrs["powercontext.scope.id"] === "string" &&
    sourceAttrs["powercontext.scope.id"]
      ? sourceAttrs["powercontext.scope.id"]
      : undefined
  const hasReference =
    referenceInput !== undefined ||
    referenceOutput !== undefined ||
    referenceScope !== undefined
  const referenceTruncated =
    sourceAttrs["powercontext.content.output.truncated"]
  const referenceInputTruncated =
    sourceAttrs["powercontext.content.input.truncated"]
  const referenceMemory = readRecalledMemory(sourceAttrs, referenceOutput)
  const memory = isRecall ? readRecalledMemory(attrs, output) : null
  const stateKey = powerContextSpanStateKey(span, run.info, run.status)
  const state = t(stateKey)
  const linkedRecall = isSearch ? run.recallInScope : recall
  const recallAttrs = linkedRecall?.attributes ?? {}
  const recallWithContent =
    isSearch &&
    linkedRecall &&
    (readRecalledMemory(recallAttrs, spanIO(recallAttrs, "output")?.value)
      ?.items.length ?? 0) > 0
      ? linkedRecall
      : null
  const showRecallStep =
    linkedRecall &&
    !recallWithContent &&
    !hasReference &&
    (!isSearch ||
      !["empty", "skipped"].includes(
        String(recallAttrs["powercontext.context.status"])
      ))
  const showMissingContent =
    input === undefined &&
    output === undefined &&
    !hasReference &&
    (!hits || isRecall) &&
    !sources &&
    stateKey !== "powerContextRun.context.empty" &&
    stateKey !== "powerContextRun.context.skipped"
  const metadata = [
    ["powerContextRun.reportedOutcome", run.info.reportedOutcome ?? undefined],
    ["powerContextRun.scope", attrs["powercontext.scope.id"]],
    ["powerContextRun.request", attrs["powercontext.request.id"]],
    ["powerContextRun.httpStatus", attrs["http.response.status_code"]],
    ["powerContextRun.searchMode", attrs["powercontext.memory.search.mode"]],
  ].filter((entry) => entry[1] !== undefined)
  const linkClass =
    "min-h-7 rounded-md px-2 py-1 text-left text-xs text-[var(--color-labs)] hover:bg-[var(--color-bg-labs-tag)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
  return (
    <LabsSection
      feature="memory"
      variant="detail"
      icon={Database}
      title={t("powerContextRun.title")}
      summary={
        !compact && (
          <>
            <TraceBadge>{t(run.info.layerLabelKey)}</TraceBadge>
            <TraceBadge
              title={state}
              tone={run.status === "error" ? "error" : "default"}
            >
              {state}
            </TraceBadge>
          </>
        )
      }
      data-powercontext-run
    >
      {sdkStep && (
        <p className="text-xs leading-5 text-muted-foreground">
          {t(
            operation === "resolve_scope_binding"
              ? "powerContextRun.bindingPurpose"
              : "powerContextRun.preparePurpose"
          )}
        </p>
      )}
      {(hits || (recallWithContent && onSelectSpan)) && (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm">
          {hits && (
            <span data-powercontext-run-hits>
              {t("powerContextRun.hits", { count: hits.value })}
            </span>
          )}
          {hits && onSelectSpan && hits.source.span_id !== span.span_id && (
            <button
              type="button"
              className={linkClass}
              onClick={() => onSelectSpan(hits.source.span_id)}
            >
              {t("powerContextRun.viewEvidence")}
            </button>
          )}
          {recallWithContent && onSelectSpan && (
            <button
              type="button"
              className={linkClass}
              onClick={() => onSelectSpan(recallWithContent.span_id)}
            >
              {t("powerContextRun.viewRecalledContent")}
            </button>
          )}
        </div>
      )}
      {sources && (
        <p className="text-sm" data-powercontext-run-sources>
          {t("powerContextRun.sources", { count: sources.value })}
        </p>
      )}
      {input !== undefined && (
        <section className="space-y-1">
          <h4 className="text-xs font-medium">{t("powerContextRun.input")}</h4>
          <SpanTypedContent value={input} />
        </section>
      )}
      {memory ? (
        <PowerContextMemoryContent
          content={memory}
          embedded
          compact={compact}
        />
      ) : output !== undefined ? (
        <section className="space-y-1">
          <h4 className="text-xs font-medium">{t("powerContextRun.output")}</h4>
          <SpanTypedContent value={output} compact={compact} />
        </section>
      ) : null}
      {showMissingContent && (
        <p className="text-xs leading-5 text-muted-foreground">
          {t(
            isRecall
              ? "powerContextRun.noRecalledContent"
              : isSearch
                ? "powerContextRun.noSearchResult"
                : compact
                  ? "powerContextRun.noSummary"
                  : "powerContextRun.noContent"
          )}
        </p>
      )}
      {hasReference && reference && (
        <section
          className="min-w-0 space-y-3 border-t border-border pt-3"
          data-powercontext-run-reference={reference.source.span_id}
        >
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="text-xs font-medium">
              {t("powerContextRun.referenceTitle")}
            </h4>
            {onSelectSpan && (
              <button
                type="button"
                className={linkClass}
                onClick={() => onSelectSpan(reference.source.span_id)}
              >
                {t("powerContextRun.viewRecall")}
              </button>
            )}
          </div>
          <p className="text-xs leading-5 text-muted-foreground">
            {t("powerContextRun.referenceNote")}
          </p>
          {referenceScope !== undefined && (
            <section className="space-y-1">
              <h5 className="text-xs font-medium">
                {t("powerContextRun.referenceScope")}
              </h5>
              <SpanTypedContent value={referenceScope} />
            </section>
          )}
          {referenceInput !== undefined && (
            <section className="space-y-1" data-powercontext-reference-input>
              <h5 className="flex flex-wrap items-center gap-2 text-xs font-medium">
                {t("powerContextRun.referenceInput")}
                {(referenceInputTruncated === true ||
                  referenceInputTruncated === "true") && (
                  <TraceBadge>{t("powerContextContent.truncated")}</TraceBadge>
                )}
              </h5>
              <SpanTypedContent value={referenceInput} compact />
            </section>
          )}
          {referenceOutput !== undefined && (
            <section className="space-y-1" data-powercontext-reference-output>
              <h5 className="flex flex-wrap items-center gap-2 text-xs font-medium">
                {t("powerContextRun.referenceOutput")}
                {(referenceTruncated === true ||
                  referenceTruncated === "true") && (
                  <TraceBadge>{t("powerContextContent.truncated")}</TraceBadge>
                )}
              </h5>
              {referenceMemory ? (
                <PowerContextMemoryContent content={referenceMemory} embedded />
              ) : (
                <SpanTypedContent value={referenceOutput} compact />
              )}
            </section>
          )}
        </section>
      )}
      {!compact && metadata.length > 0 && (
        <details className="text-xs" open={sdkStep || undefined}>
          <summary className="cursor-pointer rounded-sm text-muted-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
            {t("powerContextRun.metadata")}
          </summary>
          <dl className="mt-2 space-y-2">
            {metadata.map(([label, value]) => (
              <div className="min-w-0" key={String(label)}>
                <dt className="text-muted-foreground">{t(String(label))}</dt>
                <dd>
                  <SpanTypedContent value={value} />
                </dd>
              </div>
            ))}
          </dl>
        </details>
      )}
      {!compact && showRecallStep && linkedRecall && onSelectSpan && (
        <button
          type="button"
          className={linkClass}
          onClick={() => onSelectSpan(linkedRecall.span_id)}
        >
          {t("powerContextRun.viewRecall")}
        </button>
      )}
      {!compact && run.children.length > 0 && onSelectSpan && (
        <section
          className="space-y-1 border-t border-border pt-2"
          data-powercontext-run-children
        >
          <h4 className="text-xs font-medium">
            {t("powerContextRun.children")}
          </h4>
          <div className="flex flex-wrap gap-1">
            {run.children.slice(0, visible).map((child) => {
              const info = resolvePowerContextSpan(child)!
              return (
                <button
                  type="button"
                  className={`${linkClass} inline-flex max-w-full items-center gap-1`}
                  title={child.name}
                  key={child.span_id}
                  onClick={() => onSelectSpan(child.span_id)}
                >
                  <PowerContextSpanIcon
                    info={info}
                    className="size-3.5 shrink-0"
                    aria-hidden="true"
                  />
                  <span className="min-w-0 [overflow-wrap:anywhere]">
                    {t(info.labelKey)}
                  </span>
                  <span className="text-muted-foreground">
                    {t(info.layerLabelKey)}
                  </span>
                </button>
              )
            })}
          </div>
          <TraceRecordControls
            shown={Math.min(visible, run.children.length)}
            total={run.children.length}
            onMore={() => setVisible((n) => n + 5)}
            onAll={() => setVisible(run.children.length)}
            onCollapse={() => setVisible(5)}
          />
        </section>
      )}
    </LabsSection>
  )
}
