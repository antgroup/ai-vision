import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Blocks, FileText, Search } from "lucide-react"
import { useTranslation } from "react-i18next"
import { useLocation, useSearchParams } from "react-router-dom"
import { toast } from "sonner"
import { SpaceAgentGuard } from "@/components/error/SpaceAgentGuard"
import { PageBreadcrumb } from "@/components/layout/PageBreadcrumb"
import { LabsAgentQueryNotice } from "@/components/labs/shared/LabsAgentQueryNotice"
import {
  LabsCatalogueOverview,
  LabsCatalogueResults,
  LabsCatalogueHeading,
} from "@/components/labs/shared/LabsCatalogueLayout"
import { LabsInventoryCounts } from "@/components/labs/shared/LabsInventoryCounts"
import { LabsIntroBanner } from "@/components/labs/shared/LabsIntroBanner"
import { observabilityViewDocsUrl } from "@/components/labs/shared/observabilityViewDocs"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { useAgent } from "@/contexts/AgentContext"
import { useSpace } from "@/contexts/SpaceContext"
import { useTimeFilter } from "@/contexts/TimeFilterContext"
import { cn } from "@/lib/utils"
import { getPresetTimeWindow } from "@/lib/timeFilter"
import { SkillCard } from "@/labs/skill/SkillCard"
import { SkillConfigImportDialog } from "@/labs/skill/SkillConfigImportDialog"
import { SkillDrawer } from "@/labs/skill/SkillDrawer"
import { SkillOverview } from "@/labs/skill/SkillOverview"
import {
  deleteSkillConfig,
  getSkillConfig,
  listSkillCallHistory,
  listSkillConfigSummaries,
  listSkillStats,
} from "@/labs/skill/skillApi"
import {
  skillProvenanceCounts,
  skillQueryErrorCode,
  skillTraceHref,
} from "@/labs/skill/skillDisplay"
import type {
  SkillCallRef,
  SkillLoadState,
  SkillStatsState,
  SkillStat,
} from "@/labs/skill/types"

const FIELD_CLASS =
  "h-8 max-w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-page)] px-2 text-xs outline-none focus-visible:border-[var(--color-labs)] focus-visible:ring-2 focus-visible:ring-[var(--color-border-labs)]"
