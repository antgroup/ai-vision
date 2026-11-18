import { useQuery } from "@tanstack/react-query"
import { ChevronLeft, Database } from "lucide-react"
import { useState, type ReactNode } from "react"
import { useTranslation } from "react-i18next"

import { LabsSection } from "@/components/labs/shared/LabsSection"
import { TONE_TAG_CLASS } from "@/components/labs/shared/labsFormat"
import {
  SpanContentEnhancement,
  ToolResultBody,
} from "@/components/traces-otel/SpanContentEnhancement"
import { MemoryResultContent } from "./MemoryResultContent"
import type { SpanContentModel } from "@/components/traces-otel/spanContent"
import { resolveToolSpanOutcome } from "@/components/traces-otel/spanContent"
import { spanIO } from "@/components/traces-otel/spanIO"
import { formatDurationMs } from "@/components/traces-otel/format"
import type { OTelSpan } from "@/components/traces-otel/types"
import { cn } from "@/lib/utils"

import { McpConfigImportDialog } from "../mcp/McpConfigImportDialog"
import { McpServerDescription } from "../mcp/McpServerDescription"
import { McpSpanConfigDrawer } from "../mcp/McpSpanCard"
import { listMcpConfigs } from "../mcp/mcpApi"
import { mcpCallDurationMs, resolveMcpSpanInfo } from "../mcp/mcpDisplay"

import {
  declaredMemorySemantics,
  isMemorySpanCall,
  memoryHitsFromResult,
  declaredMemoryToolCategory,
} from "./memoryDisplay"

const LINK_CLASS =
  "rounded-[6px] px-1.5 py-0.5 text-xs leading-5 text-[var(--color-labs)] underline decoration-[var(--color-border-labs)] underline-offset-4 hover:bg-[var(--color-bg-labs-tag)] hover:decoration-current focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-labs)]"

/**
 * Detail-variant memory card for a single memory-server call (span detail).
 * Recognition mirrors the summary-level split: declared memory servers and
 * verified result shapes render here, and every other MCP call stays on the
 * MCP span card. The MCP config drawer is reused because the underlying
 * server config — the declaration source — stays reachable in place.
 */
