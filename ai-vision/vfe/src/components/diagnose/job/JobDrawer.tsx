import { useEffect, useMemo, useState } from "react"
import type { TFunction } from "i18next"
import { useTranslation } from "react-i18next"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  ChevronDown,
  ChevronUp,
  CircleHelp,
  Loader2,
  Pencil,
} from "lucide-react"
import { toast } from "sonner"

import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

import {
  createJob,
  fetchDatasetDefaultSpec,
  getDataset,
  getDiagnoser,
  getJob,
  listDatasets,
  listDiagnosers,
  toDiagnoser,
  updateJob,
} from "../diagnoseApi"
import type { CreateJobPayload, DatasetDTO, Diagnoser, JobDTO } from "../types"
import {
  SCHEDULE_FREQUENCY_OPTIONS,
  buildCreatePayload,
  canStepExecTime,
  clampExecTime,
  getDefaultFirstExecutionTime,
  getDatasetLimit,
  getDatasetTimeOffset,
  getDatasetTimeRange,
  getDatasetWhere,
  getDefaultExecTime,
  getInitialForm,
  getSpecLimit,
  getSpecTimeOffset,
  getSpecTimeRange,
  getSpecWhere,
  isMinuteScheduleFrequency,
  isValidTraceDuration,
  normalizeDatasetTemplate,
  normalizeTraceDuration,
  scheduleToCron,
  stepExecTime,
  type DrawerMode,
  type JobFormData,
  type ScheduleFrequency,
} from "./jobFormModel"
import { prepareDatasetForSubmit } from "./jobDatasetSubmission"
import {
  DetectorSelector,
  Field,
  JobDetailView,
  SectionTitle,
} from "./JobDrawerSections"

interface JobDrawerProps {
  open: boolean
  mode: DrawerMode
  spaceId: string
  agentUuid: string
  jobUuid: string | null
  prefill?: Partial<CreateJobPayload>
  existingJobs: JobDTO[]
  creatorNameMap: Map<string, string>
  canEdit: boolean
  onOpenChange: (open: boolean) => void
  onSwitchToEdit: () => void
}

function durationToMilliseconds(value: string): number {
  const match = value.match(/^(\d+)(m|h|d)$/)
  if (!match) return 0
  const [, amount, unit] = match
  const multiplier =
    unit === "m" ? 60_000 : unit === "h" ? 3_600_000 : 86_400_000
  return Number(amount) * multiplier
}

function formatWindowTime(value: Date, includeDate: boolean): string {
  const time = `${String(value.getHours()).padStart(2, "0")}:${String(
    value.getMinutes()
  ).padStart(2, "0")}`
  if (!includeDate) return time
  return `${String(value.getMonth() + 1).padStart(2, "0")}-${String(
    value.getDate()
  ).padStart(2, "0")} ${time}`
}

function getTraceWindowExample(form: JobFormData, t: TFunction): string {
  const formatDuration = (value: string) => {
    const match = value.match(/^(\d+)(m|h|d)$/)
    return match
      ? `${match[1]} ${t(`diagnose.job.durationUnit.${match[2]}`)}`
      : value
  }
  const rangeLabel = formatDuration(form.timeRange)
  const offsetLabel = formatDuration(form.timeOffset)

  if (
    !isValidTraceDuration(form.timeRange) ||
    !isValidTraceDuration(form.timeOffset)
  ) {
    return t("diagnose.job.drawer.windowInvalid", { count: form.maxTraceCount })
  }

  const triggerAt = new Date(form.firstExecutionTime.replace(" ", "T"))
  if (Number.isNaN(triggerAt.getTime())) {
    return t("diagnose.job.drawer.windowNoTime", {
      offset: offsetLabel,
      range: rangeLabel,
      count: form.maxTraceCount,
    })
  }

  const endAt = new Date(
    triggerAt.getTime() - durationToMilliseconds(form.timeOffset)
  )
  const startAt = new Date(
    endAt.getTime() - durationToMilliseconds(form.timeRange)
  )
  const includeDate = startAt.toDateString() !== endAt.toDateString()
  return t("diagnose.job.drawer.windowExample", {
    trigger: formatWindowTime(triggerAt, false),
    start: formatWindowTime(startAt, includeDate),
    end: formatWindowTime(endAt, includeDate),
    count: form.maxTraceCount,
  })
}

