import { QueryStateNotice } from "@/components/bizcharts/QueryStateNotice"
import { CopyableCode } from "@/components/labs/shared/CopyableCode"
import { TraceCopyButton } from "@/components/traces/shared/TraceTextContent"
import { useMemo, useRef, useState, type ReactNode } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Database, Plug, X } from "lucide-react"
import { useTranslation } from "react-i18next"
import { LabsMetricLabel } from "@/components/labs/shared/LabsMetricLabel"
import { Link, useLocation } from "react-router-dom"

import { Button } from "@/components/ui/button"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from "@/components/ui/sheet"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  TONE_TEXT_CLASS,
  formatPercent,
} from "@/components/labs/shared/labsFormat"
import { useSpace } from "@/contexts/SpaceContext"
import { cn } from "@/lib/utils"

import {
  fetchMcpConfigTools,
  syncMcpConfigTools,
  listMcpCallHistory,
} from "./mcpApi"
import { McpConfigCard, McpConfigEmpty } from "./McpConfigCard"
import { McpServerDescription } from "./McpServerDescription"
import { McpToolsSyncDialog } from "./McpToolsSyncDialog"
import {
  HEALTH_TAG_CLASS,
  compareMcpTimestamps,
  errorRateTone,
  mcpStatusCoverage,
  formatMcpDuration,
  formatMcpTime,
  mcpMeasurementCoverage,
} from "./mcpDisplay"
import type {
  McpCallRef,
  McpQueryParams,
  McpScanMeta,
  McpDrawerTab,
  McpServerConfig,
  McpServerDetail,
  McpToolInfo,
  McpToolStat,
} from "./types"

const CALL_LINK_CLASS =
  "shrink-0 rounded-[6px] text-xs leading-5 text-[var(--color-labs)] underline decoration-[var(--color-border-labs)] underline-offset-4 hover:decoration-current focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-labs)]"

interface McpServerDrawerProps {
  queryParams?: McpQueryParams
  scan?: McpScanMeta
  observationsStatus?: "idle" | "loading" | "error" | "ready"
  observationsError?: string | null
  observationsLoading?: boolean
  observationsUpdatedAt?: number
  onRetryObservations?: () => void
  configStatus?: "loading" | "error" | "ready"
  configError?: string | null
  configUpdatedAt?: number
  onRetryConfigs?: () => void
  deleting?: boolean
  deleteError?: string | null
  feature?: "mcp" | "memory"
  serverId: string | null
  detail: McpServerDetail | null
  config?: McpServerConfig
  initialTab?: McpDrawerTab
  /** Topic hosts control this with their URL; Trace hosts keep local tabs. */
  tab?: string
  onTabChange?: (tab: string) => void
  /** Memory drawer: an extra leading tab rendered before the MCP tabs. */
  leadingTab?: { value: string; label: string; content: ReactNode }
  recentCallsNote?: ReactNode
  queryWindow?: { from: string; to: string }
  onClose: () => void
  onImport: () => void
  onDeleteConfig: (serverId: string) => void
  /** Trace-page mode: the config tab is view-only; sync/reimport/delete
   * stay on the overview page. */
  readOnly?: boolean
  /** Provided on the trace page: recent calls locate their span in place
   * instead of navigating back to the trace page. */
  onSelectSpan?: (spanId: string) => void
  /** The trace page's trace id; calls from this trace locate their span in
   * the tree, calls from other traces navigate to that trace instead. */
  currentTraceId?: string
  /** Provided on the trace page: recent calls from other traces switch the
   * page to that trace (focusing the called span) instead of rendering a
   * link. Pages without the handler fall back to a deep link that opens the
   * trace detail and focuses the span. */
  onNavigateToTrace?: (traceId: string, spanId?: string) => void
}

export function McpServerDrawer(props: McpServerDrawerProps) {
  const { currentSpace } = useSpace()
  const spaceId = currentSpace?.id ?? ""
  const canManage = currentSpace?.role === 1
  // Auth inputs and pending syncs belong to the exact imported config version.
  // A refetched endpoint must never inherit credentials entered for the old one.
  return (
    <McpServerDrawerContent
      key={JSON.stringify([
        spaceId,
        props.serverId,
        props.config?.importedAt,
        props.queryParams,
      ])}
      {...props}
      spaceId={spaceId}
      canManage={canManage}
      readOnly={props.readOnly || !canManage}
    />
  )
}

