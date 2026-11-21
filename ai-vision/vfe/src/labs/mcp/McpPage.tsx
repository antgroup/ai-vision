import { useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { FileJson, Plug } from "lucide-react"
import { useTranslation } from "react-i18next"
import { useSearchParams } from "react-router-dom"

import { SpaceAgentGuard } from "@/components/error/SpaceAgentGuard"
import { PageBreadcrumb } from "@/components/layout/PageBreadcrumb"
import { Button } from "@/components/ui/button"
import { LabsAgentQueryNotice } from "@/components/labs/shared/LabsAgentQueryNotice"
import {
  LabsCatalogueOverview,
  LabsCatalogueResults,
  type CatalogueRequest,
} from "@/components/labs/shared/LabsCatalogueLayout"
import { LabsInventoryCounts } from "@/components/labs/shared/LabsInventoryCounts"
import {
  LabsServerCatalogueToolbar,
  type ServerCatalogueSort,
  type ServerInventoryFilter,
} from "@/components/labs/shared/LabsServerCatalogueToolbar"
import { declaredMemorySemantics } from "@/labs/memory/memoryDisplay"
import { McpConfigImportDialog } from "./McpConfigImportDialog"
import { McpIntroBanner } from "./McpIntroBanner"
import { McpOverview } from "./McpOverview"
import { McpServerCard } from "./McpServerCard"
import { McpServerDrawer } from "./McpServerDrawer"
import {
  getMcpServerDetail,
  deleteMcpConfig,
  fetchMcpConfig,
  fetchMcpServerObservation,
  listMcpConfigSummaries,
  listMcpServerSummaries,
} from "./mcpApi"
import { parseMcpDrawerTab } from "./mcpDisplay"
import type { McpHealth, McpServer, McpConfigSummary } from "./types"
import { useAgent } from "@/contexts/AgentContext"
import { useSpace } from "@/contexts/SpaceContext"
import { useTimeFilter } from "@/contexts/TimeFilterContext"

export function McpPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { currentSpace } = useSpace()
  const canManage = currentSpace?.role === 1
  const { currentAgent } = useAgent()
  const { fromTimestamp, isCustom, timeRange, toTimestamp, refreshTick } =
    useTimeFilter()
  const [importOpen, setImportOpen] = useState(false)
  const [removed, setRemoved] = useState<{
    spaceId: string
    serverId: string
  } | null>(null)
  const [searchParams, setSearchParams] = useSearchParams()
  const openServerId = searchParams.get("server")
  const rawTab = searchParams.get("tab")
  const drawerTab = parseMcpDrawerTab(rawTab)
  useEffect(() => {
    if (!openServerId || rawTab === null || rawTab === drawerTab) return
    setSearchParams(
      (current) => {
        const next = new URLSearchParams(current)
        if (next.get("server") === openServerId && next.get("tab") === rawTab)
          next.set("tab", drawerTab)
        return next
      },
      { replace: true }
    )
  }, [openServerId, rawTab, drawerTab, setSearchParams])
  const changeTab = (tab: string) =>
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      if (next.get("server") === openServerId)
        next.set("tab", parseMcpDrawerTab(tab))
      return next
    })
  const openServer = (serverId: string | null) =>
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      if (serverId) next.set("server", serverId)
      else next.delete("server")
      next.delete("tab")
      return next
    })
  const spaceId = currentSpace?.id ?? ""
  const agentUuid = searchParams.get("agent_uuid") || currentAgent?.uuid
  const sourceSystem =
    searchParams.get("source_system") || searchParams.get("source") || undefined
  const params = useMemo(
    () => ({
      spaceId,
      agentProduct: currentAgent?.agent_product ?? "",
      timeRange,
      from: isCustom ? fromTimestamp : undefined,
      to: isCustom ? toTimestamp : undefined,
      agentUuid,
      sourceSystem,
    }),
    [
      spaceId,
      currentAgent?.agent_product,
      timeRange,
      isCustom,
      fromTimestamp,
      toTimestamp,
      agentUuid,
      sourceSystem,
    ]
  )
  const canQueryStats = !!spaceId && !!params.agentProduct
  const statsKey = ["labs", "mcp", "servers", params, "summary"]
  const stats = useQuery({
    queryKey: statsKey,
    queryFn: ({ signal }) => listMcpServerSummaries(params, signal),
    enabled: canQueryStats,
    retry: false,
  })
  const configsQueryKey = ["labs", "mcp", "configs", spaceId]
  const configsQuery = useQuery({
    queryKey: [...configsQueryKey, "summary"],
    queryFn: ({ signal }) => listMcpConfigSummaries(spaceId, signal),
    staleTime: 60_000,
    enabled: !!spaceId,
    retry: false,
  })
  const previousTick = useRef(refreshTick)
  useEffect(() => {
    if (previousTick.current === refreshTick) return
    previousTick.current = refreshTick
    void queryClient.invalidateQueries({ queryKey: ["labs", "mcp"] })
  }, [refreshTick, queryClient])
  const data = stats.data
  const historyParams = useMemo(
    () => ({
      ...params,
      from: data?.scope?.from ?? params.from,
      to: data?.scope?.to ?? params.to,
    }),
    [params, data?.scope?.from, data?.scope?.to]
  )
  const configs = useMemo(() => configsQuery.data ?? [], [configsQuery.data])
  const mcpConfigs = useMemo(() => {
    const memory = declaredMemorySemantics(configs)
    return configs.filter((config) => !Object.hasOwn(memory, config.serverId))
  }, [configs])
  const configById = useMemo(
    () => new Map(configs.map((config) => [config.serverId, config])),
    [configs]
  )
  const observedServers = useMemo(
    () =>
      (data?.servers ?? []).map((server) => ({
        ...server,
        description:
          server.description || configById.get(server.serverId)?.description,
      })),
    [data?.servers, configById]
  )
  const observedIds = useMemo(
    () => new Set(observedServers.map((server) => server.serverId)),
    [observedServers]
  )
  const selectedSummary = openServerId
    ? observedServers.find((server) => server.serverId === openServerId)
    : undefined
  const selectedObservations = useQuery({
    queryKey: ["labs", "mcp", "servers", historyParams, "detail", openServerId],
    queryFn: ({ signal }) =>
      fetchMcpServerObservation(historyParams, openServerId!, signal),
    enabled: canQueryStats && !!selectedSummary,
    retry: false,
  })
  const selectedConfigQuery = useQuery({
    queryKey: [...configsQueryKey, "detail", openServerId],
    queryFn: ({ signal }) => fetchMcpConfig(spaceId, openServerId!, signal),
    enabled: !!spaceId && !!openServerId,
    staleTime: 60_000,
    retry: false,
  })
  const selectedConfig = selectedConfigQuery.data ?? undefined
  const selectedConfigSummary = openServerId
    ? configById.get(openServerId)
    : undefined
  const selectedDetail = selectedObservations.data
  const drawerServer = selectedDetail
    ? {
        ...selectedDetail,
        description:
          selectedConfig?.description || selectedSummary?.description,
      }
    : selectedSummary
      ? { ...selectedSummary, tools: [], recentCalls: [] }
      : getMcpServerDetail(
          selectedConfigQuery.data === undefined
            ? selectedConfigSummary
            : selectedConfig
        )
  const importedServers = useMemo<McpServer[]>(
    () =>
      mcpConfigs
        .filter(
          (config) =>
            !observedIds.has(config.serverId) &&
            !data?.memoryServerIds?.includes(config.serverId)
        )
        .map((config) => getMcpServerDetail(config)!),
    [mcpConfigs, observedIds, data?.memoryServerIds]
  )
  const servers = useMemo(
    () => [...observedServers, ...importedServers],
    [observedServers, importedServers]
  )
  const observedServerIdList = useMemo(() => [...observedIds], [observedIds])
  const deleteMutation = useMutation({
    mutationFn: (input: { spaceId: string; serverId: string }) =>
      deleteMcpConfig(input.spaceId, input.serverId),
    onSuccess: async (_, input) => {
      await queryClient.invalidateQueries({
        queryKey: ["labs", "mcp", "configs", input.spaceId],
      })
      if (input.spaceId === spaceId) setRemoved(input)
    },
  })
  const configStatus = configsQuery.isPending
    ? "loading"
    : configsQuery.isError
      ? "error"
      : "ready"
  const statsStatus = data
    ? "ready"
    : stats.isError
      ? "error"
      : stats.isLoading
        ? "loading"
        : "idle"
  const drawerConfigStatus = selectedConfigQuery.isPending
    ? "loading"
    : selectedConfigQuery.isError
      ? "error"
      : "ready"
  const drawerObservationsStatus = selectedSummary
    ? selectedObservations.data !== undefined
      ? "ready"
      : selectedObservations.isError
        ? "error"
        : "loading"
    : statsStatus
  return (
    <SpaceAgentGuard>
      <div className="flex min-w-0 flex-col gap-4 overflow-hidden px-4 pt-3 pb-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2.5">
            <span className="flex size-8 shrink-0 items-center justify-center rounded-lg border border-[var(--color-border-labs)] bg-[var(--color-bg-labs-tag)] text-[var(--color-labs)]">
              <Plug className="size-4" aria-hidden="true" />
            </span>
            <PageBreadcrumb
              pageTitle={t("nav.labsMcp")}
              agentProduct={currentAgent?.agent_product ?? ""}
            />
            <McpIntroBanner />
          </div>
          {canManage ? (
            <Button
              variant="outline"
              size="sm"
              className="border-[var(--color-border-labs)] text-[var(--color-labs)] hover:bg-[var(--color-bg-labs-tag)] hover:text-[var(--color-labs)] dark:hover:bg-[var(--color-bg-labs-tag)]"
              onClick={() => setImportOpen(true)}
            >
              <FileJson className="size-4" />
              {t("labs.mcp.import.open")}
            </Button>
          ) : null}
        </div>
        <LabsAgentQueryNotice />
        {removed?.spaceId === spaceId ? (
          <p role="status" className="text-xs text-muted-foreground">
            {t("labs.mcp.topic.removed", { name: removed.serverId })}
          </p>
        ) : null}
        {canQueryStats ? (
          <LabsCatalogueOverview loading={stats.isLoading}>
            {data ? (
              <McpOverview
                overview={data.overview}
                slowestServerId={slowestServerBy(
                  observedServers,
                  (s) => s.stats.p95Ms
                )}
                slowestP50ServerId={slowestServerBy(
                  observedServers,
                  (s) => s.stats.p50Ms
                )}
              />
            ) : null}
          </LabsCatalogueOverview>
        ) : null}
        <McpCatalogue
          key={JSON.stringify(params)}
          counts={
            <LabsInventoryCounts
              observed={data && observedServers.length}
              configured={configsQuery.data && mcpConfigs.length}
            />
          }
          servers={servers}
          configs={configById}
          observedIds={observedIds}
          configStatus={configStatus}
          observations={
            canQueryStats
              ? { query: stats, failure: t("labs.mcp.scanFailed") }
              : undefined
          }
          configRequest={{
            query: configsQuery,
            failure: t("labs.mcp.topic.configsFailed"),
          }}
          onOpen={openServer}
          onImport={canManage ? () => setImportOpen(true) : undefined}
        />
        {data?.scan?.truncated ? (
          <p role="status" className="text-xs text-muted-foreground">
            {t("labs.mcp.scanTruncated")}
          </p>
        ) : null}
      </div>
      <McpServerDrawer
        serverId={openServerId}
        detail={drawerServer}
        config={selectedConfig}
        tab={drawerTab}
        onTabChange={changeTab}
        onClose={() => openServer(null)}
        onImport={() => setImportOpen(true)}
        onDeleteConfig={(id) => {
          if (canManage) deleteMutation.mutate({ spaceId, serverId: id })
        }}
        readOnly={!canManage}
        queryParams={canQueryStats ? historyParams : undefined}
        scan={data?.scan}
        observationsStatus={drawerObservationsStatus}
        observationsError={(selectedObservations.error ?? stats.error)?.message}
        observationsLoading={
          stats.isFetching || selectedObservations.isFetching
        }
        observationsUpdatedAt={
          selectedSummary
            ? selectedObservations.dataUpdatedAt
            : stats.dataUpdatedAt
        }
        onRetryObservations={() =>
          void (selectedSummary &&
          (selectedObservations.isError || !stats.isError)
            ? selectedObservations.refetch()
            : stats.refetch())
        }
        configStatus={drawerConfigStatus}
        configError={selectedConfigQuery.error?.message}
        configUpdatedAt={selectedConfigQuery.dataUpdatedAt}
        onRetryConfigs={() => void selectedConfigQuery.refetch()}
        deleting={
          deleteMutation.isPending &&
          deleteMutation.variables?.spaceId === spaceId &&
          deleteMutation.variables?.serverId === openServerId
        }
        deleteError={
          deleteMutation.variables?.spaceId === spaceId &&
          deleteMutation.variables?.serverId === openServerId
            ? deleteMutation.error?.message
            : null
        }
      />
      <McpConfigImportDialog
        open={canManage && importOpen}
        onOpenChange={setImportOpen}
        spaceId={spaceId}
        observedServerIds={observedServerIdList}
      />
    </SpaceAgentGuard>
  )
}

