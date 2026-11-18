import { useQuery } from "@tanstack/react-query"
import { ChevronLeft, Info, Plug } from "lucide-react"
import { useMemo, useState, type ComponentProps, type ReactNode } from "react"
import { useTranslation } from "react-i18next"
import { Link, useLocation } from "react-router-dom"

import { LabsSection } from "@/components/labs/shared/LabsSection"
import { TONE_TAG_CLASS } from "@/components/labs/shared/labsFormat"
import { TraceTextContent } from "@/components/traces/shared/TraceTextContent"
import { resolveToolSpanOutcome } from "@/components/traces-otel/spanContent"
import { spanIO } from "@/components/traces-otel/spanIO"
import { formatDurationMs } from "@/components/traces-otel/format"
import type { OTelSpan } from "@/components/traces-otel/types"
import { useAgent } from "@/contexts/AgentContext"
import { useTimeFilter } from "@/contexts/TimeFilterContext"
import {
  declaredMemorySemantics,
  isMemorySpanCall,
} from "@/labs/memory/memoryDisplay"
import { cn } from "@/lib/utils"
import { listMemoryObservations } from "@/labs/memory/memoryApi"
import { memoryMcpDetail } from "@/labs/memory/memoryMcpPresentation"

import {
  fetchMcpConfigTools,
  getMcpServerDetail,
  listMcpConfigs,
  listMcpServers,
} from "./mcpApi"
import { McpConfigImportDialog } from "./McpConfigImportDialog"
import { McpServerDescription } from "./McpServerDescription"
import { McpServerDrawer } from "./McpServerDrawer"
import {
  mcpCallDurationMs,
  paramsFromInputSchema,
  resolveMcpSpanInfo,
} from "./mcpDisplay"
import { useMcpOverviewHref } from "./useMcpOverviewHref"

const LINK_CLASS =
  "rounded-[6px] text-xs leading-5 text-[var(--color-labs)] underline decoration-[var(--color-border-labs)] underline-offset-4 hover:decoration-current focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-labs)]"

