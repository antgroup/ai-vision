import { useQuery } from "@tanstack/react-query"
import { Blocks, ChevronLeft } from "lucide-react"
import {
  useCallback,
  useMemo,
  useState,
  type ComponentProps,
  type ReactNode,
} from "react"
import { useTranslation } from "react-i18next"
import { useLocation } from "react-router-dom"

import { LabsSection } from "@/components/labs/shared/LabsSection"
import { TONE_TAG_CLASS } from "@/components/labs/shared/labsFormat"
import { TraceTextContent } from "@/components/traces/shared/TraceTextContent"
import { formatDurationMs } from "@/components/traces-otel/format"
import { resolveToolSpanOutcome } from "@/components/traces-otel/spanContent"
import { spanIO } from "@/components/traces-otel/spanIO"
import { SpanTypedContent } from "@/components/traces-otel/SpanTypedContent"
import { formatTokenCount } from "@/components/traces-otel/spanUsage"
import type { OTelSpan } from "@/components/traces-otel/types"
import { useAgent } from "@/contexts/AgentContext"
import { useSpace } from "@/contexts/SpaceContext"
import { useTimeFilter } from "@/contexts/TimeFilterContext"
import { mcpCallDurationMs } from "@/labs/mcp/mcpDisplay"
import { getPresetTimeWindow } from "@/lib/timeFilter"
import { cn } from "@/lib/utils"

import { SkillConfigImportDialog } from "./SkillConfigImportDialog"
import { SkillDrawer } from "./SkillDrawer"
import {
  listSkillConfigs,
  listSkillStats,
  listSkillCallHistory,
} from "./skillApi"
import {
  failedSkillReadName,
  resolveSkillDetailFields,
  skillTraceHref,
} from "./skillDisplay"

const LINK_CLASS =
  "rounded-[6px] text-xs leading-5 text-[var(--color-labs)] underline decoration-[var(--color-border-labs)] underline-offset-4 hover:decoration-current focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-labs)]"

