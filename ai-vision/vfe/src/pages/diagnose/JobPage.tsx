import { useCallback, useEffect, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useSearchParams } from "react-router-dom"
import { toast } from "sonner"
import {
  Copy,
  History,
  Inbox,
  Info,
  Loader2,
  MoreHorizontal,
  Pencil,
  Plus,
  Search,
  Trash2,
  X,
  Zap,
} from "lucide-react"

import { SpaceAgentGuard } from "@/components/error/SpaceAgentGuard"
import { PageBreadcrumb } from "@/components/layout/PageBreadcrumb"
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
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import {
  Pagination,
  PaginationContent,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
} from "@/components/ui/pagination"
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
import { useAgent } from "@/contexts/AgentContext"
import { useSpace } from "@/contexts/SpaceContext"
import { useTimeFilter } from "@/contexts/TimeFilterContext"
import { ApiBusinessError, api } from "@/lib/api"
import { cn } from "@/lib/utils"
import { SpaceType } from "@/lib/schemas/space"

import {
  deleteDataset,
  deleteJob,
  listDiagnosers,
  listJobs,
  listTasks,
  toDiagnoser,
  toTaskRecord,
  triggerJob,
  updateJob,
} from "@/components/diagnose/diagnoseApi"
import {
  isUserSharedSpace,
  isPlatformManagedJob,
  PLATFORM_MANAGED_ACTOR_NAME,
} from "@/components/diagnose/resourceScopes"
import {
  displayAgentName,
  hasBuiltinDiagnosers,
} from "@/components/diagnose/builtinDiagnoserPolicy"
import {
  ExecutionHistoryDrawer,
  TaskStatusText,
} from "@/components/diagnose/job/ExecutionHistoryDrawer"
import { JobDrawer } from "@/components/diagnose/job/JobDrawer"
import type {
  CreateJobPayload,
  Diagnoser,
  JobDTO,
  JobStatus,
  TaskRecord,
} from "@/components/diagnose/types"

const PAGE_SIZES = [10, 20, 50] as const
const STATUS_FILTERS: Array<{ value: "all" | JobStatus }> = [
  { value: "all" },
  { value: "enabled" },
  { value: "disabled" },
]
const DIM_ORDER = ["outcome", "process", "safety", "system"] as const
type DrawerMode = "create" | "edit" | "view"
interface SpaceMembersResponse {
  code: number
  data: {
    items: Array<{
      name: string
      user_uuid: string
    }>
  }
}

