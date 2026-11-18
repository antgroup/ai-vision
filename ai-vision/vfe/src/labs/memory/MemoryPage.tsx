import { useEffect, useMemo, useRef, useState } from "react"

import { useQuery, useQueryClient } from "@tanstack/react-query"
import { AlertTriangle, Database, FileJson } from "lucide-react"
import { useTranslation } from "react-i18next"
import { useSearchParams } from "react-router-dom"

import { SpaceAgentGuard } from "@/components/error/SpaceAgentGuard"
import { LabsAgentQueryNotice } from "@/components/labs/shared/LabsAgentQueryNotice"
import {
  LabsCatalogueOverview,
  LabsCatalogueResults,
} from "@/components/labs/shared/LabsCatalogueLayout"
import { LabsInventoryCounts } from "@/components/labs/shared/LabsInventoryCounts"
import {
  LabsServerCatalogueToolbar,
  type ServerCatalogueSort,
  type ServerInventoryFilter,
} from "@/components/labs/shared/LabsServerCatalogueToolbar"
import { PageBreadcrumb } from "@/components/layout/PageBreadcrumb"
import { Button } from "@/components/ui/button"
import { deleteMcpConfig, listMcpConfigs } from "@/labs/mcp/mcpApi"
import { parseMcpDrawerTab } from "@/labs/mcp/mcpDisplay"
import type { McpHealth } from "@/labs/mcp/types"
import { memoryHealth } from "./memoryMcpPresentation"
import { McpConfigImportDialog } from "@/labs/mcp/McpConfigImportDialog"
import { useAgent } from "@/contexts/AgentContext"
import { useSpace } from "@/contexts/SpaceContext"
import { useTimeFilter } from "@/contexts/TimeFilterContext"

import { MemoryIntroBanner } from "./MemoryIntroBanner"
import { MemoryServerDrawer } from "./MemoryActivity"
import { MemoryOverview } from "./MemoryOverview"
import { MemoryServerCard } from "./MemoryServerCard"
import { listMemoryObservations } from "./memoryApi"
import { declaredMemorySemantics } from "./memoryDisplay"
import type { MemoryServer } from "./types"

