import { useTranslation } from "react-i18next"
import type { ReactNode } from "react"
import { scopeFromTask, skipReasonLabelKey } from "./diagnosisScope"
import { quickOutcome, sourceSpanId } from "./quickDiagnosis"
import { diagnosisExecutionMessage } from "./executionMessage"
import type { DiagnosisRecordDTO, TaskDTO } from "./types"

export function TraceDiagnosisResult({
  task,
  records,
  total,
  filterEmpty = false,
  toolbar,
  onOpenRecord,
}: {
  task: TaskDTO
  records: DiagnosisRecordDTO[]
  total: number
  filterEmpty?: boolean
  toolbar?: ReactNode
  onOpenRecord: (
    record: DiagnosisRecordDTO,
    spanId?: string,
    target?: "trace"
  ) => void
}) {
  const { t } = useTranslation()
  const label = (key: string) => t(`diagnose.quick.${key}`)
  const scope = scopeFromTask(task.stats, task)
  return (
    <section className="space-y-2" aria-live="polite">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          <h3 className="text-sm font-medium">
            {label(`states.${quickOutcome(task, filterEmpty)}`)}
          </h3>
          <span className="text-xs text-muted-foreground">
            {t("diagnose.quick.recordCount", { count: total })}
          </span>
        </div>
        {toolbar}
      </div>
      <p className="text-xs text-muted-foreground">
        {t("diagnose.run.resultScope")}
      </p>
      {task.status === "failed" || task.status === "canceled" ? (
        <p className="text-sm text-[var(--color-error)]">
          {task.msg || label("states.failed")}
        </p>
      ) : null}
      {scope.sourceIncomplete ? (
        <p role="alert" className="text-sm text-[var(--color-warning)]">
          {t("diagnose.scope.sourceIncomplete")}
        </p>
      ) : null}
      {scope.skipped ? (
        <p className="text-xs text-muted-foreground">
          {t("diagnose.scope.skipped", { value: scope.skipped })}
          {scope.skipReasons.map(({ reason, count }) => (
            <span className="ml-3 inline-block" key={reason}>
              {t(skipReasonLabelKey(reason), {
                defaultValue: t("diagnose.scope.otherReason"),
              })}
              : {count}
            </span>
          ))}
        </p>
      ) : null}
      {scope.failed ? (
        <p className="text-xs text-[var(--color-error)]">
          {t("diagnose.scope.failed", { value: scope.failed })}
        </p>
      ) : null}
      <div
        className={
          records.length
            ? "divide-y divide-border border-y border-border"
            : undefined
        }
      >
        {records.map((record) => {
          const frozen =
            record.definition_source === "task_snapshot"
              ? record.definition
              : null
          const spanId = sourceSpanId(record)
          const failed = record.execution.status === "failed"
          const summary = failed
            ? diagnosisExecutionMessage(record.execution, t)
            : record.summary
          const role =
            frozen?.score_role === "quality" || frozen?.score_role === "signal"
              ? frozen.score_role
              : "unknown"
          const reasons = [
            ...new Set(
              record.metrics
                .filter((metric) => metric.verdict !== "good")
                .map((metric) => metric.reason)
                .filter(
                  (reason) =>
                    reason &&
                    reason.trim() !== record.summary.trim() &&
                    reason.trim() !== summary.trim() &&
                    (!failed || reason.trim() !== record.execution.msg.trim())
                )
            ),
          ]
          return (
            <article key={record.uuid} className="space-y-1.5 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <h4 className="max-w-full min-w-0 text-sm font-medium break-words">
                  {frozen?.name ?? record.diagnoser_uuid}
                </h4>
                <span className="text-xs text-muted-foreground">
                  {label(`roles.${role}`)} ·{" "}
                  {record.verdict === "failed"
                    ? label("executionFailed")
                    : label(`verdict.${record.verdict}`)}
                </span>
              </div>
              <p className="text-xs break-all text-muted-foreground">
                {t("traceSearch.types.traceId")}:{" "}
                <code>{record.target_id}</code>
              </p>
              <p className="text-sm break-words whitespace-pre-wrap">
                {summary}
              </p>
              {reasons.map((reason) => (
                <p
                  key={reason}
                  className="text-xs break-words whitespace-pre-wrap text-muted-foreground"
                >
                  {reason}
                </p>
              ))}
              <div className="flex flex-wrap items-center gap-3 text-xs">
                <button
                  type="button"
                  className="inline-flex min-h-7 items-center text-primary underline-offset-4 hover:underline focus-visible:ring-2"
                  data-source-span={spanId ?? undefined}
                  onClick={() =>
                    onOpenRecord(record, spanId ?? undefined, "trace")
                  }
                >
                  {label(spanId ? "sourceStep" : "viewTrace")}
                </button>
                <button
                  type="button"
                  className="inline-flex min-h-7 items-center text-primary underline-offset-4 hover:underline focus-visible:ring-2"
                  onClick={() => onOpenRecord(record)}
                >
                  {t("diagnose.run.recordDetails")}
                </button>
                {record.review_status !== "pending" ? (
                  <span>
                    {t(
                      `diagnose.badcase.status${record.review_status[0].toUpperCase()}${record.review_status.slice(1)}`,
                      { defaultValue: record.review_status }
                    )}
                  </span>
                ) : null}
              </div>
            </article>
          )
        })}
      </div>
    </section>
  )
}