function DurationUnitHelp() {
  const { t } = useTranslation()
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          role="img"
          aria-label={t("diagnose.job.drawer.durationHelpLabel")}
          tabIndex={0}
          className="inline-flex cursor-help text-muted-foreground"
        >
          <CircleHelp className="size-3.5" />
        </span>
      </TooltipTrigger>
      <TooltipContent>{t("diagnose.job.drawer.durationHelp")}</TooltipContent>
    </Tooltip>
  )
}

function FormSwitch({
  checked,
  disabled,
  onCheckedChange,
}: {
  checked: boolean
  disabled?: boolean
  onCheckedChange: (checked: boolean) => void
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50",
        checked ? "bg-primary" : "bg-muted-foreground/30"
      )}
      onClick={() => onCheckedChange(!checked)}
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

export function JobDrawer({
  open,
  mode,
  spaceId,
  agentUuid,
  jobUuid,
  prefill,
  existingJobs,
  creatorNameMap,
  canEdit,
  onOpenChange,
  onSwitchToEdit,
}: JobDrawerProps) {
  const queryClient = useQueryClient()
  const { t } = useTranslation()
  const readonly = mode === "view" || !canEdit
  const enabled = open && mode !== "create" && !!jobUuid

  const jobQuery = useQuery({
    queryKey: ["diagnose-job", spaceId, jobUuid],
    queryFn: () => getJob(spaceId, jobUuid!),
    enabled,
  })

  const diagnosersQuery = useQuery({
    queryKey: ["diagnosers", spaceId, agentUuid],
    queryFn: () => listDiagnosers(spaceId, agentUuid),
    enabled: open && !!spaceId && !!agentUuid,
  })

  const datasetsQuery = useQuery({
    queryKey: ["diagnose-datasets", spaceId, agentUuid],
    queryFn: () => listDatasets(spaceId, agentUuid),
    enabled: open && !!spaceId && !!agentUuid,
  })

  const defaultSpecQuery = useQuery({
    queryKey: ["diagnose-dataset-default-spec", spaceId, agentUuid],
    queryFn: () => fetchDatasetDefaultSpec(spaceId, agentUuid),
    enabled: open && mode === "create" && !!spaceId && !!agentUuid,
    staleTime: 5 * 60 * 1000,
  })

  const datasets = useMemo(
    () => datasetsQuery.data?.items ?? [],
    [datasetsQuery.data?.items]
  )
  const job = jobQuery.data ?? null
  const editableResourceSpaceUuid =
    mode === "edit" && job ? job.space_uuid : spaceId

  const [form, setForm] = useState<JobFormData>(() =>
    getInitialForm(null, prefill)
  )
  const [nameError, setNameError] = useState("")
  const defaultExecTime = useMemo(() => getDefaultExecTime(), [])
  const isMinuteFrequency = isMinuteScheduleFrequency(form.frequency)
  const canIncreaseExecTime = canStepExecTime(
    form.firstExecutionTime,
    1,
    defaultExecTime,
    form.frequency
  )
  const canDecreaseExecTime = canStepExecTime(
    form.firstExecutionTime,
    -1,
    defaultExecTime,
    form.frequency
  )
  const canUseIncreaseExecTime = !isMinuteFrequency && canIncreaseExecTime
  const canUseDecreaseExecTime = !isMinuteFrequency && canDecreaseExecTime

  const jobDiagnosersQuery = useQuery({
    queryKey: [
      "diagnose-job-diagnosers",
      spaceId,
      job?.uuid,
      job?.diagnoser_uuids.join(","),
    ],
    queryFn: async () => {
      const ids = job?.diagnoser_uuids ?? []
      const results = await Promise.allSettled(
        ids.map((uuid) => getDiagnoser(spaceId, uuid))
      )
      return results.flatMap((result) =>
        result.status === "fulfilled" ? [result.value] : []
      )
    },
    enabled:
      open && mode !== "create" && !!spaceId && !!job?.diagnoser_uuids.length,
  })

  const linkedDatasetQuery = useQuery({
    queryKey: ["diagnose-dataset", spaceId, job?.dataset_uuid],
    queryFn: () => getDataset(spaceId, job!.dataset_uuid),
    enabled: open && mode !== "create" && !!spaceId && !!job?.dataset_uuid,
  })

  const diagnosers: Diagnoser[] = useMemo(() => {
    const visibleDtos = (diagnosersQuery.data?.items ?? []).filter(
      (dto) => mode === "view" || dto.space_uuid === editableResourceSpaceUuid
    )
    const byUuid = new Map(visibleDtos.map((dto) => [dto.uuid, dto]))
    ;(jobDiagnosersQuery.data ?? []).forEach((dto) => byUuid.set(dto.uuid, dto))
    return Array.from(byUuid.values()).map((dto) =>
      toDiagnoser(dto, undefined, t)
    )
  }, [
    diagnosersQuery.data,
    editableResourceSpaceUuid,
    jobDiagnosersQuery.data,
    mode,
    t,
  ])

  const selectedDiagnoserUuids = form.diagnoserUuids

  useEffect(() => {
    if (!open) return
    let cancelled = false
    queueMicrotask(() => {
      if (cancelled) return
      setForm(getInitialForm(job, prefill))
      setNameError("")
    })
    return () => {
      cancelled = true
    }
  }, [job, open, prefill])

  const defaultDataset = useMemo(
    () =>
      datasets.find((dataset) => dataset.name === "default") ??
      datasets[0] ??
      null,
    [datasets]
  )
  const selectedDataset =
    mode === "create"
      ? null
      : form.datasetUuid
        ? (linkedDatasetQuery.data ??
          datasets.find((dataset) => dataset.uuid === form.datasetUuid) ??
          null)
        : defaultDataset
  const defaultDatasetTemplate = useMemo(
    () => normalizeDatasetTemplate(defaultSpecQuery.data),
    [defaultSpecQuery.data]
  )

  useEffect(() => {
    if (!open || mode === "create" || !defaultDataset) return
    let cancelled = false
    queueMicrotask(() => {
      if (cancelled) return
      setForm((prev) => {
        if (prev.datasetUuid) return prev
        return {
          ...prev,
          datasetUuid: defaultDataset.uuid,
          maxTraceCount: getDatasetLimit(defaultDataset),
          timeRange: getDatasetTimeRange(defaultDataset),
          timeOffset: getDatasetTimeOffset(defaultDataset),
          whereFilter: getDatasetWhere(defaultDataset),
        }
      })
    })
    return () => {
      cancelled = true
    }
  }, [defaultDataset, mode, open])

  useEffect(() => {
    if (!open || mode === "create" || !selectedDataset) return
    let cancelled = false
    queueMicrotask(() => {
      if (cancelled) return
      setForm((prev) => ({
        ...prev,
        maxTraceCount: getDatasetLimit(selectedDataset),
        timeRange: getDatasetTimeRange(selectedDataset),
        timeOffset: getDatasetTimeOffset(selectedDataset),
        whereFilter: getDatasetWhere(selectedDataset),
      }))
    })
    return () => {
      cancelled = true
    }
  }, [mode, open, selectedDataset])

  useEffect(() => {
    if (!open || mode !== "create" || !defaultDatasetTemplate) return
    let cancelled = false
    queueMicrotask(() => {
      if (cancelled) return
      setForm((prev) => ({
        ...prev,
        datasetUuid: "",
        maxTraceCount: getSpecLimit(defaultDatasetTemplate.spec),
        timeRange: getSpecTimeRange(defaultDatasetTemplate.spec),
        timeOffset: getSpecTimeOffset(defaultDatasetTemplate.spec),
        whereFilter: getSpecWhere(defaultDatasetTemplate.spec),
      }))
    })
    return () => {
      cancelled = true
    }
  }, [defaultDatasetTemplate, mode, open])

  const errors = useMemo(() => {
    const next: Record<string, string> = {}
    if (form.name.length > 64) next.name = t("diagnose.job.drawer.maxName")
    if (form.description.length > 200)
      next.description = t("diagnose.job.drawer.maxDescription")
    if (!isValidTraceDuration(form.timeRange))
      next.timeRange = t("diagnose.job.drawer.durationInvalid")
    if (!isValidTraceDuration(form.timeOffset))
      next.timeOffset = t("diagnose.job.drawer.durationInvalid")

    if (nameError) next.name = nameError
    return next
  }, [form, nameError, t])
  const hasValidMaxTraceCount =
    Number.isInteger(form.maxTraceCount) && form.maxTraceCount > 0
  const hasDatasetConfig =
    mode === "create"
      ? !!defaultDatasetTemplate && !defaultSpecQuery.isError
      : !!selectedDataset
  const canSubmit =
    form.name.trim() !== "" &&
    selectedDiagnoserUuids.length > 0 &&
    hasDatasetConfig &&
    hasValidMaxTraceCount &&
    Object.keys(errors).length === 0

  const createMutation = useMutation({
    mutationFn: async () => {
      if (!canEdit) throw new Error(t("diagnose.job.defaultReadonly"))
      const dataset = await prepareDatasetForSubmit({
        mode,
        spaceId,
        agentUuid,
        form,
        defaultDatasetTemplate,
        selectedDataset,
        usedDatasetUuids: existingJobs.map((job) => job.dataset_uuid),
      })
      return createJob(
        spaceId,
        agentUuid,
        buildCreatePayload(
          { ...form, diagnoserUuids: selectedDiagnoserUuids },
          dataset.uuid
        )
      )
    },
    onSuccess: () => {
      toast.success(t("diagnose.job.drawer.created"))
      queryClient.invalidateQueries({ queryKey: ["diagnose-jobs"] })
      queryClient.invalidateQueries({ queryKey: ["diagnose-datasets"] })
      onOpenChange(false)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const updateMutation = useMutation({
    mutationFn: async () => {
      if (!canEdit) throw new Error(t("diagnose.job.defaultReadonly"))
      const dataset = await prepareDatasetForSubmit({
        mode,
        spaceId,
        agentUuid,
        form,
        defaultDatasetTemplate,
        selectedDataset,
        usedDatasetUuids: existingJobs.map((job) => job.dataset_uuid),
      })
      const updatedJob = await updateJob(spaceId, jobUuid!, {
        name: form.name.trim(),
        description: form.description.trim() || undefined,
        status: form.status,
        trigger_type: form.triggerType,
        schedule_cron:
          form.triggerType === "schedule"
            ? scheduleToCron(form.frequency, form.firstExecutionTime)
            : undefined,
        dataset_uuid: dataset.uuid,
        diagnoser_uuids: form.diagnoserUuids,
        spec: { save_badcase_only: form.saveBadcaseOnly },
      })
      return { dataset, job: updatedJob }
    },
    onSuccess: ({ dataset, job: updatedJob }) => {
      toast.success(t("diagnose.job.drawer.updated"))
      queryClient.setQueryData(
        ["diagnose-job", spaceId, updatedJob.uuid],
        updatedJob
      )
      queryClient.setQueryData<{ items: JobDTO[]; total: number }>(
        ["diagnose-jobs", spaceId, agentUuid],
        (current) => replaceListItem(current, updatedJob)
      )
      queryClient.setQueryData(
        ["diagnose-dataset", spaceId, dataset.uuid],
        dataset
      )
      queryClient.setQueryData<{ items: DatasetDTO[]; total: number }>(
        ["diagnose-datasets", spaceId, agentUuid],
        (current) => replaceListItem(current, dataset)
      )
      void queryClient.invalidateQueries({ queryKey: ["diagnose-jobs"] })
      void queryClient.invalidateQueries({ queryKey: ["diagnose-datasets"] })
      onOpenChange(false)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const submitPending = createMutation.isPending || updateMutation.isPending
  const title =
    mode === "create"
      ? t("diagnose.job.drawer.titleCreate")
      : mode === "edit"
        ? t("diagnose.job.drawer.titleEdit")
        : t("diagnose.job.drawer.titleView")
  const formControlTextClass = "text-xs"

  const handleSubmit = () => {
    if (!canEdit) return
    const targetSpaceUuid = mode === "edit" && job ? job.space_uuid : spaceId
    const duplicate = existingJobs.some(
      (item) =>
        item.space_uuid === targetSpaceUuid &&
        item.name === form.name.trim() &&
        item.uuid !== job?.uuid
    )
    if (duplicate) {
      setNameError(t("diagnose.job.drawer.duplicate"))
      toast.error(t("diagnose.job.drawer.duplicate"))
      return
    }
    setNameError("")
    if (!canSubmit) return
    if (mode === "create") createMutation.mutate()
    else if (mode === "edit") updateMutation.mutate()
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        showCloseButton={false}
        className={cn(
          "flex flex-col gap-0 overflow-hidden p-0 data-[side=right]:w-full",
          readonly
            ? "data-[side=right]:sm:max-w-[max(640px,50vw)]"
            : "data-[side=right]:sm:max-w-[clamp(640px,50vw,720px)]"
        )}
      >
        <div className="shrink-0 border-b px-4 py-4 sm:px-6">
          <div className="flex items-start justify-between gap-2">
            <SheetTitle className="text-xl font-medium">{title}</SheetTitle>
            <Button
              variant="ghost"
              size="sm"
              className="shrink-0"
              onClick={() => onOpenChange(false)}
            >
              {t("diagnose.job.drawer.close")}
            </Button>
          </div>
          {mode === "view" && job ? (
            <p className="mt-1 font-mono text-xs text-muted-foreground">
              {t("diagnose.job.history.jobId")}: {job.uuid.slice(0, 12)}
            </p>
          ) : null}
        </div>

        <div className="min-h-0 min-w-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6">
          {jobQuery.isLoading && mode !== "create" ? (
            <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              {t("diagnose.job.drawer.loading")}
            </div>
          ) : mode !== "create" && !job ? (
            <div className="rounded-lg border border-dashed p-4 text-center text-xs text-muted-foreground">
              {t("diagnose.job.drawer.missing")}
            </div>
          ) : readonly && job ? (
            <JobDetailView
              job={job}
              form={form}
              diagnosers={diagnosers}
              diagnosersLoading={
                diagnosersQuery.isLoading || jobDiagnosersQuery.isLoading
              }
              creatorNameMap={creatorNameMap}
            />
          ) : (
            <div className="flex flex-col gap-5">
              <SectionTitle>{t("diagnose.job.drawer.basic")}</SectionTitle>

              <Field
                label={t("diagnose.job.drawer.name")}
                required={!readonly}
                error={errors.name}
              >
                <Input
                  value={form.name}
                  disabled={readonly}
                  maxLength={64}
                  placeholder={t("diagnose.job.drawer.namePlaceholder")}
                  className={formControlTextClass}
                  onChange={(e) => {
                    const name = e.currentTarget.value
                    setForm((prev) => ({ ...prev, name }))
                    setNameError("")
                  }}
                />
              </Field>

              <Field
                label={t("diagnose.common.description", {
                  defaultValue: "描述",
                })}
                error={errors.description}
              >
                <div className="relative">
                  <Textarea
                    value={form.description}
                    disabled={readonly}
                    rows={1}
                    maxLength={200}
                    placeholder={t(
                      "diagnose.job.drawer.descriptionPlaceholder"
                    )}
                    className={cn("min-h-0 resize-y", formControlTextClass)}
                    onChange={(e) => {
                      const description = e.currentTarget.value
                      setForm((prev) => ({
                        ...prev,
                        description,
                      }))
                    }}
                  />
                  {!readonly ? (
                    <span className="pointer-events-none absolute right-3 bottom-2 text-xs text-muted-foreground/60">
                      {form.description.length}/200
                    </span>
                  ) : null}
                </div>
              </Field>

              <SectionTitle>
                {t("diagnose.job.drawer.detectorConfig")}
              </SectionTitle>
              <DetectorSelector
                selectedIds={selectedDiagnoserUuids}
                diagnosers={diagnosers}
                loading={
                  diagnosersQuery.isLoading || jobDiagnosersQuery.isLoading
                }
                readonly={readonly}
                onToggle={(uuid) =>
                  setForm((prev) => ({
                    ...prev,
                    diagnoserUuids: prev.diagnoserUuids.includes(uuid)
                      ? prev.diagnoserUuids.filter((item) => item !== uuid)
                      : [...prev.diagnoserUuids, uuid],
                  }))
                }
                onToggleGroup={(uuids, checked) =>
                  setForm((prev) => {
                    const next = new Set(prev.diagnoserUuids)
                    uuids.forEach((uuid) => {
                      if (checked) next.add(uuid)
                      else next.delete(uuid)
                    })
                    return { ...prev, diagnoserUuids: [...next] }
                  })
                }
              />
              <div className="flex items-center gap-3">
                <span className="text-xs font-medium text-muted-foreground">
                  {t("diagnose.job.drawer.saveBadcase")}
                </span>
                <FormSwitch
                  checked={form.saveBadcaseOnly}
                  disabled={readonly}
                  onCheckedChange={(checked) =>
                    setForm((prev) => ({
                      ...prev,
                      saveBadcaseOnly: checked,
                    }))
                  }
                />
              </div>

              <SectionTitle>{t("diagnose.job.drawer.data")}</SectionTitle>
              {mode === "create" && defaultSpecQuery.isLoading && (
                <p role="status" className="text-xs text-muted-foreground">
                  {t("diagnose.job.loadingDefaultSpec")}
                </p>
              )}
              {mode === "create" && defaultSpecQuery.isError && (
                <Alert variant="destructive">
                  <AlertDescription className="flex flex-wrap items-center justify-between gap-3">
                    <span>
                      {t("diagnose.job.defaultSpecFailed", {
                        message: defaultSpecQuery.error.message,
                      })}
                    </span>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={defaultSpecQuery.isFetching}
                      onClick={() => void defaultSpecQuery.refetch()}
                    >
                      {t("common.retry")}
                    </Button>
                  </AlertDescription>
                </Alert>
              )}
              <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
                <Field
                  label={t("diagnose.job.drawer.maxTrace")}
                  required={!readonly}
                  error={errors.maxTraceCount}
                >
                  <Input
                    type="number"
                    step={1}
                    inputMode="numeric"
                    value={form.maxTraceCount || ""}
                    disabled={readonly}
                    className="w-[154px]"
                    onChange={(event) => {
                      const value = event.currentTarget.valueAsNumber
                      setForm((prev) => ({
                        ...prev,
                        maxTraceCount: Number.isFinite(value) ? value : 0,
                      }))
                    }}
                  />
                </Field>
                <Field
                  label={t("diagnose.job.drawer.range")}
                  labelSuffix={<DurationUnitHelp />}
                  required={!readonly}
                  error={errors.timeRange}
                >
                  <Input
                    value={form.timeRange}
                    disabled={readonly}
                    maxLength={12}
                    spellCheck={false}
                    autoComplete="off"
                    placeholder={t("diagnose.job.drawer.durationExample30")}
                    className="w-[154px] font-mono"
                    onChange={(event) => {
                      const value = event.currentTarget.value
                      setForm((prev) => ({
                        ...prev,
                        timeRange: value,
                      }))
                    }}
                    onBlur={() =>
                      setForm((prev) => ({
                        ...prev,
                        timeRange: normalizeTraceDuration(prev.timeRange),
                      }))
                    }
                  />
                </Field>

                <Field
                  label={t("diagnose.job.drawer.delay")}
                  labelSuffix={<DurationUnitHelp />}
                  required={!readonly}
                  error={errors.timeOffset}
                >
                  <Input
                    value={form.timeOffset}
                    disabled={readonly}
                    maxLength={12}
                    spellCheck={false}
                    autoComplete="off"
                    placeholder={t("diagnose.job.drawer.durationExample1h")}
                    className="w-[154px] font-mono"
                    onChange={(event) => {
                      const value = event.currentTarget.value
                      setForm((prev) => ({
                        ...prev,
                        timeOffset: value,
                      }))
                    }}
                    onBlur={() =>
                      setForm((prev) => ({
                        ...prev,
                        timeOffset: normalizeTraceDuration(prev.timeOffset),
                      }))
                    }
                  />
                </Field>
              </div>
              <p className="-mt-2 text-xs leading-5 text-muted-foreground">
                {getTraceWindowExample(form, t)}
              </p>
              <Field label={t("diagnose.job.drawer.filter")}>
                <Textarea
                  value={form.whereFilter}
                  disabled={readonly}
                  rows={2}
                  placeholder={t("diagnose.job.drawer.filterPlaceholder")}
                  className={cn("resize-y", formControlTextClass)}
                  onChange={(e) => {
                    const whereFilter = e.currentTarget.value
                    setForm((prev) => ({
                      ...prev,
                      whereFilter,
                    }))
                  }}
                />
              </Field>

              <SectionTitle>
                {t("diagnose.job.drawer.scheduleSection")}
              </SectionTitle>
              <Field
                label={t("diagnose.job.drawer.triggerMode")}
                required={!readonly}
              >
                <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                  {(["manual", "schedule"] as const).map((item) => (
                    <label
                      key={item}
                      className={cn(
                        "flex items-center gap-2",
                        !readonly && "cursor-pointer"
                      )}
                    >
                      <input
                        type="radio"
                        name="triggerType"
                        checked={form.triggerType === item}
                        disabled={readonly}
                        className="accent-primary"
                        onChange={() =>
                          setForm((prev) => ({
                            ...prev,
                            triggerType: item,
                          }))
                        }
                      />
                      <span className="text-sm">
                        {item === "manual"
                          ? t("diagnose.job.drawer.manual")
                          : t("diagnose.job.drawer.scheduled")}
                      </span>
                    </label>
                  ))}
                </div>
              </Field>

              {form.triggerType === "schedule" ? (
                <div className="flex flex-wrap items-start gap-x-6 gap-y-3">
                  <Field label={t("diagnose.job.drawer.frequency")}>
                    <Select
                      value={form.frequency}
                      disabled={readonly}
                      onValueChange={(value) =>
                        setForm((prev) => {
                          const frequency = value as ScheduleFrequency
                          return {
                            ...prev,
                            frequency,
                            firstExecutionTime: isMinuteScheduleFrequency(
                              frequency
                            )
                              ? getDefaultFirstExecutionTime(frequency)
                              : clampExecTime(
                                  prev.firstExecutionTime,
                                  frequency,
                                  defaultExecTime
                                ),
                          }
                        })
                      }
                    >
                      <SelectTrigger className="w-[140px]">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent position="popper">
                        {SCHEDULE_FREQUENCY_OPTIONS.map((option) => (
                          <SelectItem key={option.value} value={option.value}>
                            {t(`diagnose.job.frequency.${option.value}`)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </Field>
                  {!readonly ? (
                    <Field
                      label={t("diagnose.job.drawer.firstRun")}
                      required
                      hint={
                        isMinuteFrequency
                          ? undefined
                          : t("diagnose.job.drawer.firstRunHint")
                      }
                    >
                      <div className="inline-flex h-9 w-[220px] shrink-0 items-center rounded-md border border-input bg-background">
                        <span className="flex-1 px-3 text-sm tabular-nums">
                          {form.firstExecutionTime}
                        </span>
                        <div className="flex flex-col border-l border-input">
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <span className="flex h-[18px] w-7">
                                <button
                                  type="button"
                                  disabled={!canUseIncreaseExecTime}
                                  className="flex h-[18px] w-7 items-center justify-center text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:text-[var(--color-icon-disabled)] disabled:hover:bg-transparent disabled:hover:text-[var(--color-icon-disabled)]"
                                  onClick={() =>
                                    setForm((prev) => ({
                                      ...prev,
                                      firstExecutionTime: stepExecTime(
                                        prev.firstExecutionTime,
                                        1,
                                        defaultExecTime,
                                        prev.frequency
                                      ),
                                    }))
                                  }
                                >
                                  <ChevronUp className="size-3" />
                                </button>
                              </span>
                            </TooltipTrigger>
                            {!isMinuteFrequency && !canIncreaseExecTime ? (
                              <TooltipContent>
                                {t("diagnose.job.drawer.outOfRange")}
                              </TooltipContent>
                            ) : null}
                          </Tooltip>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <span className="flex h-[18px] w-7 border-t border-input">
                                <button
                                  type="button"
                                  disabled={!canUseDecreaseExecTime}
                                  className="flex h-[18px] w-7 items-center justify-center text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:text-[var(--color-icon-disabled)] disabled:hover:bg-transparent disabled:hover:text-[var(--color-icon-disabled)]"
                                  onClick={() =>
                                    setForm((prev) => ({
                                      ...prev,
                                      firstExecutionTime: stepExecTime(
                                        prev.firstExecutionTime,
                                        -1,
                                        defaultExecTime,
                                        prev.frequency
                                      ),
                                    }))
                                  }
                                >
                                  <ChevronDown className="size-3" />
                                </button>
                              </span>
                            </TooltipTrigger>
                            {!isMinuteFrequency && !canDecreaseExecTime ? (
                              <TooltipContent>
                                {t("diagnose.job.drawer.outOfRange")}
                              </TooltipContent>
                            ) : null}
                          </Tooltip>
                        </div>
                      </div>
                    </Field>
                  ) : null}
                </div>
              ) : null}
            </div>
          )}
        </div>

        {!readonly || canEdit ? (
          <div className="flex shrink-0 flex-wrap justify-end gap-2 border-t px-4 py-4 sm:px-6">
            {readonly ? (
              <Button onClick={onSwitchToEdit}>
                <Pencil className="mr-1.5 size-3.5" />
                {t("diagnose.job.drawer.edit")}
              </Button>
            ) : (
              <>
                <Button
                  variant="outline"
                  onClick={() => onOpenChange(false)}
                  disabled={submitPending}
                >
                  {t("diagnose.job.drawer.cancel")}
                </Button>
                <Button
                  disabled={!canSubmit || submitPending}
                  onClick={handleSubmit}
                >
                  {submitPending ? (
                    <Loader2 className="mr-1 size-3 animate-spin" />
                  ) : null}
                  {mode === "create"
                    ? t("diagnose.job.drawer.create")
                    : t("diagnose.job.drawer.save")}
                </Button>
              </>
            )}
          </div>
        ) : null}
      </SheetContent>
    </Sheet>
  )
}

function replaceListItem<T extends { uuid: string }>(
  current: { items: T[]; total: number } | undefined,
  updated: T
): { items: T[]; total: number } | undefined {
  if (!current) return current
  return {
    ...current,
    items: current.items.map((item) =>
      item.uuid === updated.uuid ? updated : item
    ),
  }
}
