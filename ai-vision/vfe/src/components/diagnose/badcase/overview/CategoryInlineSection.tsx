import { useEffect, useMemo, useState, type CSSProperties } from "react"
import { useTranslation } from "react-i18next"
import { ChevronDown, ChevronLeft, ChevronRight } from "lucide-react"

import { ChartPanel } from "@/components/bizcharts/charts/ChartPanel"
import { seriesColors } from "@/components/bizcharts/helpers/chartHelpers"
import {
  Carousel,
  type CarouselApi,
  CarouselContent,
  CarouselItem,
} from "@/components/ui/carousel"
import { Empty, EmptyHeader, EmptyTitle } from "@/components/ui/empty"
import { cn } from "@/lib/utils"
import type {
  DiagnosisStatsCategory,
  DiagnosisStatsDiagnoser,
  DiagnosisStatsTask,
  OverviewTraceEvidence,
} from "../../types"
import { CategoryTraceTable } from "./CategoryTraceTable"
import { DiagnoserScoreSummary } from "./DiagnoserScoreSummary"
import { diagnoserForTask } from "./diagnosisStats"

const DIAGNOSER_PANEL_HEIGHT = 252
const DIAGNOSER_CARD_HEIGHT = 120

export function CategoryInlineSection({
  category,
  categoryFilter,
  detailHeading,
  diagnosers,
  statsTasks,
  cardWidth,
  taskUuid,
  jobUuid,
  ownerId,
  timeRange,
  refreshTick,
  onAnchorTask,
  onOpenTrace,
  expanded,
  onToggle,
}: {
  category: DiagnosisStatsCategory
  categoryFilter?: string
  detailHeading?: string
  diagnosers: DiagnosisStatsDiagnoser[]
  statsTasks: DiagnosisStatsTask[]
  cardWidth?: number
  taskUuid?: string
  jobUuid?: string
  ownerId?: string
  timeRange?: { start_time?: string; end_time?: string }
  refreshTick?: number
  onAnchorTask?: (taskUuid?: string) => void
  onOpenTrace?: (
    traceId: string,
    jobUuid?: string,
    recordUuid?: string,
    evidence?: OverviewTraceEvidence
  ) => void
  expanded: boolean
  onToggle: () => void
}) {
  const { t } = useTranslation()
  const trendScopeKey = `${category.id}:${jobUuid ?? ""}:${taskUuid ?? ""}:${ownerId ?? ""}:${timeRange?.start_time ?? ""}:${timeRange?.end_time ?? ""}:${refreshTick ?? 0}`
  const [hoverState, setHoverState] = useState<{
    scopeKey: string
    taskUuid: string
    scores: Record<string, number | null>
  } | null>(null)
  const hoveredScores =
    hoverState?.scopeKey === trendScopeKey ? hoverState.scores : null
  const filterScopeKey = trendScopeKey
  const [diagnoserFilter, setDiagnoserFilter] = useState<{
    scopeKey: string
    uuid: string
  } | null>(null)
  const selectedDiagnoserUuid =
    diagnoserFilter?.scopeKey === filterScopeKey
      ? diagnoserFilter.uuid
      : undefined
  const [carouselApi, setCarouselApi] = useState<CarouselApi>()
  const effectiveCardWidth = cardWidth ?? 232
  const cardColumnCount =
    diagnosers.length <= 2 ? 1 : diagnosers.length <= 4 ? 2 : 3

  const cardsPerPage = cardColumnCount * 2
  const diagnoserPages = useMemo(
    () =>
      Array.from(
        { length: Math.ceil(diagnosers.length / cardsPerPage) },
        (_, page) =>
          diagnosers.slice(page * cardsPerPage, (page + 1) * cardsPerPage)
      ),
    [cardsPerPage, diagnosers]
  )
  const cardPageCount = diagnoserPages.length
  const cardPageScopeKey = `${trendScopeKey}:${cardColumnCount}:${diagnosers.map((diagnoser) => diagnoser.uuid).join(",")}`
  const [cardPageState, setCardPageState] = useState<{
    scopeKey: string
    page: number
  } | null>(null)
  const currentCardPage =
    cardPageState?.scopeKey === cardPageScopeKey
      ? Math.min(cardPageState.page, Math.max(0, cardPageCount - 1))
      : 0

  useEffect(() => {
    carouselApi?.scrollTo(0, true)
  }, [cardPageScopeKey, carouselApi])

  useEffect(() => {
    if (!carouselApi) return
    const syncSelectedPage = () => {
      setCardPageState({
        scopeKey: cardPageScopeKey,
        page: carouselApi.selectedScrollSnap(),
      })
      setDiagnoserFilter(null)
    }
    carouselApi.on("select", syncSelectedPage)
    carouselApi.on("reInit", syncSelectedPage)

    return () => {
      carouselApi.off("select", syncSelectedPage)
      carouselApi.off("reInit", syncSelectedPage)
    }
  }, [cardPageScopeKey, carouselApi])

  const visibleDiagnosers = diagnoserPages[currentCardPage] ?? []
  const cardRowCount = Math.ceil(visibleDiagnosers.length / cardColumnCount)
  const cardGridHeight =
    cardRowCount * DIAGNOSER_CARD_HEIGHT + Math.max(0, cardRowCount - 1) * 12
  const hasPagination = cardPageCount > 1
  const trendPanelHeight = DIAGNOSER_PANEL_HEIGHT
  const cardAreaWidth =
    cardColumnCount * effectiveCardWidth + Math.max(0, cardColumnCount - 1) * 12
  const detailGridStyle = {
    "--diagnoser-card-area": `${cardAreaWidth}px`,
  } as CSSProperties

  const changeCardPage = (page: number) => {
    const nextPage = Math.max(0, Math.min(page, cardPageCount - 1))
    setCardPageState({ scopeKey: cardPageScopeKey, page: nextPage })
    setDiagnoserFilter(null)
    carouselApi?.scrollTo(nextPage)
  }

  const { trendData, trendDiagnosers } = useMemo(() => {
    const series = diagnosers.map((diagnoser, index) => ({
      key: `diagnoser:${diagnoser.uuid}`,
      name: diagnoser.name,
      color: seriesColors[index % seriesColors.length],
    }))
    return {
      trendData: statsTasks.map((task) => {
        const row: Record<string, number | string | null> = {
          time: task.time,
          task_uuid: task.task_uuid,
        }
        diagnosers.forEach((diagnoser) => {
          row[`diagnoser:${diagnoser.uuid}`] =
            diagnoserForTask(task, diagnoser.uuid, categoryFilter)?.score ??
            null
        })
        return row
      }),
      trendDiagnosers: series,
    }
  }, [categoryFilter, diagnosers, statsTasks])
  const renderDiagnoserGrid = (pageDiagnosers: DiagnosisStatsDiagnoser[]) => (
    <div
      className="grid gap-3"
      style={{
        gridTemplateColumns: `repeat(${cardColumnCount}, minmax(0, ${effectiveCardWidth}px))`,
        gridAutoRows: `${DIAGNOSER_CARD_HEIGHT}px`,
        justifyContent: "start",
      }}
    >
      {pageDiagnosers.map((diagnoser) => {
        const seriesKey = `diagnoser:${diagnoser.uuid}`
        return (
          <DiagnoserScoreSummary
            key={diagnoser.uuid}
            diagnoser={{
              ...diagnoser,
              score: hoveredScores
                ? (hoveredScores[seriesKey] ?? null)
                : diagnoser.score,
            }}
            scoreDelta={null}
            selected={selectedDiagnoserUuid === diagnoser.uuid}
            onSelect={() =>
              setDiagnoserFilter((current) =>
                current?.scopeKey === filterScopeKey &&
                current.uuid === diagnoser.uuid
                  ? null
                  : { scopeKey: filterScopeKey, uuid: diagnoser.uuid }
              )
            }
          />
        )
      })}
    </div>
  )

  return (
    <div className="rounded-lg border bg-card transition-colors">
      <button
        type="button"
        aria-expanded={expanded}
        data-quality-guide="detail"
        className="flex w-full items-center gap-2 px-4 py-3 text-left hover:bg-accent/30"
        onClick={onToggle}
      >
        {expanded ? (
          <ChevronDown className="size-4 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
        )}
        <span className="text-sm font-medium text-foreground">
          {detailHeading ??
            t("diagnose.overview.dimensionDetailHeading", {
              defaultValue: "诊断器详情：{{name}}诊断器",
              name: category.name,
            })}
        </span>
        <span className="ml-auto text-xs text-muted-foreground">
          {diagnosers.length} {t("diagnose.overview.diagnoserUnit")}
        </span>
      </button>

      {expanded ? (
        <div className="flex flex-col gap-3 border-t px-3 py-3 sm:px-4">
          {diagnosers.length === 0 ? (
            <Empty className="min-h-36 rounded-none border-0 p-6">
              <EmptyHeader>
                <EmptyTitle className="text-sm font-medium">
                  {t("diagnose.overview.noDiagnoser", {
                    defaultValue: "当前任务暂无诊断器",
                  })}
                </EmptyTitle>
              </EmptyHeader>
            </Empty>
          ) : (
            <>
              <div
                data-quality-guide="detail"
                className="grid grid-cols-1 items-start gap-3 lg:grid-cols-[var(--diagnoser-card-area)_minmax(0,1fr)]"
                style={detailGridStyle}
              >
                <div className="min-w-0">
                  {hasPagination ? (
                    <Carousel
                      setApi={setCarouselApi}
                      opts={{ align: "start", loop: false }}
                    >
                      <CarouselContent
                        className="ml-0 transition-[height] duration-200 ease-in-out"
                        style={{ height: cardGridHeight }}
                      >
                        {diagnoserPages.map((pageDiagnosers, page) => (
                          <CarouselItem key={page} className="pl-0">
                            {renderDiagnoserGrid(pageDiagnosers)}
                          </CarouselItem>
                        ))}
                      </CarouselContent>
                    </Carousel>
                  ) : (
                    renderDiagnoserGrid(visibleDiagnosers)
                  )}

                  {hasPagination ? (
                    <div className="mt-3 flex h-6 items-center justify-center gap-1">
                      <button
                        type="button"
                        className="inline-flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-[var(--color-bg-hover)] hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:pointer-events-none disabled:opacity-30"
                        disabled={currentCardPage === 0}
                        onClick={() => changeCardPage(currentCardPage - 1)}
                        aria-label={t("common.prev", {
                          defaultValue: "上一页",
                        })}
                        title={t("common.prev", { defaultValue: "上一页" })}
                      >
                        <ChevronLeft className="size-3.5" />
                      </button>
                      <div className="flex flex-wrap items-center justify-center gap-1">
                        {Array.from({ length: cardPageCount }, (_, page) => {
                          const active = page === currentCardPage
                          const label = t("common.page", { page: page + 1 })
                          return (
                            <button
                              key={page}
                              type="button"
                              className="inline-flex size-4 items-center justify-center rounded-full focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                              onClick={() => changeCardPage(page)}
                              aria-label={label}
                              aria-current={active ? "page" : undefined}
                              title={label}
                            >
                              <span
                                className={cn(
                                  "size-2 rounded-full transition-colors",
                                  active
                                    ? "bg-[var(--color-text-secondary)]"
                                    : "bg-[var(--color-text-disabled)] hover:bg-[var(--color-text-description)]"
                                )}
                              />
                            </button>
                          )
                        })}
                      </div>
                      <button
                        type="button"
                        className="inline-flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-[var(--color-bg-hover)] hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:pointer-events-none disabled:opacity-30"
                        disabled={currentCardPage >= cardPageCount - 1}
                        onClick={() => changeCardPage(currentCardPage + 1)}
                        aria-label={t("common.next", {
                          defaultValue: "下一页",
                        })}
                        title={t("common.next", { defaultValue: "下一页" })}
                      >
                        <ChevronRight className="size-3.5" />
                      </button>
                    </div>
                  ) : null}
                </div>

                <div
                  className="min-w-0 overflow-hidden transition-[height] duration-200 ease-in-out"
                  style={{ height: trendPanelHeight }}
                >
                  <ChartPanel
                    title={t("diagnose.overview.diagnoserTrendTitle")}
                    series={{
                      data: trendData,
                      xKey: "time",
                      onPointClick: onAnchorTask
                        ? (point) => {
                            const uuid = point.task_uuid
                            if (typeof uuid === "string" && uuid)
                              onAnchorTask(uuid)
                          }
                        : undefined,
                      selectedKey: "task_uuid",
                      selectedValue: taskUuid,
                      onPointHover: (point) => {
                        if (!point) {
                          setHoverState(null)
                          return
                        }
                        setHoverState({
                          scopeKey: trendScopeKey,
                          taskUuid:
                            typeof point.task_uuid === "string"
                              ? point.task_uuid
                              : "",
                          scores: Object.fromEntries(
                            trendDiagnosers.map(({ key }) => {
                              const value = point[key]
                              return [
                                key,
                                typeof value === "number" &&
                                Number.isFinite(value)
                                  ? value
                                  : null,
                              ]
                            })
                          ),
                        })
                      },
                      metrics: selectedDiagnoserUuid
                        ? trendDiagnosers.filter(
                            ({ key }) =>
                              key === `diagnoser:${selectedDiagnoserUuid}`
                          )
                        : trendDiagnosers,
                    }}
                    emptyText={t("diagnose.overview.trendNoTask")}
                    yAxisFormatter={(value) => String(Math.round(value))}
                    statsKeys={["mean", "min", "max"]}
                    className="h-full"
                    compact
                    height={Math.max(48, trendPanelHeight - 72)}
                    showLegend={trendDiagnosers.length > 0}
                    showFooter={false}
                  />
                </div>
              </div>

              <CategoryTraceTable
                key={`${category.id}:${selectedDiagnoserUuid ?? "all"}:${taskUuid ?? ""}:${jobUuid ?? ""}:${timeRange?.start_time ?? ""}:${timeRange?.end_time ?? ""}`}
                category={categoryFilter}
                diagnosers={diagnosers}
                diagnoserUuid={selectedDiagnoserUuid}
                taskUuid={taskUuid}
                jobUuid={jobUuid}
                ownerId={ownerId}
                timeRange={timeRange}
                refreshTick={refreshTick}
                onOpenTrace={onOpenTrace}
              />
            </>
          )}
        </div>
      ) : null}
    </div>
  )
}
