import {
  historicalExperimentFilter,
  historicalExperimentOptions,
} from "./historicalExperimentFilter"
import { useMemo, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { useTranslation } from "react-i18next"
import { useQuery } from "@tanstack/react-query"
import {
  Search,
  X,
  Inbox,
  Loader2,
  ArrowUp,
  ArrowDown,
  ArrowUpDown,
  CircleAlert,
  ChevronDown,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  Pagination,
  PaginationContent,
  PaginationEllipsis,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
} from "@/components/ui/pagination"
import { useSpace } from "@/contexts/SpaceContext"
import { useAgent } from "@/contexts/AgentContext"
import { useUser } from "@/contexts/UserContext"
import { SpaceType } from "@/lib/schemas/space"
import { JobSelector } from "@/components/diagnose/common/JobSelector"

import type { BadCaseResult, DiagnosisVerdict } from "../types"
import { BadCaseStatusBadge } from "./BadCaseStatusBadge"
import {
  getDataset,
  listRecords,
  getTask,
  listTasks,
  listJobs,
  toRecord,
} from "../diagnoseApi"
import { isUserSharedSpace } from "../resourceScopes"

const ALL_VALUE = "__all__"
const PAGE_SIZES = [10, 20, 50, 100]

type SortField = "traceTime" | "detectedAt"
type SortOrder = "asc" | "desc"

const thBase =
  "whitespace-nowrap border-b border-border bg-muted px-3 py-2 text-xs font-semibold text-muted-foreground"
const thSortable = `${thBase} cursor-pointer select-none hover:text-foreground`

function formatTime(iso: string, locale: string | undefined): string {
  const d = new Date(iso)
  return d.toLocaleString(locale, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  })
}

interface BadCaseListViewProps {
  onSelectBadCase: (result: BadCaseResult) => void
  selectedResultId: string | null
  /** 选定的 task uuid —— 用 task_uuid 精确取批,样本数稳定。
   *  不传则查询平台默认诊断任务。 */
  taskUuid?: string
  /** 选定 task 所属的 job uuid（records API 必填） */
  jobUuid?: string
  onJobChange: (jobUuid: string | undefined) => void
  timeRange?: { start_time?: string; end_time?: string }
  refreshTick?: number
}