function McpServerDrawerContent({
  feature = "mcp",
  serverId,
  detail,
  config,
  initialTab = "tools",
  tab,
  onTabChange,
  leadingTab,
  recentCallsNote,
  queryWindow,
  onClose,
  onImport,
  onDeleteConfig,
  readOnly = false,
  onSelectSpan,
  currentTraceId,
  onNavigateToTrace,
  spaceId,
  canManage,
  queryParams,
  scan,
  observationsStatus = "ready",
  observationsError,
  observationsLoading,
  observationsUpdatedAt,
  onRetryObservations,
  configStatus = "ready",
  configError,
  configUpdatedAt,
  onRetryConfigs,
  deleting,
  deleteError,
}: McpServerDrawerProps & { spaceId: string; canManage: boolean }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [syncOpen, setSyncOpen] = useState(false)
  const [localTab, setLocalTab] = useState<string>(initialTab)
  const syncSession = useRef(0)
  const server = detail
  const historyUnavailable =
    observationsStatus !== "ready" &&
    !(queryParams?.from && queryParams.to) &&
    !server?.recentCalls.length

  const toolsQueryKey = [
    "labs",
    "mcp",
    "config-tools",
    spaceId,
    config?.serverId,
    config?.importedAt,
  ]
  const toolsQuery = useQuery({
    queryKey: toolsQueryKey,
    queryFn: ({ signal }) =>
      config
        ? fetchMcpConfigTools(spaceId, config.serverId, signal)
        : Promise.resolve({ tools: [], syncedAt: null }),
    enabled: !!serverId && !!config && !!spaceId,
    staleTime: 60_000,
  })
  const toolsData = toolsQuery.data
  const syncMutation = useMutation({
    mutationFn: ({
      headerValues,
    }: {
      headerValues?: Record<string, string>
      session: number
    }) =>
      config
        ? syncMcpConfigTools(
            spaceId,
            config.serverId,
            headerValues,
            config.importedAt
          )
        : Promise.reject(new Error("No MCP config")),
    onSuccess: (result, request) => {
      queryClient.setQueryData(toolsQueryKey, result)
      if (request.session === syncSession.current) setSyncOpen(false)
    },
  })
  const syncError = syncMutation.error?.message ?? null
  const toolInfoByName = useMemo(() => {
    const map = new Map<string, McpToolInfo>()
    toolsData?.tools.forEach((tool) => map.set(tool.name, tool))
    return map
  }, [toolsData])

  const requestSync = () => {
    if (!config || readOnly) return
    syncSession.current += 1
    syncMutation.reset()
    // Auth values were masked on import, so declared headers need the
    // dialog; configs without headers can sync straight away.
    if (config.headerKeys.length) {
      setSyncOpen(true)
    } else {
      syncMutation.mutate({ session: syncSession.current })
    }
  }

  const FeatureIcon = feature === "memory" ? Database : Plug

  return (
    <Sheet open={!!serverId} onOpenChange={(open) => !open && onClose()}>
      <SheetContent
        showCloseButton={false}
        className="flex !w-full !max-w-full flex-col gap-0 overflow-hidden p-0 sm:!w-[min(960px,85vw)] sm:!max-w-[85vw]"
      >
        <header className="flex shrink-0 items-start justify-between gap-3 border-b border-[var(--color-border-labs)] bg-[var(--color-bg-labs)] px-4 py-3 sm:px-6">
          <div className="flex min-w-0 flex-col gap-1">
            <div className="flex min-w-0 items-start gap-2">
              <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-[var(--color-bg-labs-tag)] text-[var(--color-labs)]">
                <FeatureIcon className="size-4" aria-hidden="true" />
              </span>
              <div className="flex min-w-0 items-start gap-1 pt-1">
                <SheetTitle className="min-w-0 text-lg leading-6 font-medium break-all text-[var(--color-text-primary)]">
                  {serverId}
                </SheetTitle>
                <TraceCopyButton
                  value={serverId ?? ""}
                  label={`${t("common.copy")}: ${serverId}`}
                  className="size-6 text-[var(--color-text-description)] hover:text-[var(--color-labs)]"
                />
              </div>
              {server && server.health !== "unknown" ? (
                <span
                  className={cn(
                    "mt-1 inline-flex h-6 shrink-0 items-center rounded-[6px] px-2 text-xs leading-5 whitespace-nowrap",
                    HEALTH_TAG_CLASS[server.health]
                  )}
                >
                  {t(`labs.mcp.health.${server.health}`)}
                </span>
              ) : null}
            </div>
            {server ? (
              <McpServerDescription
                server={server}
                config={config}
                feature={feature}
              />
            ) : null}
            <SheetDescription className="text-sm leading-[22px] text-[var(--color-text-description)]">
              {server && server.stats.calls > 0
                ? server.sourceAgents.length
                  ? server.lastSeen
                    ? t("labs.mcp.card.usage", {
                        agents: server.sourceAgents.join(", "),
                        time: formatMcpTime(server.lastSeen, t),
                      })
                    : t("labs.mcp.measurement.usageUnknownTime", {
                        agents: server.sourceAgents.join(", "),
                      })
                  : t("labs.mcp.card.usageImported")
                : null}
            </SheetDescription>
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="size-8 shrink-0 rounded-[10px]"
            onClick={onClose}
            data-product-guide="mcp-config-close"
            aria-label={t("labs.common.close")}
          >
            <X className="size-4" />
          </Button>
        </header>

        {queryParams ? (
          <div className="flex flex-col gap-1 border-b px-4 py-2 text-xs text-muted-foreground sm:px-6">
            <span>
              {queryParams.from && queryParams.to
                ? t("labs.mcp.topic.scope", {
                    agent: queryParams.agentProduct,
                    from: queryParams.from,
                    to: queryParams.to,
                  })
                : t("labs.mcp.topic.relativeScope", {
                    agent: queryParams.agentProduct,
                    range: queryParams.timeRange,
                  })}
            </span>
            {queryParams.agentUuid ? (
              <span className="break-all">
                {t("labs.mcp.topic.instance", { id: queryParams.agentUuid })}
              </span>
            ) : null}
            {scan ? (
              <span>
                {t(
                  scan.truncated
                    ? "labs.mcp.topic.scanPartial"
                    : "labs.mcp.topic.scanComplete",
                  { scanned: scan.scanned, total: scan.availableTraces }
                )}
              </span>
            ) : null}
            <span>{t("labs.mcp.topic.configScope")}</span>
            <QueryStateNotice
              error={observationsError}
              loading={observationsLoading}
              hasData={observationsStatus === "ready"}
              updatedAt={observationsUpdatedAt}
              onRetry={onRetryObservations}
            />
          </div>
        ) : null}
        {!server ? (
          <div className="flex flex-1 items-center justify-center p-4 text-sm text-muted-foreground">
            {configStatus === "error" ? (
              <QueryStateNotice
                error={t("labs.mcp.topic.configsFailed")}
                inlineError
                hasData={false}
                onRetry={onRetryConfigs}
              />
            ) : (
              <p role="status">
                {t(
                  observationsStatus === "loading" || configStatus === "loading"
                    ? "labs.common.loading"
                    : observationsStatus === "error"
                      ? "labs.mcp.topic.statsUnknown"
                      : `labs.${feature}.drawer.notFound`
                )}
              </p>
            )}
          </div>
        ) : (
          <Tabs
            value={tab ?? localTab}
            onValueChange={(value) => {
              if (onTabChange) onTabChange(value)
              else setLocalTab(value)
            }}
            className="flex min-h-0 flex-1 flex-col gap-0"
          >
            <TabsList
              variant="line"
              className="h-auto shrink-0 flex-wrap px-4 pt-2 group-data-horizontal/tabs:h-auto sm:px-6"
            >
              {leadingTab ? (
                <TabsTrigger
                  value={leadingTab.value}
                  className="h-8 flex-none focus-visible:outline-[var(--color-labs)] data-[state=active]:text-[var(--color-labs)] data-[state=active]:after:bg-[var(--color-labs)] data-[state=active]:after:opacity-100 dark:data-[state=active]:text-[var(--color-labs)]"
                >
                  {leadingTab.label}
                </TabsTrigger>
              ) : null}
              <TabsTrigger
                value="tools"
                className="h-8 flex-none focus-visible:outline-[var(--color-labs)] data-[state=active]:text-[var(--color-labs)] data-[state=active]:after:bg-[var(--color-labs)] data-[state=active]:after:opacity-100 dark:data-[state=active]:text-[var(--color-labs)]"
              >
                {t(
                  server.callEntity
                    ? `labs.memory.${server.callEntity}.calledTitle`
                    : "labs.mcp.tools.calledTitle"
                )}
                <span className="font-stats text-xs text-[var(--color-text-description)]">
                  {observationsStatus === "ready" ? server.tools.length : "—"}
                </span>
              </TabsTrigger>
              <TabsTrigger
                value="calls"
                className="h-8 flex-none focus-visible:outline-[var(--color-labs)] data-[state=active]:text-[var(--color-labs)] data-[state=active]:after:bg-[var(--color-labs)] data-[state=active]:after:opacity-100 dark:data-[state=active]:text-[var(--color-labs)]"
              >
                {t("labs.mcp.drawer.tabCalls")}
              </TabsTrigger>
              <TabsTrigger
                value="config"
                className="h-8 flex-none focus-visible:outline-[var(--color-labs)] data-[state=active]:text-[var(--color-labs)] data-[state=active]:after:bg-[var(--color-labs)] data-[state=active]:after:opacity-100 dark:data-[state=active]:text-[var(--color-labs)]"
              >
                {t("labs.mcp.drawer.tabConfig")}
                {config ? null : (
                  <span className="text-xs text-[var(--color-text-description)]">
                    {t(
                      configStatus === "ready"
                        ? "labs.mcp.drawer.configMissing"
                        : configStatus === "error"
                          ? "labs.mcp.topic.configsFailed"
                          : "labs.common.loading"
                    )}
                  </span>
                )}
              </TabsTrigger>
            </TabsList>
            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3 sm:px-6">
              {leadingTab ? (
                <TabsContent value={leadingTab.value}>
                  {leadingTab.content}
                </TabsContent>
              ) : null}
              <TabsContent value="tools" className="flex flex-col gap-3">
                <p className="text-xs leading-5 text-[var(--color-text-description)]">
                  {t(
                    server.callEntity
                      ? `labs.memory.${server.callEntity}.scopeNote`
                      : "labs.mcp.tools.scopeNote"
                  )}
                </p>
                {observationsStatus === "ready" ? (
                  <ToolTable
                    callEntity={server.callEntity}
                    tools={server.tools}
                    toolInfoByName={toolInfoByName}
                    queryParams={queryParams}
                  />
                ) : (
                  <p className="text-sm text-muted-foreground">
                    {t("labs.mcp.topic.statsUnknown")}
                  </p>
                )}
              </TabsContent>
              <TabsContent value="calls" className="space-y-3">
                {historyUnavailable ? (
                  <p role="status" className="text-sm text-muted-foreground">
                    {t(
                      observationsStatus === "idle"
                        ? "dashboard.noAgentDesc"
                        : observationsStatus === "loading"
                          ? "labs.common.loading"
                          : "labs.mcp.topic.statsUnknown"
                    )}
                  </p>
                ) : (
                  <>
                    {recentCallsNote}
                    <RecentCalls
                      key={server.serverId}
                      calls={server.recentCalls}
                      queryParams={queryParams}
                      onAdjustRange={onClose}
                      serverId={server.serverId}
                      totalCalls={
                        observationsStatus === "ready"
                          ? server.stats.calls
                          : undefined
                      }
                      onSelectSpan={onSelectSpan}
                      currentTraceId={currentTraceId}
                      onNavigateToTrace={onNavigateToTrace}
                      queryWindow={queryWindow}
                    />
                  </>
                )}
              </TabsContent>
              <TabsContent
                value="config"
                data-product-guide="mcp-config"
                data-guide-server={serverId}
                data-guide-state={
                  configStatus === "ready"
                    ? config
                      ? "ready"
                      : "empty"
                    : configStatus
                }
              >
                {configStatus !== "ready" ? (
                  <div className="mb-3">
                    <p className="text-xs text-muted-foreground">
                      {t(
                        configStatus === "error"
                          ? "labs.mcp.topic.configsFailed"
                          : "labs.common.loading"
                      )}
                    </p>
                    <QueryStateNotice
                      error={configError}
                      hasData={!!config}
                      updatedAt={configUpdatedAt}
                      onRetry={onRetryConfigs}
                    />
                  </div>
                ) : null}
                {config ? (
                  <McpConfigCard
                    feature={feature}
                    config={config}
                    tools={toolsData?.tools ?? []}
                    syncedAt={toolsData?.syncedAt ?? null}
                    toolsStatus={
                      toolsQuery.isPending
                        ? "loading"
                        : toolsQuery.isError
                          ? "error"
                          : "ready"
                    }
                    onRetryTools={() => void toolsQuery.refetch()}
                    isSyncing={syncMutation.isPending}
                    syncError={syncError}
                    onRequestSync={requestSync}
                    onReimport={onImport}
                    onDelete={() => onDeleteConfig(config.serverId)}
                    readOnly={readOnly}
                    deleting={deleting}
                    deleteError={deleteError}
                  />
                ) : configStatus !== "ready" ? null : (
                  // Import guide in both modes: on the overview page it opens
                  // the import dialog; read-only trace pages open their own
                  // dialog via onImport. Blank served nobody.
                  <McpConfigEmpty
                    feature={feature}
                    onImport={canManage ? onImport : undefined}
                  />
                )}
              </TabsContent>
            </div>
          </Tabs>
        )}

        {config && !readOnly && syncOpen ? (
          <McpToolsSyncDialog
            feature={feature}
            open={syncOpen}
            onOpenChange={(next) => {
              setSyncOpen(next)
              if (!next) {
                syncSession.current += 1
                syncMutation.reset()
              }
            }}
            config={config}
            onSubmit={(headerValues) =>
              syncMutation.mutate({
                headerValues,
                session: syncSession.current,
              })
            }
            syncing={syncMutation.isPending}
            error={syncError}
          />
        ) : null}
      </SheetContent>
    </Sheet>
  )
}

