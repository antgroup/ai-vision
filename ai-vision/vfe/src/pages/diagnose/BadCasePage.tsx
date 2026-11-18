import { useProductGuide } from "@/components/product-guide/useProductGuide"
import { useCallback, useEffect, useMemo, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { useTranslation } from "react-i18next"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { History, RotateCcw } from "lucide-react"

import { SpaceAgentGuard } from "@/components/error/SpaceAgentGuard"
import { PageBreadcrumb } from "@/components/layout/PageBreadcrumb"
import { useAgent } from "@/contexts/AgentContext"
import { useSpace } from "@/contexts/SpaceContext"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Button } from "@/components/ui/button"
import { JobSelector } from "@/components/diagnose/common/JobSelector"
import { BadCaseListView } from "@/components/diagnose/badcase/BadCaseListView"
import { QualityOverviewView } from "@/components/diagnose/badcase/QualityOverviewView"
import {
  QualityOverviewGuide,
  type QualityOverviewGuideStepKey,
} from "@/components/diagnose/badcase/QualityOverviewGuide"
import { TraceTranscriptDrawer } from "@/components/diagnose/badcase-detail/TraceTranscriptDrawer"
import { markQualityOverviewGuideSeen } from "@/components/diagnose/qualityOverviewGuideState"
import { useTimeFilter } from "@/contexts/TimeFilterContext"
import { useUser } from "@/contexts/UserContext"
import { getPresetTimeWindow } from "@/lib/timeFilter"
import { formatDateTime } from "@/lib/formatters"
import { getDatasourceConfig } from "@/agents/lib/observabilityV2Api"
import { setAgentProductInSearch } from "@/agents/lib/agentDashboardRoute"
import { ApiAuthRedirectError, ApiBusinessError } from "@/lib/api"
import { getTask } from "@/components/diagnose/jobApi"
import type { QuickInvalidatedScope } from "@/components/diagnose/TraceDiagnosisPanel"
import { getDataset, listJobs } from "@/components/diagnose/diagnoseApi"
import { formatTraceTimeOffset } from "@/components/diagnose/job/jobFormModel"

import { NativeDetailDrawer } from "@/components/traces-otel/DetailDrawer/NativeDetailDrawer"
import { resolveBadCaseTraceSource } from "@/components/diagnose/badcase-detail/traceSourceSelection"
import {
  extractTraceTimestampMs,
  TRACE_DETAIL_PADDING_MS,
} from "@/agents/traces/sofaTraceTime"
import type {
  BadCaseResult,
  OverviewTraceEvidence,
} from "@/components/diagnose/types"

type TraceQueryTimeRange = { from?: string; to?: string }