export function McpSpanCard({
  span,
  spaceId,
  traceId,
  onSelectSpan,
  onNavigateToTrace,
  onBackToTrace,
  overviewLinks = true,
  labsApi = true,
  children,
  statusInContent = false,
}: {
  children?: ReactNode
  /** Hide the badge only when shared content actually presents the outcome. */
  statusInContent?: boolean
  span: OTelSpan
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
  /** Gates the config / tool-snapshot queries; public pages must pass false. */
  labsApi?: boolean
}) {
  const { t } = useTranslation()
  const overviewHref = useMcpOverviewHref()
  // In-place overview-style drawer: closing returns to the trace detail
  // because the page never navigated away.
  const [configOpen, setConfigOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [importedServerId, setImportedServerId] = useState<string | null>(null)
  const info = resolveMcpSpanInfo(span.attributes ?? {})
  const serverId = info?.serverId ?? ""
  const configsQuery = useQuery({
    queryKey: ["labs", "mcp", "configs", spaceId],
    queryFn: ({ signal }) => listMcpConfigs(spaceId, signal),
    staleTime: 60_000,
    enabled: labsApi && !!info && !!spaceId,
  })
  const configs = configsQuery.data ?? []
  // Memory-registry members render in MemorySpanCard; drop them here so the
  // same call never shows in both cards (a declared config or a verified
  // memory result shape counts as memory — mirrors the summary-level split).
  const isMemory =
    !!info &&
    isMemorySpanCall(
      info,
      declaredMemorySemantics(labsApi ? configs : undefined),
      span.attributes?.["gen_ai.tool.call.result"],
      span.attributes?.["agentic.tool.category"]
    )
  const config =
    labsApi && info?.serverId
      ? configs.find((item) => item.serverId === info.serverId)
      : undefined
  const { data: toolsData } = useQuery({
    queryKey: [
      "labs",
      "mcp",
      "config-tools",
      spaceId,
      serverId,
      config?.importedAt,
    ],
    queryFn: ({ signal }) => fetchMcpConfigTools(spaceId, serverId, signal),
    enabled: labsApi && !!config && !!serverId && !!spaceId && !isMemory,
    staleTime: 60_000,
  })
  if (!info) return null
  if (isMemory) return null

  const outcome = resolveToolSpanOutcome(
    span,
    spanIO(span.attributes ?? {}, "output")?.value
  )
  const failed = outcome === "error"
  const durationMs = mcpCallDurationMs(span)
  const toolInfo =
    labsApi && info.toolName
      ? toolsData?.tools.find((tool) => tool.name === info.toolName)
      : undefined
  // Review decision: the trace-carried description wins, the imported
  // config's tools/list snapshot fills in when the span carries none, and
  // neither present means no row. Wrong values in either source are shown
  // as-is; there is no conflict to resolve.
  const toolDescription = info.description ?? toolInfo?.description
  const params = paramsFromInputSchema(toolInfo?.inputSchema)
  // Guide entry: config imported but this tool still has no description
  // anywhere (stale snapshot, tool missing from it) — point at the sync
  // action on the overview page.
  const missingToolDescription =
    labsApi && overviewLinks && !!config && !!info.toolName && !toolDescription

  return (
    <LabsSection
      data-product-guide="mcp-span"
      icon={Plug}
      feature="mcp"
      variant="detail"
      beta
      title={t("labs.mcp.span.title")}
      summary={
        <>
          {/* Call status first; the config entry comes after so the badge is
              not read as the outcome of "import config". Opens the in-place
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
                ? t("labs.mcp.span.failed")
                : outcome === "ok"
                  ? t("labs.mcp.span.succeeded")
                  : t("spanContent.statusUnknown")}
            </span>
          )}
          {labsApi && overviewLinks && info.serverId ? (
            <button
              type="button"
              onClick={() => {
                setImportedServerId(null)
                setConfigOpen(true)
              }}
              data-product-guide="mcp-config-open"
              className={LINK_CLASS}
            >
              {t("labs.mcp.span.viewConfig")}
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
        />
      ) : null}

      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 rounded-md border border-[var(--color-border-labs)] bg-[var(--color-bg-page)] p-3 text-xs leading-5">
        <Row label={t("labs.mcp.span.server")} value={info.serverId} mono />
        <Row label={t("labs.mcp.span.tool")} value={info.toolName} mono />
        <Row
          label={t("labs.mcp.span.toolDescription")}
          value={
            toolDescription ? (
              <TraceTextContent
                content={toolDescription}
                previewLines={2}
                textClassName="leading-5"
              />
            ) : null
          }
          wrap
          mono
        />
      </dl>
      {children}
      {durationMs !== null || (failed && span.status?.message) || params ? (
        <details className="border-t border-border pt-3 text-xs">
          <summary className="cursor-pointer rounded-md bg-[var(--color-bg-labs-tag)] px-2 py-1.5 text-[var(--color-labs)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-labs)]">
            {t("spanContent.callDetails")}
          </summary>
          <dl className="mt-3 grid min-w-0 grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-xs leading-5">
            <Row
              label={t("labs.mcp.span.duration")}
              value={durationMs === null ? null : formatDurationMs(durationMs)}
            />
            {failed && span.status?.message ? (
              <Row
                label={t("labs.mcp.span.error")}
                value={
                  <span className="break-all whitespace-normal text-[var(--color-error)]">
                    {span.status.message}
                  </span>
                }
              />
            ) : null}
            {/* Parameters live inside the dl: the label stays a caption column
            instead of looking like a sibling of the parameter rows. */}
            {params && params.length === 0 ? (
              <Row
                label={t("labs.mcp.span.params")}
                value={
                  <span className="text-[var(--color-text-secondary)]">
                    {t("labs.mcp.span.noParams")}
                  </span>
                }
              />
            ) : null}
            {params && params.length > 0 ? (
              <Row
                label={t("labs.mcp.span.params")}
                wrap
                value={
                  <ul className="flex flex-col gap-1.5">
                    {params.map((param) => (
                      <li
                        key={param.name}
                        className="flex min-w-0 flex-wrap items-start gap-x-2 gap-y-0.5 text-xs leading-5"
                      >
                        <span className="min-w-0 font-mono break-all text-[var(--color-text-primary)]">
                          {param.name}
                        </span>
                        {param.type ? (
                          <span className="shrink-0 rounded-[4px] bg-[var(--color-bg-hover)] px-1 font-mono text-[10px] leading-4 text-[var(--color-text-secondary)]">
                            {param.type}
                          </span>
                        ) : null}
                        {param.required ? (
                          <span className="shrink-0 text-[10px] leading-4 text-[var(--color-alert)]">
                            {t("labs.mcp.span.paramRequired")}
                          </span>
                        ) : null}
                        {param.description ? (
                          <TraceTextContent
                            content={param.description}
                            previewLines={2}
                            textClassName="leading-5"
                            className="min-w-0 flex-[1_1_160px] text-[var(--color-text-description)]"
                          />
                        ) : null}
                      </li>
                    ))}
                  </ul>
                }
              />
            ) : null}
          </dl>
        </details>
      ) : null}

      {missingToolDescription ? (
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs leading-5 text-[var(--color-text-description)]">
          <Info className="size-3.5 shrink-0" />
          {t("labs.mcp.span.missingToolDescription")}
          {info.serverId ? (
            <Link
              to={overviewHref(info.serverId, "config")}
              className={LINK_CLASS}
            >
              {t("labs.mcp.span.syncToolsHint")}
            </Link>
          ) : null}
        </p>
      ) : null}

      {/* Full overview-style drawer rendered in place; the config tab opens
          by default — the stored config is read-only (sync/reimport/delete
          stay on the overview page) and a missing config shows the import
          guide. In-trace recent calls locate their span; closing the drawer
          lands back on the trace detail. */}
      {labsApi && overviewLinks ? (
        <>
          <McpSpanConfigDrawer
            spaceId={spaceId}
            serverId={
              labsApi && overviewLinks && configOpen
                ? (importedServerId ?? info.serverId ?? null)
                : null
            }
            config={
              importedServerId
                ? configs.find((item) => item.serverId === importedServerId)
                : config
            }
            configStatus={
              configsQuery.isPending
                ? "loading"
                : configsQuery.isError
                  ? "error"
                  : "ready"
            }
            configError={
              configsQuery.isError ? t("labs.mcp.topic.configsFailed") : null
            }
            onRetryConfigs={() => void configsQuery.refetch()}
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
            open={importOpen}
            onOpenChange={setImportOpen}
            spaceId={spaceId}
            observedServerIds={info.serverId ? [info.serverId] : []}
            onImported={(saved) => {
              if (saved[0])
                setImportedServerId(
                  saved.find((item) => item.serverId === info.serverId)
                    ?.serverId ?? saved[0].serverId
                )
            }}
          />
        </>
      ) : null}
    </LabsSection>
  )
}