export function SkillSpanCard({
  span,
  spaceId,
  traceId,
  onSelectSpan,
  onNavigateToTrace,
  onBackToTrace,
  overviewLinks = true,
  labsApi = true,
  children,
  detailHeader = false,
  statusInContent = false,
  sourceInContent = false,
}: {
  span: OTelSpan
  spaceId: string
  /** Current trace id; the drawer's call history uses it to tell in-trace
   * calls (locate) from other traces' (switch page). */
  traceId?: string
  /** Lets the drawer's call history locate their span in the trace tree. */
  onSelectSpan?: (spanId: string) => void
  /** Lets the drawer's call history switch the page to another trace (and
   * focus the called span there) instead of rendering a dead same-URL link. */
  onNavigateToTrace?: (traceId: string, spanId?: string) => void
  onBackToTrace?: () => void
  /** Off on shared trace pages, where the overview links are not reachable. */
  overviewLinks?: boolean
  /** Gates the config / scan queries; public pages must pass false. */
  labsApi?: boolean
  /** Shared tool IO owns the body when supplied by the span detail panel. */
  children?: ReactNode
  /** The outer detail panel already presents span duration and errors. */
  detailHeader?: boolean
  /** Hide the badge only when shared content actually presents the outcome. */
  statusInContent?: boolean
  sourceInContent?: boolean
}) {
  const { t } = useTranslation()
  // In-place overview-style drawer: closing returns to the trace detail
  // because the page never navigated away.
  const [configOpen, setConfigOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const fields = resolveSkillDetailFields(span.attributes ?? {})
  const configQuery = useQuery({
    queryKey: ["labs", "skill", "configs", spaceId],
    queryFn: ({ signal }) => listSkillConfigs(spaceId, signal),
    staleTime: 60_000,
    enabled: labsApi && !!spaceId && !!fields,
  })
  if (!fields) return null

  const attrs = span.attributes ?? {}
  const durationMs = mcpCallDurationMs(span)
  const reportedInput = attrs["agentic.skill.input"]
  const reportedOutput = attrs["agentic.skill.output"]
  const callInput = spanIO(attrs, "input")
  const callOutput = spanIO(attrs, "output")
  const input = Object.hasOwn(attrs, "agentic.skill.input")
    ? reportedInput
    : callInput !== undefined
      ? callInput.value
      : (fields.input ?? fields.rawArguments ?? undefined)
  const output = Object.hasOwn(attrs, "agentic.skill.output")
    ? reportedOutput
    : callOutput !== undefined
      ? callOutput.value
      : (fields.output ?? undefined)
  const outcomeOutput =
    callOutput !== undefined ? callOutput.value : reportedOutput
  const failedRead = failedSkillReadName(span, outcomeOutput)
  const outcome = failedRead
    ? "error"
    : resolveToolSpanOutcome(span, outcomeOutput)
  const failed = outcome === "error"
  const sharedContent =
    children !== undefined && children !== null && children !== false
  const rawArguments =
    !sharedContent && fields.rawArguments !== input ? fields.rawArguments : null
  const invocation =
    fields.invocation !== fields.rawName ? fields.invocation : null
  const hasCallDetails = Boolean(
    fields.info.id ||
    invocation ||
    fields.byteRange ||
    fields.owner ||
    fields.provider ||
    fields.tags?.length ||
    fields.phase ||
    fields.tokens ||
    fields.errorType ||
    rawArguments ||
    (detailHeader && durationMs !== null)
  )
  const configs = labsApi ? (configQuery.data ?? []) : []
  const config = fields.info.name
    ? configs.find((item) => item.name === fields.info.name)
    : undefined
  const hasConfigDetails = Boolean(
    config?.description ||
    config?.allowedTools.length ||
    config?.license ||
    config?.compatibility
  )

  return (
    <LabsSection
      icon={Blocks}
      feature="skill"
      title={t("labs.skill.span.title")}
      variant="detail"
      summary={
        <>
          {!statusInContent && (
            <span
              className={cn(
                "inline-flex h-5 items-center rounded-[6px] px-1 text-xs leading-5 font-normal",
                failed
                  ? TONE_TAG_CLASS.error
                  : outcome === "ok"
                    ? TONE_TAG_CLASS.success
                    : TONE_TAG_CLASS.default
              )}
            >
              {t(
                failedRead
                  ? "labs.skill.provenance.readError"
                  : failed
                    ? "labs.skill.span.failed"
                    : outcome === "ok"
                      ? "labs.skill.span.succeeded"
                      : "spanContent.statusUnknown"
              )}
            </span>
          )}
          {!detailHeader && durationMs !== null ? (
            <span className="text-[var(--color-text-description)]">
              {formatDurationMs(durationMs)}
            </span>
          ) : null}
          {labsApi && overviewLinks && fields.info.name ? (
            // Mirrors the MCP card: the entry opens the in-place config
            // drawer without leaving the trace page.
            <button
              type="button"
              onClick={() => setConfigOpen(true)}
              className={LINK_CLASS}
            >
              {t("labs.skill.span.viewConfig")}
            </button>
          ) : null}
          {onBackToTrace ? (
            // Deep links land on the span detail; this exits back to the
            // trace preview without hunting for the panel's back button.
            <button
              type="button"
              onClick={onBackToTrace}
              className={cn(LINK_CLASS, "inline-flex items-center gap-0.5")}
            >
              <ChevronLeft className="size-3.5 shrink-0" />
              {t("labs.common.backToTrace")}
            </button>
          ) : null}
        </>
      }
    >
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 rounded-md border border-[var(--color-border-labs)] bg-[var(--color-bg-page)] p-3 text-xs leading-5">
        <Row label={t("labs.skill.span.skill")} value={fields.info.name} mono />
        {!sourceInContent && (
          <Row label={t("labs.skill.span.source")} value={fields.source} mono />
        )}
        <Row label={t("labs.skill.span.rawName")} value={fields.rawName} mono />
      </dl>

      {sharedContent ? (
        children
      ) : (
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-xs leading-5">
          <FullTextRow label={t("labs.skill.span.input")} value={input} />
          <FullTextRow label={t("labs.skill.span.output")} value={output} />
        </dl>
      )}

      {hasCallDetails && (
        <details className="text-xs" data-skill-call-details>
          <summary className="cursor-pointer rounded-md bg-[var(--color-bg-labs-tag)] px-2 py-1.5 text-[var(--color-labs)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-labs)]">
            {t("traceLabels.callInfo")}
          </summary>
          <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 leading-5">
            <Row
              label={t("labs.skill.span.toolId")}
              value={fields.info.id}
              mono
            />
            <Row
              label={t("labs.skill.span.invocation")}
              value={invocation}
              mono
            />
            <Row
              label={t("labs.skill.span.byteRange")}
              value={fields.byteRange}
              mono
            />
            {detailHeader && durationMs !== null ? (
              <Row
                label={t("traceLabels.latency")}
                value={formatDurationMs(durationMs)}
              />
            ) : null}
            <Row label={t("labs.skill.span.owner")} value={fields.owner} />
            <Row
              label={t("labs.skill.span.provider")}
              value={fields.provider}
            />
            <Row
              label={t("labs.skill.span.tags")}
              value={fields.tags?.join(", ")}
            />
            <Row label={t("labs.skill.span.phase")} value={fields.phase} />
            <TokenRow
              label={t("labs.skill.span.inputTokens")}
              value={fields.tokens?.input ?? null}
            />
            <TokenRow
              label={t("labs.skill.span.outputTokens")}
              value={fields.tokens?.output ?? null}
            />
            <TokenRow
              label={t("labs.skill.span.totalTokens")}
              value={fields.tokens?.total ?? null}
            />
            <Row
              label={t("labs.skill.span.error")}
              value={fields.errorType}
              mono
            />
            {rawArguments !== null && (
              <FullTextRow
                label={t("labs.skill.span.rawArgs")}
                value={rawArguments}
              />
            )}
          </dl>
        </details>
      )}

      {hasConfigDetails && (
        <details className="text-xs" data-skill-config-details>
          <summary className="cursor-pointer rounded-md bg-[var(--color-bg-labs-tag)] px-2 py-1.5 text-[var(--color-labs)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-labs)]">
            {t("labs.skill.drawer.metadata")}
          </summary>
          <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 leading-5">
            {config?.description ? (
              <FullTextRow
                label={t("labs.skill.span.description")}
                value={config.description}
                copy={false}
              />
            ) : null}
            <Row
              label={t("labs.skill.span.allowedTools")}
              value={config?.allowedTools.join(", ")}
              mono
            />
            <Row
              label={t("labs.skill.span.license")}
              value={config?.license}
              mono
            />
            <Row
              label={t("labs.skill.span.compatibility")}
              value={config?.compatibility}
            />
          </dl>
        </details>
      )}

      {/* Full overview-style drawer rendered in place: the SKILL.md tab opens
          by default and a missing config shows the import guide. In-trace
          call history locates its span; closing the drawer lands back on the
          trace detail. Delete stays on the overview page. */}
      {labsApi && overviewLinks ? (
        <>
          <SkillSpanConfigDrawer
            spaceId={spaceId}
            skillName={configOpen ? (fields.info.name ?? null) : null}
            config={config}
            configState={
              configQuery.data
                ? "ready"
                : configQuery.isError
                  ? "error"
                  : configQuery.isLoading
                    ? "loading"
                    : "ready"
            }
            configError={configQuery.isError}
            onRetryConfig={() => void configQuery.refetch()}
            onClose={() => setConfigOpen(false)}
            onImport={() => setImportOpen(true)}
            currentTraceId={traceId}
            onSelectSpan={
              onSelectSpan
                ? (spanId) => {
                    setConfigOpen(false)
                    onSelectSpan(spanId)
                  }
                : undefined
            }
            onNavigateToTrace={
              onNavigateToTrace
                ? (traceId, spanId) => {
                    setConfigOpen(false)
                    onNavigateToTrace(traceId, spanId)
                  }
                : undefined
            }
          />
          <SkillConfigImportDialog
            open={importOpen}
            onOpenChange={setImportOpen}
            spaceId={spaceId}
            observedSkillNames={fields.info.name ? [fields.info.name] : []}
          />
        </>
      ) : null}
    </LabsSection>
  )
}

// Same keys the overview page carries into trace deep links, so the drawer
// fallback keeps the current space / window context.

// Public trace pages do not provide private Agent/TimeFilter contexts.
// Keep their consumers inside the Labs-only drawer branch.
function SkillSpanConfigDrawer({
  spaceId,
  ...props
}: Omit<ComponentProps<typeof SkillDrawer>, "stat"> & {
  spaceId: string
}) {
  const { currentSpace } = useSpace()
  const canManage = currentSpace?.role === 1
  const { currentAgent } = useAgent()
  const {
    timeRange: globalTimeRange,
    isCustom,
    fromTimestamp,
    toTimestamp,
    refreshTick,
  } = useTimeFilter()
  const location = useLocation()
  const searchParams = new URLSearchParams(location.search)
  const agentProduct =
    searchParams.get("agent") || currentAgent?.agent_product || ""
  const urlRange = searchParams.get("range")
  const timeRange = urlRange || globalTimeRange
  const urlFrom = searchParams.get("from")
  const urlTo = searchParams.get("to")
  const hasUrlWindow = !!urlFrom && !!urlTo
  const window = useMemo(() => {
    void refreshTick
    return hasUrlWindow
      ? { from: urlFrom!, to: urlTo! }
      : !urlRange && isCustom && fromTimestamp && toTimestamp
        ? { from: fromTimestamp, to: toTimestamp }
        : getPresetTimeWindow(timeRange)
  }, [
    hasUrlWindow,
    urlFrom,
    urlTo,
    urlRange,
    isCustom,
    fromTimestamp,
    toTimestamp,
    timeRange,
    refreshTick,
  ])
  const { skillName } = props
  const scanParams = {
    spaceId,
    agentProduct,
    timeRange,
    from: window.from,
    to: window.to,
    agentUuid: searchParams.get("agent_uuid") || undefined,
  }
  const statsQuery = useQuery({
    queryKey: ["labs", "skill", "stats", scanParams, refreshTick],
    queryFn: () => listSkillStats(scanParams),
    enabled:
      !!skillName &&
      !!spaceId &&
      !!agentProduct &&
      (!!timeRange || (!!window.from && !!window.to)),
  })
  const data = statsQuery.data
  // Open immediately with the stored config, then fill stats from the same
  // observability window as the overview page.
  const stat = skillName
    ? (data?.skills.find((item) => item.name === skillName) ?? null)
    : null
  // Fallback for hosts without in-page handlers: deep-link the called span.
  const traceHref = useCallback(
    (
      traceId: string,
      spanId: string,
      call?: import("./types").SkillCallRef
    ) => {
      const exactSearch = new URLSearchParams(location.search)
      exactSearch.set("range", "custom")
      exactSearch.set("from", window.from)
      exactSearch.set("to", window.to)
      return skillTraceHref(
        exactSearch.toString(),
        call ?? { traceId, spanId, agentProduct },
        agentProduct
      )
    },
    [location.search, agentProduct, window]
  )
  return (
    <SkillDrawer
      {...props}
      stat={stat}
      statState={
        data
          ? "ready"
          : statsQuery.isError
            ? "error"
            : statsQuery.isLoading
              ? "loading"
              : "ready"
      }
      statsError={statsQuery.isError}
      onRetryStats={() => void statsQuery.refetch()}
      history={{ params: scanParams, load: listSkillCallHistory }}
      onImport={canManage ? props.onImport : undefined}
      traceHref={traceHref}
      // Read-only like the MCP drawer: reimport / remove stay on the
      // overview page; the no-config import guide still works.
      readOnly
    />
  )
}

/** Two-line evidence preview with full text and exact-content copy. */
function FullTextRow({
  label,
  value,
  copy = true,
}: {
  label: string
  value: unknown
  copy?: boolean
}) {
  const { t } = useTranslation()
  if (value === undefined) return null
  return (
    <Row
      label={label}
      wrap
      mono
      value={
        typeof value === "string" ? (
          <TraceTextContent
            content={value}
            previewLines={2}
            textClassName="leading-5"
            copyLabel={copy ? `${t("common.copy")}: ${label}` : undefined}
          />
        ) : (
          <SpanTypedContent
            value={value}
            copyLabel={copy ? `${t("common.copy")}: ${label}` : undefined}
          />
        )
      }
    />
  )
}

function TokenRow({ label, value }: { label: string; value: number | null }) {
  if (value === null || value === undefined) return null
  return <Row label={label} value={formatTokenCount(value)} mono />
}

function Row({
  label,
  value,
  mono,
  wrap,
}: {
  label: string
  value: React.ReactNode
  mono?: boolean
  wrap?: boolean
}) {
  if (value === null || value === undefined || value === "") return null
  return (
    <div className="contents">
      <dt className="text-[var(--color-text-description)]">{label}</dt>
      <dd
        className={cn(
          "min-w-0 text-[var(--color-text-primary)]",
          wrap
            ? "break-words whitespace-normal"
            : "break-all whitespace-normal",
          mono && "font-mono"
        )}
      >
        {value}
      </dd>
    </div>
  )
}
