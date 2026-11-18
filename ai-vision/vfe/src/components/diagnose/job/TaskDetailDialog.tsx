/**
 * TaskDetailDialog — 单个 Task 的详情弹窗
 *
 * 通过 spaceId + taskUuid 拉取详情 (useQuery)
 * 含：所属 Job / 状态 / 触发时间 / 开始 / 结束 / 耗时 / Worker / Attempt
 * 底部：取消按钮（pending/dispatched 状态可点）+ 关闭
 */

import { useState } from "react"
import { useTranslation } from "react-i18next"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Loader2, X, Trash2 } from "lucide-react"
import { toast } from "sonner"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { AlertDialogContent } from "@/components/dialogs/AlertDialogContent"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { DialogContent } from "@/components/dialogs/DialogContent"

import { useUser } from "@/contexts/UserContext"
import { useSpace } from "@/contexts/SpaceContext"

import { cancelTask, deleteTask, getTask } from "../diagnoseApi"
import { JudgeRequestSummaryView } from "../JudgeRequestSummaryView"
import { TaskStatsContent } from "./TaskOutcomeContent"

interface TaskDetailDialogProps {
  spaceId: string
  taskUuid: string | null
  onClose: () => void
}

export function TaskDetailDialog({
  spaceId,
  taskUuid,
  onClose,
}: TaskDetailDialogProps) {
  const { t, i18n } = useTranslation()
  const { user } = useUser()
  const { currentSpace } = useSpace()
  const canManage =
    currentSpace?.id === spaceId &&
    currentSpace.permissions.includes("diagnosis.job.manage")
  const queryClient = useQueryClient()
  const open = !!taskUuid
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)

  const taskQuery = useQuery({
    queryKey: ["diagnose-task", spaceId, taskUuid, user?.user_uuid],
    queryFn: () => getTask(spaceId, taskUuid!),
    enabled: !!taskUuid && !!user?.user_uuid,
    retry: false,
  })

  const cancelMutation = useMutation({
    mutationFn: () => cancelTask(spaceId, taskUuid!),
    onSuccess: () => {
      toast.success(t("diagnose.job.task.cancelSuccess"))
      queryClient.invalidateQueries({
        queryKey: ["diagnose-task", spaceId, taskUuid, user?.user_uuid],
      })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const deleteMutation = useMutation({
    mutationFn: () => deleteTask(spaceId, taskUuid!),
    onSuccess: () => {
      toast.success(t("diagnose.job.task.deleteSuccess"))
      queryClient.invalidateQueries({ queryKey: ["diagnose-tasks"] })
      setDeleteConfirmOpen(false)
      onClose()
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const task = taskQuery.isError ? undefined : taskQuery.data
  const canCancel =
    canManage &&
    !task?.quick_run &&
    (task?.status === "pending" || task?.status === "dispatched")
  const canDelete =
    canManage &&
    task &&
    !task.quick_run &&
    task.status !== "running" &&
    task.status !== "pending" &&
    task.status !== "dispatched"
  const taskDuration =
    task?.started_at && task?.finished_at
      ? formatDuration(task.started_at, task.finished_at)
      : null

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !deleteMutation.isPending) {
          setDeleteConfirmOpen(false)
          onClose()
        }
      }}
    >
      <DialogContent size="detail" className="flex flex-col">
        <DialogHeader>
          <DialogTitle className="text-xl font-medium">
            {t("diagnose.job.task.title")}
          </DialogTitle>
          {task ? (
            <p className="mt-1 font-mono text-xs text-muted-foreground">
              {t("diagnose.job.history.taskId")}: {task.uuid.slice(0, 12)}
            </p>
          ) : null}
        </DialogHeader>

        <div className="min-h-0 flex-1 overflow-y-auto pr-1">
          {taskQuery.isLoading ? (
            <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              {t("common.loading")}
            </div>
          ) : taskQuery.error || !task ? (
            <div className="rounded border border-dashed p-4 text-center text-xs text-muted-foreground">
              {t("diagnose.job.task.loadFailed")}
            </div>
          ) : (
            <dl className="grid grid-cols-1 gap-x-4 gap-y-2 text-xs sm:grid-cols-[max-content_minmax(0,1fr)] [&>dd]:[overflow-wrap:anywhere]">
              <dt className="text-muted-foreground">
                {t("diagnose.job.task.job")}
              </dt>
              <dd className="font-mono">{task.job_uuid}</dd>

              <dt className="text-muted-foreground">
                {t("diagnose.job.task.status")}
              </dt>
              <dd>
                <Badge variant="outline" className="text-xs">
                  {t(`diagnose.job.taskStatus.${task.status}`)}
                </Badge>
              </dd>

              <dt className="text-muted-foreground">
                {t("diagnose.job.task.scheduledAt")}
              </dt>
              <dd className="font-mono">
                {formatAbsoluteTime(task.scheduled_at, i18n?.resolvedLanguage)}
              </dd>

              <dt className="text-muted-foreground">
                {t("diagnose.job.task.startedAt")}
              </dt>
              <dd className="font-mono">
                {task.started_at
                  ? formatAbsoluteTime(task.started_at, i18n?.resolvedLanguage)
                  : "—"}
              </dd>

              <dt className="text-muted-foreground">
                {t("diagnose.job.task.finishedAt")}
              </dt>
              <dd className="font-mono">
                {task.finished_at
                  ? formatAbsoluteTime(task.finished_at, i18n?.resolvedLanguage)
                  : "—"}
              </dd>

              {taskDuration ? (
                <>
                  <dt className="text-muted-foreground">
                    {t("diagnose.job.task.duration")}
                  </dt>
                  <dd className="font-mono">{taskDuration}</dd>
                </>
              ) : null}

              <dt className="text-muted-foreground">
                {t("diagnose.job.task.worker")}
              </dt>
              <dd className="font-mono">{task.worker ?? "—"}</dd>

              <dt className="text-muted-foreground">
                {t("diagnose.job.task.attempt")}
              </dt>
              <dd>{task.attempt}</dd>

              {task.status === "failed" && task.msg ? (
                <>
                  <dt className="text-muted-foreground">
                    {t("diagnose.job.task.msg")}
                  </dt>
                  <dd className="text-xs break-all">{task.msg}</dd>
                </>
              ) : null}

              {task.stats ||
              task.status === "succeeded" ||
              task.status === "failed" ? (
                <>
                  <dd className="col-span-full mt-2 min-w-0 text-xs">
                    <TaskStatsContent stats={task.stats} metadata={task} />
                    <JudgeRequestSummaryView
                      summary={task.judge_request_summary}
                      state={task.judge_request_summary_state}
                    />
                  </dd>
                </>
              ) : null}
            </dl>
          )}
        </div>

        <DialogFooter className="shrink-0">
          {canCancel ? (
            <Button
              variant="destructive"
              size="sm"
              disabled={cancelMutation.isPending}
              onClick={() => cancelMutation.mutate()}
            >
              {cancelMutation.isPending ? (
                <Loader2 className="mr-1 size-3 animate-spin" />
              ) : (
                <X className="mr-1 size-3" />
              )}
              {t("diagnose.job.task.cancel")}
            </Button>
          ) : null}
          {canDelete ? (
            <Button
              variant="destructive"
              size="sm"
              disabled={deleteMutation.isPending}
              onClick={() => setDeleteConfirmOpen(true)}
            >
              <Trash2 className="mr-1 size-3" />
              {t("diagnose.job.task.delete")}
            </Button>
          ) : null}
          <Button
            variant="outline"
            disabled={deleteMutation.isPending}
            onClick={() => {
              setDeleteConfirmOpen(false)
              onClose()
            }}
          >
            {t("common.close")}
          </Button>
        </DialogFooter>
      </DialogContent>
      <AlertDialog
        open={deleteConfirmOpen}
        onOpenChange={(next) => {
          if (!deleteMutation.isPending) setDeleteConfirmOpen(next)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("diagnose.job.task.deleteTitle")}
            </AlertDialogTitle>
            <AlertDialogDescription className="flex flex-col gap-1">
              <span>{t("diagnose.job.task.deleteIrreversible")}</span>
              <span>
                {t("diagnose.job.task.deleteConfirm", {
                  uuid: task?.uuid ?? taskUuid,
                })}
              </span>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteMutation.isPending}>
              {t("common.cancel")}
            </AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={!canDelete || deleteMutation.isPending}
              onClick={(event) => {
                event.preventDefault()
                if (canDelete) deleteMutation.mutate()
              }}
            >
              {deleteMutation.isPending ? (
                <Loader2 className="mr-1 size-3 animate-spin" />
              ) : null}
              {t("diagnose.job.task.confirmDelete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Dialog>
  )
}

function formatAbsoluteTime(iso: string, locale: string | undefined): string {
  return new Date(iso).toLocaleString(locale, {
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
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m${s % 60}s`
}
