import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { CircleHelp, Inbox, Info, Loader2, Plus } from "lucide-react"
import { useQuery, useQueryClient } from "@tanstack/react-query"

import { StatsCard } from "@/components/bizcharts/statscard/StatsCard"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useAgent } from "@/contexts/AgentContext"
import { useSpace } from "@/contexts/SpaceContext"
import { useUser } from "@/contexts/UserContext"
import { SpaceType } from "@/lib/schemas/space"
import { cn } from "@/lib/utils"
import { ApiBusinessError, ApiAuthRedirectError } from "@/lib/api"
import { isTimeRangeLongerThanDays } from "@/lib/timeFilter"
import { QUALITY_STATS_MAX_RANGE_DAYS } from "../qualityApi"
import { getTask } from "../jobApi"
import type { QuickInvalidatedScope } from "../TraceDiagnosisPanel"

import { fetchDiagnosisStats, listJobs } from "../diagnoseApi"
import { DiagnosisScopeSummary } from "../DiagnosisScopeSummary"
import { scopeFromQuality } from "../diagnosisScope"
import { isUserSharedSpace } from "../resourceScopes"
import type {
  DiagnosisStatsCategory,
  DiagnosisStatsDiagnoser,
  OverviewTraceEvidence,
} from "../types"
import { CategoryCard, CategoryDetailSection } from "./overview/CategoryViews"
import {
  categoryTrend,
  findStatsTask,
  globalTrend,
} from "./overview/diagnosisStats"
import {
  L0TrendChart,
  type QualityTrendHoverPoint,
} from "./overview/L0TrendChart"

const ALL_DIAGNOSERS_SCOPE = "__all_diagnosers__"
const POINT_READ_LIMIT = 10
function accessDenied(error: unknown) {
  if (error instanceof ApiAuthRedirectError) return true
  if (!(error instanceof ApiBusinessError)) return false
  if ([401, 403].includes(error.statusCode ?? 0)) return true
  return (
    error.statusCode === 404 &&
    !!error.data &&
    typeof error.data === "object" &&
    "error_key" in error.data &&
    error.data.error_key === "QUICK_AGENT_UNAVAILABLE"
  )
}

function EmptyQualityOverviewRow({
  categories,
}: {
  categories: Array<Pick<DiagnosisStatsCategory, "id" | "name">>
}) {
  const { t } = useTranslation()
  const dimensionPlaceholders = categories.length
    ? categories
    : Array.from({ length: 4 }, (_, index) => ({
        id: `empty-${index}`,
        name: "",
      }))
  const categoryIndicators = categories.length
    ? categories
    : Array.from({ length: 4 }, (_, index) => ({
        id: `empty-indicator-${index}`,
        name: "",
      }))

  return (
    <div className="grid grid-cols-1 items-start gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)_minmax(0,5fr)]">
      <article className="flex h-[252px] flex-col gap-2 rounded-xl border border-[var(--color-border)] bg-card p-4">
        <span className="flex w-full items-center gap-2 text-sm text-[var(--color-text-secondary)]">
          {t("diagnose.overview.totalScoreTitle")}
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                className="inline-flex size-4 items-center justify-center rounded-full text-muted-foreground transition-colors hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                aria-label={t("diagnose.overview.totalScoreHelpLabel", {
                  defaultValue: "查看质量分计算公式",
                })}
              >
                <CircleHelp className="size-3.5" />
              </button>
            </TooltipTrigger>
            <TooltipContent
              side="bottom"
              className="max-w-[320px] text-xs leading-relaxed"
            >
              {t("diagnose.overview.totalScoreHelp")}
            </TooltipContent>
          </Tooltip>
          <span className="ml-auto flex shrink-0 items-center gap-1">
            {categoryIndicators.map((category) => (
              <span
                key={category.id}
                className="size-2 rounded-full bg-muted-foreground/30"
              />
            ))}
          </span>
        </span>
        <div className="flex min-h-[60px] flex-1 items-center rounded-lg bg-[var(--color-bg-auxiliary)] px-4 py-3">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <strong className="text-4xl font-bold text-[var(--color-auxiliary)] tabular-nums">
              —
            </strong>
            <span className="text-sm text-muted-foreground">
              {t("diagnose.overview.scoreUnit")}
            </span>
          </div>
        </div>
        <p className="text-xs text-muted-foreground">
          {t("diagnose.overview.trendNoTask")}
        </p>
      </article>

      <div className="grid grid-cols-2 gap-3" data-quality-guide="scores">
        {dimensionPlaceholders.map((category) =>
          category.name ? (
            <StatsCard
              key={category.id}
              title={category.name}
              value="—"
              unit={t("diagnose.overview.scoreUnit")}
              tone="disabled"
              className="h-[120px]"
            />
          ) : (
            <Skeleton key={category.id} className="h-[120px] rounded-xl" />
          )
        )}
      </div>

      <div className="h-[252px] min-w-0" data-quality-guide="trend">
        <L0TrendChart
          trend={[]}
          dimensionTrends={categories.map((category) => ({
            key: category.id,
            name: category.name,
            points: [],
          }))}
        />
      </div>
    </div>
  )
}