function ToolTable({
  callEntity,
  tools,
  toolInfoByName,
  queryParams,
}: {
  callEntity?: McpServerDetail["callEntity"]
  tools: McpToolStat[]
  toolInfoByName?: Map<string, McpToolInfo>
  queryParams?: McpQueryParams
}) {
  const { t } = useTranslation()
  const location = useLocation()
  const [keyword, setKeyword] = useState("")
  const [sort, setSort] = useState("calls")
  const [limit, setLimit] = useState(5)
  const matches = useMemo(
    () =>
      tools
        .filter((tool) =>
          `${tool.toolName} ${tool.displayName ?? ""} ${toolInfoByName?.get(tool.toolName)?.description ?? ""}`
            .toLowerCase()
            .includes(keyword.trim().toLowerCase())
        )
        .sort((a, b) =>
          sort === "name"
            ? a.toolName.localeCompare(b.toolName)
            : sort === "errors"
              ? (b.errorRate ?? -1) - (a.errorRate ?? -1)
              : sort === "p95"
                ? (b.p95Ms ?? -1) - (a.p95Ms ?? -1)
                : b.calls - a.calls
        ),
    [tools, toolInfoByName, keyword, sort]
  )
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <input
          data-mcp-tool-search
          aria-label={t("labs.mcp.topic.search")}
          placeholder={t("labs.mcp.topic.search")}
          value={keyword}
          onChange={(e) => {
            setKeyword(e.target.value)
            setLimit(5)
          }}
          className="h-8 min-w-32 flex-1 rounded-md border border-input bg-background px-2 text-xs outline-none focus-visible:border-[var(--color-labs)] focus-visible:ring-2 focus-visible:ring-[var(--color-border-labs)]"
        />
        <select
          aria-label={t("labs.mcp.topic.sortLabel")}
          value={sort}
          onChange={(e) => {
            setSort(e.target.value)
            setLimit(5)
          }}
          className="h-8 rounded-md border border-input bg-background px-2 text-xs outline-none focus-visible:border-[var(--color-labs)] focus-visible:ring-2 focus-visible:ring-[var(--color-border-labs)]"
        >
          {["name", "calls", "errors", "p95"].map((value) => (
            <option key={value} value={value}>
              {t(`labs.mcp.topic.sort.${value}`)}
            </option>
          ))}
        </select>
        <span className="text-xs text-muted-foreground">
          {t(
            callEntity
              ? `labs.memory.${callEntity}.shown`
              : "labs.mcp.topic.toolsShown",
            {
              shown: Math.min(limit, matches.length),
              total: matches.length,
            }
          )}
        </span>
      </div>
      <div className="overflow-hidden rounded-[12px] border border-[var(--color-border)]">
        <Table>
          <TableHeader className="bg-[var(--color-bg-labs)]">
            <TableRow>
              <TableHead className="min-w-36">
                {t(
                  callEntity
                    ? `labs.memory.${callEntity}.name`
                    : "labs.mcp.tools.name"
                )}
              </TableHead>
              <TableHead className="text-right">
                {t("labs.mcp.tools.calls")}
              </TableHead>
              <TableHead className="text-right">
                {t("labs.mcp.tools.errorRate")}
              </TableHead>
              <TableHead className="text-right">
                <LabsMetricLabel
                  label="P50 / P95"
                  help={[
                    t(
                      callEntity
                        ? `labs.memory.${callEntity}.latencyHelp`
                        : "labs.mcp.tools.latencyHelp"
                    ),
                    t("labs.common.percentile.p50"),
                    t("labs.common.percentile.p95"),
                    t("labs.common.percentile.samples"),
                    t("labs.mcp.measurement.help"),
                  ].join("\n\n")}
                />
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {matches.slice(0, limit).map((tool) => {
              const info = toolInfoByName?.get(tool.toolName)
              return (
                <TableRow
                  key={tool.rawName}
                  className="align-top hover:bg-[var(--color-bg-labs)]"
                >
                  <TableCell className="max-w-[280px] whitespace-normal">
                    <div className="flex flex-col gap-1">
                      <span
                        title={tool.displayName ? tool.toolName : undefined}
                        className={cn(
                          "text-xs leading-5 break-all text-[var(--color-text-primary)]",
                          !tool.displayName && "font-mono"
                        )}
                      >
                        {tool.displayName ?? tool.toolName}
                      </span>
                      {info?.description ? (
                        <ClampedToolDescription
                          description={info.description}
                        />
                      ) : null}
                      {tool.lastError || tool.lastErrorCall ? (
                        <details className="text-xs leading-5 text-[var(--color-error)]">
                          <summary className="cursor-pointer rounded focus-visible:outline-2">
                            {t("labs.mcp.tools.lastError")}
                            {tool.lastErrorCall?.timestamp
                              ? ` · ${tool.lastErrorCall.timestamp}`
                              : ` · ${t("labs.mcp.measurement.timeUnknown")}`}
                          </summary>
                          <p className="break-all whitespace-pre-wrap">
                            {tool.lastError ||
                              t("labs.mcp.topic.noErrorMessage")}
                          </p>
                          {tool.lastErrorCall ? (
                            <Link
                              className={CALL_LINK_CLASS}
                              to={mcpTraceHref(
                                location.search,
                                tool.lastErrorCall,
                                queryParams
                              )}
                            >
                              {t("labs.mcp.calls.viewTrace")}
                            </Link>
                          ) : null}
                        </details>
                      ) : null}
                      {info ? (
                        <details className="text-xs">
                          <summary className="cursor-pointer rounded focus-visible:outline-2">
                            {t("labs.mcp.topic.definition")}
                          </summary>
                          <CopyableCode
                            value={JSON.stringify(info, null, 2)}
                            multiline
                          />
                        </details>
                      ) : null}
                    </div>
                  </TableCell>
                  <TableCell className="text-right font-stats">
                    {tool.calls.toLocaleString()}
                  </TableCell>
                  <TableCell
                    title={mcpStatusCoverage(tool, t)}
                    className={cn(
                      "text-right font-stats",
                      TONE_TEXT_CLASS[errorRateTone(tool.errorRate)]
                    )}
                  >
                    {formatPercent(tool.errorRate)}
                    {tool.errorRate === null && (tool.errors ?? 0) > 0 ? (
                      <div className="text-xs leading-5 text-[var(--color-error)]">
                        {t("labs.mcp.trace.failures", { count: tool.errors })}
                      </div>
                    ) : null}
                  </TableCell>
                  <TableCell className="text-right font-stats whitespace-nowrap">
                    {formatMcpDuration(tool.p50Ms, t)} /{" "}
                    {formatMcpDuration(tool.p95Ms, t)}
                    {mcpMeasurementCoverage(
                      tool.measuredCalls,
                      tool.calls,
                      t
                    ) ? (
                      <div className="text-xs leading-5 text-[var(--color-text-description)]">
                        {mcpMeasurementCoverage(
                          tool.measuredCalls,
                          tool.calls,
                          t
                        )}
                      </div>
                    ) : null}
                  </TableCell>
                </TableRow>
              )
            })}
            {matches.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={4}
                  className="py-8 text-center text-xs text-[var(--color-text-description)]"
                >
                  {t("common.noData")}
                </TableCell>
              </TableRow>
            ) : null}
          </TableBody>
        </Table>
      </div>
      {matches.length > 5 ? (
        <div className="flex gap-2">
          {limit < matches.length ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setLimit((value) => value + 5)}
            >
              {t("labs.mcp.topic.more")}
            </Button>
          ) : null}
          {limit > 5 ? (
            <Button variant="ghost" size="sm" onClick={() => setLimit(5)}>
              {t("labs.mcp.topic.less")}
            </Button>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

function mcpTraceHref(
  search: string,
  call: McpCallRef,
  queryParams?: Partial<
    Pick<McpQueryParams, "agentProduct" | "sourceSystem" | "from" | "to">
  >
) {
  const params = new URLSearchParams(search)
  if ((queryParams?.agentProduct ?? params.get("agent")) !== call.agentProduct)
    params.delete("agent_uuid")
  const source =
    queryParams?.sourceSystem ||
    params.get("source_system") ||
    params.get("source")
  if (source) params.set("source_system", source)
  params.delete("source")
  if (queryParams?.from && queryParams?.to) {
    params.set("from", queryParams.from)
    params.set("to", queryParams.to)
    params.set("trace_from", queryParams.from)
    params.set("trace_to", queryParams.to)
  } else if (params.get("from") && params.get("to")) {
    params.set("trace_from", params.get("from")!)
    params.set("trace_to", params.get("to")!)
  } else if (params.get("traceId") !== call.traceId) {
    params.delete("trace_from")
    params.delete("trace_to")
  }
  params.set("agent", call.agentProduct)
  params.set("traceId", call.traceId)
  params.set("focus_span", call.spanId)
  for (const key of ["query_traceid", "server", "tab"]) params.delete(key)
  return `/trace?${params}`
}

function RecentCalls({
  calls,
  queryParams,
  serverId,
  totalCalls,
  onAdjustRange,
  onSelectSpan,
  currentTraceId,
  onNavigateToTrace,
  queryWindow,
}: {
  calls: McpCallRef[]
  queryParams?: McpQueryParams
  serverId: string
  totalCalls?: number
  onAdjustRange?: () => void
  onSelectSpan?: (id: string) => void
  currentTraceId?: string
  onNavigateToTrace?: (id: string, spanId?: string) => void
  queryWindow?: { from: string; to: string }
}) {
  const { t } = useTranslation()
  const location = useLocation()
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState("all")
  const [sort, setSort] = useState("newest")
  const [limit, setLimit] = useState(5)
  const canFetchHistory = !!queryParams?.from && !!queryParams?.to
  const history = useQuery({
    queryKey: [
      "labs",
      "mcp",
      "history",
      queryParams,
      serverId,
      page,
      status,
      sort,
    ],
    queryFn: () =>
      listMcpCallHistory(queryParams!, serverId, page, status, sort),
    enabled: canFetchHistory,
    retry: false,
  })
  const local = useMemo(
    () =>
      calls
        .filter((call) => status === "all" || call.status === status)
        .sort((a, b) => {
          if (sort === "duration")
            return (b.durationMs ?? -1) - (a.durationMs ?? -1)
          return compareMcpTimestamps(
            a.timestamp,
            b.timestamp,
            sort === "oldest" ? "oldest" : "newest"
          )
        }),
    [calls, status, sort]
  )
  const visible =
    queryParams && history.data ? history.data.calls : local.slice(0, limit)
  const showLocalCount =
    !canFetchHistory || (history.isError && calls.length > 0)
  const total =
    history.data?.total ?? (showLocalCount ? local.length : undefined)
  const complete = queryParams
    ? history.data?.complete === true
    : calls.length >= (totalCalls ?? Infinity)
  const next = () =>
    queryParams
      ? setPage((history.data?.page ?? page) + 1)
      : setLimit((value) => value + 5)
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <select
          aria-label={t("labs.mcp.topic.historyStatus")}
          className="h-8 rounded-md border border-input bg-background px-2 text-xs outline-none focus-visible:border-[var(--color-labs)] focus-visible:ring-2 focus-visible:ring-[var(--color-border-labs)]"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value)
            setPage(1)
            setLimit(5)
          }}
        >
          {["all", "ok", "error", "unknown"].map((value) => (
            <option key={value} value={value}>
              {t(
                value === "all"
                  ? "labs.common.all"
                  : value === "unknown"
                    ? "spanContent.statusUnknown"
                    : `labs.mcp.calls.${value}`
              )}
            </option>
          ))}
        </select>
        <select
          aria-label={t("labs.mcp.topic.sortLabel")}
          className="h-8 rounded-md border border-input bg-background px-2 text-xs outline-none focus-visible:border-[var(--color-labs)] focus-visible:ring-2 focus-visible:ring-[var(--color-border-labs)]"
          value={sort}
          onChange={(e) => {
            setSort(e.target.value)
            setPage(1)
            setLimit(5)
          }}
        >
          {["newest", "oldest", "duration"].map((value) => (
            <option key={value} value={value}>
              {t(`labs.mcp.topic.order.${value}`)}
            </option>
          ))}
        </select>
        {total !== undefined ? (
          <span className="text-xs text-muted-foreground">
            {t("labs.mcp.topic.historyCount", {
              shown: visible.length,
              total,
            })}
          </span>
        ) : history.isFetching ? (
          <span role="status" className="text-xs text-muted-foreground">
            {t("labs.common.loading")}
          </span>
        ) : null}
        {queryParams && history.data ? (
          <span className="text-xs text-muted-foreground">
            {t("labs.mcp.topic.windowCalls", {
              count: history.data.totalCalls,
            })}
          </span>
        ) : null}
      </div>
      <QueryStateNotice
        error={history.error?.message}
        loading={history.isFetching}
        hasData={!!history.data}
        updatedAt={history.dataUpdatedAt}
        onRetry={() => void history.refetch()}
      />
      {history.error ? (
        <p className="text-xs text-muted-foreground">
          {t("labs.mcp.topic.historyRecovery")}
          {onAdjustRange ? (
            <Button
              variant="outline"
              size="sm"
              className="ml-2"
              onClick={onAdjustRange}
            >
              {t("labs.mcp.topic.adjustRange")}
            </Button>
          ) : null}
        </p>
      ) : null}
      {!complete && calls.length > 0 ? (
        <p className="text-xs text-muted-foreground">
          {t("labs.mcp.topic.loadedSample", {
            loaded: calls.length,
            total: totalCalls ?? "—",
          })}
        </p>
      ) : null}
      {total === 0 && !history.isFetching && !history.isError ? (
        <p className="rounded-lg border border-dashed border-[var(--color-border-labs)] bg-[var(--color-bg-labs)] p-4 text-sm text-muted-foreground">
          {t("labs.mcp.topic.noCalls")}
        </p>
      ) : null}
      {visible.length ? (
        <ul className="divide-y rounded-lg border border-[var(--color-border)]">
          {visible.map((call) => (
            <li
              key={`${call.traceId}-${call.spanId}`}
              className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 px-3 py-2 sm:flex sm:items-center"
            >
              <span
                className={cn(
                  "inline-flex h-6 shrink-0 items-center rounded px-2 text-xs whitespace-nowrap",
                  call.status === "ok"
                    ? "bg-[var(--color-bg-success)] text-[var(--color-success)]"
                    : call.status === "error"
                      ? "bg-[var(--color-bg-error)] text-[var(--color-error)]"
                      : "bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)]"
                )}
              >
                {t(
                  call.status === "unknown"
                    ? "spanContent.statusUnknown"
                    : `labs.mcp.calls.${call.status}`
                )}
              </span>
              <div className="min-w-0 flex-1">
                <span
                  title={call.displayName ? call.toolName : undefined}
                  className={cn(
                    "block text-xs leading-5 break-all",
                    !call.displayName && "font-mono"
                  )}
                >
                  {call.displayName ?? call.toolName}
                  {call.transport === "http" && (
                    <span className="ml-2 rounded border px-1 text-xs">
                      HTTP
                    </span>
                  )}
                </span>
                <span className="text-xs text-muted-foreground">
                  {call.agentProduct} ·{" "}
                  {call.timestamp ?? t("labs.mcp.measurement.timeUnknown")}
                </span>
                {call.lastError ? (
                  <details className="text-xs text-destructive">
                    <summary className="cursor-pointer">
                      {t("metrics.errorDetails")}
                    </summary>
                    <p className="break-all whitespace-pre-wrap">
                      {call.lastError}
                    </p>
                  </details>
                ) : null}
              </div>
              <div className="col-start-2 flex flex-wrap items-center justify-between gap-3 sm:contents">
                <span className="shrink-0 text-xs tabular-nums">
                  {formatMcpDuration(call.durationMs, t)}
                </span>
                {onSelectSpan && call.traceId === currentTraceId ? (
                  <button
                    type="button"
                    className={CALL_LINK_CLASS}
                    onClick={() => onSelectSpan(call.spanId)}
                  >
                    {t("labs.mcp.calls.viewTrace")}
                  </button>
                ) : onNavigateToTrace ? (
                  <button
                    type="button"
                    className={CALL_LINK_CLASS}
                    onClick={() => onNavigateToTrace(call.traceId, call.spanId)}
                  >
                    {t("labs.mcp.calls.viewTrace")}
                  </button>
                ) : (
                  <Link
                    className={CALL_LINK_CLASS}
                    to={mcpTraceHref(
                      location.search,
                      call,
                      queryParams ??
                        (queryWindow
                          ? { from: queryWindow.from, to: queryWindow.to }
                          : undefined)
                    )}
                  >
                    {t("labs.mcp.calls.viewTrace")}
                  </Link>
                )}
              </div>
            </li>
          ))}
        </ul>
      ) : null}
      {queryParams && history.data ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={history.data.page <= 1 || history.isFetching}
            onClick={() => setPage(history.data!.page - 1)}
          >
            {t("labs.mcp.topic.previous")}
          </Button>
          <span className="text-xs text-muted-foreground">
            {t("labs.mcp.topic.page", {
              page: history.data.page,
              pages: Math.max(
                1,
                Math.ceil(history.data.total / history.data.pageSize)
              ),
            })}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={
              history.data.page * history.data.pageSize >= history.data.total ||
              history.isFetching
            }
            onClick={next}
          >
            {t("labs.mcp.topic.next")}
          </Button>
        </div>
      ) : null}
      {(!queryParams || history.isError) && total !== undefined && total > 5 ? (
        <div className="flex flex-wrap gap-2">
          {limit < total ? (
            <>
              <Button variant="outline" size="sm" onClick={next}>
                {t("labs.mcp.topic.more")}
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setLimit(total)}
              >
                {t("traceRecords.all")}
              </Button>
            </>
          ) : null}
          {limit > 5 ? (
            <Button variant="ghost" size="sm" onClick={() => setLimit(5)}>
              {t("labs.mcp.topic.less")}
            </Button>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

/** Expand in place with a native keyboard-accessible button. */
function ClampedToolDescription({ description }: { description: string }) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)
  return (
    <button
      type="button"
      onClick={() => setExpanded((value) => !value)}
      aria-expanded={expanded}
      className="rounded-[4px] text-left text-xs leading-5 whitespace-normal text-[var(--color-text-secondary)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-text-primary)]"
    >
      <span className={expanded ? "block" : "line-clamp-2"}>{description}</span>
      <span className="underline underline-offset-2">
        {t(expanded ? "labs.mcp.tools.showLess" : "labs.mcp.tools.showMore")}
      </span>
    </button>
  )
}