export function MemoryPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { currentSpace } = useSpace()
  const canManage = currentSpace?.role === 1
  const { currentAgent } = useAgent()
  const { fromTimestamp, isCustom, timeRange, toTimestamp, refreshTick } =
    useTimeFilter()
  const [keyword, setKeyword] = useState("")
  const [health, setHealth] = useState<McpHealth | "all">("all")
  const [inventory, setInventory] = useState<ServerInventoryFilter>("all")
  const [sort, setSort] = useState<ServerCatalogueSort>("name")
  const [importTarget, setImportTarget] = useState<{
    spaceId: string
    serverId?: string
  } | null>(null)
  const [searchParams, setSearchParams] = useSearchParams()
  const openServerId = searchParams.get("server")
  const requestedTab = searchParams.get("tab")
  const initialTab =
    requestedTab && requestedTab !== "entries"
      ? parseMcpDrawerTab(requestedTab)
      : undefined
  const openServer = (serverId: string | null) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      if (serverId) next.set("server", serverId)
      else next.delete("server")
      next.delete("tab")
      return next
    })
  }

  const spaceId = currentSpace?.id ?? ""
  const params = useMemo(
    () => ({
      spaceId,
      agentProduct: currentAgent?.agent_product ?? "",
      agentUuid: currentAgent?.uuid,
      timeRange,
      from: isCustom ? fromTimestamp : undefined,
      to: isCustom ? toTimestamp : undefined,
    }),
    [
      spaceId,
      currentAgent?.agent_product,
      currentAgent?.uuid,
      timeRange,
      isCustom,
      fromTimestamp,
      toTimestamp,
    ]
  )

  const canQueryStats = !!spaceId && !!params.agentProduct
  const statsQuery = useQuery({
    queryKey: ["labs", "memory", "observations", params],
    queryFn: ({ signal }) => listMemoryObservations(params, signal),
    retry: false,
    enabled: canQueryStats,
  })
  const { data, refetch } = statsQuery
  const configsQueryKey = ["labs", "mcp", "configs", spaceId]
  const configsQuery = useQuery({
    queryKey: configsQueryKey,
    queryFn: ({ signal }) => listMcpConfigs(spaceId, signal),
    staleTime: 60_000,
    enabled: !!spaceId,
    retry: false,
  })
  const previousTick = useRef(refreshTick)
  useEffect(() => {
    if (previousTick.current === refreshTick) return
    previousTick.current = refreshTick
    void queryClient.invalidateQueries({ queryKey: ["labs", "memory"] })
  }, [refreshTick, queryClient])
  const configs = useMemo(() => configsQuery.data ?? [], [configsQuery.data])
  const configById = useMemo(
    () => new Map(configs.map((config) => [config.serverId, config])),
    [configs]
  )

  const observedServers = useMemo(
    () =>
      (data?.servers ?? []).map((server) => ({
        ...server,
        description:
          server.description ||
          configById.get(server.serverId)?.description ||
          "",
      })),
    [data?.servers, configById]
  )
  const observedIds = useMemo(
    () => new Set(observedServers.map((server) => server.serverId)),
    [observedServers]
  )
  const memoryConfigs = useMemo(() => {
    const declared = declaredMemorySemantics(configs)
    return configs.filter((config) => Object.hasOwn(declared, config.serverId))
  }, [configs])
  // Imported memory servers without calls in the window keep their card so
  // the setup stays visible even before the first trace arrives.
  const importedServers = useMemo<MemoryServer[]>(
    () =>
      memoryConfigs
        .filter((config) => !observedIds.has(config.serverId))
        .map<MemoryServer>((config) => ({
          serverId: config.serverId,
          description: config.description ?? "",
          declared: true,
          sourceAgents: [],
          lastSeen: config.importedAt,
          calls: 0,
          errors: 0,
          durationSamples: 0,
          errorRate: 0,
          p50Ms: null,
          p95Ms: null,
          searchCalls: 0,
          searchResultCalls: 0,
          searchHitCalls: 0,
          searchContentCalls: 0,
          searchDurationSamples: 0,
          searchHitRate: null,
          totalHits: 0,
          entryCount: null,
          entryCountIsPartial: false,
          entrySnapshot: null,
          tools: [],
          recentCalls: [],
          entries: [],
        })),
    [memoryConfigs, observedIds]
  )
  const totalServerCount = observedServers.length + importedServers.length
  const removeConfig = async (serverId: string) => {
    if (!canManage) return
    await deleteMcpConfig(spaceId, serverId)
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: configsQueryKey }),
      queryClient.invalidateQueries({
        queryKey: ["labs", "mcp", "config-tools", spaceId, serverId],
      }),
      canQueryStats ? refetch() : undefined,
    ])
  }
  const servers = useMemo(() => {
    const needle = keyword.trim().toLowerCase()
    return [...observedServers, ...importedServers]
      .filter(
        (server) =>
          (health === "all" || memoryHealth(server, !!data) === health) &&
          (inventory === "all" ||
            (inventory === "observed"
              ? observedIds.has(server.serverId)
              : !observedIds.has(server.serverId))) &&
          (!needle ||
            `${server.serverId} ${server.description}`
              .toLowerCase()
              .includes(needle))
      )
      .sort((a, b) => {
        if (sort === "calls")
          return b.calls - a.calls || a.serverId.localeCompare(b.serverId)
        if (sort === "errors")
          return (
            (b.errorRate ?? -1) - (a.errorRate ?? -1) ||
            a.serverId.localeCompare(b.serverId)
          )
        if (sort === "p95")
          return (
            (b.p95Ms ?? -1) - (a.p95Ms ?? -1) ||
            a.serverId.localeCompare(b.serverId)
          )
        return a.serverId.localeCompare(b.serverId)
      })
  }, [
    observedServers,
    importedServers,
    observedIds,
    keyword,
    health,
    inventory,
    sort,
    data,
  ])
  return (
    <SpaceAgentGuard>
      <div className="flex min-w-0 flex-col gap-4 overflow-hidden px-4 pt-3 pb-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2.5">
            <span className="flex size-8 shrink-0 items-center justify-center rounded-lg border border-[var(--color-border-labs)] bg-[var(--color-bg-labs-tag)] text-[var(--color-labs)]">
              <Database className="size-4" aria-hidden="true" />
            </span>
            <PageBreadcrumb
              pageTitle={t("nav.labsMemory")}
              agentProduct={currentAgent?.agent_product ?? ""}
            />
            <MemoryIntroBanner />
          </div>
          {canManage ? (
            <Button
              variant="outline"
              size="sm"
              className="border-[var(--color-border-labs)] text-[var(--color-labs)] hover:bg-[var(--color-bg-labs-tag)] hover:text-[var(--color-labs)] dark:hover:bg-[var(--color-bg-labs-tag)]"
              onClick={() => setImportTarget({ spaceId })}
            >
              <FileJson className="size-4" />
              {t("labs.mcp.import.open")}
            </Button>
          ) : null}
        </div>

        <LabsAgentQueryNotice />
        {canQueryStats ? (
          <LabsCatalogueOverview loading={statsQuery.isLoading}>
            {data ? (
              <MemoryOverview
                overview={data.overview}
                servers={observedServers}
              />
            ) : null}
          </LabsCatalogueOverview>
        ) : null}
        <section className="flex flex-col gap-3">
          <LabsServerCatalogueToolbar
            title={t("labs.memory.serverList")}
            counts={
              <LabsInventoryCounts
                observed={data && observedServers.length}
                configured={configsQuery.data && memoryConfigs.length}
              />
            }
            shown={servers.length}
            total={servers.length}
            keyword={keyword}
            health={health}
            inventory={inventory}
            sort={sort}
            onKeywordChange={setKeyword}
            onHealthChange={setHealth}
            onInventoryChange={setInventory}
            onSortChange={setSort}
            onReset={() => {
              setKeyword("")
              setHealth("all")
              setInventory("all")
              setSort("name")
            }}
          />

          <LabsCatalogueResults
            observations={
              canQueryStats
                ? { query: statsQuery, failure: t("labs.memory.scanFailed") }
                : undefined
            }
            configs={{
              query: configsQuery,
              failure: t("labs.memory.tools.configsFailed"),
            }}
            empty={
              <p>
                {t(
                  totalServerCount
                    ? "labs.memory.list.noServers"
                    : "labs.memory.list.noInventory"
                )}
              </p>
            }
          >
            {servers.length ? (
              <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,_320px),1fr))] gap-3">
                {servers.map((server) => (
                  <MemoryServerCard
                    key={server.serverId}
                    server={server}
                    config={configById.get(server.serverId)}
                    statsAvailable={!!data}
                    configStatus={
                      configsQuery.isError
                        ? "error"
                        : configsQuery.isLoading
                          ? "loading"
                          : "ready"
                    }
                    onOpen={openServer}
                    onImport={
                      canManage
                        ? () =>
                            setImportTarget({
                              spaceId,
                              serverId: server.serverId,
                            })
                        : undefined
                    }
                  />
                ))}
              </div>
            ) : null}
          </LabsCatalogueResults>
        </section>
        {data?.overview.truncated ? (
          <div
            role="status"
            className="flex items-center gap-2 rounded-[12px] border border-[var(--color-border)] bg-[var(--color-bg-warning)] px-3 py-2 text-xs leading-5 text-[var(--color-text-secondary)]"
          >
            <AlertTriangle
              className="size-4 shrink-0 text-[var(--color-warning)]"
              aria-hidden="true"
            />
            {t("labs.mcp.scanTruncated")}
          </div>
        ) : null}
        <MemoryServerDrawer
          window={data?.window}
          config={openServerId ? configById.get(openServerId) : undefined}
          statsAvailable={!!data}
          server={
            [...observedServers, ...importedServers].find(
              (server) => server.serverId === openServerId
            ) ?? null
          }
          onClose={() => openServer(null)}
          onImportConfig={(serverId) => setImportTarget({ spaceId, serverId })}
          onDeleteConfig={removeConfig}
          initialTab={initialTab}
          onTabChange={(tab) =>
            setSearchParams(
              (current) => {
                const next = new URLSearchParams(current)
                next.set("tab", tab)
                return next
              },
              { replace: true }
            )
          }
          callsLimit={data?.limits.recentCalls ?? 200}
        />
        <McpConfigImportDialog
          feature="memory"
          open={canManage && importTarget?.spaceId === spaceId}
          onOpenChange={(open) => {
            if (!open) {
              setImportTarget(null)
            }
          }}
          spaceId={spaceId}
          observedServerIds={[...observedIds]}
          targetServerId={importTarget?.serverId}
        />
      </div>
    </SpaceAgentGuard>
  )
}