export function SkillPage() {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const { currentSpace } = useSpace()
  const { currentAgent } = useAgent()
  const {
    fromTimestamp,
    isCustom,
    timeRange,
    toTimestamp,
    refreshTick,
    setCustomTime,
  } = useTimeFilter()
  const [keyword, setKeyword] = useState("")
  const [filter, setFilter] = useState("all")
  const [sort, setSort] = useState("calls")
  const [visible, setVisible] = useState(24)
  const [importOpen, setImportOpen] = useState(false)
  const [deletion, setDeletion] = useState<{
    context: object
    name: string
    pending: boolean
    error: string
  } | null>(null)
  const deletingKeys = useRef(new Set<string>())
  const [searchParams, setSearchParams] = useSearchParams()
  const { search } = useLocation()
  const openSkillName = searchParams.get("skill")
  const openSkill = (name: string | null) =>
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      if (name) next.set("skill", name)
      else next.delete("skill")
      return next
    })
  const spaceId = currentSpace?.id ?? ""
  const canManage = currentSpace?.role === 1
  const drawerContext = useMemo(
    () => ({ spaceId, name: openSkillName }),
    [spaceId, openSkillName]
  )
  const activeDrawer = useRef<object | null>(drawerContext)
  useLayoutEffect(() => {
    activeDrawer.current = drawerContext
    return () => {
      activeDrawer.current = null
    }
  }, [drawerContext])
  const scopedDeletion =
    deletion?.context === drawerContext && deletion.name === openSkillName
      ? deletion
      : null
  const agentUuid = searchParams.get("agent_uuid") || currentAgent?.uuid
  const scanWindow = useMemo(() => {
    // Refresh anchors one absolute window for totals, history pages and exits.
    void refreshTick
    return isCustom && fromTimestamp && toTimestamp
      ? { from: fromTimestamp, to: toTimestamp }
      : getPresetTimeWindow(timeRange)
  }, [timeRange, isCustom, fromTimestamp, toTimestamp, refreshTick])
  const params = useMemo(
    () => ({
      spaceId,
      agentProduct: currentAgent?.agent_product ?? "",
      timeRange,
      from: scanWindow.from,
      to: scanWindow.to,
      agentUuid,
    }),
    [spaceId, currentAgent?.agent_product, timeRange, scanWindow, agentUuid]
  )
  const canQueryStats = !!spaceId && !!params.agentProduct
  const statsQuery = useQuery({
    queryKey: ["labs", "skill", "stats", params, "summary"],
    queryFn: ({ signal }) => listSkillStats(params, signal, "summary"),
    retry: false,
    enabled: canQueryStats,
  })
  const configsQueryKey = ["labs", "skill", "configs", spaceId]
  const configsQuery = useQuery({
    queryKey: [...configsQueryKey, "summary"],
    queryFn: ({ signal }) => listSkillConfigSummaries(spaceId, signal),
    staleTime: 60_000,
    enabled: !!spaceId,
    retry: false,
  })
  const canLoadDefinition = !!spaceId && !!openSkillName
  const detailQuery = useQuery({
    queryKey: [...configsQueryKey, "detail", openSkillName],
    queryFn: ({ signal }) =>
      getSkillConfig(spaceId, openSkillName ?? "", signal),
    staleTime: 60_000,
    enabled: canLoadDefinition,
    retry: false,
  })
  const { refetch: refetchStats } = statsQuery
  const { refetch: refetchConfigs } = configsQuery
  const { refetch: refetchDetail } = detailQuery
  const absoluteScope = JSON.stringify(params)
  const refresh = useRef({ tick: refreshTick, scope: absoluteScope })
  useEffect(() => {
    if (refresh.current.tick !== refreshTick) {
      const sameWindow = refresh.current.scope === absoluteScope
      refresh.current = { tick: refreshTick, scope: absoluteScope }
      // A changed absolute window is a new query; identical fixed windows refetch.
      if (sameWindow && canQueryStats) void refetchStats()
      void refetchConfigs()
      if (canLoadDefinition) void refetchDetail()
    }
  }, [
    refreshTick,
    absoluteScope,
    canQueryStats,
    canLoadDefinition,
    refetchStats,
    refetchConfigs,
    refetchDetail,
  ])
  const data = statsQuery.data
  const configs = useMemo(() => configsQuery.data ?? [], [configsQuery.data])
  const statsState: SkillStatsState = !canQueryStats
    ? "idle"
    : data
      ? "ready"
      : statsQuery.isError
        ? "error"
        : "loading"
  const configState: SkillLoadState = configsQuery.data
    ? "ready"
    : configsQuery.isError
      ? "error"
      : configsQuery.isLoading
        ? "loading"
        : "ready"
  const configByName = useMemo(
    () => new Map(configs.map((config) => [config.name, config])),
    [configs]
  )
  const observedStats = useMemo(() => data?.skills ?? [], [data?.skills])
  const statByName = useMemo(
    () => new Map(observedStats.map((stat) => [stat.name, stat])),
    [observedStats]
  )
  const observedIds = useMemo(
    () => new Set(observedStats.map((stat) => stat.name)),
    [observedStats]
  )
  const allNames = useMemo(
    () => [
      ...new Set([
        ...observedStats.map((stat) => stat.name),
        ...configs.map((config) => config.name),
      ]),
    ],
    [observedStats, configs]
  )
  const matchesFilter = (name: string, value: string) => {
    const stat = statByName.get(name),
      config = configByName.get(name),
      provenance = stat ? skillProvenanceCounts(stat) : null
    return (
      value === "all" ||
      (value === "observed" && !!stat) ||
      (value === "imported" && statsState === "ready" && !!config && !stat) ||
      (value === "error" && !!stat && stat.errors > 0) ||
      (value === "inferred" && !!provenance?.inferred) ||
      (value === "reported" && !!provenance?.reported)
    )
  }
  const filterValues = [
    "all",
    "observed",
    "imported",
    "error",
    "inferred",
    "reported",
  ]
  const cards = (() => {
    const needle = keyword.trim().toLowerCase()
    return allNames
      .filter((name) => {
        const config = configByName.get(name)
        const matches =
          !needle ||
          `${name}\n${config?.description ?? ""}`.toLowerCase().includes(needle)
        return matches && matchesFilter(name, filter)
      })
      .sort((a, b) => {
        const x = statByName.get(a),
          y = statByName.get(b)
        const value = (stat: SkillStat | undefined) =>
          sort === "p95"
            ? stat?.p95Ms
            : sort === "errors"
              ? stat?.errorRate
              : stat?.calls
        return sort === "name"
          ? a.localeCompare(b)
          : (value(y) ?? -1) - (value(x) ?? -1) || a.localeCompare(b)
      })
  })()
  const identity = JSON.stringify({
    ...params,
    from: isCustom ? fromTimestamp : undefined,
    to: isCustom ? toTimestamp : undefined,
  })
  const [previousIdentity, setPreviousIdentity] = useState(identity)
  if (previousIdentity !== identity) {
    setPreviousIdentity(identity)
    setVisible(24)
    setFilter("all")
    setKeyword("")
  }
  const traceHref = useCallback(
    (traceId: string, spanId: string, call?: SkillCallRef) => {
      const exactSearch = new URLSearchParams(search)
      exactSearch.set("range", "custom")
      exactSearch.set("from", params.from)
      exactSearch.set("to", params.to)
      return skillTraceHref(
        exactSearch.toString(),
        call ?? { traceId, spanId, agentProduct: params.agentProduct },
        params.agentProduct
      )
    },
    [search, params]
  )
  const narrowWindow = () => {
    const from = Date.parse(params.from),
      to = Date.parse(params.to)
    if (Number.isFinite(from) && Number.isFinite(to) && to > from)
      setCustomTime?.(
        new Date(
          Math.max(from, to - Math.min((to - from) / 2, 3600000))
        ).toISOString(),
        new Date(to).toISOString()
      )
  }
  const removeConfig = async (name: string) => {
    const key = JSON.stringify([spaceId, name])
    const context = drawerContext
    if (
      !canManage ||
      activeDrawer.current !== context ||
      deletingKeys.current.has(key)
    )
      return
    deletingKeys.current.add(key)
    setDeletion({ context, name, pending: true, error: "" })
    let failure = ""
    try {
      await deleteSkillConfig(spaceId, name)
      await queryClient.invalidateQueries({ queryKey: configsQueryKey })
      if (activeDrawer.current === context)
        toast.success(t("labs.skill.topic.removed"))
    } catch (error) {
      failure =
        error instanceof Error
          ? error.message
          : t("labs.skill.topic.removeFailed")
    } finally {
      deletingKeys.current.delete(key)
      if (activeDrawer.current === context)
        setDeletion((current) =>
          current?.context === context && current.name === name
            ? { context, name, pending: false, error: failure }
            : current
        )
    }
  }
  const slowest = (pick: (stat: SkillStat) => number | null) =>
    slowestSkillBy(observedStats, pick)
  return (
    <SpaceAgentGuard>
      <div className="flex min-w-0 flex-col gap-4 overflow-hidden px-4 pt-3 pb-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2.5">
            <span className="flex size-8 shrink-0 items-center justify-center rounded-lg border border-[var(--color-border-labs)] bg-[var(--color-bg-labs-tag)] text-[var(--color-labs)]">
              <Blocks className="size-4" aria-hidden="true" />
            </span>
            <PageBreadcrumb
              pageTitle={t("nav.labsSkill")}
              agentProduct={params.agentProduct}
            />
            <LabsIntroBanner
              icon={Blocks}
              title={t("labs.skill.intro.title")}
              summary={t("labs.skill.intro.summary")}
              links={[
                {
                  label: t("labs.skill.intro.linkGuide"),
                  href: observabilityViewDocsUrl("skill", i18n.language),
                },
              ]}
            >
              <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
                {(["skillFile", "call"] as const).map((key) => (
                  <div
                    key={key}
                    className="flex min-w-0 gap-2 text-xs leading-5"
                  >
                    <dt className="shrink-0 font-medium text-[var(--color-text-primary)]">
                      {t(`labs.skill.glossary.${key}.term`)}
                    </dt>
                    <dd className="min-w-0 [overflow-wrap:anywhere] text-[var(--color-text-secondary)]">
                      {t(`labs.skill.glossary.${key}.desc`)}
                    </dd>
                  </div>
                ))}
              </dl>
            </LabsIntroBanner>
          </div>
          {canManage ? (
            <Button
              variant="outline"
              size="sm"
              className="border-[var(--color-border-labs)] text-[var(--color-labs)] hover:bg-[var(--color-bg-labs-tag)] hover:text-[var(--color-labs)] dark:hover:bg-[var(--color-bg-labs-tag)]"
              onClick={() => setImportOpen(true)}
            >
              <FileText className="size-4" />
              {t("labs.skill.import.open")}
            </Button>
          ) : null}
        </div>
        <LabsAgentQueryNotice />
        {canQueryStats ? (
          <LabsCatalogueOverview columns={4} loading={statsQuery.isLoading}>
            {data ? (
              <SkillOverview
                overview={data.overview}
                slowestSkillName={slowest((stat) => stat.p95Ms)}
                slowestP50SkillName={slowest((stat) => stat.p50Ms)}
              />
            ) : null}
          </LabsCatalogueOverview>
        ) : null}
        <section className="flex min-w-0 flex-col gap-3">
          <LabsCatalogueHeading
            title={t("labs.skill.skillList")}
            counts={
              <LabsInventoryCounts
                observed={data && observedStats.length}
                configured={configsQuery.data && configs.length}
              />
            }
          >
            {cards.length > 0 &&
            (cards.length > visible || keyword.trim() || filter !== "all") ? (
              <span className="ml-auto text-xs text-muted-foreground">
                {t("labs.skill.topic.shown", {
                  shown: Math.min(visible, cards.length),
                  total: cards.length,
                })}
              </span>
            ) : null}
          </LabsCatalogueHeading>
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-0 basis-full md:min-w-40 md:flex-1 md:basis-auto">
              <Search className="pointer-events-none absolute top-2 left-2 size-4 text-muted-foreground" />
              <Input
                aria-label={t("labs.skill.topic.search")}
                value={keyword}
                onChange={(event) => {
                  setKeyword(event.target.value)
                  setVisible(24)
                }}
                placeholder={t("labs.skill.topic.search")}
                className="h-8 pl-8 focus-visible:border-[var(--color-labs)] focus-visible:ring-[var(--color-border-labs)]"
              />
            </div>
            <select
              aria-label={t("labs.skill.topic.filter")}
              value={filter}
              disabled={statsState !== "ready"}
              onChange={(event) => {
                setFilter(event.target.value)
                setVisible(24)
              }}
              className={cn(
                FIELD_CLASS,
                "disabled:opacity-50",
                filter !== "all" &&
                  "border-[var(--color-border-labs)] bg-[var(--color-bg-labs)] text-[var(--color-labs)]"
              )}
            >
              {filterValues.map((value) => (
                <option key={value} value={value}>
                  {t(`labs.skill.topic.filter${value}`)}
                </option>
              ))}
            </select>
            <select
              aria-label={t("labs.skill.topic.sort")}
              value={sort}
              onChange={(event) => setSort(event.target.value)}
              className={cn(
                FIELD_CLASS,
                sort !== "calls" &&
                  "border-[var(--color-border-labs)] bg-[var(--color-bg-labs)] text-[var(--color-labs)]"
              )}
            >
              {["calls", "errors", "p95", "name"].map((value) => (
                <option key={value} value={value}>
                  {t(`labs.skill.topic.sort${value}`)}
                </option>
              ))}
            </select>
            {filter !== "all" || keyword.trim() || sort !== "calls" ? (
              <Button
                variant="ghost"
                size="sm"
                className="shrink-0 text-[var(--color-labs)] hover:bg-[var(--color-bg-labs-tag)] hover:text-[var(--color-labs)] dark:hover:bg-[var(--color-bg-labs-tag)]"
                onClick={() => {
                  setFilter("all")
                  setKeyword("")
                  setSort("calls")
                  setVisible(24)
                }}
              >
                {t("labs.skill.topic.reset")}
              </Button>
            ) : null}
          </div>
          <LabsCatalogueResults
            observations={
              canQueryStats
                ? {
                    query: statsQuery,
                    failure: (
                      <>
                        {t("labs.skill.scanFailed")}
                        {skillQueryErrorCode(statsQuery.error) === 3027 ? (
                          <>
                            {" "}
                            {t("labs.skill.topic.scanLimit")}{" "}
                            <button
                              type="button"
                              onClick={narrowWindow}
                              className="rounded underline underline-offset-4 focus-visible:outline-2"
                            >
                              {t("labs.skill.topic.narrowWindow")}
                            </button>
                          </>
                        ) : null}
                      </>
                    ),
                  }
                : undefined
            }
            configs={{
              query: configsQuery,
              failure: t("labs.skill.configsFailed"),
            }}
            empty={
              <p>
                {t(
                  keyword.trim() || filter !== "all"
                    ? "labs.skill.searchEmpty.title"
                    : "labs.skill.empty.title"
                )}
              </p>
            }
          >
            {cards.length ? (
              <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,_320px),1fr))] gap-3">
                {cards.slice(0, visible).map((name) => (
                  <SkillCard
                    key={name}
                    stat={statByName.get(name) ?? null}
                    config={configByName.get(name)}
                    statState={statsState}
                    configState={configState}
                    onOpen={openSkill}
                    onImport={canManage ? () => setImportOpen(true) : undefined}
                  />
                ))}
              </div>
            ) : null}
          </LabsCatalogueResults>
          {cards.length > visible ? (
            <div className="flex gap-3">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setVisible((value) => value + 24)}
              >
                {t("labs.skill.topic.loadMore")}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setVisible(cards.length)}
              >
                {t("labs.skill.topic.showAll")}
              </Button>
            </div>
          ) : cards.length > 24 ? (
            <Button variant="ghost" size="sm" onClick={() => setVisible(24)}>
              {t("labs.skill.topic.collapse")}
            </Button>
          ) : null}
        </section>
      </div>
      <SkillDrawer
        key={`${spaceId}:${openSkillName}`}
        skillName={openSkillName}
        stat={openSkillName ? (statByName.get(openSkillName) ?? null) : null}
        config={detailQuery.data ?? undefined}
        statState={statsState}
        configState={
          detailQuery.data !== undefined
            ? "ready"
            : detailQuery.isError
              ? "error"
              : "loading"
        }
        statsError={statsQuery.isError}
        configError={detailQuery.isError}
        onRetryStats={
          canQueryStats ? () => void statsQuery.refetch() : undefined
        }
        onRetryConfig={() => void refetchDetail()}
        onClose={() => openSkill(null)}
        onImport={canManage ? () => setImportOpen(true) : undefined}
        onDeleteConfig={canManage ? removeConfig : undefined}
        deletePending={scopedDeletion?.pending ?? false}
        deleteError={scopedDeletion?.error ?? ""}
        traceHref={traceHref}
        history={
          canQueryStats
            ? {
                params,
                load: listSkillCallHistory,
                onNarrowWindow: narrowWindow,
              }
            : undefined
        }
      />
      <SkillConfigImportDialog
        open={canManage && importOpen}
        onOpenChange={setImportOpen}
        spaceId={spaceId}
        observedSkillNames={[...observedIds]}
      />
    </SpaceAgentGuard>
  )
}
function slowestSkillBy(
  stats: SkillStat[],
  pick: (stat: SkillStat) => number | null
): string {
  let name = "",
    value = -Infinity
  for (const stat of stats) {
    if (!stat.calls) continue
    const current = pick(stat)
    if (current !== null && Number.isFinite(current) && current > value) {
      name = stat.name
      value = current
    }
  }
  return name
}