function McpCatalogue({
  counts,
  servers,
  configs,
  observedIds,
  configStatus,
  observations,
  configRequest,
  onOpen,
  onImport,
}: {
  counts: ReactNode
  servers: McpServer[]
  configs: Map<string, McpConfigSummary>
  observedIds: Set<string>
  configStatus: "loading" | "error" | "ready"
  observations?: CatalogueRequest
  configRequest: CatalogueRequest
  onOpen: (id: string) => void
  onImport?: () => void
}) {
  const { t } = useTranslation()
  const [keyword, setKeyword] = useState("")
  const [health, setHealth] = useState<McpHealth | "all">("all")
  const [inventory, setInventory] = useState<ServerInventoryFilter>("all")
  const [sort, setSort] = useState<ServerCatalogueSort>("name")
  const [limit, setLimit] = useState(12)
  const statsKnown = Boolean(observations?.query.data)
  const matches = useMemo(
    () =>
      servers
        .filter(
          (server) =>
            (health === "all" ||
              (statsKnown ? server.health : "unknown") === health) &&
            (inventory === "all" ||
              (inventory === "observed"
                ? observedIds.has(server.serverId)
                : statsKnown && !observedIds.has(server.serverId))) &&
            (!keyword.trim() ||
              `${server.serverId} ${server.description ?? ""}`
                .toLowerCase()
                .includes(keyword.trim().toLowerCase()))
        )
        .sort((a, b) => {
          if (sort === "calls")
            return (
              b.stats.calls - a.stats.calls ||
              a.serverId.localeCompare(b.serverId)
            )
          if (sort === "errors")
            return (
              (b.stats.errorRate ?? -1) - (a.stats.errorRate ?? -1) ||
              a.serverId.localeCompare(b.serverId)
            )
          if (sort === "p95")
            return (
              (b.stats.p95Ms ?? -1) - (a.stats.p95Ms ?? -1) ||
              a.serverId.localeCompare(b.serverId)
            )
          return a.serverId.localeCompare(b.serverId)
        }),
    [servers, health, inventory, keyword, sort, statsKnown, observedIds]
  )
  const resetWindow = () => setLimit(12)
  return (
    <section className="flex flex-col gap-3">
      <LabsServerCatalogueToolbar
        title={t("labs.mcp.serverList")}
        counts={counts}
        shown={Math.min(limit, matches.length)}
        total={matches.length}
        keyword={keyword}
        health={health}
        inventory={inventory}
        inventoryDisabled={!statsKnown}
        sort={sort}
        onKeywordChange={(value) => {
          setKeyword(value)
          resetWindow()
        }}
        onHealthChange={(value) => {
          setHealth(value)
          resetWindow()
        }}
        onInventoryChange={(value) => {
          setInventory(value)
          resetWindow()
        }}
        onSortChange={(value) => {
          setSort(value)
          resetWindow()
        }}
        onReset={() => {
          setKeyword("")
          setHealth("all")
          setInventory("all")
          setSort("name")
          resetWindow()
        }}
      />
      <LabsCatalogueResults
        observations={observations}
        configs={configRequest}
        empty={
          <p>
            {t(
              servers.length
                ? "labs.mcp.empty.title"
                : "labs.mcp.empty.noInventory"
            )}
          </p>
        }
      >
        {matches.length ? (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,_320px),1fr))] gap-3">
            {matches.slice(0, limit).map((server) => (
              <McpServerCard
                key={server.serverId}
                server={server}
                config={configs.get(server.serverId)}
                configStatus={configStatus}
                statsKnown={statsKnown}
                onOpen={onOpen}
                onImport={onImport}
              />
            ))}
          </div>
        ) : null}
      </LabsCatalogueResults>
      {matches.length > 12 ? (
        <div className="flex flex-wrap items-center gap-2">
          {limit < matches.length ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setLimit((value) => value + 12)}
            >
              {t("labs.mcp.topic.more")}
            </Button>
          ) : null}
          {limit > 12 ? (
            <Button variant="ghost" size="sm" onClick={resetWindow}>
              {t("labs.mcp.topic.less")}
            </Button>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}

function slowestServerBy(
  servers: McpServer[],
  pick: (server: McpServer) => number | null
): string | undefined {
  let owner: string | undefined = undefined
  let slowest = -1
  for (const server of servers) {
    const value = pick(server)
    if (server.stats.calls > 0 && value !== null && value > slowest) {
      owner = server.serverId
      slowest = value
    }
  }
  return owner
}