function toBackendTime(value: string): string {
  const date = new Date(value)
  const pad = (part: number) => String(part).padStart(2, "0")
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

function readDatasetTimeOffset(
  spec: Record<string, unknown> | null | undefined
): string | undefined {
  const query = spec?.query
  if (!query || typeof query !== "object" || Array.isArray(query)) {
    return undefined
  }
  const timeOffset = (query as Record<string, unknown>).time_offset
  return typeof timeOffset === "string" && timeOffset.trim()
    ? timeOffset.trim()
    : undefined
}

export function BadCasePage() {
  const productGuide = useProductGuide()
  const { t } = useTranslation()
  const { currentAgent } = useAgent()
  const { currentSpace } = useSpace()
  const { user } = useUser()
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()

  const agentProduct =
    currentAgent?.agent_product ?? currentAgent?.agent_name ?? ""
  const agentUuid = currentAgent?.uuid ?? ""
  const spaceId = currentSpace?.id ?? ""
  const identityKey = `${user?.user_uuid ?? ""}:${spaceId}:${agentUuid}`
  const jobUuid = searchParams.get("job") ?? undefined
  const anchorTaskUuid = searchParams.get("task") ?? undefined
  const [deniedScope, setDeniedScope] = useState<
    (QuickInvalidatedScope & { identity: string }) | null
  >(null)
  const diagnosisDenied =
    deniedScope?.identity === identityKey &&
    ((!!anchorTaskUuid && deniedScope.taskUuids.includes(anchorTaskUuid)) ||
      (!!jobUuid && deniedScope.jobUuids.includes(jobUuid)))
  // Both overview and record-list routes can display private quick-run history.
  // Keep its authorization watch at the page boundary, independent of the picker.
  const anchoredTaskQuery = useQuery({
    queryKey: ["quick-task", identityKey, anchorTaskUuid],
    queryFn: () => getTask(spaceId, anchorTaskUuid!),
    enabled:
      !!spaceId &&
      !!agentUuid &&
      !!user?.user_uuid &&
      !!anchorTaskUuid &&
      !diagnosisDenied,
    retry: false,
  })

  const view = searchParams.get("view") ?? "overview"
  const pageTitle =
    view === "list" ? t("diagnose.badcase.listTitle") : t("nav.badcase")
  const selectedResultId = searchParams.get("detail") ?? null
  const guideUserKey = user?.user_uuid
  const [qualityGuideStep, setQualityGuideStep] =
    useState<QualityOverviewGuideStepKey | null>(null)

  /** 概览 trace 列表点开的 trace 详情(React state,非 URL)。 */
  const [traceDetailId, setTraceDetailId] = useState<string | null>(null)
  const [traceJobUuid, setTraceJobUuid] = useState<string | undefined>(
    undefined
  )
  const [traceRecordUuid, setTraceRecordUuid] = useState<string | undefined>(
    undefined
  )
  const [traceOverviewEvidence, setTraceOverviewEvidence] = useState<
    OverviewTraceEvidence | undefined
  >(undefined)
  const [rawSpanId, setRawSpanId] = useState<string | null>(null)
  const [rawTraceId, setRawTraceId] = useState<string | null>(null)
  const [rawTraceTimestamp, setRawTraceTimestamp] = useState<string | null>(
    null
  )

  const { data: dsConfig } = useQuery({
    queryKey: ["datasourceConfig", spaceId, agentProduct],
    queryFn: () => getDatasourceConfig(spaceId, agentProduct),
    enabled: !!spaceId && !!agentProduct,
  })
  const traceSource = useMemo(
    () => resolveBadCaseTraceSource(dsConfig),
    [dsConfig]
  )
  /** 转录抽屉的 center_time:SigNoz 按 ±10min 取 spans。 */
  const traceCenterTime = useMemo(() => {
    if (!traceDetailId) return undefined
    const ts = extractTraceTimestampMs(traceDetailId)
    if (ts === null) return undefined
    const d = new Date(ts)
    const pad = (n: number) => String(n).padStart(2, "0")
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  }, [traceDetailId])

  const rawTraceTimeRange: TraceQueryTimeRange | null = useMemo(() => {
    if (!rawTraceId) return null
    const traceIdTimestamp = extractTraceTimestampMs(rawTraceId)
    const recordTimestamp = rawTraceTimestamp
      ? Date.parse(rawTraceTimestamp)
      : Number.NaN
    const ts =
      traceIdTimestamp ??
      (Number.isFinite(recordTimestamp) ? recordTimestamp : null)
    if (ts === null) return null
    return {
      from: new Date(ts - TRACE_DETAIL_PADDING_MS).toISOString(),
      to: new Date(ts + TRACE_DETAIL_PADDING_MS).toISOString(),
    }
  }, [rawTraceId, rawTraceTimestamp])

  const handleQuickDenied = useCallback(
    (scope: QuickInvalidatedScope) => {
      setDeniedScope({ ...scope, identity: identityKey })
      const privateIds = [
        ...scope.taskUuids,
        ...scope.jobUuids,
        ...scope.recordUuids,
        ...scope.traceIds,
      ]
      const deniedCache = (query: {
        queryKey: readonly unknown[]
        state?: { data?: unknown }
      }) => {
        const key = query.queryKey
        if (key[1] === identityKey && String(key[0]).startsWith("quick-"))
          return true
        if (
          !user?.user_uuid ||
          !key.includes(spaceId) ||
          !key.includes(user.user_uuid) ||
          ![
            "diagnosis-record",
            "diagnosis-records",
            "diagnosis-stats",
            "trace-projection",
            "nativeTraceDetail",
            "diagnose-tasks",
            "diagnose-task",
            "diagnosis-filter-definitions",
          ].includes(String(key[0]))
        )
          return false
        // Both records layouts bind the reading principal at index 2 (owner is separate).
        if (
          key[0] === "diagnosis-records" &&
          (key[1] !== spaceId || key[2] !== user.user_uuid)
        )
          return false
        // BadCaseListView stores Job UUID[] at slot 4; mixed responses clear as a whole.
        // CategoryTraceTable stores flat Job/Task IDs, handled by the shared fallback.
        if (
          key[0] === "diagnosis-records" &&
          Array.isArray(key[4]) &&
          key[4].some((job) => scope.jobUuids.includes(job))
        )
          return true
        if (
          key[0] === "diagnosis-record" &&
          query.state?.data &&
          typeof query.state.data === "object"
        ) {
          const record = query.state.data as {
            task_uuid?: string
            job_uuid?: string
          }
          if (
            (record.task_uuid && scope.taskUuids.includes(record.task_uuid)) ||
            (record.job_uuid && scope.jobUuids.includes(record.job_uuid))
          )
            return true
        }
        return privateIds.some((id) => key.includes(id))
      }
      void queryClient.cancelQueries({ predicate: deniedCache })
      queryClient.removeQueries({ predicate: deniedCache })
      if (
        (!!anchorTaskUuid && scope.taskUuids.includes(anchorTaskUuid)) ||
        (!!jobUuid && scope.jobUuids.includes(jobUuid))
      ) {
        setTraceDetailId(null)
        setTraceRecordUuid(undefined)
        setTraceOverviewEvidence(undefined)
        setRawTraceId(null)
        setRawSpanId(null)
      }
    },
    [
      identityKey,
      setDeniedScope,
      queryClient,
      spaceId,
      user,
      anchorTaskUuid,
      jobUuid,
    ]
  )
  useEffect(() => {
    const error = anchoredTaskQuery.error
    const unavailable =
      error instanceof ApiBusinessError &&
      error.statusCode === 404 &&
      !!error.data &&
      typeof error.data === "object" &&
      "error_key" in error.data &&
      error.data.error_key === "QUICK_AGENT_UNAVAILABLE"
    const forbidden =
      error instanceof ApiAuthRedirectError ||
      (error instanceof ApiBusinessError &&
        [401, 403].includes(error.statusCode ?? 0))
    if (!anchorTaskUuid || diagnosisDenied || (!unavailable && !forbidden))
      return
    const task = anchoredTaskQuery.data
    // Synchronize local drawers and caches with the server's revocation signal.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    handleQuickDenied({
      taskUuids: [anchorTaskUuid],
      jobUuids: [
        ...new Set(
          [jobUuid, task?.job_uuid].filter((id): id is string => !!id)
        ),
      ],
      recordUuids: [selectedResultId, traceRecordUuid].filter(
        (id): id is string => !!id
      ),
      traceIds: [
        ...new Set(
          [
            ...(task?.quick_run?.trace_ids ?? []),
            traceDetailId,
            rawTraceId,
          ].filter((id): id is string => !!id)
        ),
      ],
    })
  }, [
    anchoredTaskQuery.error,
    anchoredTaskQuery.data,
    anchorTaskUuid,
    diagnosisDenied,
    handleQuickDenied,
    jobUuid,
    selectedResultId,
    traceRecordUuid,
    traceDetailId,
    rawTraceId,
  ])
  const [historicalTaskFinishedAt, setHistoricalTaskFinishedAt] = useState<
    string | null
  >(null)

  const setJobUuid = useCallback(
    (nextJobUuid: string | undefined) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          next.set("agent", agentProduct)
          if (nextJobUuid) next.set("job", nextJobUuid)
          else next.delete("job")
          next.delete("task")
          next.delete("diagnoser")
          next.delete("exp_ver_id")
          next.delete("page")
          return next
        },
        { replace: true }
      )
    },
    [agentProduct, setSearchParams]
  )

  const setAnchorTaskUuid = useCallback(
    (nextTaskUuid: string | undefined) => {
      setHistoricalTaskFinishedAt(null)
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          next.set("agent", agentProduct)
          if (nextTaskUuid) next.set("task", nextTaskUuid)
          else next.delete("task")
          return next
        },
        { replace: true }
      )
    },
    [agentProduct, setSearchParams]
  )
  const handleAnchorTaskUuid = useCallback(
    (nextTaskUuid: string | undefined) => {
      if (nextTaskUuid && qualityGuideStep && guideUserKey) {
        markQualityOverviewGuideSeen(guideUserKey)
      }
      setAnchorTaskUuid(nextTaskUuid)
    },
    [guideUserKey, qualityGuideStep, setAnchorTaskUuid]
  )
  const [lastIdentityKey, setLastIdentityKey] = useState(identityKey)
  /* eslint-disable react-hooks/set-state-in-effect -- 切换 space/agent 时清空旧空间选择 */
  useEffect(() => {
    if (identityKey !== lastIdentityKey) {
      setLastIdentityKey(identityKey)
      setTraceDetailId(null)
      setTraceJobUuid(undefined)
      setTraceRecordUuid(undefined)
      setTraceOverviewEvidence(undefined)
      setRawTraceId(null)
      setRawSpanId(null)
      setRawTraceTimestamp(null)
    }
  }, [identityKey, lastIdentityKey])
  /* eslint-enable react-hooks/set-state-in-effect */
  const { timeRange, isCustom, fromTimestamp, toTimestamp, refreshTick } =
    useTimeFilter()
  const qualityJobsQuery = useQuery({
    queryKey: [
      "diagnose-jobs",
      spaceId,
      agentUuid,
      refreshTick,
      user?.user_uuid,
    ],
    queryFn: () => listJobs(spaceId, { agent_uuid: agentUuid }),
    enabled:
      view === "overview" && !!spaceId && !!agentUuid && !diagnosisDenied,
  })
  const selectedQualityJob = qualityJobsQuery.data?.items.find(
    (job) => job.uuid === jobUuid
  )
  const selectedDatasetUuid = selectedQualityJob?.dataset_uuid
  const qualityDatasetQuery = useQuery({
    queryKey: [
      "diagnose-dataset",
      spaceId,
      selectedDatasetUuid,
      refreshTick,
      user?.user_uuid,
    ],
    queryFn: () => getDataset(spaceId, selectedDatasetUuid!),
    enabled:
      view === "overview" &&
      !!spaceId &&
      !!selectedDatasetUuid &&
      !diagnosisDenied,
  })
  const diagnosisDelay = readDatasetTimeOffset(qualityDatasetQuery.data?.spec)
  const resolvedRange = useMemo(() => {
    const current =
      isCustom && fromTimestamp && toTimestamp
        ? { from: fromTimestamp, to: toTimestamp }
        : getPresetTimeWindow(timeRange)
    return {
      start_time: toBackendTime(current.from),
      end_time: toBackendTime(current.to),
    }
    // A manual refresh advances preset "now" while preserving an explicit Task URL.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timeRange, isCustom, fromTimestamp, toTimestamp, refreshTick])

  const scopeKey = `${spaceId}::${agentProduct}::${jobUuid ?? "all"}::${resolvedRange.start_time}::${resolvedRange.end_time}::${refreshTick}`
  const [lastScopeKey, setLastScopeKey] = useState(scopeKey)
  /* eslint-disable react-hooks/set-state-in-effect -- 查询条件变化或重新查询时重置本地详情状态 */
  useEffect(() => {
    if (scopeKey !== lastScopeKey) {
      setLastScopeKey(scopeKey)
      setHistoricalTaskFinishedAt(null)
      setTraceDetailId(null)
      setTraceJobUuid(undefined)
      setTraceRecordUuid(undefined)
      setTraceOverviewEvidence(undefined)
    }
  }, [lastScopeKey, scopeKey])

  const setView = (v: string) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        next.set("view", v)
        next.delete("page")
        next.delete("detail")
        next.delete("intent")
        next.delete("passed")
        return next
      },
      { replace: true }
    )
  }

  const openDetail = (result: BadCaseResult) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        next.set("detail", result.uuid)
        return next
      },
      { replace: true }
    )
    setTraceDetailId(result.traceId)
    setTraceJobUuid(result.jobUuid ?? jobUuid)
    setTraceRecordUuid(result.uuid)
    setTraceOverviewEvidence(undefined)
  }

  const closeDetail = () => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        next.delete("detail")
        return next
      },
      { replace: true }
    )
  }

  /* 统一抽屉:badcase 列表入口(URL ?detail=) 和概览 trace 入口(state) 共用同一个组件 */
  const effectiveRecordUuid = selectedResultId ?? traceRecordUuid ?? null

  const handleCloseDrawer = () => {
    if (selectedResultId) closeDetail()
    if (traceDetailId) {
      setTraceDetailId(null)
      setTraceJobUuid(undefined)
      setTraceRecordUuid(undefined)
      setTraceOverviewEvidence(undefined)
    }
  }

  return (
    <SpaceAgentGuard>
      <div className="flex min-w-0 flex-col gap-4 overflow-hidden px-4 pt-3 pb-6">
        <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
          <PageBreadcrumb pageTitle={pageTitle} agentProduct={agentProduct} />
          <div className="flex flex-wrap items-center gap-2">
            {traceSource === "signoz" ? (
              <Button variant="outline" size="sm" asChild>
                <Link
                  to={`/trace?${setAgentProductInSearch(new URLSearchParams({ spaceId, source_system: "signoz" }).toString(), agentProduct, { agentUuid })}`}
                >
                  {t("diagnose.quick.open")}
                </Link>
              </Button>
            ) : null}
            <Tabs value={view} onValueChange={setView}>
              <TabsList>
                <TabsTrigger value="overview">
                  {t("diagnose.badcase.viewOverview")}
                </TabsTrigger>
                <TabsTrigger value="list" data-quality-guide="badcase-list">
                  {t("diagnose.badcase.viewList")}
                </TabsTrigger>
              </TabsList>
            </Tabs>
          </div>
        </div>

        {diagnosisDenied ? (
          <p role="alert" className="rounded-lg border p-4 text-sm">
            {t("diagnose.quick.permissionLost")}
          </p>
        ) : null}
        {view !== "list" && !diagnosisDenied && (
          <div
            className="grid w-full grid-cols-1 items-center gap-2 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)_minmax(0,5fr)]"
            data-quality-guide="filters"
          >
            <JobSelector
              spaceId={spaceId}
              agentUuid={agentUuid}
              value={jobUuid}
              onChange={setJobUuid}
              preserveAnchoredValue={!!anchorTaskUuid}
              className="min-w-0"
              refreshTick={refreshTick}
              groupByStatus
              fullWidth
            />
            {diagnosisDelay ? (
              <span className="text-xs whitespace-nowrap text-[var(--color-text-description)] lg:col-start-2">
                {t("diagnose.overview.diagnosisDelay", {
                  offset: formatTraceTimeOffset(diagnosisDelay),
                })}
              </span>
            ) : null}
            {anchorTaskUuid ? (
              <div className="flex flex-wrap items-center justify-end gap-2 lg:col-start-3">
                <span className="inline-flex min-h-8 max-w-full flex-wrap items-center gap-x-2 gap-y-1 rounded-lg bg-[var(--color-bg-warning)] px-3 py-1.5 text-xs text-[var(--color-text-secondary)]">
                  <History className="size-3.5 text-[var(--color-warning)]" />
                  <span>
                    {t("diagnose.overview.historicalBatch", {
                      defaultValue: "正在查看历史诊断批次",
                    })}
                  </span>
                  {historicalTaskFinishedAt ? (
                    <span className="text-[var(--color-text-description)] tabular-nums">
                      {t("diagnose.overview.completedAt", {
                        defaultValue: "{{time}}",
                        time: formatDateTime(historicalTaskFinishedAt),
                      })}
                    </span>
                  ) : null}
                </span>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setAnchorTaskUuid(undefined)}
                >
                  <RotateCcw className="size-4" />
                  {t("diagnose.overview.returnLatest", {
                    defaultValue: "取消",
                  })}
                </Button>
              </div>
            ) : null}
          </div>
        )}

        {diagnosisDenied ? null : view === "list" ? (
          <BadCaseListView
            onSelectBadCase={openDetail}
            selectedResultId={selectedResultId}
            jobUuid={jobUuid}
            onJobChange={setJobUuid}
            taskUuid={anchorTaskUuid}
            timeRange={resolvedRange}
            refreshTick={refreshTick}
          />
        ) : (
          <QualityOverviewView
            onQuickDenied={handleQuickDenied}
            taskUuid={anchorTaskUuid}
            jobUuid={jobUuid}
            timeRange={resolvedRange}
            refreshTick={refreshTick}
            onAnchorTask={handleAnchorTaskUuid}
            forceDetailExpanded={qualityGuideStep === "detail"}
            onResolvedTaskFinishedAt={setHistoricalTaskFinishedAt}
            onOpenTrace={(traceId, traceJob, recordUuid, evidence) => {
              setTraceDetailId(traceId)
              setTraceJobUuid(traceJob)
              setTraceRecordUuid(recordUuid)
              setTraceOverviewEvidence(evidence)
            }}
          />
        )}
      </div>

      {view === "overview" && !anchorTaskUuid && !diagnosisDenied ? (
        <QualityOverviewGuide
          userKey={guideUserKey}
          autoStart={
            !productGuide?.active && !diagnosisDenied && !selectedResultId
          }
          onStepChange={setQualityGuideStep}
        />
      ) : null}

      {/* 统一抽屉:badcase 列表 + 概览 trace 共用 */}
      <TraceTranscriptDrawer
        key={`${user?.user_uuid}:${spaceId}:${effectiveRecordUuid ?? traceDetailId ?? "closed"}`}
        recordUuid={diagnosisDenied ? null : effectiveRecordUuid}
        traceId={diagnosisDenied ? null : traceDetailId}
        jobUuid={traceJobUuid ?? jobUuid}
        centerTime={traceCenterTime}
        agentUuid={agentUuid}
        overviewEvidence={traceOverviewEvidence}
        onClose={handleCloseDrawer}
        onOpenRawTrace={(traceId, traceTimestamp) => {
          setRawTraceId(traceId)
          setRawSpanId(null)
          setRawTraceTimestamp(traceTimestamp ?? null)
        }}
      />

      {traceSource === "signoz" ? (
        <NativeDetailDrawer
          userUuid={user?.user_uuid}
          spaceId={spaceId}
          agentProduct={agentProduct || undefined}
          agentUuid={agentUuid || null}
          sourceSystem={"signoz"}
          focusSpanId={rawSpanId}
          traceId={rawTraceId}
          traceTimeRange={rawTraceTimeRange}
          sessionId={null}
          onClose={() => {
            setRawTraceId(null)
            setRawSpanId(null)
            setRawTraceTimestamp(null)
          }}
          onOpenTrace={(tid) => {
            setRawTraceId(tid)
            setRawTraceTimestamp(null)
          }}
          onOpenSession={() => {}}
        />
      ) : null}
    </SpaceAgentGuard>
  )
}