// Public trace pages do not provide private Agent/TimeFilter contexts.
// Keep their consumers inside the Labs-only drawer branch.
export function McpSpanConfigDrawer({
  spaceId,
  ...props
}: Omit<ComponentProps<typeof McpServerDrawer>, "detail"> & {
  spaceId: string
}) {
  const { t } = useTranslation()
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
  const from = hasUrlWindow
    ? urlFrom
    : !urlRange && isCustom
      ? fromTimestamp
      : undefined
  const to = hasUrlWindow
    ? urlTo
    : !urlRange && isCustom
      ? toTimestamp
      : undefined
  const { serverId, config } = props
  const isMemory = props.feature === "memory"
  const scanParams = useMemo(
    () => ({ spaceId, agentProduct, timeRange, from, to }),
    [spaceId, agentProduct, timeRange, from, to]
  )
  const { data: scan } = useQuery({
    queryKey: ["labs", "mcp", "servers", scanParams, refreshTick],
    queryFn: ({ signal }) => listMcpServers(scanParams, signal),
    enabled:
      !isMemory &&
      !!serverId &&
      !!spaceId &&
      !!agentProduct &&
      (!!timeRange || (!!from && !!to)),
  })
  const memoryParams = useMemo(
    () => ({ ...scanParams, agentUuid: currentAgent?.uuid }),
    [scanParams, currentAgent?.uuid]
  )
  const { data: memoryScan } = useQuery({
    queryKey: ["labs", "memory", "observations", memoryParams, refreshTick],
    queryFn: ({ signal }) => listMemoryObservations(memoryParams, signal),
    enabled: isMemory && !!serverId && !!spaceId && !!agentProduct,
  })
  const memoryServer = memoryScan?.servers.find(
    (server) => server.serverId === serverId
  )
  const observedDetail = isMemory
    ? memoryServer
      ? memoryMcpDetail(memoryServer, true, t)
      : undefined
    : scan?.serverDetails?.get(serverId ?? "")
  // Open immediately with the stored config, then fill calls and tool stats
  // from the same observability window as the overview page.
  const detail = !serverId
    ? null
    : (observedDetail ??
      getMcpServerDetail(config) ?? {
        serverId,
        health: "unknown" as const,
        sourceAgents: [],
        lastSeen: null,
        stats: {
          calledToolCount: 0,
          calls: 0,
          errorRate: 0,
          measuredCalls: 0,
          p50Ms: null,
          p95Ms: null,
        },
        tools: [],
        recentCalls: [],
      })
  return <McpServerDrawer {...props} detail={detail} />
}

function Row({
  label,
  value,
  mono,
  wrap,
}: {
  label: string
  value: ReactNode
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