export function JobPage() {
  const { currentAgent } = useAgent()
  const { currentSpace } = useSpace()
  const { refreshTick } = useTimeFilter()
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const { t } = useTranslation()
  const spaceId = currentSpace?.id ?? ""
  const agentUuid = currentAgent?.uuid ?? ""
  const noBuiltinDiagnosers = !hasBuiltinDiagnosers(currentAgent?.agent_product)
  const agentDisplayName =
    displayAgentName(
      currentAgent?.agent_name,
      currentAgent?.agent_product,
      currentAgent?.display_name
    ) || t("diagnose.currentChannel")
  const isPersonal = currentSpace?.type === SpaceType.Personal
  const canCreateDiagnosisJob =
    currentSpace?.permissions.includes("diagnosis.job.manage") ?? false
  const canResolveCreatePermission = !!currentSpace
  const createDisabledReason = isPersonal
    ? t("diagnose.job.personalCreateDisabled")
    : t("diagnose.job.createPermissionDenied")
  const statusLabels: Record<string, string> = {
    all: t("common.all"),
    enabled: t("common.enabled"),
    disabled: t("common.disabled"),
  }

  const [searchKeyword, setSearchKeyword] = useState("")
  const [statusFilter, setStatusFilter] = useState<"all" | JobStatus>("all")
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [drawerMode, setDrawerMode] = useState<DrawerMode>("create")
  const [drawerJobUuid, setDrawerJobUuid] = useState<string | null>(null)
  const [drawerPrefill, setDrawerPrefill] = useState<
    Partial<CreateJobPayload> | undefined
  >()
  const [deleteJobTarget, setDeleteJobTarget] = useState<JobDTO | null>(null)
  const [historyJob, setHistoryJob] = useState<JobDTO | null>(null)

  const jobsQuery = useQuery({
    queryKey: ["diagnose-jobs", spaceId, agentUuid, refreshTick],
    queryFn: () => listJobs(spaceId, { agent_uuid: agentUuid }),
    enabled: !!spaceId && !!agentUuid,
  })

  const diagnosersQuery = useQuery({
    queryKey: ["diagnosers", spaceId, agentUuid, refreshTick],
    queryFn: () => listDiagnosers(spaceId, agentUuid),
    enabled: !!spaceId && !!agentUuid,
  })

  const membersQuery = useQuery({
    queryKey: ["space-members", spaceId],
    queryFn: () => api.get<SpaceMembersResponse>(`/spaces/${spaceId}/members`),
    enabled: !!spaceId,
    staleTime: 5 * 60 * 1000,
  })

  const jobs = useMemo(() => jobsQuery.data?.items ?? [], [jobsQuery.data])
  const diagnosers: Diagnoser[] = useMemo(() => {
    return (diagnosersQuery.data?.items ?? []).map((dto) =>
      toDiagnoser(dto, undefined, t)
    )
  }, [diagnosersQuery.data, t])
  const diagnoserMap = useMemo(
    () => new Map(diagnosers.map((diagnoser) => [diagnoser.uuid, diagnoser])),
    [diagnosers]
  )
  const creatorNameMap = useMemo(() => {
    const items =
      membersQuery.data?.code === 0 ? membersQuery.data.data.items : []
    return new Map(
      items.flatMap((member) => {
        if (!member.user_uuid) return []
        return [[member.user_uuid, member.name]] as Array<[string, string]>
      })
    )
  }, [membersQuery.data])

  const filteredJobs = useMemo(() => {
    return jobs.filter((job) => {
      if (statusFilter === "enabled" && job.status !== "enabled") return false
      if (statusFilter === "disabled" && job.status !== "disabled") {
        return false
      }
      const keyword = searchKeyword.trim().toLowerCase()
      if (keyword && !job.name.toLowerCase().includes(keyword)) return false
      return true
    })
  }, [jobs, searchKeyword, statusFilter])

  const totalItems = filteredJobs.length
  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize))
  const safePage = Math.min(page, totalPages)
  const paginatedJobs = filteredJobs.slice(
    (safePage - 1) * pageSize,
    safePage * pageSize
  )
  const isEmpty = !jobsQuery.isLoading && jobs.length === 0

  const deleteMutation = useMutation({
    mutationFn: async (job: JobDTO) => {
      if (!isJobEditable(job, canCreateDiagnosisJob)) {
        throw new Error(t("diagnose.job.defaultReadonly"))
      }
      const tasks = await listTasks(spaceId, {
        job_uuid: job.uuid,
        limit: 100,
      })
      if (tasks.items.some((task) => isUnfinished(task.status))) {
        throw new Error(t("diagnose.job.unfinishedTask"))
      }
      await deleteJob(spaceId, job.uuid)
      if (job.dataset_uuid)
        await deleteDatasetIfExists(spaceId, job.dataset_uuid)
    },
    onSuccess: () => {
      toast.success(t("diagnose.job.deleteSuccess"))
      queryClient.invalidateQueries({ queryKey: ["diagnose-jobs"] })
      queryClient.invalidateQueries({ queryKey: ["diagnose-datasets"] })
      setDeleteJobTarget(null)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const openCreate = useCallback(
    (prefill?: Partial<CreateJobPayload>) => {
      if (!canCreateDiagnosisJob) return
      setDrawerPrefill(prefill)
      setDrawerJobUuid(null)
      setDrawerMode("create")
      setDrawerOpen(true)
    },
    [
      canCreateDiagnosisJob,
      setDrawerJobUuid,
      setDrawerMode,
      setDrawerOpen,
      setDrawerPrefill,
    ]
  )

  useEffect(() => {
    if (searchParams.get("create") !== "1" || !canResolveCreatePermission)
      return
    const timer = window.setTimeout(() => {
      openCreate()
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          next.delete("create")
          return next
        },
        { replace: true }
      )
    }, 0)
    return () => window.clearTimeout(timer)
  }, [canResolveCreatePermission, openCreate, searchParams, setSearchParams])

  const openView = useCallback(
    (job: JobDTO) => {
      setDrawerPrefill(undefined)
      setDrawerJobUuid(job.uuid)
      setDrawerMode("view")
      setDrawerOpen(true)
    },
    [setDrawerJobUuid, setDrawerMode, setDrawerOpen, setDrawerPrefill]
  )

  const openEdit = useCallback(
    (job: JobDTO) => {
      if (!isJobEditable(job, canCreateDiagnosisJob)) return
      setDrawerPrefill(undefined)
      setDrawerJobUuid(job.uuid)
      setDrawerMode("edit")
      setDrawerOpen(true)
    },
    [
      canCreateDiagnosisJob,
      setDrawerJobUuid,
      setDrawerMode,
      setDrawerOpen,
      setDrawerPrefill,
    ]
  )

  const handleCopy = useCallback(
    (job: JobDTO) => {
      if (!isJobEditable(job, canCreateDiagnosisJob)) return
      openCreate({
        name: t("diagnose.job.copyName", { name: job.name }),
        description: job.description || undefined,
        diagnoser_uuids: isUserSharedSpace(job.space_uuid)
          ? []
          : job.diagnoser_uuids,
        dataset_uuid: job.dataset_uuid,
        trigger_type: job.trigger_type,
        schedule_cron: job.schedule_cron || undefined,
        status: "enabled",
        spec: { ...job.spec },
      })
    },
    [canCreateDiagnosisJob, openCreate, t]
  )

  const drawerJob = drawerJobUuid
    ? jobs.find((job) => job.uuid === drawerJobUuid)
    : null
  const canEditDrawerJob =
    (drawerMode === "create" && canCreateDiagnosisJob) ||
    (!!drawerJob && isJobEditable(drawerJob, canCreateDiagnosisJob))

  return (
    <SpaceAgentGuard>
      <div className="flex min-w-0 flex-col gap-4 overflow-hidden px-4 pt-3 pb-6">
        <div className="flex items-center justify-between">
          <PageBreadcrumb
            pageTitle={t("nav.diagnoseJobs")}
            agentProduct={currentAgent?.agent_product ?? ""}
          />
        </div>

        {noBuiltinDiagnosers && (
          <Alert>
            <Info />
            <AlertDescription>
              {t("diagnose.job.unsupportedBanner", {
                defaultValue:
                  "「{{agent}}」暂未内置诊断器，暂不提供默认诊断任务",
                agent: agentDisplayName,
              })}
            </AlertDescription>
          </Alert>
        )}

        {isPersonal && !noBuiltinDiagnosers && (
          <Alert>
            <Info />
            <AlertDescription>
              {t("diagnose.job.personalScheduleHint")}
            </AlertDescription>
          </Alert>
        )}

        {jobsQuery.isError ? (
          <Alert variant="destructive">
            <AlertDescription>
              {jobsQuery.error.message}
              <Button
                variant="outline"
                size="sm"
                onClick={() => jobsQuery.refetch()}
              >
                {t("common.retry")}
              </Button>
            </AlertDescription>
          </Alert>
        ) : jobsQuery.isLoading ? (
          <div className="flex items-center justify-center py-16 text-sm text-muted-foreground">
            <Loader2 className="mr-2 size-4 animate-spin" />
            {t("common.loading")}
          </div>
        ) : isEmpty ? (
          <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border bg-[var(--color-bg-subtle)] py-20">
            <div className="mb-4 flex size-14 items-center justify-center rounded-2xl bg-muted/60">
              <Inbox className="size-7 text-muted-foreground/40" />
            </div>
            <p className="text-sm font-medium text-muted-foreground">
              {t("diagnose.job.emptyTitle")}
            </p>
            {isPersonal && (
              <p className="mt-1 text-xs text-muted-foreground/70">
                {t("diagnose.job.personalEmptyHint")}
              </p>
            )}
            {canCreateDiagnosisJob ? (
              <Button
                size="sm"
                className="mt-4 h-8 gap-1.5"
                onClick={() => openCreate()}
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
                <TooltipContent>{createDisabledReason}</TooltipContent>
              </Tooltip>
            )}
          </div>
        ) : (
          <Card className="gap-0 overflow-hidden">
            <CardContent className="pt-4">
              <div className="mb-4 flex items-center gap-2">
                <div className="relative w-64">
                  <Search className="absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    placeholder={t("diagnose.job.searchPlaceholder")}
                    value={searchKeyword}
                    onChange={(e) => {
                      setSearchKeyword(e.target.value)
                      setPage(1)
                    }}
                    className="h-8 pr-8 pl-8 text-xs font-normal"
                  />
                  {searchKeyword ? (
                    <button
                      type="button"
                      className="absolute top-1/2 right-2.5 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                      onClick={() => {
                        setSearchKeyword("")
                        setPage(1)
                      }}
                    >
                      <X className="size-3.5" />
                    </button>
                  ) : null}
                </div>
                <Select
                  value={statusFilter}
                  onValueChange={(value) => {
                    setStatusFilter(value as "all" | JobStatus)
                    setPage(1)
                  }}
                >
                  <SelectTrigger size="sm" className="w-28 text-xs font-normal">
                    <span>
                      {t("diagnose.job.statusFilterLabel")}
                      <SelectValue />
                    </span>
                  </SelectTrigger>
                  <SelectContent position="popper">
                    {STATUS_FILTERS.map((option) => (
                      <SelectItem key={option.value} value={option.value}>
                        {statusLabels[option.value]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <div className="ml-auto">
                  {canCreateDiagnosisJob ? (
                    <Button
                      size="sm"
                      className="h-8 gap-1.5"
                      onClick={() => openCreate()}
                    >
                      <Plus className="size-3.5" />
                      {t("diagnose.job.createCta")}
                    </Button>
                  ) : (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="inline-flex">
                          <Button size="sm" className="h-8 gap-1.5" disabled>
                            <Plus className="size-3.5" />
                            {t("diagnose.job.createCta")}
                          </Button>
                        </span>
                      </TooltipTrigger>
                      <TooltipContent>{createDisabledReason}</TooltipContent>
                    </Tooltip>
                  )}
                </div>
              </div>

              <div className="overflow-x-auto">
                {paginatedJobs.length === 0 ? (
                  <div className="flex flex-col items-center gap-3 py-12">
                    <p className="text-sm text-muted-foreground">
                      {t("diagnose.job.noMatch")}
                    </p>
                  </div>
                ) : (
                  <table className="w-full min-w-[1100px] border-collapse text-sm">
                    <thead className="sticky top-0 z-10">
                      <tr>
                        <TableHead className="w-[200px]">
                          {t("diagnose.job.col.name")}
                        </TableHead>
                        <TableHead className="w-[120px]">
                          {t("diagnose.job.col.status")}
                        </TableHead>
                        <TableHead className="w-[150px]">
                          {t("diagnose.job.col.schedule")}
                        </TableHead>
                        <TableHead className="w-[110px]">
                          {t("diagnose.job.col.coverage")}
                        </TableHead>
                        <TableHead className="w-[160px]">
                          {t("diagnose.job.col.lastRun")}
                        </TableHead>
                        <TableHead className="w-[120px]">
                          {t("diagnose.job.col.creator")}
                        </TableHead>
                        <TableHead className="w-[150px]">
                          {t("diagnose.job.col.time")}
                        </TableHead>
                        <TableHead className="w-[140px] text-right">
                          {t("diagnose.job.col.actions")}
                        </TableHead>
                      </tr>
                    </thead>
                    <tbody>
                      {paginatedJobs.map((job) => (
                        <JobTableRow
                          key={job.uuid}
                          job={job}
                          spaceId={spaceId}
                          refreshTick={refreshTick}
                          canEdit={isJobEditable(job, canCreateDiagnosisJob)}
                          canCreate={canCreateDiagnosisJob}
                          diagnoserMap={diagnoserMap}
                          creatorNameMap={creatorNameMap}
                          onView={() => openView(job)}
                          onEdit={() => openEdit(job)}
                          onCopy={() => handleCopy(job)}
                          onDelete={() => setDeleteJobTarget(job)}
                          onHistory={() => {
                            setHistoryJob(job)
                          }}
                        />
                      ))}
                    </tbody>
                  </table>
                )}
              </div>

              {totalItems > 0 ? (
                <div className="flex items-center justify-between px-3 pt-4 pb-3">
                  <span className="text-sm whitespace-nowrap text-muted-foreground">
                    {t("diagnose.job.totalItems", { count: totalItems })}
                  </span>
                  <Pagination className="w-auto">
                    <PaginationContent>
                      <PaginationItem>
                        <PaginationPrevious
                          href="#"
                          text={t("common.prev")}
                          onClick={(e) => {
                            e.preventDefault()
                            setPage((prev) => Math.max(1, prev - 1))
                          }}
                          className={cn(
                            safePage <= 1 && "pointer-events-none opacity-50"
                          )}
                        />
                      </PaginationItem>
                      {Array.from(
                        { length: totalPages },
                        (_, index) => index + 1
                      ).map((item) => (
                        <PaginationItem key={item}>
                          <PaginationLink
                            href="#"
                            isActive={item === safePage}
                            onClick={(e) => {
                              e.preventDefault()
                              setPage(item)
                            }}
                          >
                            {item}
                          </PaginationLink>
                        </PaginationItem>
                      ))}
                      <PaginationItem>
                        <PaginationNext
                          href="#"
                          text={t("common.next")}
                          onClick={(e) => {
                            e.preventDefault()
                            setPage((prev) => Math.min(totalPages, prev + 1))
                          }}
                          className={cn(
                            safePage >= totalPages &&
                              "pointer-events-none opacity-50"
                          )}
                        />
                      </PaginationItem>
                      <Select
                        value={pageSize.toString()}
                        onValueChange={(value) => {
                          setPageSize(Number(value))
                          setPage(1)
                        }}
                      >
                        <SelectTrigger size="sm" className="ml-1 w-[110px]">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent position="popper">
                          {PAGE_SIZES.map((size) => (
                            <SelectItem key={size} value={size.toString()}>
                              {t("diagnose.job.perPage", { count: size })}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </PaginationContent>
                  </Pagination>
                </div>
              ) : null}
            </CardContent>
          </Card>
        )}
      </div>

      <JobDrawer
        open={drawerOpen}
        mode={drawerMode}
        spaceId={spaceId}
        agentUuid={agentUuid}
        jobUuid={drawerJobUuid}
        prefill={drawerPrefill}
        existingJobs={jobs}
        creatorNameMap={creatorNameMap}
        canEdit={canEditDrawerJob}
        onOpenChange={(open) => {
          setDrawerOpen(open)
          if (!open) {
            setDrawerPrefill(undefined)
            setDrawerJobUuid(null)
          }
        }}
        onSwitchToEdit={() => {
          if (canEditDrawerJob) setDrawerMode("edit")
        }}
      />

      <ExecutionHistoryDrawer
        open={historyJob !== null}
        spaceId={spaceId}
        job={historyJob}
        canView={historyJob !== null}
        onOpenChange={(open) => {
          if (!open) setHistoryJob(null)
        }}
      />

      <AlertDialog
        open={deleteJobTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteJobTarget(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("diagnose.job.deleteTitle")}</AlertDialogTitle>
            <AlertDialogDescription className="flex flex-col gap-1">
              <span>{t("diagnose.job.deleteIrreversible")}</span>
              <span>{t("diagnose.job.deleteTaskWarning")}</span>
              <span>
                {t("diagnose.job.deleteConfirm", {
                  name: deleteJobTarget?.name,
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
              disabled={
                !deleteJobTarget ||
                !isJobEditable(deleteJobTarget, canCreateDiagnosisJob) ||
                deleteMutation.isPending
              }
              onClick={(e) => {
                e.preventDefault()
                if (deleteJobTarget) deleteMutation.mutate(deleteJobTarget)
              }}
            >
              {deleteMutation.isPending ? (
                <Loader2 className="mr-1 size-3 animate-spin" />
              ) : null}
              {t("diagnose.job.confirmDelete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </SpaceAgentGuard>
  )
}

function JobTableRow({
  job,
  spaceId,
  refreshTick,
  canEdit,
  canCreate,
  diagnoserMap,
  creatorNameMap,
  onView,
  onEdit,
  onCopy,
  onDelete,
  onHistory,
}: {
  job: JobDTO
  spaceId: string
  refreshTick: number
  canEdit: boolean
  canCreate: boolean
  diagnoserMap: Map<string, Diagnoser>
  creatorNameMap: Map<string, string>
  onView: () => void
  onEdit: () => void
  onCopy: () => void
  onDelete: () => void
  onHistory: () => void
}) {
  const queryClient = useQueryClient()
  const { t } = useTranslation()
  const tasksQuery = useQuery({
    queryKey: ["diagnose-tasks", spaceId, job.uuid, "latest", refreshTick],
    queryFn: () => listTasks(spaceId, { job_uuid: job.uuid, limit: 10 }),
    refetchInterval: (q) => {
      const items = q.state.data?.items ?? []
      return items.some((task) => isUnfinished(task.status)) ? 10_000 : false
    },
  })
  const latestTask = useMemo(() => {
    return (tasksQuery.data?.items ?? [])
      .map(toTaskRecord)
      .sort((a, b) => getTimeValue(b.gmtCreate) - getTimeValue(a.gmtCreate))[0]
  }, [tasksQuery.data])
  const hasUnfinishedTask = (tasksQuery.data?.items ?? []).some((task) =>
    isUnfinished(task.status)
  )
  const coveredDimensions = getCoveredDimensions(job, diagnoserMap)
  const isRunningTask = latestTask ? isUnfinished(latestTask.status) : false
  const canManualTrigger = job.trigger_type === "manual"
  const schedule = getScheduleDisplay(job, t)
  const upcomingScheduledAt =
    !latestTask && job.trigger_type === "schedule"
      ? getNextScheduledTime(job.schedule_cron)
      : null

  const toggleMutation = useMutation({
    mutationFn: async () => {
      if (!canEdit) throw new Error(t("diagnose.job.defaultReadonly"))
      return updateJob(spaceId, job.uuid, {
        status: job.status === "enabled" ? "disabled" : "enabled",
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["diagnose-jobs"] })
      toast.info(
        job.status === "enabled"
          ? t("diagnose.job.disabledToast")
          : t("diagnose.job.enabledToast")
      )
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const triggerMutation = useMutation({
    mutationFn: async () => {
      if (!canEdit) throw new Error(t("diagnose.job.defaultReadonly"))
      return triggerJob(spaceId, job.uuid)
    },
    onSuccess: () => {
      toast.info(t("diagnose.job.triggeredToast"))
      queryClient.invalidateQueries({
        queryKey: ["diagnose-tasks", spaceId, job.uuid],
      })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const row = (
    <tr
      aria-readonly={!canEdit}
      className={cn(
        "cursor-pointer border-b border-border transition-colors hover:bg-accent"
      )}
      onClick={onView}
    >
      <TableCell className="max-w-[200px] text-blue-600 dark:text-blue-400">
        <div className="flex items-center gap-1.5">
          <span className="truncate">{job.name}</span>
          {isPlatformManagedJob(job) ? (
            <span className="inline-flex h-4 shrink-0 items-center rounded bg-muted/60 px-1.5 text-[11px] font-normal text-muted-foreground">
              {t("diagnose.job.builtin")}
            </span>
          ) : null}
        </div>
      </TableCell>
      <TableCell onClick={(e) => e.stopPropagation()}>
        <div className="flex w-fit items-center gap-1.5">
          <StatusSwitch
            checked={job.status === "enabled"}
            disabled={!canEdit || toggleMutation.isPending}
            onClick={() => toggleMutation.mutate()}
          />
          <span className="text-sm text-[var(--color-text-secondary)]">
            {job.status === "enabled"
              ? t("common.enabled")
              : t("common.disabled")}
          </span>
        </div>
      </TableCell>
      <TableCell>
        <div className="flex flex-col">
          <span className="text-sm leading-5 text-foreground">
            {schedule.trigger}
          </span>
          <span className="text-xs leading-5 text-muted-foreground">
            {schedule.detail}
          </span>
        </div>
      </TableCell>
      <TableCell>
        <CoverageDots covered={coveredDimensions} />
      </TableCell>
      <TableCell>
        {tasksQuery.isLoading ? (
          <span className="text-xs text-[var(--color-text-secondary)]">
            {t("common.loading")}
          </span>
        ) : latestTask ? (
          <div className="flex items-center gap-2">
            <span className="text-[var(--color-text-secondary)] tabular-nums">
              {formatNullableAbsoluteTime(latestTask.gmtCreate)}
            </span>
            <TaskStatusText
              status={latestTask.status}
              message={latestTask.msg}
            />
          </div>
        ) : upcomingScheduledAt ? (
          <div className="flex items-center gap-2">
            <span className="text-[var(--color-text-secondary)] tabular-nums">
              {formatAbsoluteTime(upcomingScheduledAt.toISOString())}
            </span>
            <PendingStartText />
          </div>
        ) : (
          <span className="text-muted-foreground">
            {job.trigger_type === "schedule"
              ? t("diagnose.job.awaitFirstRun")
              : "-"}
          </span>
        )}
      </TableCell>
      <TableCell>
        <div className="flex flex-col text-xs leading-5 text-muted-foreground">
          <span>{resolveJobActorName(job, "creator", creatorNameMap)}</span>
          <span>{resolveJobActorName(job, "modifier", creatorNameMap)}</span>
        </div>
      </TableCell>
      <TableCell>
        <div className="flex flex-col text-xs leading-5 text-muted-foreground tabular-nums">
          <span>{formatNullableAbsoluteTime(job.gmt_create)}</span>
          <span>{formatNullableAbsoluteTime(job.gmt_modified)}</span>
        </div>
      </TableCell>
      <TableCell className="text-right" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-end gap-3">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="size-7"
                onClick={onHistory}
              >
                <History className="size-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>
              {t("diagnose.job.executionHistory")}
            </TooltipContent>
          </Tooltip>
          {canEdit ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="inline-flex">
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-7"
                    disabled={
                      triggerMutation.isPending ||
                      !canManualTrigger ||
                      job.status !== "enabled" ||
                      isRunningTask
                    }
                    onClick={() => triggerMutation.mutate()}
                  >
                    {triggerMutation.isPending ? (
                      <Loader2 className="size-3.5 animate-spin" />
                    ) : (
                      <Zap className="size-3.5" />
                    )}
                  </Button>
                </span>
              </TooltipTrigger>
              <TooltipContent>
                {!canManualTrigger
                  ? t("diagnose.job.cannotTriggerManual")
                  : job.status !== "enabled"
                    ? t("diagnose.job.cannotTriggerDisabled")
                    : isRunningTask
                      ? t("diagnose.job.runningNow")
                      : t("diagnose.job.triggerNow")}
              </TooltipContent>
            </Tooltip>
          ) : (
            <Button variant="ghost" size="icon" className="size-7" disabled>
              <Zap className="size-3.5" />
            </Button>
          )}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="size-7"
                disabled={!canEdit}
              >
                <MoreHorizontal className="size-3.5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-28">
              <DropdownMenuItem onClick={onEdit}>
                <Pencil className="size-3.5" />
                {t("common.edit")}
              </DropdownMenuItem>
              {canCreate ? (
                <DropdownMenuItem onClick={onCopy}>
                  <Copy className="size-3.5" />
                  {t("common.copy")}
                </DropdownMenuItem>
              ) : (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="block">
                      <DropdownMenuItem disabled>
                        <Copy className="size-3.5" />
                        {t("common.copy")}
                      </DropdownMenuItem>
                    </span>
                  </TooltipTrigger>
                  <TooltipContent>
                    {t("diagnose.job.defaultReadonly")}
                  </TooltipContent>
                </Tooltip>
              )}
              {hasUnfinishedTask ? (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="block">
                      <DropdownMenuItem
                        className="text-destructive focus:text-destructive"
                        disabled
                      >
                        <Trash2 className="size-3.5" />
                        {t("common.delete")}
                      </DropdownMenuItem>
                    </span>
                  </TooltipTrigger>
                  <TooltipContent>
                    {t("diagnose.job.unfinishedTask")}
                  </TooltipContent>
                </Tooltip>
              ) : (
                <DropdownMenuItem
                  className="text-destructive focus:text-destructive"
                  onClick={onDelete}
                >
                  <Trash2 className="size-3.5" />
                  {t("common.delete")}
                </DropdownMenuItem>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </TableCell>
    </tr>
  )

  if (canEdit) return row

  return (
    <Tooltip>
      <TooltipTrigger asChild>{row}</TooltipTrigger>
      <TooltipContent>{t("diagnose.job.defaultReadonly")}</TooltipContent>
    </Tooltip>
  )
}

function isJobEditable(job: JobDTO, canCreateDiagnosisJob: boolean): boolean {
  return canCreateDiagnosisJob && !isPlatformManagedJob(job)
}

function TableHead({
  className,
  children,
}: {
  className?: string
  children: React.ReactNode
}) {
  return (
    <th
      className={cn(
        "border-b border-border bg-muted px-3 py-2 text-left text-xs font-semibold whitespace-nowrap text-muted-foreground",
        className
      )}
    >
      {children}
    </th>
  )
}

function TableCell({
  className,
  children,
  onClick,
}: {
  className?: string
  children: React.ReactNode
  onClick?: React.MouseEventHandler<HTMLTableCellElement>
}) {
  return (
    <td
      className={cn("px-3 py-3 align-middle whitespace-nowrap", className)}
      onClick={onClick}
    >
      {children}
    </td>
  )
}

function StatusSwitch({
  checked,
  disabled,
  onClick,
}: {
  checked: boolean
  disabled?: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      aria-pressed={checked}
      className={cn(
        "relative inline-flex h-5 w-9 items-center rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        checked ? "bg-primary" : "bg-muted-foreground/30"
      )}
      onClick={onClick}
    >
      <span
        className={cn(
          "inline-block size-4 rounded-full bg-background transition-transform",
          checked ? "translate-x-4" : "translate-x-0.5"
        )}
      />
    </button>
  )
}

function CoverageDots({ covered }: { covered: string[] }) {
  const { t } = useTranslation()
  const dimLabels: Record<string, string> = {
    outcome: t("diagnose.diagnoser.catOutcome"),
    process: t("diagnose.diagnoser.catProcess"),
    safety: t("diagnose.diagnoser.catSafety"),
    system: t("diagnose.diagnoser.catSystem"),
  }
  return (
    <div className="flex items-center gap-1">
      {DIM_ORDER.map((cat) => {
        const isCovered = covered.includes(cat)
        const label = dimLabels[cat]
        return (
          <Tooltip key={cat}>
            <TooltipTrigger asChild>
              <span
                aria-label={`${label}${isCovered ? t("diagnose.job.covered") : t("diagnose.job.uncovered")}`}
                className={cn(
                  "relative size-2 shrink-0 rounded-full before:absolute before:-inset-2 before:content-['']",
                  isCovered
                    ? "bg-[var(--color-success)]"
                    : "bg-muted-foreground/30"
                )}
              />
            </TooltipTrigger>
            <TooltipContent side="bottom">
              <span className="text-xs">
                {label}
                {isCovered
                  ? t("diagnose.job.coveredSuffix")
                  : t("diagnose.job.uncoveredSuffix")}
              </span>
            </TooltipContent>
          </Tooltip>
        )
      })}
    </div>
  )
}

function PendingStartText() {
  const { t } = useTranslation()
  return (
    <span className="inline-flex h-5 items-center rounded-full bg-[var(--color-bg-info)] px-2 text-xs font-medium text-[var(--color-text-secondary)]">
      {t("diagnose.job.pendingStart")}
    </span>
  )
}

function getCoveredDimensions(
  job: JobDTO,
  diagnoserMap: Map<string, Diagnoser>
): string[] {
  const covered = new Set<string>()
  job.diagnoser_uuids.forEach((uuid) => {
    const diagnoser = diagnoserMap.get(uuid)
    const candidate = diagnoser?.category || ""
    const normalized = candidate.toLowerCase()
    if (candidate.includes("安全") || normalized.includes("safety"))
      covered.add("safety")
    if (candidate.includes("过程") || normalized.includes("process"))
      covered.add("process")
    if (
      candidate.includes("结果") ||
      normalized.includes("result") ||
      normalized.includes("outcome")
    )
      covered.add("outcome")
    if (
      candidate.includes("系统") ||
      candidate.includes("性能") ||
      normalized.includes("system") ||
      normalized.includes("performance")
    )
      covered.add("system")
  })
  return Array.from(covered)
}

function resolveCreatorName(
  creator: string | null,
  creatorNameMap: Map<string, string>
): string {
  if (!creator) return "-"
  return creatorNameMap.get(creator) ?? creator
}

function resolveJobActorName(
  job: Pick<JobDTO, "space_uuid" | "spec" | "creator" | "modifier">,
  field: "creator" | "modifier",
  creatorNameMap: Map<string, string>
): string {
  if (isPlatformManagedJob(job)) return PLATFORM_MANAGED_ACTOR_NAME
  return resolveCreatorName(job[field], creatorNameMap)
}

function getScheduleDisplay(
  job: JobDTO,
  t: (k: string, o?: Record<string, unknown>) => string
): { trigger: string; detail: string } {
  if (job.trigger_type === "manual") {
    return {
      trigger: t("diagnose.job.scheduleManual"),
      detail: t("diagnose.job.scheduleManualDetail"),
    }
  }
  return {
    trigger: t("diagnose.job.scheduleScheduled"),
    detail: formatScheduleCronLabel(job.schedule_cron, t),
  }
}

function formatScheduleCronLabel(
  cron: string | null | undefined,
  t: (k: string, o?: Record<string, unknown>) => string
): string {
  if (!cron) return t("diagnose.job.cronLabel.pendingConfig")
  const parts = cron.trim().split(/\s+/)
  if (parts.length < 5) return t("diagnose.job.cronLabel.pendingConfig")
  const minutePart = parts[0]
  const hourPart = parts[1]
  if (hourPart === "*") {
    const minutes = parseCronMinutes(minutePart)
    const minuteFrequency = detectMinuteCronFrequency(minutes, t)
    if (minuteFrequency) return minuteFrequency
    return t("diagnose.job.cronLabel.everyHour")
  }
  if (hourPart === "*/3" || hourPart.endsWith("/3"))
    return t("diagnose.job.cronLabel.every3Hours")
  if (hourPart === "*/6" || hourPart.endsWith("/6"))
    return t("diagnose.job.cronLabel.every6Hours")
  if (hourPart === "*/12" || hourPart.endsWith("/12"))
    return t("diagnose.job.cronLabel.every12Hours")

  const hours = hourPart.split(",").map(parseCronHour)
  if (isRecurringCronHours(hours, 3))
    return t("diagnose.job.cronLabel.every3Hours")
  if (isRecurringCronHours(hours, 6))
    return t("diagnose.job.cronLabel.every6Hours")
  if (isRecurringCronHours(hours, 12))
    return t("diagnose.job.cronLabel.every12Hours")
  if (parseCronHour(hourPart) !== null) return t("diagnose.job.cronLabel.daily")
  return t("diagnose.job.cronLabel.pendingConfig")
}

function detectMinuteCronFrequency(
  minutes: number[],
  t: (k: string, o?: Record<string, unknown>) => string
): string | null {
  if (minutes.length === 60) return t("diagnose.job.cronLabel.every1Min")
  if (isRecurringCronMinutes(minutes, 2))
    return t("diagnose.job.cronLabel.every2Min")
  if (isRecurringCronMinutes(minutes, 3))
    return t("diagnose.job.cronLabel.every3Min")
  if (isRecurringCronMinutes(minutes, 5))
    return t("diagnose.job.cronLabel.every5Min")
  return null
}

function isRecurringCronMinutes(
  minutes: number[],
  interval: 2 | 3 | 5
): boolean {
  if (minutes.length === 0) return false
  const remainder = minutes[0] % interval
  return (
    minutes.length === 60 / interval &&
    minutes.every(
      (minute) => Number.isInteger(minute) && minute >= 0 && minute <= 59
    ) &&
    minutes.every((minute) => minute % interval === remainder)
  )
}

function parseCronHour(value: string): number | null {
  if (!/^\d{1,2}$/.test(value)) return null
  const hour = Number.parseInt(value, 10)
  return hour >= 0 && hour <= 23 ? hour : null
}

function isRecurringCronHours(
  hours: Array<number | null>,
  interval: 3 | 6 | 12
): boolean {
  return (
    hours.length === 24 / interval &&
    hours.every((hour) => hour !== null) &&
    hours.every((hour, index) => {
      if (hour === null) return false
      const prev = hours[index - 1]
      return index === 0 || (prev !== null && hour === (prev + interval) % 24)
    })
  )
}

function getNextScheduledTime(
  cron: string | null | undefined,
  now = new Date()
): Date | null {
  if (!cron) return null
  const parts = cron.trim().split(/\s+/)
  if (parts.length < 5) return null
  const minutes = parseCronMinutes(parts[0])
  const hours = parseCronHours(parts[1])
  if (minutes.length === 0 || hours.length === 0) return null

  const sortedMinutes = [...new Set(minutes)].sort((a, b) => a - b)
  const sortedHours = [...new Set(hours)].sort((a, b) => a - b)
  for (let dayOffset = 0; dayOffset <= 2; dayOffset += 1) {
    for (const hour of sortedHours) {
      for (const minute of sortedMinutes) {
        const candidate = new Date(now)
        candidate.setDate(now.getDate() + dayOffset)
        candidate.setHours(hour, minute, 0, 0)
        if (candidate.getTime() >= now.getTime()) return candidate
      }
    }
  }
  return null
}

function parseCronMinutes(value: string): number[] {
  if (value === "*") return Array.from({ length: 60 }, (_, minute) => minute)
  const stepped = value.match(/^(\*|\d{1,2})\/(\d{1,2})$/)
  if (stepped) {
    const start = stepped[1] === "*" ? 0 : Number.parseInt(stepped[1], 10)
    const step = Number.parseInt(stepped[2], 10)
    if (start < 0 || start > 59 || step <= 0 || step > 59) return []
    const normalizedStart = start % step
    return Array.from(
      { length: Math.ceil((60 - normalizedStart) / step) },
      (_, index) => normalizedStart + index * step
    ).filter((minute) => minute <= 59)
  }
  if (value.includes(",")) {
    const minutes = value.split(",").map(parseCronMinute)
    return minutes.every((minute) => minute !== null)
      ? (minutes as number[])
      : []
  }
  const minute = parseCronMinute(value)
  return minute === null ? [] : [minute]
}

function parseCronMinute(value: string): number | null {
  if (!/^\d{1,2}$/.test(value)) return null
  const minute = Number.parseInt(value, 10)
  return minute >= 0 && minute <= 59 ? minute : null
}

function parseCronHours(value: string): number[] {
  if (value === "*") return Array.from({ length: 24 }, (_, hour) => hour)
  const stepped = value.match(/^(\*|\d{1,2})\/(\d{1,2})$/)
  if (stepped) {
    const start = stepped[1] === "*" ? 0 : Number.parseInt(stepped[1], 10)
    const step = Number.parseInt(stepped[2], 10)
    if (start < 0 || start > 23 || step <= 0 || step > 23) return []
    return Array.from(
      { length: Math.ceil((24 - start) / step) },
      (_, index) => start + index * step
    ).filter((hour) => hour <= 23)
  }
  if (value.includes(",")) {
    const hours = value.split(",").map(parseCronHour)
    return hours.every((hour) => hour !== null) ? (hours as number[]) : []
  }
  const hour = parseCronHour(value)
  return hour === null ? [] : [hour]
}

function isUnfinished(status: TaskRecord["status"]): boolean {
  return status !== "succeeded" && status !== "failed" && status !== "canceled"
}

function formatNullableAbsoluteTime(iso: string | null | undefined): string {
  return iso ? formatAbsoluteTime(iso) : "-"
}

async function deleteDatasetIfExists(
  spaceId: string,
  datasetUuid: string
): Promise<void> {
  try {
    await deleteDataset(spaceId, datasetUuid)
  } catch (error) {
    if (isDatasetNotFoundError(error)) return
    throw error
  }
}

function isDatasetNotFoundError(error: unknown): boolean {
  if (getErrorStatusCode(error) === 404) return true
  return getErrorBodyCode(error) === "not_found"
}

function getErrorStatusCode(error: unknown): number | undefined {
  if (error instanceof ApiBusinessError) return error.statusCode
  if (!isRecord(error)) return undefined
  const statusCode = error.statusCode ?? error.status
  return typeof statusCode === "number" ? statusCode : undefined
}

function getErrorBodyCode(error: unknown): string | undefined {
  if (error instanceof ApiBusinessError) return String(error.code)
  if (!isRecord(error)) return undefined
  const response = isRecord(error.response) ? error.response : undefined
  const data = response && isRecord(response.data) ? response.data : undefined
  const code = data?.code ?? error.code
  return typeof code === "string" ? code : undefined
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object"
}

function getTimeValue(iso: string | null | undefined): number {
  if (!iso) return 0
  const time = new Date(iso).getTime()
  return Number.isNaN(time) ? 0 : time
}

function formatAbsoluteTime(iso: string): string {
  return new Date(iso).toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  })
}