export function QualityOverviewView({
  taskUuid,
  jobUuid,
  timeRange,
  refreshTick,
  forceDetailExpanded,
  onCreateJob,
  onAnchorTask,
  onResolvedTaskFinishedAt,
  onOpenTrace,
  onQuickDenied,
}: {
  onQuickDenied?: (scope: QuickInvalidatedScope) => void
  taskUuid?: string
  jobUuid?: string
  timeRange?: { start_time?: string; end_time?: string }
  refreshTick?: number
  forceDetailExpanded?: boolean
  onCreateJob?: () => void
  onAnchorTask?: (taskUuid?: string) => void
  onResolvedTaskFinishedAt?: (finishedAt: string | null) => void
  onOpenTrace?: (
    traceId: string,
    jobUuid?: string,
    recordUuid?: string,
    evidence?: OverviewTraceEvidence
  ) => void
}) {
  const { t } = useTranslation()
  const categoryLabel = (cat: { id: string; name: string }) => {
    const map: Record<string, string> = {
      outcome: t("diagnose.diagnoser.catOutcome"),
      process: t("diagnose.diagnoser.catProcess"),
      safety: t("diagnose.diagnoser.catSafety"),
      system: t("diagnose.diagnoser.catSystem"),
    }
    return map[cat.id] ?? cat.name
  }
  const { currentSpace } = useSpace()
  const { user } = useUser()
  const { currentAgent } = useAgent()
  const spaceId = currentSpace?.id ?? ""
  const agentUuid = currentAgent?.uuid ?? ""
  const queryClient = useQueryClient()
  const identity = `${user?.user_uuid ?? ""}:${spaceId}:${agentUuid}`
  const pointScope = `${identity}:${jobUuid ?? ""}:${taskUuid ?? ""}`
  const [deniedScope, setDeniedScope] = useState<string | null>(null)
  const pointRevoked = deniedScope === pointScope
  const canCreateDiagnosisJob =
    currentSpace?.permissions.includes("diagnosis.job.manage") ?? false

  const jobsQuery = useQuery({
    queryKey: [
      "diagnose-jobs",
      spaceId,
      agentUuid,
      refreshTick,
      user?.user_uuid,
    ],
    queryFn: () => listJobs(spaceId, { agent_uuid: agentUuid }),
    enabled: !!spaceId && !!agentUuid,
  })
  const selectedJob = jobsQuery.data?.items.find((job) => job.uuid === jobUuid)
  const needsOwner =
    currentSpace?.type === SpaceType.Personal &&
    isUserSharedSpace(selectedJob?.space_uuid)
  const ownerId = needsOwner ? user?.user_uuid || undefined : undefined

  const canQuerySelectedJob =
    !!spaceId &&
    !!agentUuid &&
    !!selectedJob &&
    selectedJob.agent_uuid === agentUuid &&
    (!needsOwner || !!ownerId)

  const exceedsMaxRange = isTimeRangeLongerThanDays(
    timeRange?.start_time,
    timeRange?.end_time,
    QUALITY_STATS_MAX_RANGE_DAYS
  )
  const query = useQuery({
    queryKey: [
      "diagnosis-stats",
      spaceId,
      user?.user_uuid,
      agentUuid,
      jobUuid ?? "nojob",
      ownerId,
      timeRange?.start_time,
      timeRange?.end_time,
      refreshTick,
    ],
    queryFn: () =>
      fetchDiagnosisStats(spaceId, {
        job_uuid: selectedJob!.uuid,
        start_time: timeRange?.start_time,
        end_time: timeRange?.end_time,
        owner_id: ownerId,
      }),
    enabled: canQuerySelectedJob && !exceedsMaxRange,
  })
  const taskQuery = useQuery({
    queryKey: ["quick-task", identity, taskUuid],
    queryFn: () => getTask(spaceId, taskUuid!),
    enabled:
      canQuerySelectedJob && !!taskUuid && !!user?.user_uuid && !pointRevoked,
    retry: false,
    refetchInterval: (q) =>
      q.state.error
        ? false
        : ["pending", "dispatched", "running"].includes(
              q.state.data?.status ?? ""
            )
          ? 2000
          : false,
  })
  const task = taskQuery.data
  const taskMatchesScope =
    !!task &&
    task.uuid === taskUuid &&
    task.job_uuid === jobUuid &&
    (!task.quick_run || task.quick_run.agent_uuid === agentUuid)
  const currentTaskSucceeded =
    canQuerySelectedJob &&
    taskMatchesScope &&
    taskQuery.isSuccess &&
    task.status === "succeeded"
  const taskMetadataUnavailable =
    taskQuery.isError || (taskQuery.isSuccess && !taskMatchesScope)
  const pointKey = [
    "diagnosis-stats",
    spaceId,
    user?.user_uuid,
    agentUuid,
    jobUuid,
    "point",
    taskUuid,
    ownerId,
    refreshTick,
  ]
  const pointQuery = useQuery({
    queryKey: pointKey,
    queryFn: () =>
      fetchDiagnosisStats(spaceId, {
        job_uuid: jobUuid!,
        task_uuid: taskUuid!,
        agent_uuid: agentUuid,
        owner_id: ownerId,
      }),
    enabled:
      canQuerySelectedJob &&
      !!taskUuid &&
      !!user?.user_uuid &&
      currentTaskSucceeded &&
      !pointRevoked,
    retry: false,
    refetchInterval: (q) =>
      q.state.error ||
      q.state.data?.tasks.some((point) => point.task_uuid === taskUuid) ||
      q.state.dataUpdateCount >= POINT_READ_LIMIT
        ? false
        : 2000,
  })
  const denied = accessDenied(taskQuery.error) || accessDenied(pointQuery.error)
  useEffect(() => {
    if (!taskUuid || !denied || pointRevoked) return
    // Latch final server revocation before deleting its error-bearing cache entry.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDeniedScope(pointScope)
    const scope: QuickInvalidatedScope = {
      taskUuids: [taskUuid],
      jobUuids: jobUuid ? [jobUuid] : [],
      recordUuids: [],
      traceIds: task?.quick_run?.trace_ids ?? [],
    }
    const privatePoint = (q: { queryKey: readonly unknown[] }) =>
      q.queryKey[0] === "diagnosis-stats" &&
      q.queryKey[1] === spaceId &&
      q.queryKey[2] === user?.user_uuid &&
      q.queryKey[3] === agentUuid &&
      q.queryKey.includes(taskUuid)
    void queryClient.cancelQueries({ predicate: privatePoint })
    queryClient.removeQueries({ predicate: privatePoint })
    if (task?.quick_run?.agent_uuid === agentUuid) onQuickDenied?.(scope)
  }, [
    taskUuid,
    denied,
    pointRevoked,
    pointScope,
    jobUuid,
    task,
    spaceId,
    user,
    agentUuid,
    queryClient,
    onQuickDenied,
  ])
  const categorySelectionScopeKey = `${spaceId}:${agentUuid}:${jobUuid ?? ""}`
  const [categorySelection, setCategorySelection] = useState<{
    scopeKey: string
    categoryName: string
  } | null>(null)
  const selectedCategoryName =
    categorySelection?.scopeKey === categorySelectionScopeKey
      ? categorySelection.categoryName
      : ALL_DIAGNOSERS_SCOPE
  const hoverScopeKey = `${spaceId}:${agentUuid}:${jobUuid ?? ""}:${taskUuid ?? ""}:${timeRange?.start_time ?? ""}:${timeRange?.end_time ?? ""}`
  const [trendHoverState, setTrendHoverState] = useState<{
    scopeKey: string
    point: QualityTrendHoverPoint
  } | null>(null)
  const hoveredTrendPoint =
    trendHoverState?.scopeKey === hoverScopeKey ? trendHoverState.point : null

  const statsTasks = useMemo(() => query.data?.tasks ?? [], [query.data?.tasks])
  const resolvedTask = taskUuid
    ? pointRevoked || denied || pointQuery.isError || !currentTaskSucceeded
      ? undefined
      : findStatsTask(pointQuery.data?.tasks ?? [], taskUuid)
    : findStatsTask(statsTasks)
  const pointReadExhausted =
    pointQuery.dataUpdatedAt > 0 &&
    (queryClient.getQueryState(pointKey)?.dataUpdateCount ?? 0) >=
      POINT_READ_LIMIT
  const categoryDefinitions = useMemo(
    () => query.data?.categories ?? [],
    [query.data?.categories]
  )
  const categoryViews = useMemo(
    () =>
      resolvedTask?.global.categories.length
        ? resolvedTask.global.categories.map((category) => ({
            ...category,
            score:
              resolvedTask.definition_source === "task_snapshot" &&
              category.diagnosers.some((d) => d.score_role === "quality")
                ? category.score
                : null,
            diagnosers: category.diagnosers.map((d) => ({
              ...d,
              score:
                resolvedTask.definition_source === "task_snapshot" &&
                d.score_role === "quality"
                  ? d.score
                  : null,
            })),
          }))
        : categoryDefinitions.map((category) => ({
            ...category,
            score: null,
            diagnosers: [],
          })),
    [categoryDefinitions, resolvedTask]
  )
  const totalTrend = useMemo(() => globalTrend(statsTasks), [statsTasks])
  const categoryTrends = useMemo(
    () =>
      categoryViews.map((category) => categoryTrend(statsTasks, category.id)),
    [categoryViews, statsTasks]
  )
  const dimensionGridRef = useRef<HTMLDivElement>(null)
  const [dimensionCardWidth, setDimensionCardWidth] = useState<number>()

  useLayoutEffect(() => {
    const grid = dimensionGridRef.current
    if (!grid || typeof ResizeObserver === "undefined") return

    let frame = 0
    const syncCardWidth = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        const firstCard = grid.firstElementChild
        const nextWidth =
          firstCard instanceof HTMLElement
            ? Math.round(firstCard.getBoundingClientRect().width)
            : undefined
        setDimensionCardWidth((current) =>
          current === nextWidth ? current : nextWidth
        )
      })
    }
    const observer = new ResizeObserver(syncCardWidth)
    observer.observe(grid)
    syncCardWidth()

    return () => {
      cancelAnimationFrame(frame)
      observer.disconnect()
    }
  }, [categoryViews.length])
  const allDiagnosers = useMemo(() => {
    const diagnosersByUuid = new Map<string, DiagnosisStatsDiagnoser>()
    for (const category of categoryViews) {
      for (const diagnoser of category.diagnosers) {
        if (!diagnosersByUuid.has(diagnoser.uuid)) {
          diagnosersByUuid.set(diagnoser.uuid, diagnoser)
        }
      }
    }
    return Array.from(diagnosersByUuid.values())
  }, [categoryViews])

  const selectedCategoryView = categoryViews.find(
    (category) => category.id === selectedCategoryName
  )
  const isAllDiagnosersSelected =
    selectedCategoryName === ALL_DIAGNOSERS_SCOPE || !selectedCategoryView
  const activeCategoryView = isAllDiagnosersSelected
    ? {
        id: ALL_DIAGNOSERS_SCOPE,
        name: t("diagnose.overview.totalScoreTitle"),
        score:
          resolvedTask?.definition_source === "task_snapshot" &&
          allDiagnosers.some((d) => d.score_role === "quality")
            ? (resolvedTask.global.score ?? null)
            : null,
        diagnosers: allDiagnosers,
      }
    : selectedCategoryView
  const activeCategoryName = activeCategoryView?.id ?? ""
  const selectCategoryAndReveal = (categoryName: string) => {
    setCategorySelection({
      scopeKey: categorySelectionScopeKey,
      categoryName,
    })
    requestAnimationFrame(() => {
      document.getElementById("quality-dimension-detail")?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      })
    })
  }

  useEffect(() => {
    onResolvedTaskFinishedAt?.(taskUuid ? (resolvedTask?.time ?? null) : null)
  }, [onResolvedTaskFinishedAt, resolvedTask?.time, taskUuid])

  if (!!agentUuid && jobsQuery.isError) {
    return (
      <div className="flex items-center justify-center py-16 text-sm text-muted-foreground">
        {t("diagnose.overview.loadError")}
      </div>
    )
  }

  if (!selectedJob) {
    if (jobsQuery.isLoading) {
      return (
        <div className="flex items-center justify-center py-16 text-sm text-muted-foreground">
          <Loader2 className="mr-2 size-4 animate-spin" />
          {t("common.loading")}
        </div>
      )
    }

    if ((jobsQuery.data?.items.length ?? 0) > 0) {
      return (
        <div className="flex flex-col items-center justify-center py-16 text-sm text-muted-foreground">
          {t("diagnose.overview.selectJobFirst")}
        </div>
      )
    }

    return (
      <div
        className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border bg-[var(--color-bg-subtle)] py-20"
        data-quality-guide="empty"
      >
        <div className="mb-4 flex size-14 items-center justify-center rounded-2xl bg-muted/60">
          <Inbox className="size-7 text-muted-foreground/40" />
        </div>
        <p className="text-sm text-muted-foreground">
          {t("diagnose.overview.emptyCreateDesc")}
        </p>
        {onCreateJob ? (
          canCreateDiagnosisJob ? (
            <Button
              size="sm"
              className="mt-4 h-8 gap-1.5"
              onClick={onCreateJob}
            >
              <Plus className="size-3.5" />
              {t("diagnose.job.createCta")}
            </Button>
          ) : (
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="mt-4 inline-flex">
                  <Button size="sm" className="h-8 gap-1.5" disabled>
                    <Plus className="size-3.5" />
                    {t("diagnose.job.createCta")}
                  </Button>
                </span>
              </TooltipTrigger>
              <TooltipContent>
                {t("diagnose.overview.createPermissionRequired")}
              </TooltipContent>
            </Tooltip>
          )
        ) : null}
      </div>
    )
  }

  if (exceedsMaxRange && !taskUuid) {
    return (
      <div
        role="status"
        className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground"
      >
        <Info className="size-4 shrink-0" aria-hidden="true" />
        {t("timeFilter.maxRangeHint", { days: QUALITY_STATS_MAX_RANGE_DAYS })}
      </div>
    )
  }

  if (query.isLoading && !query.data) {
    return (
      <div className="flex flex-col gap-3">
        <div className="grid grid-cols-1 items-start gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)_minmax(0,5fr)]">
          <Skeleton className="h-[252px] rounded-xl" />
          <div className="grid grid-cols-2 gap-3">
            {Array.from({ length: 4 }, (_, index) => (
              <Skeleton key={index} className="h-[120px] rounded-xl" />
            ))}
          </div>
          <Skeleton className="h-[252px] rounded-xl" />
        </div>
        <Skeleton className="h-[212px] rounded-xl" />
      </div>
    )
  }

  if (canQuerySelectedJob && query.isError && !taskUuid) {
    return (
      <div className="flex items-center justify-center py-16 text-sm text-muted-foreground">
        {t("diagnose.overview.loadError")}
      </div>
    )
  }

  if (!query.data && !taskUuid) return null
  if (!resolvedTask) {
    return (
      <div data-quality-guide="empty">
        {taskUuid ? (
          <div className="mb-3 flex items-center gap-2 text-sm" role="status">
            <span>
              {pointRevoked ||
              denied ||
              pointQuery.isError ||
              taskMetadataUnavailable
                ? t("diagnose.overview.loadError")
                : task?.status === "failed" || task?.status === "canceled"
                  ? t("diagnose.quick.states.failed")
                  : pointReadExhausted
                    ? t("diagnose.quick.states.unknown")
                    : t("common.loading")}
            </span>
            {!pointRevoked &&
            !denied &&
            (pointReadExhausted ||
              pointQuery.isError ||
              taskMetadataUnavailable) ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  taskMetadataUnavailable
                    ? void taskQuery.refetch()
                    : void queryClient.resetQueries({
                        queryKey: pointKey,
                        exact: true,
                      })
                }
              >
                {t("common.retry")}
              </Button>
            ) : null}
          </div>
        ) : null}
        <EmptyQualityOverviewRow categories={categoryDefinitions} />
      </div>
    )
  }
  const resolvedTaskUuid = resolvedTask.task_uuid
  const displayedTaskUuid = hoveredTrendPoint?.taskUuid || resolvedTaskUuid
  const displayedTask = hoveredTrendPoint
    ? findStatsTask(statsTasks, displayedTaskUuid)
    : resolvedTask
  const displayedQualityScore = hoveredTrendPoint
    ? hoveredTrendPoint.qualityScore
    : displayedTask?.definition_source === "task_snapshot" &&
        displayedTask.global.categories.some((c) =>
          c.diagnosers.some((d) => d.score_role === "quality")
        )
      ? displayedTask.global.score
      : null
  const displayedCategoryScore = (name: string, score: number | null) =>
    hoveredTrendPoint
      ? (hoveredTrendPoint.dimensionScores[name] ?? null)
      : score

  return (
    <div className="space-y-3">
      {displayedTask ? (
        <DiagnosisScopeSummary
          scope={scopeFromQuality(displayedTask)}
          scoreUnavailable={displayedTask.global.score === null}
        />
      ) : null}
      <div className="grid grid-cols-1 items-start gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)_minmax(0,5fr)]">
        <article
          role="button"
          tabIndex={0}
          aria-pressed={isAllDiagnosersSelected}
          className={cn(
            "flex h-[252px] cursor-pointer flex-col gap-2 rounded-xl border border-[var(--color-border)] bg-card p-4 transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
            isAllDiagnosersSelected && "border-primary/25"
          )}
          onClick={() => selectCategoryAndReveal(ALL_DIAGNOSERS_SCOPE)}
          onKeyDown={(event) => {
            if (
              event.target === event.currentTarget &&
              (event.key === "Enter" || event.key === " ")
            ) {
              event.preventDefault()
              selectCategoryAndReveal(ALL_DIAGNOSERS_SCOPE)
            }
          }}
          data-quality-guide="scores"
        >
          <span className="flex w-full items-center gap-2 text-sm text-[var(--color-text-secondary)]">
            {t("diagnose.overview.totalScoreTitle")}
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  className="inline-flex size-4 items-center justify-center rounded-full text-muted-foreground transition-colors hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  onClick={(event) => event.stopPropagation()}
                  aria-label={t("diagnose.overview.totalScoreHelpLabel", {
                    defaultValue: "查看质量分计算公式",
                  })}
                >
                  <CircleHelp className="size-3.5" />
                </button>
              </TooltipTrigger>
              <TooltipContent
                side="bottom"
                className="max-w-[320px] text-xs leading-relaxed"
              >
                {t("diagnose.overview.totalScoreHelp")}
              </TooltipContent>
            </Tooltip>
            <span className="ml-auto flex shrink-0 items-center gap-1">
              {categoryViews.map((category) => (
                <Tooltip key={category.id}>
                  <TooltipTrigger asChild>
                    <span
                      className={`size-2 rounded-full ${displayedCategoryScore(category.id, category.score) !== null ? "bg-[var(--color-auxiliary)]" : "bg-muted-foreground/30"}`}
                    />
                  </TooltipTrigger>
                  <TooltipContent side="bottom" className="text-xs">
                    {categoryLabel(category)}
                    {displayedCategoryScore(category.id, category.score) !==
                    null
                      ? ` ${displayedCategoryScore(category.id, category.score)!.toFixed(1)}`
                      : t("diagnose.overview.scoreEmpty")}
                  </TooltipContent>
                </Tooltip>
              ))}
            </span>
          </span>
          <div className="flex min-h-[60px] flex-1 items-center rounded-lg bg-[var(--color-bg-auxiliary)] px-4 py-3">
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
              <strong className="text-4xl font-bold text-[var(--color-auxiliary)] tabular-nums">
                {displayedQualityScore !== null
                  ? displayedQualityScore.toFixed(1)
                  : "—"}
              </strong>
              <span className="text-sm text-muted-foreground">
                {t("diagnose.overview.scoreUnit")}
              </span>
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            {t("diagnose.overview.totalScoreDesc", {
              corpus: displayedTask?.target_count ?? resolvedTask.target_count,
            })}
          </p>
        </article>

        <div
          ref={dimensionGridRef}
          className="grid grid-cols-2 gap-3"
          data-quality-guide="scores"
        >
          {categoryViews.map((category) => (
            <CategoryCard
              key={category.id}
              category={{
                ...category,
                score: displayedCategoryScore(category.id, category.score),
              }}
              title={categoryLabel(category)}
              scoreDelta={null}
              selected={
                !isAllDiagnosersSelected &&
                selectedCategoryView?.id === category.id
              }
              onClick={() => selectCategoryAndReveal(category.id)}
            />
          ))}
        </div>

        <div className="h-[252px] min-w-0" data-quality-guide="trend">
          <L0TrendChart
            trend={totalTrend}
            dimensionTrends={categoryViews.map((category, index) => ({
              key: category.id,
              name: categoryLabel(category),
              points: categoryTrends[index] ?? [],
            }))}
            taskUuid={resolvedTaskUuid}
            onAnchorTask={onAnchorTask}
            onPointHover={(point) =>
              setTrendHoverState(
                point ? { scopeKey: hoverScopeKey, point } : null
              )
            }
            loading={query.isFetching}
          />
        </div>
      </div>

      {activeCategoryView ? (
        <div id="quality-dimension-detail" className="scroll-mt-20">
          <CategoryDetailSection
            key={activeCategoryName}
            category={{
              ...activeCategoryView,
              name: isAllDiagnosersSelected
                ? activeCategoryView.name
                : categoryLabel(activeCategoryView),
            }}
            categoryFilter={
              isAllDiagnosersSelected ? undefined : activeCategoryView.id
            }
            detailHeading={
              isAllDiagnosersSelected
                ? t("diagnose.overview.allDiagnosersHeading")
                : undefined
            }
            diagnosers={activeCategoryView.diagnosers}
            statsTasks={statsTasks}
            cardWidth={dimensionCardWidth}
            taskUuid={resolvedTaskUuid}
            jobUuid={jobUuid}
            ownerId={ownerId}
            timeRange={timeRange}
            refreshTick={refreshTick}
            forceExpanded={forceDetailExpanded}
            onAnchorTask={onAnchorTask}
            onOpenTrace={onOpenTrace}
          />
        </div>
      ) : null}
    </div>
  )
}
