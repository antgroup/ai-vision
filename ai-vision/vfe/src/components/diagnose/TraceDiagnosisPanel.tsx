import { useEffect, useRef, useState, type ComponentProps } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import { Link } from "react-router-dom"
import { ExternalLink, RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { ApiBusinessError, ApiAuthRedirectError } from "@/lib/api"
import { getTask } from "./jobApi"
import { listRecords, getRecord } from "./recordApi"
import { createDiagnosisRun, getDiagnosisRunOptions } from "./diagnosisRunApi"
import { diagnosisRunLabels } from "./diagnosisRunLabels"
import {
  QuickRunIntent,
  expectedQuickRecords,
  sourceSpanId,
} from "./quickDiagnosis"
import { TraceDiagnosisResult } from "./TraceDiagnosisResult"
import type {
  DiagnosisRecordDTO,
  QuickRunResponse,
  DiagnosisRunSelection,
} from "./types"

function errorKey(error: unknown): string {
  if (
    error instanceof ApiBusinessError &&
    error.data &&
    typeof error.data === "object" &&
    "error_key" in error.data
  )
    return String(error.data.error_key)
  return error instanceof Error &&
    /^(QUICK_|DIAGNOSIS_RUN_)/.test(error.message)
    ? error.message
    : "QUICK_UPSTREAM_UNAVAILABLE"
}
function canRetry(error: unknown) {
  if (error instanceof ApiBusinessError) {
    if (
      error.data &&
      typeof error.data === "object" &&
      "retryable" in error.data &&
      typeof error.data.retryable === "boolean"
    )
      return error.data.retryable
    // A generic server failure may have happened after accepting the request.
    return (error.statusCode ?? 0) >= 500
  }
  return !(error instanceof Error && error.message === "QUICK_INPUT_INVALID")
}
function bindingUnavailable(error: unknown) {
  return (
    error instanceof ApiBusinessError &&
    error.statusCode === 404 &&
    errorKey(error) === "QUICK_AGENT_UNAVAILABLE"
  )
}

export type QuickInvalidatedScope = {
  taskUuids: string[]
  jobUuids: string[]
  recordUuids: string[]
  traceIds: string[]
}

function permissionDenied(error: unknown) {
  return (
    error instanceof ApiAuthRedirectError ||
    (error instanceof ApiBusinessError &&
      [401, 403].includes(error.statusCode ?? 0))
  )
}

export function TraceDiagnosisPanel(
  props: ComponentProps<typeof TraceDiagnosisPanelContent>
) {
  // Scope changes also invalidate in-flight intent callbacks before they can
  // publish another identity's or selection's submission into this panel.
  const scope = JSON.stringify([
    props.userUuid,
    props.spaceId,
    props.agentUuid,
    props.agentProduct,
    props.traceIds,
  ])
  return <TraceDiagnosisPanelContent key={scope} {...props} />
}

function TraceDiagnosisPanelContent({
  spaceId,
  agentUuid,
  agentProduct,
  userUuid,
  taskUuid,
  traceIds,
  onAccepted,
  onOpenRecord,
  onDenied,
  authorizationRevoked = false,
  embedded = false,
}: {
  spaceId: string
  agentUuid: string
  agentProduct: string
  userUuid: string
  taskUuid?: string
  traceIds: readonly string[]
  onAccepted: (
    response: Extract<QuickRunResponse, { state: "accepted" }>
  ) => void
  onOpenRecord: (
    record: DiagnosisRecordDTO,
    spanId?: string,
    target?: "trace",
    completedRuleSource?: boolean
  ) => void
  onDenied: (scope: QuickInvalidatedScope) => void
  authorizationRevoked?: boolean
  embedded?: boolean
}) {
  const { t } = useTranslation()
  const label = (key: string) => t(`diagnose.quick.${key}`)
  const queryClient = useQueryClient()
  const identity = `${userUuid}:${spaceId}:${agentUuid}`
  const [intent] = useState(() => new QuickRunIntent(identity))
  const [diagnoserUuid, setDiagnoserUuid] = useState("")
  const [busy, setBusy] = useState(false)
  const [provisioning, setProvisioning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [retryable, setRetryable] = useState(false)
  const [revoked, setRevoked] = useState(false)
  const [verdict, setVerdict] = useState("all")
  const [recordPage, setRecordPage] = useState(0)
  const notified = useRef(false)
  const [accepted, setAccepted] = useState<Extract<
    QuickRunResponse,
    { state: "accepted" }
  > | null>(null)
  const submitting = useRef(false)
  const alive = useRef(true)
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      intent.invalidate()
      if (retryTimer.current) clearTimeout(retryTimer.current)
    }
  }, [intent])
  const optionsQuery = useQuery({
    queryKey: ["quick-options", identity],
    queryFn: () => getDiagnosisRunOptions(spaceId, agentUuid),
    enabled: !!spaceId && !!agentUuid && !!userUuid && !revoked,
    staleTime: 0,
    retry: false,
    refetchOnWindowFocus: true,
  })
  const options = optionsQuery.data
  const taskQuery = useQuery({
    queryKey: ["quick-task", identity, taskUuid],
    queryFn: () => getTask(spaceId, taskUuid!),
    enabled: !!taskUuid && !revoked && !!userUuid,
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
  const blocked =
    authorizationRevoked ||
    revoked ||
    permissionDenied(optionsQuery.error) ||
    permissionDenied(taskQuery.error) ||
    bindingUnavailable(optionsQuery.error) ||
    bindingUnavailable(taskQuery.error)
  const canExecute =
    options?.can_execute === true && !optionsQuery.isError && !blocked
  const recordsKey = ["quick-records", identity, taskUuid, verdict, recordPage]
  const currentRecordTask =
    taskQuery.isSuccess &&
    task?.uuid === taskUuid &&
    task?.quick_run?.agent_uuid === agentUuid
  const recordsTerminal =
    currentRecordTask &&
    ["succeeded", "failed", "canceled"].includes(task.status)
  const expectedRecords = recordsTerminal
    ? expectedQuickRecords(task, verdict)
    : null
  const recordsComplete = (data?: {
    total: number
    items: DiagnosisRecordDTO[]
  }) =>
    expectedRecords !== null &&
    data?.total === expectedRecords &&
    data.items.length ===
      Math.min(10, Math.max(0, expectedRecords - recordPage * 10))
  const recordsQuery = useQuery({
    queryKey: recordsKey,
    queryFn: async () => {
      const result = await listRecords(spaceId, {
        job_uuid: task!.job_uuid,
        task_uuid: taskUuid,
        verdict:
          verdict === "all"
            ? undefined
            : (verdict as "bad" | "good" | "failed"),
        limit: 10,
        offset: recordPage * 10,
      })
      return {
        total: result.total,
        terminalReads: recordsTerminal
          ? (queryClient.getQueryData<{ terminalReads?: number }>(recordsKey)
              ?.terminalReads ?? 0) + 1
          : 0,
        items: await Promise.all(
          result.items.map((record) => getRecord(spaceId, record.uuid))
        ),
      }
    },
    enabled: currentRecordTask && !blocked,
    retry: false,
    refetchInterval: (q) =>
      q.state.error
        ? false
        : (task &&
              ["pending", "dispatched", "running"].includes(task.status)) ||
            (recordsTerminal &&
              !recordsComplete(q.state.data) &&
              (q.state.data?.terminalReads ?? 0) < 10)
          ? 2000
          : false,
  })
  const recordsSettling = recordsTerminal && !recordsComplete(recordsQuery.data)
  const recordsUnconfirmed =
    recordsSettling && (recordsQuery.data?.terminalReads ?? 0) >= 10
  const recordsCountUnknown =
    recordsTerminal && expectedRecords === null && recordsQuery.isSuccess
  const hasPrivateQuick =
    !!intent.body || !!accepted || task?.quick_run?.agent_uuid === agentUuid
  const denied =
    authorizationRevoked ||
    revoked ||
    [optionsQuery.error, taskQuery.error, recordsQuery.error].some(
      bindingUnavailable
    ) ||
    (hasPrivateQuick &&
      (permissionDenied(optionsQuery.error) ||
        permissionDenied(taskQuery.error))) ||
    permissionDenied(recordsQuery.error)
  // Optional EXECUTE denial does not revoke protected business READ history.
  useEffect(() => {
    if (!denied || notified.current) return
    notified.current = true
    const scope: QuickInvalidatedScope = {
      taskUuids: [],
      jobUuids: [],
      recordUuids: [],
      traceIds: [...(intent.body?.trace_ids ?? [])],
    }
    for (const query of queryClient
      .getQueryCache()
      .findAll({ queryKey: ["quick-task", identity] })) {
      const cached = query.state.data as typeof task
      if (cached?.quick_run?.agent_uuid !== agentUuid) continue
      scope.taskUuids.push(cached.uuid)
      scope.jobUuids.push(cached.job_uuid)
      scope.traceIds.push(...cached.quick_run.trace_ids)
    }
    if (accepted) {
      scope.taskUuids.push(accepted.task_uuid)
      scope.jobUuids.push(accepted.job_uuid)
    }
    for (const query of queryClient
      .getQueryCache()
      .findAll({ queryKey: ["quick-records", identity] })) {
      const cached = query.state.data as
        | { items?: DiagnosisRecordDTO[] }
        | undefined
      scope.recordUuids.push(
        ...(cached?.items?.map((record) => record.uuid) ?? [])
      )
    }
    setRevoked(true)
    intent.invalidate()
    if (retryTimer.current) clearTimeout(retryTimer.current)
    const privateCache = (query: { queryKey: readonly unknown[] }) =>
      query.queryKey[1] === identity &&
      String(query.queryKey[0]).startsWith("quick-")
    void queryClient.cancelQueries({ predicate: privateCache })
    queryClient.removeQueries({ predicate: privateCache })
    onDenied(scope)
  }, [denied, identity, agentUuid, intent, onDenied, queryClient, accepted])
  const selectedDiagnoser = options?.diagnosers.find(
    (item) => item.uuid === diagnoserUuid
  )
  const selectedLabels = selectedDiagnoser
    ? diagnosisRunLabels(selectedDiagnoser, t)
    : null
  const selection: DiagnosisRunSelection = {
    trace_ids: [...traceIds],
    diagnoser_uuid: diagnoserUuid,
  }
  const configureParams = new URLSearchParams({
    spaceId,
    agent: agentProduct,
    agent_uuid: agentUuid,
  })
  if (diagnoserUuid) configureParams.set("diagnoser", diagnoserUuid)
  const configurationUrl = `/diagnosers?${configureParams}`
  const failureLabel = (key: string | null | undefined) =>
    t(`diagnose.quick.errors.${key ?? "DIAGNOSIS_RUN_DIAGNOSER_UNAVAILABLE"}`, {
      defaultValue: t("diagnose.run.unavailable"),
    })
  const change = (action: () => void) => {
    intent.invalidate()
    if (retryTimer.current) clearTimeout(retryTimer.current)
    setProvisioning(false)
    setError(null)
    setRetryable(false)
    action()
  }
  const validSelection =
    traceIds.length > 0 &&
    traceIds.length <= (options?.limits.max_traces ?? 10) &&
    traceIds.every((id) => id.trim().length > 0) &&
    new Set(traceIds).size === traceIds.length
  const submitted = intent.body
  const completedRuleRecords =
    !busy &&
    !provisioning &&
    !error &&
    accepted &&
    submitted &&
    selectedDiagnoser?.mode === "rule" &&
    submitted.diagnoser_uuid === selectedDiagnoser.uuid &&
    submitted.request_id === accepted.request_id &&
    accepted.task_uuid === taskUuid &&
    currentRecordTask &&
    task.status === "succeeded" &&
    task.job_uuid === accepted.job_uuid &&
    task.quick_run?.request_id === accepted.request_id &&
    task.quick_run.trace_ids.length === submitted.trace_ids.length &&
    task.quick_run.trace_ids.every((id) => submitted.trace_ids.includes(id)) &&
    recordsQuery.isSuccess &&
    recordsComplete(recordsQuery.data) &&
    recordsQuery.data.items.every(
      (record) =>
        record.task_uuid === accepted.task_uuid &&
        record.job_uuid === accepted.job_uuid &&
        record.diagnoser_uuid === submitted.diagnoser_uuid &&
        submitted.trace_ids.includes(record.target_id)
    )
      ? recordsQuery.data.items.filter(
          (record) => record.execution.status === "succeeded"
        )
      : []
  const submit = async (fresh = false) => {
    if (
      submitting.current ||
      !canExecute ||
      !validSelection ||
      ((fresh || !intent.body) && !selectedDiagnoser?.selectable)
    )
      return
    submitting.current = true
    if (fresh) intent.invalidate()
    setBusy(true)
    setError(null)
    try {
      const transport = (body: Parameters<typeof createDiagnosisRun>[2]) =>
        createDiagnosisRun(spaceId, agentUuid, body)
      // Refresh never changes an unresolved intent. Only an explicit selection
      // change or a new run after acceptance creates a new request identity.
      const response = await (intent.body
        ? intent.replay(transport)
        : intent.submit(selection, transport))
      if (!alive.current || !response) return
      if (response.state === "provisioning") {
        setProvisioning(true)
        retryTimer.current = setTimeout(() => {
          if (alive.current) void submit()
        }, response.retry_after_seconds * 1000)
      } else {
        setProvisioning(false)
        setAccepted(response)
        setVerdict("all")
        setRecordPage(0)
        onAccepted(response)
        void queryClient.invalidateQueries({ queryKey: ["diagnose-jobs"] })
        void queryClient.invalidateQueries({
          queryKey: ["quick-task", identity],
        })
      }
    } catch (failure) {
      if (!alive.current) return
      const key = errorKey(failure)
      setRetryable(canRetry(failure))
      setError(
        !intent.body &&
          key !== "QUICK_INPUT_INVALID" &&
          key !== "DIAGNOSIS_RUN_REQUEST_ID_UNAVAILABLE"
          ? "DIAGNOSIS_RUN_NOT_SUBMITTED"
          : key
      )
      setProvisioning(false)
      if (permissionDenied(failure) || bindingUnavailable(failure))
        setRevoked(true)
    } finally {
      submitting.current = false
      if (alive.current) setBusy(false)
    }
  }
  const recordToolbar = (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <label className="flex items-center gap-2">
        <span className="sr-only">{label("recordFilter")}</span>
        <select
          className="h-8 max-w-full rounded-md border border-input bg-background px-2 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          value={verdict}
          onChange={(event) => {
            setVerdict(event.target.value)
            setRecordPage(0)
          }}
        >
          <option value="all">{label("allRecords")}</option>
          <option value="bad">{label("verdict.bad")}</option>
          <option value="good">{label("verdict.good")}</option>
          <option value="failed">{label("executionFailed")}</option>
        </select>
      </label>
      {verdict !== "all" ? (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            setVerdict("all")
            setRecordPage(0)
          }}
        >
          {label("clearFilter")}
        </Button>
      ) : null}
    </div>
  )
  if (denied)
    return (
      <section
        data-product-guide="diagnosis"
        data-guide-state="denied"
        className="rounded-lg border p-4 text-sm"
      >
        {label("permissionLost")}
      </section>
    )
  return (
    <section
      className={
        embedded ? "space-y-3" : "space-y-3 rounded-lg border bg-card p-4"
      }
      data-product-guide="diagnosis"
      data-guide-mode={selectedDiagnoser?.mode}
      data-guide-trace={traceIds.length === 1 ? traceIds[0] : undefined}
      data-guide-rule={selectedDiagnoser?.uuid}
      data-guide-evidence={
        completedRuleRecords.map(sourceSpanId).find(Boolean) ?? undefined
      }
      data-guide-state={
        optionsQuery.isLoading
          ? "pending"
          : blocked || !canExecute
            ? "denied"
            : completedRuleRecords.length > 0
              ? "succeeded"
              : error ||
                  taskQuery.isError ||
                  recordsQuery.isError ||
                  (recordsTerminal &&
                    recordsComplete(recordsQuery.data) &&
                    !recordsQuery.data?.items.some(
                      (record) => record.execution.status === "succeeded"
                    )) ||
                  task?.status === "failed" ||
                  task?.status === "canceled"
                ? "failed"
                : "pending"
      }
      aria-label={label("title")}
    >
      {!embedded ? (
        <h2 className="text-base font-medium">{label("title")}</h2>
      ) : null}
      {optionsQuery.isLoading ? (
        <p>{t("common.loading")}</p>
      ) : optionsQuery.error ? (
        <p className="text-sm text-[var(--color-error)]">
          {t(`diagnose.quick.errors.${errorKey(optionsQuery.error)}`)}
        </p>
      ) : !canExecute ? (
        <p className="text-sm text-muted-foreground">
          {t(
            `diagnose.quick.errors.${options?.denial_reason ?? "QUICK_PERMISSION_DENIED"}`
          )}
        </p>
      ) : (
        <>
          <div className="space-y-2">
            <div className="flex flex-wrap items-end gap-2">
              <label className="grid min-w-0 flex-1 basis-72 gap-1 text-xs font-medium">
                {t("diagnose.run.diagnoser")}
                <select
                  className="h-9 w-full min-w-0 rounded-md border border-input bg-background px-3 text-sm font-normal focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                  data-product-guide="diagnosis-select"
                  value={diagnoserUuid}
                  disabled={busy || provisioning}
                  onChange={(event) =>
                    change(() => setDiagnoserUuid(event.target.value))
                  }
                >
                  <option value="">{t("diagnose.run.choose")}</option>
                  {options.diagnosers.map((item) => (
                    <option
                      key={item.uuid}
                      value={item.uuid}
                      disabled={!item.selectable}
                    >
                      {diagnosisRunLabels(item, t).name}
                      {!item.selectable
                        ? ` — ${failureLabel(item.unavailable_reason)}`
                        : ""}
                    </option>
                  ))}
                </select>
              </label>
              <Button
                data-product-guide="diagnosis-run"
                data-guide-state={
                  busy ||
                  provisioning ||
                  ["pending", "dispatched", "running"].includes(
                    task?.status ?? ""
                  )
                    ? "running"
                    : "ready"
                }
                className="h-9 max-w-full shrink-0"
                disabled={
                  busy ||
                  provisioning ||
                  !validSelection ||
                  !selectedDiagnoser?.selectable
                }
                onClick={() =>
                  void submit((!!accepted && !error) || (!!error && !retryable))
                }
              >
                {busy
                  ? label("submitting")
                  : error && !retryable
                    ? t("diagnose.run.startNew")
                    : accepted && !error
                      ? t("diagnose.run.runAgain")
                      : label("run")}
              </Button>
            </div>
            <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1 text-xs">
              <details
                id="trace-diagnosis-selection"
                className="min-w-0 flex-1 basis-40 scroll-mt-20"
              >
                <summary className="w-fit cursor-pointer rounded-sm py-1 font-medium focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
                  {t("diagnose.quick.selectedTraces", {
                    count: traceIds.length,
                  })}
                </summary>
                <p className="mt-1 text-muted-foreground">
                  {label("selectedScope")}
                </p>
                {traceIds.length ? (
                  <ul className="mt-2 max-h-32 space-y-1 overflow-auto rounded-md bg-muted/40 p-2">
                    {traceIds.map((id) => (
                      <li key={id} className="font-mono break-all">
                        {id}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-1 text-muted-foreground">
                    {label("selectionHint")}
                  </p>
                )}
              </details>
              <div className="ml-auto flex flex-wrap items-center gap-2">
                <Link
                  className="inline-flex min-h-7 items-center gap-1 text-muted-foreground underline-offset-4 hover:text-foreground hover:underline focus-visible:ring-2"
                  to={configurationUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label={t("diagnose.run.manageNewTab")}
                  title={t("diagnose.run.manageNewTab")}
                >
                  {t("diagnose.run.manageShort")}
                  <ExternalLink className="size-3" aria-hidden="true" />
                </Link>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-7"
                  aria-label={t("diagnose.run.refresh")}
                  title={t("diagnose.run.refresh")}
                  disabled={busy || provisioning || optionsQuery.isFetching}
                  onClick={() => void optionsQuery.refetch()}
                >
                  <RefreshCw className="size-3.5" aria-hidden="true" />
                </Button>
              </div>
            </div>
            {selectedDiagnoser ? (
              <details className="text-xs text-muted-foreground">
                <summary className="w-fit cursor-pointer rounded-sm py-1 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
                  {t("diagnose.run.configurationDetails")}
                  <span className="ml-2">
                    {t(
                      selectedDiagnoser.is_specialist
                        ? "diagnose.specialist.tab"
                        : "diagnose.run.savedDiagnoser"
                    )}
                    {" · "}
                    {selectedDiagnoser.mode === "llm"
                      ? "LLM"
                      : t("diagnose.diagnoser.engineRule")}
                  </span>
                </summary>
                <div className="mt-1 space-y-2 border-l border-border pl-3 leading-relaxed">
                  {selectedLabels?.description ? (
                    <p className="whitespace-pre-wrap text-foreground">
                      {selectedLabels.description}
                    </p>
                  ) : null}
                  <p>{label("intro")}</p>
                  <p>{t("diagnose.run.savedConfiguration")}</p>
                  {accepted ? <p>{t("diagnose.run.againHint")}</p> : null}
                </div>
              </details>
            ) : null}
            {selectedDiagnoser && !selectedDiagnoser.selectable ? (
              <p className="text-sm text-destructive" role="status">
                {failureLabel(selectedDiagnoser.unavailable_reason)}
              </p>
            ) : !selectedDiagnoser && diagnoserUuid ? (
              <p className="text-sm text-destructive" role="status">
                {t("diagnose.run.unavailable")}
              </p>
            ) : !options.diagnosers.some((item) => item.selectable) ? (
              <p className="text-sm text-muted-foreground">
                {t("diagnose.run.empty")}
              </p>
            ) : null}
            {provisioning || error ? (
              <div className="flex flex-wrap items-center gap-2">
                {provisioning ? (
                  <span className="text-xs">{label("provisioning")}</span>
                ) : null}
                {error ? (
                  <>
                    <p className="text-xs text-[var(--color-error)]">
                      {failureLabel(error)}
                    </p>
                    {retryable ? (
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={busy}
                        onClick={() => void submit()}
                      >
                        {intent.body
                          ? label("recover")
                          : t("diagnose.run.retrySubmission")}
                      </Button>
                    ) : null}
                  </>
                ) : null}
              </div>
            ) : null}
          </div>
        </>
      )}
      {!taskUuid &&
      !accepted &&
      !busy &&
      !provisioning &&
      !(error && retryable && intent.body) ? (
        <p className="text-xs text-muted-foreground">
          {label("states.not_run")}
        </p>
      ) : null}
      {taskQuery.error && !denied ? (
        <p className="text-sm text-[var(--color-error)]">
          {t(`diagnose.quick.errors.${errorKey(taskQuery.error)}`)}
        </p>
      ) : task?.quick_run && task.quick_run.agent_uuid === agentUuid ? (
        <div
          data-product-guide="diagnosis-results"
          className="space-y-3 border-t pt-3"
        >
          {recordsQuery.error ? recordToolbar : null}
          {recordsQuery.error || recordsSettling ? (
            <div
              className="flex flex-wrap items-center gap-2 text-xs"
              role="status"
            >
              <span>
                {label(
                  recordsQuery.error
                    ? "recordsFailed"
                    : recordsCountUnknown
                      ? "recordsCountUnknown"
                      : recordsUnconfirmed
                        ? "recordsUnconfirmed"
                        : "recordsWaiting"
                )}
              </span>
              {recordsQuery.error ||
              recordsUnconfirmed ||
              recordsCountUnknown ? (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    void queryClient.resetQueries({
                      queryKey: recordsKey,
                      exact: true,
                    })
                  }
                >
                  {label("recordsRefresh")}
                </Button>
              ) : null}
              {recordsQuery.error ||
              recordsUnconfirmed ||
              recordsCountUnknown ? (
                <p className="w-full text-muted-foreground">
                  {label("recordsRefreshHelp")}
                </p>
              ) : null}
            </div>
          ) : null}
          {recordsQuery.error ? null : (
            <TraceDiagnosisResult
              task={task}
              records={recordsQuery.data?.items ?? []}
              total={recordsQuery.data?.total ?? 0}
              filterEmpty={
                verdict !== "all" &&
                recordsComplete(recordsQuery.data) &&
                recordsQuery.isSuccess &&
                recordsQuery.data.total === 0
              }
              onOpenRecord={(record, spanId, target) =>
                onOpenRecord(
                  record,
                  spanId,
                  target,
                  !!spanId &&
                    spanId === sourceSpanId(record) &&
                    completedRuleRecords.some(
                      (item) => item.uuid === record.uuid
                    )
                )
              }
              toolbar={recordToolbar}
            />
          )}
          {recordPage > 0 || (recordsQuery.data?.total ?? 0) > 10 ? (
            <div className="flex gap-2">
              <Button
                variant="ghost"
                size="sm"
                disabled={!recordPage}
                onClick={() => setRecordPage((p) => p - 1)}
              >
                {label("previous")}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                disabled={
                  (recordPage + 1) * 10 >= (recordsQuery.data?.total ?? 0)
                }
                onClick={() => setRecordPage((p) => p + 1)}
              >
                {label("next")}
              </Button>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}