export function BadCaseListView({
  onSelectBadCase,
  selectedResultId,
  taskUuid,
  jobUuid,
  onJobChange,
  timeRange,
  refreshTick,
}: BadCaseListViewProps) {
  const { t } = useTranslation()
  const [searchParams, setSearchParams] = useSearchParams()
  const { currentSpace } = useSpace()
  const { currentAgent } = useAgent()
  const { user, loading: userLoading } = useUser()

  const spaceId = currentSpace?.id ?? ""
  const agentUuid = currentAgent?.uuid ?? ""

  const [keywordDraft, setKeywordDraft] = useState("")
  const [sortField, setSortField] = useState<SortField>("detectedAt")
  const [sortOrder, setSortOrder] = useState<SortOrder>("desc")
  const [keyword, setKeyword] = useState("")
  const [isComposing, setIsComposing] = useState(false)

  const jobsQuery = useQuery({
    queryKey: [
      "diagnose-jobs",
      spaceId,
      agentUuid,
      refreshTick,
      user?.user_uuid,
    ],
    queryFn: () => listJobs(spaceId, { agent_uuid: agentUuid }),
    enabled: !!spaceId && !!agentUuid && !!user?.user_uuid,
    retry: false,
  })
  const jobs = jobsQuery.isError ? [] : (jobsQuery.data?.items ?? [])
  const defaultJob = jobs.find((job) => job.name === "default")
  const defaultJobUuid = defaultJob?.uuid ?? jobs[0]?.uuid

  const rawVerdict = searchParams.get("verdict")
  const verdictFilter: DiagnosisVerdict | typeof ALL_VALUE =
    rawVerdict === "good" || rawVerdict === "failed" || rawVerdict === ALL_VALUE
      ? rawVerdict
      : "bad"
  const statusFilter = searchParams.get("status") ?? ALL_VALUE
  const effectiveVerdictFilter =
    statusFilter === "pending" ? "bad" : verdictFilter
  const verdictParam: DiagnosisVerdict | undefined =
    effectiveVerdictFilter === ALL_VALUE ? undefined : effectiveVerdictFilter
  const jobFilterParam = searchParams.get("job")
  const selectedJobUuid = taskUuid
    ? jobUuid
    : (jobFilterParam ?? defaultJobUuid)
  const queryJobUuids = selectedJobUuid ? [selectedJobUuid] : []
  const selectedJob = jobs.find((job) => job.uuid === selectedJobUuid)
  const selectedDatasetUuid = selectedJob?.dataset_uuid
  const datasetQuery = useQuery({
    queryKey: [
      "diagnose-dataset",
      spaceId,
      selectedDatasetUuid,
      refreshTick,
      user?.user_uuid,
    ],
    queryFn: () => getDataset(spaceId, selectedDatasetUuid!),
    enabled: !!spaceId && !!selectedDatasetUuid && !!user?.user_uuid,
    retry: false,
  })
  const currentExperimentOptions = useMemo(
    () =>
      historicalExperimentOptions(
        datasetQuery.isError ? undefined : datasetQuery.data?.spec
      ),
    [datasetQuery.data?.spec, datasetQuery.isError]
  )
  const requestedExperimentIds = searchParams.getAll("exp_ver_id")
  const experimentFilter = historicalExperimentFilter(
    requestedExperimentIds,
    currentExperimentOptions
  )
  const experimentOptions = experimentFilter.options
  const selectedExperimentIds = experimentFilter.selected
  const showExperimentGroup = experimentOptions.length > 0
  const datasetResolved =
    !selectedDatasetUuid || datasetQuery.isSuccess || datasetQuery.isError
  const includesUserSharedJob =
    currentSpace?.type === SpaceType.Personal &&
    queryJobUuids.some((uuid) =>
      jobs.some((job) => job.uuid === uuid && isUserSharedSpace(job.space_uuid))
    )
  const queriedJobScopesResolved = queryJobUuids.every((uuid) =>
    jobs.some((job) => job.uuid === uuid)
  )
  const currentOwnerId =
    user?.user_uuid || (typeof window !== "undefined" ? "" : "")
  const ownerIdParam = includesUserSharedJob
    ? currentOwnerId || undefined
    : undefined

  const requestedDiagnoserFilter = searchParams.get("diagnoser")
  const diagnoserFilter = requestedDiagnoserFilter ?? ALL_VALUE
  const page = Number(searchParams.get("page") ?? "1")
  const limit = Number(searchParams.get("limit") ?? "10")
  const sortBy =
    sortField === "traceTime" ? "target_created_at" : "diagnosed_at"
  // A selected Task owns the complete batch, including older source Traces.
  const recordTimeRange = taskUuid ? undefined : timeRange

  const recordsQuery = useQuery({
    queryKey: [
      "diagnosis-records",
      spaceId,
      user?.user_uuid,
      agentUuid,
      queryJobUuids,
      ownerIdParam,
      taskUuid,
      recordTimeRange?.start_time,
      recordTimeRange?.end_time,
      verdictParam,
      statusFilter,
      diagnoserFilter,
      selectedExperimentIds,
      keyword,
      page,
      limit,
      sortBy,
      sortOrder,
      refreshTick,
    ],
    queryFn: () =>
      listRecords(spaceId, {
        job_uuid: queryJobUuids,
        owner_id: ownerIdParam,
        task_uuid: taskUuid,
        start_time: recordTimeRange?.start_time,
        end_time: recordTimeRange?.end_time,
        verdict: verdictParam,
        review_status: statusFilter === ALL_VALUE ? undefined : statusFilter,
        diagnoser_uuid:
          diagnoserFilter === ALL_VALUE ? undefined : diagnoserFilter,
        exp_ver_id:
          selectedExperimentIds.length > 0 ? selectedExperimentIds : undefined,
        keyword: keyword || undefined,
        limit,
        offset: (page - 1) * limit,
        sort_by: sortBy,
        sort_order: sortOrder,
      }),
    enabled:
      !!spaceId &&
      !!user?.user_uuid &&
      queryJobUuids.length > 0 &&
      queriedJobScopesResolved &&
      datasetResolved &&
      !experimentFilter.invalid &&
      (!includesUserSharedJob || !!ownerIdParam),
    retry: false,
  })

  const frozenTaskQuery = useQuery({
    queryKey: [
      "diagnosis-filter-definitions",
      spaceId,
      user?.user_uuid,
      selectedJobUuid,
      taskUuid,
      refreshTick,
    ],
    queryFn: async () =>
      taskUuid
        ? getTask(spaceId, taskUuid)
        : (await listTasks(spaceId, { job_uuid: selectedJobUuid!, limit: 1 }))
            .items[0],
    enabled: !!spaceId && !!selectedJobUuid && !!user?.user_uuid,
    retry: false,
  })
  const allRecords = useMemo(
    () =>
      recordsQuery.isError || experimentFilter.invalid
        ? []
        : (recordsQuery.data?.items ?? []).map((dto) => toRecord(dto, [])),
    [recordsQuery.data, recordsQuery.isError, experimentFilter.invalid]
  )
  const diagnoserOptions = useMemo(() => {
    const names = new Map<string, string>()
    if (
      !frozenTaskQuery.isError &&
      frozenTaskQuery.data?.definition_source === "task_snapshot"
    ) {
      for (const definition of Object.values(
        frozenTaskQuery.data.diagnoser_definitions ?? {}
      ))
        names.set(definition.uuid, definition.name)
    }
    if (!(recordsQuery.isError || experimentFilter.invalid)) {
      for (const record of recordsQuery.data?.items ?? []) {
        if (record.definition_source === "task_snapshot" && record.definition)
          names.set(record.definition.uuid, record.definition.name)
      }
    }
    if (requestedDiagnoserFilter && !names.has(requestedDiagnoserFilter))
      names.set(requestedDiagnoserFilter, requestedDiagnoserFilter)
    return Array.from(names, ([id, name]) => ({ id, name }))
  }, [
    frozenTaskQuery.data,
    frozenTaskQuery.isError,
    recordsQuery.data,
    recordsQuery.isError,
    experimentFilter.invalid,
    requestedDiagnoserFilter,
  ])

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(searchParams)
    if (value === ALL_VALUE) {
      next.delete(key)
    } else {
      next.set(key, value)
      if (key === "status" && value === "pending") {
        next.delete("verdict")
      }
    }
    next.set("page", "1")
    setSearchParams(next, { replace: true })
  }

  const setPage = (p: number) => {
    const next = new URLSearchParams(searchParams)
    next.set("page", String(p))
    setSearchParams(next, { replace: true })
  }

  const setExperimentFilter = (values: string[]) => {
    const next = new URLSearchParams(searchParams)
    next.delete("exp_ver_id")
    for (const value of values) next.append("exp_ver_id", value)
    next.set("page", "1")
    setSearchParams(next, { replace: true })
  }

  const setLimit = (l: number) => {
    const next = new URLSearchParams(searchParams)
    next.set("limit", String(l))
    next.set("page", "1")
    setSearchParams(next, { replace: true })
  }

  const submitSearch = () => {
    setKeyword(keywordDraft.trim())
    setPage(1)
  }

  const resetFilters = () => {
    const next = new URLSearchParams(searchParams)
    next.delete("job")
    next.delete("verdict")
    next.delete("status")
    next.delete("diagnoser")
    next.delete("exp_ver_id")
    next.delete("page")
    setKeywordDraft("")
    setKeyword("")
    setSearchParams(next, { replace: true })
  }

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortOrder((prev) => (prev === "asc" ? "desc" : "asc"))
    } else {
      setSortField(field)
      setSortOrder("desc")
    }
    setPage(1)
  }

  const totalItems = recordsQuery.data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(totalItems / limit))
  const safePage = Math.min(page, totalPages)
  const pageItems = allRecords

  const hasAnyFilter =
    verdictFilter !== "bad" ||
    (jobFilterParam !== null && jobFilterParam !== defaultJobUuid) ||
    statusFilter !== ALL_VALUE ||
    diagnoserFilter !== ALL_VALUE ||
    selectedExperimentIds.length > 0 ||
    keyword !== ""
  const isEmpty = totalItems === 0
  const isLoading =
    recordsQuery.isLoading ||
    frozenTaskQuery.isLoading ||
    jobsQuery.isLoading ||
    (!!selectedDatasetUuid && datasetQuery.isLoading) ||
    (includesUserSharedJob && userLoading && !ownerIdParam)

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16 text-sm text-muted-foreground">
        <Loader2 className="mr-2 size-4 animate-spin" />
        {t("common.loading")}
      </div>
    )
  }

  return (
    <Card className="overflow-hidden">
      {taskUuid ? (
        <p className="border-b px-4 py-3 text-xs text-muted-foreground">
          {t("diagnose.badcase.fixedTaskScope")}
        </p>
      ) : null}
      <div className="flex w-full flex-wrap items-center justify-between gap-3 px-4 py-3">
        <div className="flex items-center gap-3">
          <div className="relative w-56">
            <button
              type="button"
              className="absolute top-1/2 left-2.5 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              onClick={submitSearch}
            >
              <Search className="size-3.5" />
            </button>
            <Input
              className="h-8 pr-8 pl-8 text-xs font-normal"
              placeholder={t("diagnose.badcase.searchPlaceholder")}
              value={keywordDraft}
              onChange={(e) => setKeywordDraft(e.target.value)}
              onCompositionStart={() => setIsComposing(true)}
              onCompositionEnd={() => setIsComposing(false)}
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !isComposing &&
                  !e.nativeEvent.isComposing
                ) {
                  submitSearch()
                }
              }}
            />
            {keywordDraft && (
              <button
                type="button"
                className="absolute top-1/2 right-2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                onClick={() => {
                  setKeywordDraft("")
                  setKeyword("")
                  setPage(1)
                }}
              >
                <X className="size-3.5" />
              </button>
            )}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <JobSelector
            spaceId={spaceId}
            agentUuid={agentUuid}
            value={jobUuid}
            onChange={onJobChange}
            disabled={!!taskUuid}
            refreshTick={refreshTick}
            groupByStatus
          />

          {showExperimentGroup ? (
            <ExperimentMultiSelect
              options={experimentOptions}
              value={selectedExperimentIds}
              onChange={setExperimentFilter}
            />
          ) : null}

          <Select
            value={effectiveVerdictFilter}
            onValueChange={(v) => {
              const next = new URLSearchParams(searchParams)
              if (v === "bad") {
                next.delete("verdict")
              } else {
                next.set("verdict", v)
              }
              next.delete("status")
              next.set("page", "1")
              setSearchParams(next, { replace: true })
            }}
          >
            <SelectTrigger size="sm" className="w-[150px] text-xs font-normal">
              <span className="truncate">
                {t("diagnose.badcase.verdictLabel")}
                <SelectValue />
              </span>
            </SelectTrigger>
            <SelectContent position="popper">
              <SelectItem value="bad">
                {t("diagnose.badcase.verdictBad")}
              </SelectItem>
              <SelectItem value="good">
                {t("diagnose.badcase.verdictGood")}
              </SelectItem>
              <SelectItem value="failed">
                {t("diagnose.badcase.verdictFailed")}
              </SelectItem>
              <SelectItem value={ALL_VALUE}>
                {t("diagnose.badcase.verdictAll")}
              </SelectItem>
            </SelectContent>
          </Select>

          <Select
            value={statusFilter}
            onValueChange={(v) => setFilter("status", v)}
          >
            <SelectTrigger size="sm" className="w-[140px] text-xs font-normal">
              <span className="truncate">
                {t("diagnose.badcase.statusLabel")}
                <SelectValue />
              </span>
            </SelectTrigger>
            <SelectContent position="popper">
              <SelectItem value={ALL_VALUE}>{t("common.all")}</SelectItem>
              <SelectItem value="pending">
                {t("diagnose.badcase.statusPending")}
              </SelectItem>
              <SelectItem value="skipped">
                {t("diagnose.badcase.statusSkipped")}
              </SelectItem>
              <SelectItem value="cannotJudge">
                {t("diagnose.badcase.statusCannotJudge")}
              </SelectItem>
              <SelectItem value="confirmed">
                {t("diagnose.badcase.statusConfirmed")}
              </SelectItem>
              <SelectItem value="falsePositive">
                {t("diagnose.badcase.statusFalsePositive")}
              </SelectItem>
            </SelectContent>
          </Select>

          <Select
            value={diagnoserFilter}
            onValueChange={(v) => setFilter("diagnoser", v)}
          >
            <SelectTrigger size="sm" className="w-[180px] text-xs font-normal">
              <span className="truncate">
                {t("diagnose.badcase.diagnoserLabel")}
                <SelectValue />
              </span>
            </SelectTrigger>
            <SelectContent position="popper">
              <SelectItem value={ALL_VALUE}>{t("common.all")}</SelectItem>
              {diagnoserOptions.map((d) => (
                <SelectItem key={d.id} value={d.id}>
                  {d.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {hasAnyFilter && (
            <Button
              variant="ghost"
              size="sm"
              className="h-8 px-2 text-xs font-normal text-muted-foreground"
              onClick={resetFilters}
            >
              {t("diagnose.badcase.resetFilters")}
            </Button>
          )}
        </div>
      </div>

      <CardContent>
        {experimentFilter.invalid ? (
          <div role="alert" className="grid gap-3 p-4 text-sm">
            <p>{t("diagnose.specialist.historyFilterInvalid")}</p>
            <Button variant="outline" onClick={resetFilters}>
              {t("diagnose.badcase.resetFilters")}
            </Button>
          </div>
        ) : recordsQuery.isError ? (
          <BadCaseQueryError onRetry={() => void recordsQuery.refetch()} />
        ) : isEmpty ? (
          <BadCaseEmptyState
            hasAnyFilter={hasAnyFilter}
            onReset={resetFilters}
          />
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-sm">
                <thead className="sticky top-0 z-10">
                  <tr>
                    <th className={`w-[240px] text-left ${thBase}`}>
                      {t("diagnose.badcase.colTraceId")}
                    </th>
                    {showExperimentGroup ? (
                      <th className={`w-[150px] text-left ${thBase}`}>
                        {t("diagnose.badcase.colExperimentGroup")}
                      </th>
                    ) : null}
                    <th className={`w-[88px] text-left ${thBase}`}>
                      {t("diagnose.badcase.colStatus")}
                    </th>
                    <th className={`w-[140px] text-left ${thBase}`}>
                      {t("diagnose.badcase.colDiagnoser")}
                    </th>
                    <th className={`text-left ${thBase}`}>
                      {t("diagnose.badcase.colSummary")}
                    </th>
                    <th
                      className={`w-[148px] text-left ${thSortable}`}
                      onClick={() => handleSort("traceTime")}
                    >
                      <span className="inline-flex items-center gap-1">
                        {t("diagnose.badcase.colTraceTime")}
                        {sortField === "traceTime" ? (
                          sortOrder === "asc" ? (
                            <ArrowUp className="size-3.5" />
                          ) : (
                            <ArrowDown className="size-3.5" />
                          )
                        ) : (
                          <ArrowUpDown className="size-3.5 opacity-30" />
                        )}
                      </span>
                    </th>
                    <th
                      className={`w-[100px] text-left ${thSortable}`}
                      onClick={() => handleSort("detectedAt")}
                    >
                      <span className="inline-flex items-center gap-1">
                        {t("diagnose.badcase.colDetectedAt")}
                        {sortField === "detectedAt" ? (
                          sortOrder === "asc" ? (
                            <ArrowUp className="size-3.5" />
                          ) : (
                            <ArrowDown className="size-3.5" />
                          )
                        ) : (
                          <ArrowUpDown className="size-3.5 opacity-30" />
                        )}
                      </span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {pageItems.map((item) => (
                    <BadCaseRow
                      key={item.uuid}
                      item={item}
                      isSelected={selectedResultId === item.uuid}
                      onClick={() => onSelectBadCase(item)}
                      showExperimentGroup={showExperimentGroup}
                    />
                  ))}
                </tbody>
              </table>
            </div>

            <BadCasePagination
              page={safePage}
              totalPages={totalPages}
              totalItems={totalItems}
              limit={limit}
              setPage={setPage}
              setLimit={setLimit}
            />
          </>
        )}
      </CardContent>
    </Card>
  )
}

function BadCaseQueryError({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation()

  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
      <CircleAlert className="size-7 text-destructive" />
      <p className="text-sm text-muted-foreground">
        {t("diagnose.badcase.loadFailed")}
      </p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        {t("common.retry")}
      </Button>
    </div>
  )
}

function ExperimentMultiSelect({
  options,
  value,
  onChange,
}: {
  options: string[]
  value: string[]
  onChange: (value: string[]) => void
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [draftValue, setDraftValue] = useState(value)
  const selected = new Set(draftValue)
  const label =
    value.length === 0
      ? t("diagnose.badcase.experimentAll")
      : value.length === 1
        ? value[0]
        : t("diagnose.badcase.experimentSelected", { count: value.length })

  const toggle = (experimentId: string) => {
    setDraftValue(
      selected.has(experimentId)
        ? draftValue.filter((id) => id !== experimentId)
        : [...draftValue, experimentId]
    )
  }

  return (
    <Popover
      open={open}
      onOpenChange={(nextOpen) => {
        if (nextOpen) setDraftValue(value)
        setOpen(nextOpen)
      }}
    >
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          className="h-8 w-[180px] justify-between gap-1.5 rounded-3xl border border-transparent bg-input/50 px-3 py-2 text-xs font-normal whitespace-nowrap transition-[color,box-shadow,background-color] outline-none hover:bg-input/70 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/30"
        >
          <span className="truncate">
            {t("diagnose.badcase.filterExperiment")}：{label}
          </span>
          <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[240px] gap-0 rounded-3xl p-1.5">
        <div className="max-h-64 overflow-y-auto">
          {options.map((experimentId) => (
            <label
              key={experimentId}
              className="flex cursor-pointer items-center gap-2.5 rounded-2xl px-3 py-2 text-sm font-medium transition-colors hover:bg-accent hover:text-accent-foreground"
            >
              <Checkbox
                checked={selected.has(experimentId)}
                onCheckedChange={() => toggle(experimentId)}
              />
              <span className="min-w-0 flex-1 truncate font-mono">
                {experimentId}
              </span>
            </label>
          ))}
        </div>
        <div className="mx-1 mt-1 flex items-center justify-between gap-2 border-t border-border pt-1">
          <div className="flex items-center">
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs font-normal"
              onClick={() => setDraftValue(options)}
            >
              {t("diagnose.badcase.experimentSelectAll")}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs font-normal"
              disabled={draftValue.length === 0}
              onClick={() => setDraftValue([])}
            >
              {t("diagnose.badcase.experimentClear")}
            </Button>
          </div>
          <Button
            size="sm"
            className="h-7 px-2 text-xs font-normal"
            onClick={() => {
              onChange(draftValue)
              setOpen(false)
            }}
          >
            {t("diagnose.badcase.experimentConfirm")}
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  )
}

function BadCaseEmptyState({
  hasAnyFilter,
  onReset,
}: {
  hasAnyFilter: boolean
  onReset: () => void
}) {
  const { t } = useTranslation()

  if (hasAnyFilter) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 py-12 text-center">
        <p className="text-sm text-muted-foreground">
          {t("diagnose.badcase.emptyFilterTitle")}
        </p>
        <Button
          variant="outline"
          size="sm"
          className="mt-1 h-7 text-xs font-normal"
          onClick={onReset}
        >
          {t("diagnose.badcase.resetFilters")}
        </Button>
      </div>
    )
  }

  return (
    <div className="flex flex-col items-center justify-center gap-3 py-20 text-center">
      <div className="flex size-14 items-center justify-center rounded-xl bg-muted/60">
        <Inbox className="size-7 text-muted-foreground/40" />
      </div>
      <div className="flex flex-col gap-1">
        <p className="text-sm font-medium text-muted-foreground">
          {t("diagnose.badcase.emptyTitle")}
        </p>
        <p className="text-xs text-muted-foreground/70">
          {t("diagnose.badcase.emptyDesc")}
        </p>
      </div>
    </div>
  )
}

function BadCaseRow({
  item,
  isSelected,
  onClick,
  showExperimentGroup,
}: {
  item: BadCaseResult
  isSelected: boolean
  onClick: () => void
  showExperimentGroup: boolean
}) {
  const { i18n } = useTranslation()
  return (
    <tr
      onClick={onClick}
      className={`cursor-pointer border-b border-border transition-colors hover:bg-accent ${
        isSelected ? "bg-accent" : ""
      }`}
    >
      <td className="px-3 py-3 align-top whitespace-nowrap">
        <span
          className="truncate font-mono text-xs text-foreground/80"
          title={item.traceId}
        >
          {item.traceId}
        </span>
      </td>
      {showExperimentGroup ? (
        <td className="max-w-[150px] px-3 py-3 align-top whitespace-nowrap">
          <span
            className="block truncate font-mono text-xs text-muted-foreground"
            title={item.experimentGroupIds.join(", ")}
          >
            {item.experimentGroupIds.length > 0
              ? item.experimentGroupIds.join(", ")
              : "-"}
          </span>
        </td>
      ) : null}
      <td className="px-3 py-3 align-top whitespace-nowrap">
        <BadCaseStatusBadge
          status={item.reviewStatus}
          tone={item.verdict === "good" ? "noAction" : "review"}
        />
      </td>
      <td className="px-3 py-3 align-top text-xs whitespace-nowrap text-muted-foreground">
        {item.diagnoserName}
      </td>
      <td className="max-w-0 px-3 py-3 align-top">
        <Tooltip>
          <TooltipTrigger asChild>
            <div className="line-clamp-2 text-xs text-muted-foreground">
              {item.summary}
            </div>
          </TooltipTrigger>
          <TooltipContent className="max-w-[480px] text-xs break-words whitespace-pre-wrap">
            {item.summary}
          </TooltipContent>
        </Tooltip>
      </td>
      <td className="px-3 py-3 align-top text-xs whitespace-nowrap text-muted-foreground tabular-nums">
        {formatTime(item.traceTimestamp, i18n?.resolvedLanguage)}
      </td>
      <td className="px-3 py-3 align-top text-xs whitespace-nowrap text-muted-foreground tabular-nums">
        {formatTime(item.detectedAt, i18n?.resolvedLanguage)}
      </td>
    </tr>
  )
}

function BadCasePagination({
  page,
  totalPages,
  totalItems,
  limit,
  setPage,
  setLimit,
}: {
  page: number
  totalPages: number
  totalItems: number
  limit: number
  setPage: (p: number) => void
  setLimit: (l: number) => void
}) {
  const { t } = useTranslation()
  const pageNumbers = useMemo(() => {
    const pages: (number | "ellipsis-start" | "ellipsis-end")[] = []
    if (totalPages <= 7) {
      for (let i = 1; i <= totalPages; i++) pages.push(i)
    } else {
      pages.push(1)
      if (page > 3) pages.push("ellipsis-start")
      for (
        let i = Math.max(2, page - 1);
        i <= Math.min(totalPages - 1, page + 1);
        i++
      )
        pages.push(i)
      if (page < totalPages - 2) pages.push("ellipsis-end")
      pages.push(totalPages)
    }
    return pages
  }, [page, totalPages])

  return (
    <div className="flex items-center justify-end gap-2 pt-4">
      <span className="mr-2 text-sm whitespace-nowrap text-muted-foreground">
        {t("common.total", { count: totalItems })}
      </span>
      <Pagination
        className="mx-0 w-auto"
        aria-label={t("dashboard.selectPage")}
      >
        <PaginationContent>
          <PaginationItem>
            <PaginationPrevious
              href="#"
              aria-label={t("common.prev")}
              text=""
              onClick={(e) => {
                e.preventDefault()
                if (page > 1) setPage(page - 1)
              }}
              className={page <= 1 ? "pointer-events-none opacity-50" : ""}
            />
          </PaginationItem>
          {pageNumbers.map((item) =>
            typeof item === "string" ? (
              <PaginationItem key={item}>
                <PaginationEllipsis />
              </PaginationItem>
            ) : (
              <PaginationItem key={item}>
                <PaginationLink
                  href="#"
                  isActive={item === page}
                  aria-label={t("common.page", { page: item })}
                  onClick={(e) => {
                    e.preventDefault()
                    setPage(item)
                  }}
                >
                  {item}
                </PaginationLink>
              </PaginationItem>
            )
          )}
          <PaginationItem>
            <PaginationNext
              href="#"
              aria-label={t("common.next")}
              text=""
              onClick={(e) => {
                e.preventDefault()
                if (page < totalPages) setPage(page + 1)
              }}
              className={
                page >= totalPages ? "pointer-events-none opacity-50" : ""
              }
            />
          </PaginationItem>
        </PaginationContent>
      </Pagination>
      <Select
        value={String(limit)}
        onValueChange={(v) => {
          setLimit(Number(v))
        }}
      >
        <SelectTrigger size="sm" aria-label={t("dashboard.selectPageSize")}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent position="popper">
          {PAGE_SIZES.map((count) => (
            <SelectItem key={count} value={String(count)}>
              {t("common.perPage", { count: count })}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}