export function MemorySpanCard({
  span,
  children,
  contentModel,
  output,
  statusInContent = false,
  spaceId,
  traceId,
  onSelectSpan,
  onNavigateToTrace,
  onBackToTrace,
  overviewLinks = true,
  labsApi = true,
}: {
  span: OTelSpan
  children?: ReactNode
  contentModel?: SpanContentModel | null
  output?: unknown
  statusInContent?: boolean
  spaceId: string
  /** Current trace id; the drawer's recent calls use it to tell in-trace
   * calls (locate) from other traces' (fallback link). */
  traceId?: string
  /** Lets the drawer's recent calls locate their span in the trace tree. */
  onSelectSpan?: (spanId: string) => void
  /** Lets the drawer's recent calls switch the page to another trace (and
   * focus the called span there) instead of rendering a dead same-URL link. */
  onNavigateToTrace?: (traceId: string, spanId?: string) => void
  /** Deselects the span (back to the trace preview); offered as an in-card
   * exit for deep links that land directly on the span detail. */
  onBackToTrace?: () => void
  /** Off on shared trace pages, where the overview links are not reachable. */
  overviewLinks?: boolean
  /** Gates the config queries; public pages must pass false. */
  labsApi?: boolean
}) {
  const { t } = useTranslation()
  // In-place overview-style drawer: closing returns to the trace detail
  // because the page never navigated away.
  const [configOpen, setConfigOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const info = resolveMcpSpanInfo(span.attributes ?? {})
  const { data: configs = [] } = useQuery({
    queryKey: ["labs", "mcp", "configs", spaceId],
    queryFn: ({ signal }) => listMcpConfigs(spaceId, signal),
    staleTime: 60_000,
    enabled: labsApi && !!info && !!spaceId,
  })
  if (!info) return null
  // Same predicate as MemoryTraceSummary: declared memory servers always
  // count, undeclared ones join only on a verified memory result shape.
  if (
    !isMemorySpanCall(
      info,
      declaredMemorySemantics(labsApi ? configs : undefined),
      span.attributes?.["gen_ai.tool.call.result"],
      span.attributes?.["agentic.tool.category"]
    )
  ) {
    return null
  }

  const config =
    labsApi && info.serverId
      ? configs.find((item) => item.serverId === info.serverId)
      : undefined
  const outcome = resolveToolSpanOutcome(
    span,
    spanIO(span.attributes ?? {}, "output")?.value
  )
  const failed = outcome === "error"
  const durationMs = mcpCallDurationMs(span)
  // Outcome line: only when the hit count is actually parseable from the
  // result; hidden otherwise instead of guessing a number.
  const hits = failed
    ? 0
    : (memoryHitsFromResult(span.attributes?.["gen_ai.tool.call.result"]) ?? 0)

  return (
    <LabsSection
      icon={Database}
      feature="memory"
      variant="detail"
      beta
      title={t("labs.memory.span.title")}
      summary={
        <>
          {/* Call status first; the config entry comes after so the badge is
              not read as the outcome of "view config". Opens the in-place
              config drawer (below) without leaving the trace page. */}
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
              {failed
                ? t("labs.memory.span.failed")
                : outcome === "ok"
                  ? t("labs.memory.span.succeeded")
                  : t("spanContent.statusUnknown")}
            </span>
          )}
          {labsApi && overviewLinks && info.serverId ? (
            <button
              type="button"
              onClick={() => setConfigOpen(true)}
              className={LINK_CLASS}
            >
              {t("labs.memory.span.viewConfig")}
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
      {info.serverId ? (
        <McpServerDescription
          server={{ serverId: info.serverId }}
          config={config}
          feature="memory"
        />
      ) : null}

      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 rounded-lg border border-[var(--color-border-labs)] bg-[var(--color-bg-page)] px-3 py-2 text-xs leading-5">
        <Row label={t("labs.memory.span.server")} value={info.serverId} mono />
        <Row label={t("labs.memory.span.tool")} value={info.toolName} mono />
        <Row
          label={t("labs.memory.span.duration")}
          value={durationMs === null ? null : formatDurationMs(durationMs)}
        />
        {failed && span.status?.message ? (
          <Row
            label={t("labs.memory.span.error")}
            value={
              <span className="break-all whitespace-normal text-[var(--color-error)]">
                {span.status.message}
              </span>
            }
          />
        ) : null}
      </dl>

      {contentModel ? (
        <SpanContentEnhancement
          key={span.span_id}
          model={contentModel}
          embedded
          argumentLabel={(key) =>
            t(`labs.memory.arguments.${key}`, { defaultValue: key })
          }
          resultView={
            contentModel.kind === "tool" &&
            contentModel.output !== undefined ? (
              <MemoryResultContent
                value={output !== undefined ? output : contentModel.output}
                fallback={<ToolResultBody model={contentModel} />}
                outcome={contentModel.outcome}
                category={declaredMemoryToolCategory(
                  declaredMemorySemantics(labsApi ? configs : undefined),
                  info.serverId,
                  info.toolName
                )}
              />
            ) : undefined
          }
        />
      ) : (
        children
      )}

      {!contentModel && hits > 0 ? (
        <p className="text-xs leading-5 text-[var(--color-text-description)]">
          {t("labs.memory.trace.hits", { count: hits })}
        </p>
      ) : null}

      {/* Full overview-style drawer rendered in place; the config tab opens
          by default and the stored config is read-only (sync/reimport/delete
          stay on the overview page). In-trace recent calls locate their span;
          closing the drawer lands back on the trace detail. */}
      {labsApi && overviewLinks ? (
        <>
          <McpSpanConfigDrawer
            feature="memory"
            spaceId={spaceId}
            serverId={configOpen ? (info.serverId ?? null) : null}
            config={config}
            initialTab="config"
            onClose={() => setConfigOpen(false)}
            onImport={() => setImportOpen(true)}
            onDeleteConfig={() => {}}
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
            readOnly
          />
          <McpConfigImportDialog
            feature="memory"
            open={importOpen}
            onOpenChange={setImportOpen}
            spaceId={spaceId}
            observedServerIds={info.serverId ? [info.serverId] : []}
          />
        </>
      ) : null}
    </LabsSection>
  )
}

function Row({
  label,
  value,
  mono,
}: {
  label: string
  value: ReactNode
  mono?: boolean
}) {
  if (value === null || value === undefined || value === "") return null
  return (
    <div className="contents">
      <dt className="text-[var(--color-text-description)]">{label}</dt>
      <dd
        className={cn(
          "min-w-0 break-all whitespace-normal text-[var(--color-text-primary)]",
          mono && "font-mono"
        )}
      >
        {value}
      </dd>
    </div>
  )
}
