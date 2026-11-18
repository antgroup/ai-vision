import { useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { useQuery } from "@tanstack/react-query"
import { Loader2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useUser } from "@/contexts/UserContext"
import { getCurrentLocale } from "@/i18n"
import { cn } from "@/lib/utils"

import { listTasks, toTaskRecord } from "../diagnoseApi"
import type { JobDTO, TaskRecord, TaskStats, TaskStatus } from "../types"
import { TaskDetailDialog } from "./TaskDetailDialog"
import { TaskOutcomeContent } from "./TaskOutcomeContent"

type TimeRange = "today" | "7d" | "30d"

const TIME_RANGE_OPTIONS: Array<{ value: TimeRange; label: string }> = [
  { value: "today", label: "timeFilter.presets.today" },
  { value: "7d", label: "timeFilter.presets.7d" },
  { value: "30d", label: "diagnose.job.history.last30Days" },
]

interface ExecutionHistoryDrawerProps {
  open: boolean
  spaceId: string
  job: JobDTO | null
  canView: boolean
  onOpenChange: (open: boolean) => void
}

export function ExecutionHistoryDrawer({
  open,
  spaceId,
  job,
  canView,
  onOpenChange,
}: ExecutionHistoryDrawerProps) {
  const { t } = useTranslation()
  const { user } = useUser()
  const [timeRange, setTimeRange] = useState<TimeRange>("7d")
  const [selectedTaskUuid, setSelectedTaskUuid] = useState<string | null>(null)
  const tasksQuery = useQuery({
    queryKey: [
      "diagnose-tasks",
      spaceId,
      job?.uuid,
      "history",
      user?.user_uuid,
    ],
    queryFn: () => listTasks(spaceId, { job_uuid: job!.uuid, limit: 50 }),
    enabled: open && !!job && canView && !!user?.user_uuid,
    retry: false,
    refetchInterval: (q) => {
      if (q.state.error) return false
      const items = q.state.data?.items ?? []
      return items.some((task) => isUnfinished(task.status)) ? 10_000 : false
    },
  })

  const filteredTasks = useMemo(() => {
    const tasks = (
      canView && !tasksQuery.isError ? (tasksQuery.data?.items ?? []) : []
    )
      .map(toTaskRecord)
      .sort(
        (a, b) =>
          new Date(b.scheduledAt).getTime() - new Date(a.scheduledAt).getTime()
      )
    return filterByTimeRange(tasks, timeRange)
  }, [canView, tasksQuery.data, tasksQuery.isError, timeRange])

  const thCls =
    "whitespace-nowrap border-b border-border bg-[var(--color-bg-subtle)] px-3 py-2 text-left text-[length:var(--font-size-base)] leading-[var(--line-height-base)] font-medium text-[var(--color-text-secondary)]"

  return (
    <>
      <Sheet open={open && canView} onOpenChange={onOpenChange}>
        <SheetContent
          showCloseButton={false}
          className="flex flex-col gap-0 overflow-hidden p-0 data-[side=right]:w-full data-[side=right]:sm:max-w-[max(640px,50vw)]"
        >
          <div className="shrink-0 border-b px-4 py-4 sm:px-6">
            <div className="flex items-start justify-between gap-2">
              <SheetTitle className="text-xl font-medium">
                {t("diagnose.job.executionHistory")}
              </SheetTitle>
              <Button
                variant="ghost"
                size="sm"
                className="shrink-0"
                onClick={() => onOpenChange(false)}
              >
                {t("common.close")}
              </Button>
            </div>
            {job ? (
              <p className="mt-1 font-mono text-xs text-[var(--color-text-secondary)]">
                {job.name}
              </p>
            ) : null}
          </div>
          <div className="min-h-0 min-w-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6">
            <div className="flex flex-col gap-4">
              <div className="flex flex-wrap items-center gap-2">
                {TIME_RANGE_OPTIONS.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => setTimeRange(option.value)}
                    className={cn(
                      "inline-flex h-7 items-center rounded-[var(--ai-radius-md)] px-2 text-xs font-medium transition-all",
                      timeRange === option.value
                        ? "bg-primary text-primary-foreground"
                        : "border border-border/80 bg-background text-muted-foreground hover:border-border hover:text-foreground"
                    )}
                  >
                    {t(option.label)}
                  </button>
                ))}
                <span className="ml-2 text-xs text-[var(--color-text-secondary)]">
                  {t("common.total", { count: filteredTasks.length })}
                </span>
              </div>

              {tasksQuery.isLoading ? (
                <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" />
                  {t("common.loading")}
                </div>
              ) : filteredTasks.length === 0 ? (
                <div className="flex items-center justify-center py-12 text-sm text-muted-foreground">
                  {t("diagnose.job.history.empty")}
                </div>
              ) : (
                <div className="overflow-x-auto rounded-lg border border-border">
                  <table className="w-full border-collapse text-sm">
                    <thead>
                      <tr>
                        <th className={cn(thCls, "w-[120px]")}>
                          {t("diagnose.job.history.taskId")}
                        </th>
                        <th className={cn(thCls, "w-[150px]")}>
                          {t("diagnose.job.task.scheduledAt")}
                        </th>
                        <th className={cn(thCls, "w-[150px]")}>
                          {t("diagnose.job.task.startedAt")}
                        </th>
                        <th className={cn(thCls, "w-[80px]")}>
                          {t("diagnose.job.task.duration")}
                        </th>
                        <th className={cn(thCls, "w-[90px]")}>
                          {t("diagnose.job.task.status")}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredTasks.map((task) => (
                        <tr
                          key={task.uuid}
                          className="cursor-pointer border-b border-border transition-colors hover:bg-accent"
                          onClick={() => setSelectedTaskUuid(task.uuid)}
                        >
                          <td className="px-3 py-2 font-mono text-xs whitespace-nowrap text-[var(--color-text-secondary)]">
                            {task.uuid.slice(0, 12)}
                          </td>
                          <td className="px-3 py-2 text-xs whitespace-nowrap text-[var(--color-text-secondary)] tabular-nums">
                            {formatAbsoluteTime(task.scheduledAt)}
                          </td>
                          <td className="px-3 py-2 text-xs whitespace-nowrap text-[var(--color-text-secondary)] tabular-nums">
                            {task.startedAt
                              ? formatAbsoluteTime(task.startedAt)
                              : "-"}
                          </td>
                          <td className="px-3 py-2 text-xs whitespace-nowrap text-[var(--color-text-secondary)]">
                            {task.startedAt && task.finishedAt
                              ? formatDuration(task.startedAt, task.finishedAt)
                              : "-"}
                          </td>
                          <td className="px-3 py-2 whitespace-nowrap">
                            <TaskStatusText
                              status={task.status}
                              message={task.msg}
                              stats={task.stats}
                              metadata={task.definitionMetadata}
                            />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        </SheetContent>
      </Sheet>

      <TaskDetailDialog
        spaceId={spaceId}
        taskUuid={canView ? selectedTaskUuid : null}
        onClose={() => setSelectedTaskUuid(null)}
      />
    </>
  )
}

export function TaskStatusText({
  status,
  message,
  stats,
  metadata,
}: {
  status: TaskStatus
  message?: string | null
  stats?: TaskStats | null
  metadata?: TaskRecord["definitionMetadata"]
}) {
  const { t } = useTranslation()
  const config = {
    pending: {
      label: t("diagnose.job.taskStatus.pending"),
      cls: "bg-[var(--color-bg-warning)] text-[var(--color-warning)]",
    },
    dispatched: {
      label: t("diagnose.job.taskStatus.dispatched"),
      cls: "bg-[var(--color-bg-warning)] text-[var(--color-warning)]",
    },
    running: {
      label: t("diagnose.job.taskStatus.running"),
      cls: "bg-[var(--color-bg-info)] text-[var(--color-text-secondary)]",
    },
    succeeded: {
      label: t("diagnose.scope.executionEnded"),
      cls: "bg-[var(--color-bg-success)] text-[var(--color-success)]",
    },
    failed: {
      label: t("diagnose.scope.executionFailed"),
      cls: "bg-[var(--color-bg-error)] text-[var(--color-error)]",
    },
    canceled: {
      label: t("diagnose.job.taskStatus.canceled"),
      cls: "bg-muted text-muted-foreground",
    },
  }[status]

  const content = (
    <span
      className={cn(
        "inline-flex h-5 items-center rounded-full px-2 text-xs font-medium",
        config.cls
      )}
    >
      {config.label}
    </span>
  )

  const hasTooltipContent =
    (status === "failed" && !!message?.trim()) ||
    ((status === "succeeded" || status === "failed") &&
      (!!stats || metadata?.definition_source === "unsupported_snapshot"))

  if (!hasTooltipContent) return content

  return (
    <Tooltip>
      <TooltipTrigger asChild>{content}</TooltipTrigger>
      <TooltipContent className="max-w-[480px] text-xs break-words whitespace-pre-wrap">
        <TaskOutcomeContent
          status={status}
          message={message}
          stats={stats}
          metadata={metadata}
        />
      </TooltipContent>
    </Tooltip>
  )
}

function filterByTimeRange(
  tasks: TaskRecord[],
  range: TimeRange
): TaskRecord[] {
  const now = new Date()
  let cutoff: Date
  if (range === "today") {
    cutoff = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  } else if (range === "7d") {
    cutoff = new Date(now.getTime() - 7 * 86400000)
  } else {
    cutoff = new Date(now.getTime() - 30 * 86400000)
  }
  return tasks.filter((task) => new Date(task.scheduledAt) >= cutoff)
}

function isUnfinished(status: TaskStatus): boolean {
  return status === "pending" || status === "dispatched" || status === "running"
}

function formatAbsoluteTime(iso: string): string {
  return new Date(iso).toLocaleString(getCurrentLocale(), {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  })
}

function formatDuration(startIso: string, endIso: string): string {
  const ms = new Date(endIso).getTime() - new Date(startIso).getTime()
  if (ms <= 0) return "0ms"
  if (ms < 1000) return `${ms}ms`
  const seconds = Math.floor(ms / 1000)
  if (seconds < 60) return `${seconds}s`
  return `${Math.floor(seconds / 60)}m${seconds % 60}s`
}
