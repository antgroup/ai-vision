import { useEffect, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { ExternalLink, Loader2, X } from "lucide-react"

import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { ApiBusinessError } from "@/lib/api"
import { useUser } from "@/contexts/UserContext"
import { useSpace } from "@/contexts/SpaceContext"

import { extractTraceTimestampMs } from "@/agents/traces/sofaTraceTime"
import {
  fetchTraceProjection,
  getRecord,
  submitFeedback,
  toDetail,
} from "../diagnoseApi"
import type {
  RawTraceProjection,
  SemanticTraceProjection,
  TraceProjection,
} from "../diagnoseApi"
import type {
  BadCaseDetail,
  FeedbackPayload,
  OverviewTraceEvidence,
  ReviewStatus,
} from "../types"
import { BadCaseStatusBadge } from "../badcase/BadCaseStatusBadge"
import { diagnosisExecutionMessage } from "../executionMessage"
import { FeedbackBar } from "./FeedbackBar"
import { GraphTrajectoryView } from "./GraphTrajectoryView"
import { StepList } from "./TraceReplayView"
import { extractContentText, stepRole } from "./traceReplayUtils"
import {
  CopyIconButton,
  DetailSection,
  DiagnosisConclusionSection,
  QuestionAnswerSection,
  SnapshotOnlySections,
} from "./TraceDetailSections"

/**
 * 统一 BadCase / 转录抽屉。
 *
 * 两种入口:
 *   A. recordUuid(从 badcase 列表):读取保存的 Record 快照和诊断结果
 *   B. traceId(从概览 trace 列表):直接拉转录;有 recordUuid 时以 Record 快照为准
 *
 * 布局:诊断 banner(可折叠) → 转录正文 → FeedbackBar
 */
interface TraceTranscriptDrawerProps {
  /** 入口 A:从 badcase 列表,传 record UUID(从中推导 trace 参数) */
  recordUuid?: string | null
  /** 入口 B:从概览 trace 列表,直接传 trace 参数 */
  traceId?: string | null
  jobUuid?: string
  centerTime?: string
  agentUuid?: string
  overviewEvidence?: OverviewTraceEvidence
  onClose: () => void
  onOpenRawTrace?: (traceId: string, traceTimestamp?: string) => void
}

const FEEDBACK_VISIBILITY_TIMEOUT_CODE = 5031

function isFeedbackVisibilityTimeout(error: unknown): boolean {
  return (
    error instanceof ApiBusinessError &&
    Number(error.code) === FEEDBACK_VISIBILITY_TIMEOUT_CODE
  )
}

/* ── 内部工具函数 ── */

function deriveCenterTime(traceId: string): string | undefined {
  const ts = extractTraceTimestampMs(traceId)
  if (ts === null) return undefined
  const d = new Date(ts)
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "—"
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (part: number) => String(part).padStart(2, "0")
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

function isSemanticProjection(
  projection: TraceProjection | undefined
): projection is SemanticTraceProjection {
  return projection?.projection_kind === "semantic"
}

function isRawProjection(
  projection: TraceProjection | undefined
): projection is RawTraceProjection {
  return projection?.projection_kind === "raw"
}

/* ── 内部子组件 ── */

/* ── DetailSection / IOSection / StepList(对齐 demo AgentEvalResultDetailPanel) ── */

/* ── 主组件 ── */

export function TraceTranscriptDrawer({
  recordUuid,
  traceId: propsTraceId,
  jobUuid: propsJobUuid,
  centerTime: propsCenterTime,
  agentUuid,
  overviewEvidence,
  onClose,
  onOpenRawTrace,
}: TraceTranscriptDrawerProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { user } = useUser()
  const { currentSpace } = useSpace()
  const spaceId = currentSpace?.id ?? ""

  const effectiveRecordUuid = recordUuid ?? null
  const replayRecordKey = [
    effectiveRecordUuid ?? "",
    propsTraceId ?? "",
    propsJobUuid ?? "",
    propsCenterTime ?? "",
  ].join(":")
  const [selectedDetailView, setSelectedDetailView] = useState<
    "diagnosis" | "graph"
  >("diagnosis")
  const [detailViewKey, setDetailViewKey] = useState(replayRecordKey)
  const transcriptBodyRef = useRef<HTMLDivElement | null>(null)
  const replaySectionRef = useRef<HTMLDivElement | null>(null)
  const outerReplayScrollTopRef = useRef(0)
  const detailView =
    detailViewKey === replayRecordKey ? selectedDetailView : "diagnosis"
  const setDetailView = (view: "diagnosis" | "graph") => {
    if (view === "graph") {
      outerReplayScrollTopRef.current =
        transcriptBodyRef.current?.scrollTop ?? 0
    }
    setDetailViewKey(replayRecordKey)
    setSelectedDetailView(view)
    window.requestAnimationFrame(() => {
      if (view === "graph") {
        replaySectionRef.current?.scrollIntoView({ block: "start" })
      } else if (transcriptBodyRef.current) {
        transcriptBodyRef.current.scrollTop = outerReplayScrollTopRef.current
      }
    })
  }
  const [awaitingReview, setAwaitingReview] = useState<ReviewStatus | null>(
    null
  )
  const feedbackPending = awaitingReview !== null
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])
  const open = !!effectiveRecordUuid || !!propsTraceId

  // ── 诊断 record 拉取(入口 A 必须;入口 B 有 recordUuid 时也拉) ──
  const recordQuery = useQuery({
    queryKey: [
      "diagnosis-record",
      spaceId,
      effectiveRecordUuid,
      agentUuid,
      user?.user_uuid,
    ],
    queryFn: () => getRecord(spaceId, effectiveRecordUuid!),
    enabled: open && !!effectiveRecordUuid && !!spaceId && !!user?.user_uuid,
    retry: false,
    refetchInterval: (q) =>
      feedbackPending &&
      !q.state.error &&
      q.state.data?.review_status !== awaitingReview
        ? 1000
        : false,
    staleTime: 60_000,
  })
  // Record already contains the query, answer, reason and metric results needed
  // for the first paint. Diagnoser metadata is an optional background enrichment.
  const detail: BadCaseDetail | null =
    recordQuery.data && !recordQuery.isError
      ? toDetail(recordQuery.data, [])
      : null
  // The overview only has aggregate diagnoser statistics. Once a record is
  // available, its metrics are the sole source for verdicts and evidence.
  const evidenceHitMetrics =
    !detail && overviewEvidence?.hitMetrics?.length
      ? overviewEvidence.hitMetrics
      : null
  const overviewMetricLabels = useMemo(() => {
    const labels = new Map<string, string>()
    for (const metric of overviewEvidence?.hitMetrics ?? []) {
      if (metric.name && metric.desc) labels.set(metric.name, metric.desc)
    }
    return labels
  }, [overviewEvidence?.hitMetrics])
  const evidenceOverview = detail?.summary || overviewEvidence?.summary?.trim()
  const diagnosisInput = detail?.evidence.renderedPrompt
  const diagnosisMetrics = (() => {
    if (detail) {
      const perMetric = detail.evidence.perMetric
      const hitMetricKeys = new Set(detail.evidence.hitMetrics)
      return Object.entries(perMetric ?? {})
        .filter(([key, metric]) =>
          hitMetricKeys.size > 0
            ? hitMetricKeys.has(key)
            : metric.verdict === "bad" || metric.verdict === "failed"
        )
        .map(([key, metric]) => ({
          id: key,
          name:
            detail.evidence.metricLabels?.[key] ??
            overviewMetricLabels.get(key) ??
            key,
          verdict: metric.verdict,
          reason: metric.reason,
        }))
    }
    return (evidenceHitMetrics ?? []).map((metric) => ({
      id: `${metric.diagnoser_uuid}:${metric.name}`,
      name: metric.desc,
    }))
  })()
  const drawerTitle = t("diagnose.detail.title", {
    defaultValue: "诊断详情",
  })

  // ── 推导 trace 参数:入口 A 从 record 推导,入口 B 直接用 props ──
  const recordDTO = recordQuery.isError ? undefined : recordQuery.data
  const derivedTraceId = recordDTO?.target_id ?? null
  const derivedJobUuid = recordDTO?.job_uuid ?? undefined
  const derivedCenterTime = useMemo(() => {
    if (derivedTraceId) {
      const fromId = deriveCenterTime(derivedTraceId)
      if (fromId) return fromId
    }
    const fallback = recordDTO?.target_created_at || recordDTO?.diagnosed_at
    if (fallback) {
      const d = new Date(fallback)
      if (!isNaN(d.getTime())) {
        const pad = (n: number) => String(n).padStart(2, "0")
        return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
      }
    }
    return undefined
  }, [derivedTraceId, recordDTO])

  const finalTraceId = propsTraceId ?? derivedTraceId
  const finalJobUuid = propsJobUuid ?? derivedJobUuid
  const finalCenterTime = propsCenterTime ?? derivedCenterTime
  // ── 转录拉取 ──
  const traceQuery = useQuery({
    queryKey: [
      "trace-projection",
      spaceId,
      user?.user_uuid,
      finalTraceId,
      finalJobUuid,
      finalCenterTime,
      agentUuid,
    ],
    queryFn: () =>
      fetchTraceProjection(spaceId, finalTraceId!, {
        job_uuid: finalJobUuid!,
        center_time: finalCenterTime!,
      }),
    enabled:
      open &&
      !effectiveRecordUuid &&
      !!spaceId &&
      !!user?.user_uuid &&
      !recordQuery.isError &&
      !!finalTraceId &&
      !!finalJobUuid &&
      !!finalCenterTime &&
      !!agentUuid,
    staleTime: 300_000,
    retry: false,
  })

  // ── Feedback ──
  const feedbackMutation = useMutation({
    mutationFn: (payload: FeedbackPayload) =>
      submitFeedback(spaceId, effectiveRecordUuid!, payload),
    onSuccess: (result, payload) => {
      if (!alive.current) return
      queryClient.invalidateQueries({ queryKey: ["diagnosis-records"] })
      if (result.visibilityPending) {
        setAwaitingReview(payload.review_status)
        queryClient.invalidateQueries({
          queryKey: ["diagnosis-record", spaceId, effectiveRecordUuid],
        })
        toast.info(t("diagnose.badcase.feedbackSyncing"))
        return
      }
      queryClient.setQueryData(
        [
          "diagnosis-record",
          spaceId,
          effectiveRecordUuid,
          agentUuid,
          user?.user_uuid,
        ],
        result.record
      )
      setAwaitingReview(null)
      toast.success(t("diagnose.badcase.feedbackSuccess"))
      queryClient.invalidateQueries({ queryKey: ["quick-records"] })
    },
    onError: (err, payload) => {
      if (!alive.current) return
      if (isFeedbackVisibilityTimeout(err)) {
        setAwaitingReview(payload.review_status)
        void queryClient.invalidateQueries({
          queryKey: ["diagnosis-record", spaceId, effectiveRecordUuid],
        })
        toast.info(t("diagnose.badcase.feedbackSyncing"))
        return
      }
      const status =
        err instanceof ApiBusinessError
          ? err.statusCode
          : (err as { response?: { status?: number } })?.response?.status
      if (status === 409) {
        toast.error(
          t("diagnose.badcase.feedbackAlreadySubmitted", "该记录已被反馈")
        )
        queryClient.invalidateQueries({
          queryKey: [
            "diagnosis-record",
            spaceId,
            effectiveRecordUuid,
            agentUuid,
            user?.user_uuid,
          ],
        })
      } else {
        toast.error(
          t("diagnose.badcase.feedbackFailed", {
            defaultValue: "反馈提交失败，请稍后重试",
          })
        )
      }
    },
  })
  const canFeedback =
    recordQuery.data?.can_feedback === true &&
    !recordQuery.isError &&
    !feedbackPending &&
    recordQuery.data.review_status !== "confirmed" &&
    recordQuery.data.review_status !== "falsePositive"
  const handleFeedback = (
    reviewStatus: Exclude<ReviewStatus, "pending">,
    remark: string
  ) => {
    if (!canFeedback) return
    feedbackMutation.mutate({
      review_status: reviewStatus,
      feedback: { remark },
    })
  }

  const permissionLost = [
    recordQuery.error,
    effectiveRecordUuid ? null : traceQuery.error,
    feedbackMutation.error,
  ].some(
    (error) => error instanceof ApiBusinessError && error.statusCode === 403
  )
  /* eslint-disable react-hooks/set-state-in-effect -- Read back feedback visibility and clear denied cached body. */
  useEffect(() => {
    if (permissionLost) {
      setAwaitingReview(null)
      queryClient.removeQueries({
        queryKey: [
          "diagnosis-record",
          spaceId,
          effectiveRecordUuid,
          agentUuid,
          user?.user_uuid,
        ],
      })
      queryClient.removeQueries({
        queryKey: ["trace-projection", spaceId, user?.user_uuid],
      })
      onClose()
    } else if (
      awaitingReview &&
      recordQuery.data?.review_status === awaitingReview &&
      !recordQuery.isError
    ) {
      setAwaitingReview(null)
      toast.success(t("diagnose.badcase.feedbackSuccess"))
      void queryClient.invalidateQueries({ queryKey: ["quick-records"] })
    }
  }, [
    permissionLost,
    awaitingReview,
    recordQuery.data?.review_status,
    recordQuery.isError,
    queryClient,
    spaceId,
    effectiveRecordUuid,
    agentUuid,
    user?.user_uuid,
    onClose,
    t,
  ])
  /* eslint-enable react-hooks/set-state-in-effect */

  // Record snapshots retain the diagnosis-time content and may be server-masked.
  // A live or cached original Trace must never replace them, including while the
  // Record is loading, absent, or denied. Live replay belongs to trace-only entry.
  const projection =
    effectiveRecordUuid || recordQuery.isError || traceQuery.isError
      ? undefined
      : traceQuery.data
  const data = isSemanticProjection(projection) ? projection : null
  const rawProjection = isRawProjection(projection) ? projection : null
  const deviationsBySpan = useMemo(() => {
    const labelsBySpan = new Map<string, string[]>()
    const fallbackLabelByMetric: Record<string, string> = {
      tool_success_rate: "工具失败",
      stuck_loop_health: "工具循环",
      secret_leakage_block_rate: "疑似敏感信息",
      high_risk_command_execution: "高危命令",
      rewrite_overlap_rate: "重复改写",
    }
    const metrics = recordDTO?.metrics ?? []
    for (const metric of metrics) {
      if (metric.verdict !== "bad" && metric.verdict !== "failed") continue
      const badSpans = metric.evidence?.bad_spans
      if (!Array.isArray(badSpans)) continue
      for (const item of badSpans) {
        if (!item || typeof item !== "object") continue
        const badSpan = item as Record<string, unknown>
        const spanId = badSpan.span_id
        if (typeof spanId !== "string" || !spanId) continue
        const findings = Array.isArray(badSpan.findings) ? badSpan.findings : []
        const labels = findings.flatMap((finding) => {
          if (!finding || typeof finding !== "object") return []
          const category = (finding as Record<string, unknown>).category
          return typeof category === "string" && category ? [category] : []
        })
        const fallback =
          fallbackLabelByMetric[metric.name] ||
          metric.reason.trim() ||
          "问题来源"
        const values = labelsBySpan.get(spanId) ?? []
        for (const label of labels.length ? labels : [fallback]) {
          if (!values.includes(label)) values.push(label)
        }
        labelsBySpan.set(spanId, values)
      }
    }
    return labelsBySpan
  }, [recordDTO])
  const steps = useMemo(
    () =>
      (data?.steps ?? []).map((step) => {
        const descriptions = step.span_id
          ? deviationsBySpan.get(step.span_id)
          : undefined
        return descriptions
          ? {
              ...step,
              is_deviation: true,
              deviation_labels: descriptions,
            }
          : step
      }),
    [data?.steps, deviationsBySpan]
  )
  const trajectory = data?.graph?.trajectory ?? null
  const graphSteps = trajectory?.graph_steps
  const retrievedDocs = data?.retrieval?.items
  const historyTurns = data?.history?.turns
  const answerStep = steps.find((s) => stepRole(s) === "answer")
  const finalAgentOutput =
    extractContentText(data?.agent_output) ??
    extractContentText(answerStep?.output) ??
    null
  const canViewTrajectory = Boolean(trajectory?.graph_steps.length)

  // Record entry waits only for its saved snapshot; trace entry waits for replay.
  const recordLoading = !!effectiveRecordUuid && recordQuery.isLoading
  const traceLoading = !effectiveRecordUuid && traceQuery.isLoading
  const isFeedbackSubmitting = feedbackMutation.isPending

  if (permissionLost) return null

  return (
    <Sheet
      open={open}
      onOpenChange={(n) => {
        if (!n) {
          if (isFeedbackSubmitting) return
          setDetailView("diagnosis")
          onClose()
        }
      }}
    >
      <SheetContent
        side="right"
        showCloseButton={false}
        aria-busy={isFeedbackSubmitting}
        onClickCapture={(event) => {
          if (!isFeedbackSubmitting) return
          event.preventDefault()
          event.stopPropagation()
        }}
        onKeyDownCapture={(event) => {
          if (!isFeedbackSubmitting) return
          event.preventDefault()
          event.stopPropagation()
        }}
        className="flex h-dvh !w-[94vw] flex-col gap-0 overflow-hidden p-0 sm:!w-[88vw] sm:!max-w-none lg:!w-[65vw] lg:!max-w-[1200px]"
      >
        {/* Header */}
        <div className="shrink-0 border-b p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <SheetTitle className="truncate text-base font-medium text-foreground">
                {drawerTitle}
              </SheetTitle>
              {finalTraceId && (
                <div className="mt-1 inline-flex max-w-full min-w-0 items-center gap-1 text-xs text-muted-foreground sm:max-w-80">
                  <code className="min-w-0 truncate font-mono">
                    {finalTraceId}
                  </code>
                  <CopyIconButton text={finalTraceId} />
                </div>
              )}
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                size="icon"
                aria-label={t("common.close")}
                title={t("common.close")}
                disabled={isFeedbackSubmitting}
                onClick={onClose}
              >
                <X className="size-4" />
              </Button>
            </div>
          </div>
        </div>

        <div className="flex shrink-0 items-center border-b bg-muted/20 px-4 py-2">
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-1 text-xs">
            {detail ? (
              <BadCaseStatusBadge status={detail.reviewStatus} />
            ) : (
              <span className="inline-block h-5 w-14 animate-pulse rounded-full bg-muted" />
            )}
            {detail?.diagnoserName && (
              <>
                <span className="h-3 w-px bg-border" />
                <span
                  className="max-w-48 truncate text-foreground"
                  title={detail.diagnoserName}
                >
                  {detail.diagnoserName}
                </span>
              </>
            )}
            <span className="h-3 w-px bg-border" />
            <span className="text-muted-foreground">
              {t("diagnose.detail.diagnosisTime", {
                defaultValue: "诊断时间",
              })}
            </span>
            <span className="text-foreground tabular-nums">
              {formatTimestamp(detail?.detectedAt)}
            </span>
            <span className="h-3 w-px bg-border" />
            <span className="text-muted-foreground">
              {t("diagnose.detail.traceTime", {
                defaultValue: "轨迹时间",
              })}
            </span>
            <span className="text-foreground tabular-nums">
              {formatTimestamp(detail?.traceTimestamp)}
            </span>
          </div>
          <div className="ml-3 flex shrink-0 items-center gap-2">
            {onOpenRawTrace && finalTraceId && (
              <Button
                variant="outline"
                size="sm"
                className="h-7 gap-1 px-2 text-xs font-normal"
                onClick={() =>
                  onOpenRawTrace(
                    finalTraceId,
                    recordDTO?.target_created_at || detail?.traceTimestamp
                  )
                }
              >
                <ExternalLink className="size-3.5" />
                {t("diagnose.quick.viewTrace")}
              </Button>
            )}
          </div>
        </div>

        {/* Transcript Body */}
        <div
          ref={transcriptBodyRef}
          className="min-h-0 flex-1 overflow-y-auto p-4"
        >
          {data ? (
            <div className="flex flex-col gap-4">
              {recordDTO?.execution.status === "failed" && (
                <div className="rounded-md border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
                  {diagnosisExecutionMessage(recordDTO.execution, t)}
                  {recordDTO.execution.code ? (
                    <code className="ml-2 text-xs">
                      {recordDTO.execution.code}
                    </code>
                  ) : null}
                </div>
              )}
              {/* 1. 诊断依据 */}
              {(detail || evidenceHitMetrics) && (
                <DiagnosisConclusionSection
                  summary={evidenceOverview}
                  metrics={diagnosisMetrics}
                  diagnosisInput={diagnosisInput}
                />
              )}

              {/* 2. 用户问题与 Agent 回答 */}
              <QuestionAnswerSection
                userQuestion={
                  data.user_query || detail?.businessImpact.userQuery || null
                }
                agentAnswer={finalAgentOutput}
              />

              {/* 3. 关键步骤回放 */}
              <div ref={replaySectionRef}>
                <DetailSection
                  title={t("diagnose.detail.keySteps", {
                    defaultValue: "关键步骤回放",
                  })}
                  loading={traceLoading}
                >
                  <div className="pt-1">
                    <div
                      className={cn(
                        detailView === "diagnosis" ? "block" : "hidden"
                      )}
                    >
                      <StepList
                        key={replayRecordKey}
                        steps={steps}
                        graphSteps={graphSteps}
                        retrieved={retrievedDocs}
                        history={historyTurns}
                        onOpenGraphReplay={
                          canViewTrajectory
                            ? () => setDetailView("graph")
                            : undefined
                        }
                      />
                    </div>
                    {trajectory && (
                      <div
                        className={cn(
                          detailView === "graph" ? "block" : "hidden"
                        )}
                      >
                        <GraphTrajectoryView
                          key={replayRecordKey}
                          trajectory={trajectory}
                          active={detailView === "graph"}
                          onBack={() => setDetailView("diagnosis")}
                        />
                      </div>
                    )}
                  </div>
                </DetailSection>
              </div>
            </div>
          ) : detail ? (
            <div className="flex flex-col gap-4">
              {recordDTO?.execution.status === "failed" && (
                <div className="rounded-md border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
                  {diagnosisExecutionMessage(recordDTO.execution, t)}
                  {recordDTO.execution.code ? (
                    <code className="ml-2 text-xs">
                      {recordDTO.execution.code}
                    </code>
                  ) : null}
                </div>
              )}
              <SnapshotOnlySections
                detail={detail}
                evidenceOverview={evidenceOverview ?? undefined}
                diagnosisMetrics={diagnosisMetrics}
                diagnosisInput={diagnosisInput ?? undefined}
              />
              {rawProjection && (
                <DetailSection
                  title={t("diagnose.detail.keySteps", {
                    defaultValue: "关键步骤回放",
                  })}
                >
                  <p className="py-2 text-sm text-muted-foreground">
                    当前轨迹未生成可回放的语义步骤。
                  </p>
                </DetailSection>
              )}
              {traceLoading ? (
                <DetailSection
                  title={t("diagnose.detail.keySteps", {
                    defaultValue: "关键步骤回放",
                  })}
                  loading
                >
                  <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
                    <Loader2 className="size-4 animate-spin" />
                    {t("common.loading")}
                  </div>
                </DetailSection>
              ) : !effectiveRecordUuid && traceQuery.isError ? (
                <DetailSection
                  title={t("diagnose.detail.keySteps", {
                    defaultValue: "关键步骤回放",
                  })}
                >
                  <p className="py-2 text-sm text-muted-foreground">
                    {t("diagnose.transcript.loadError")}
                  </p>
                </DetailSection>
              ) : null}
            </div>
          ) : recordQuery.isError ? (
            <div className="flex flex-col items-center justify-center gap-3 py-16 text-sm text-muted-foreground">
              <p>诊断详情加载失败</p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => void recordQuery.refetch()}
              >
                {t("common.retry")}
              </Button>
            </div>
          ) : !effectiveRecordUuid && traceQuery.isError ? (
            <div className="flex items-center justify-center py-16 text-sm text-muted-foreground">
              {t("diagnose.transcript.loadError")}
            </div>
          ) : recordLoading ? (
            <div className="flex items-center justify-center py-16 text-sm text-muted-foreground">
              <Loader2 className="mr-2 size-4 animate-spin" />
              {t("common.loading")}
            </div>
          ) : null}
        </div>

        {feedbackPending ? (
          <p
            className="border-t px-6 py-2 text-xs text-muted-foreground"
            role="status"
          >
            {t("diagnose.badcase.feedbackSyncing")}
          </p>
        ) : null}
        {/* Feedback Bar */}
        {effectiveRecordUuid &&
          (detail ? (
            <FeedbackBar
              status={detail.reviewStatus}
              feedback={recordQuery.data?.feedback}
              onSubmit={handleFeedback}
              canSubmit={canFeedback && !feedbackPending}
              onClose={onClose}
              isSubmitting={isFeedbackSubmitting}
              reviewedBy={recordQuery.data?.reviewed_by}
              reviewedAt={recordQuery.data?.reviewed_at}
            />
          ) : recordLoading ? (
            <div className="shrink-0 border-t bg-card px-6 py-3">
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2 className="size-3.5 animate-spin" />
                {t("common.loading")}
              </div>
            </div>
          ) : null)}

        {isFeedbackSubmitting ? (
          <div
            className="absolute inset-0 z-50 flex cursor-wait items-center justify-center bg-background/75 backdrop-blur-[1px]"
            role="status"
            aria-live="polite"
            aria-busy="true"
          >
            <div className="flex items-center gap-2 rounded-[var(--ai-radius-xl)] border bg-card px-4 py-3 text-sm text-foreground">
              <Loader2 className="size-4 animate-spin text-primary" />
              {t("diagnose.badcase.feedbackSubmitting")}
            </div>
          </div>
        ) : null}
      </SheetContent>
    </Sheet>
  )
}
