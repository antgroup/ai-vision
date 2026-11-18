import { genericBuiltinModelConfigAllowed } from "@/components/diagnose/specialistPolicy"
import { useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { useSearchParams } from "react-router-dom"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { Info, Plus, Loader2, Search } from "lucide-react"
import { toast } from "sonner"

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
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useAgent } from "@/contexts/AgentContext"
import { useSpace } from "@/contexts/SpaceContext"
import { useTimeFilter } from "@/contexts/TimeFilterContext"
import { SpaceType } from "@/lib/schemas/space"

import { SpecialistConfigurationForm } from "@/components/diagnose/diagnoser/SpecialistConfigurationForm"
import { DiagnoserCard } from "@/components/diagnose/diagnoser/DiagnoserCard"
import { DiagnoserDetailDrawer } from "@/components/diagnose/diagnoser/DiagnoserDetailDrawer"
import { BuiltinRuleConfigurationDialog } from "@/components/diagnose/diagnoser/BuiltinRuleConfigurationDialog"
import { ModelConnectionPickerModal } from "@/components/diagnose/diagnoser/ModelConnectionPickerModal"
import { savedCoreVariant } from "@/components/diagnose/diagnosisRunLabels"
import {
  builtinRuleSource,
  builtinRuleThreshold,
  canCopyBuiltinRule,
  displayAgentName,
  hasBuiltinDiagnosers,
} from "@/components/diagnose/builtinDiagnoserPolicy"
import {
  CreateDiagnoserModal,
  type DiagnoserEditTarget,
} from "@/components/diagnose/diagnoser/CreateDiagnoserModal"
import {
  listDiagnosers,
  listJobs,
  updateJob,
  deleteDiagnoser,
  listModelConnections,
  toDiagnoser,
  toDiagnoserDetailSpec,
} from "@/components/diagnose/diagnoseApi"
import {
  SCENARIO_LABEL_KEYS,
  type Diagnoser,
  type DiagnoserDTO,
  type JobDTO,
  type ModelConnection,
} from "@/components/diagnose/types"

const DIM_ORDER = ["outcome", "process", "safety", "system"] as const
type DimKey = (typeof DIM_ORDER)[number]

const ENGINE_OPTIONS = [{ value: "rule" }, { value: "llm" }] as const
type EngineValue = (typeof ENGINE_OPTIONS)[number]["value"]

const SOURCE_OPTIONS = [{ value: "builtin" }, { value: "custom" }] as const
type SourceValue = (typeof SOURCE_OPTIONS)[number]["value"]

const CARD_GRID =
  "grid grid-cols-[repeat(auto-fill,minmax(min(100%,_340px),1fr))] gap-5"

function toggleSetItem<T>(set: Set<T>, value: T): Set<T> {
  const next = new Set(set)
  if (next.has(value)) next.delete(value)
  else next.add(value)
  return next
}

function FilterChipGroup<T extends string>({
  title,
  options,
  labels,
  selected,
  onChange,
}: {
  title: string
  options: readonly T[]
  labels?: Record<string, string>
  selected: Set<T>
  onChange: (next: Set<T>) => void
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs font-medium text-foreground/70">{title}</span>
      <div className="flex flex-wrap gap-1.5">
        {options.map((opt) => {
          const checked = selected.has(opt)
          const label = labels?.[opt] ?? opt
          return (
            <button
              key={opt}
              type="button"
              onClick={() => onChange(toggleSetItem(selected, opt))}
              className={
                "inline-flex h-7 items-center rounded-full px-2.5 text-xs font-medium transition-all " +
                (checked
                  ? "bg-primary text-primary-foreground shadow-sm"
                  : "border border-border/80 bg-background text-muted-foreground hover:border-border hover:text-foreground")
              }
            >
              {label}
            </button>
          )
        })}
      </div>
    </div>
  )
}

function diagnoserCategory(d: Diagnoser): DimKey {
  const dim = d.category ?? "outcome"
  if (DIM_ORDER.includes(dim as DimKey)) return dim as DimKey
  return "outcome"
}

export function DiagnoserPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { currentAgent } = useAgent()
  const { currentSpace } = useSpace()
  const { refreshTick } = useTimeFilter()
  const [searchParams, setSearchParams] = useSearchParams()
  const detailUuid = searchParams.get("diagnoser")
  const setDetailUuid = (uuid: string | null) => {
    setSearchParams(
      (previous) => {
        const next = new URLSearchParams(previous)
        if (uuid) next.set("diagnoser", uuid)
        else next.delete("diagnoser")
        return next
      },
      { replace: true }
    )
  }

  const dimLabels = useMemo<Record<string, string>>(
    () => ({
      outcome: t("diagnose.diagnoser.catOutcome"),
      process: t("diagnose.diagnoser.catProcess"),
      safety: t("diagnose.diagnoser.catSafety"),
      system: t("diagnose.diagnoser.catSystem"),
    }),
    [t]
  )
  const engineLabels = useMemo<Record<string, string>>(
    () => ({ rule: t("diagnose.diagnoser.engineRule"), llm: "LLM" }),
    [t]
  )
  const sourceLabels = useMemo<Record<string, string>>(
    () => ({
      builtin: t("diagnose.diagnoser.sourceOfficial"),
      custom: t("diagnose.diagnoser.sourceCustom"),
    }),
    [t]
  )
  const spaceId = currentSpace?.id ?? ""
  const agentUuid = currentAgent?.uuid ?? ""
  const noBuiltinDiagnosers = !hasBuiltinDiagnosers(currentAgent?.agent_product)
  const agentDisplayName =
    displayAgentName(
      currentAgent?.agent_name,
      currentAgent?.agent_product,
      currentAgent?.display_name
    ) || t("diagnose.currentChannel")
  const canManageDiagnosers =
    !!spaceId &&
    !!agentUuid &&
    currentSpace?.permissions.includes("diagnosis.manage") === true
  const canManageJobs =
    currentSpace?.permissions.includes("diagnosis.job.manage") === true
  const canTrialSpecialist =
    !!spaceId &&
    !!agentUuid &&
    currentSpace?.permissions.includes("diagnosis.execute") === true
  const [modelConfigTarget, setModelConfigTarget] = useState<{
    uuid: string
    name: string
    spec: Record<string, unknown>
    spaceId: string
    agentUuid: string
    availableModelsOnly: boolean
  } | null>(null)
  const activeModelTarget =
    canManageDiagnosers &&
    modelConfigTarget?.spaceId === spaceId &&
    modelConfigTarget.agentUuid === agentUuid
      ? modelConfigTarget
      : null
  if (modelConfigTarget && !activeModelTarget) setModelConfigTarget(null)
  const [editor, setEditor] = useState<{
    spaceId: string
    agentUuid: string
    editing?: DiagnoserEditTarget
  } | null>(null)
  const activeEditor =
    editor?.spaceId === spaceId && editor.agentUuid === agentUuid
      ? editor
      : null
  const [specialistOpen, setSpecialistOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<Diagnoser | null>(null)
  const [ruleDialog, setRuleDialog] = useState<{
    mode: "copy" | "configure"
    target: DiagnoserDTO
    displayName: string
  } | null>(null)

  // 多选筛选：分类 / 来源 / 类型 / 业务场景（空 Set = 不筛）
  const [selectedCategories, setSelectedCategories] = useState<Set<DimKey>>(
    new Set()
  )
  const [selectedSources, setSelectedSources] = useState<Set<SourceValue>>(
    new Set()
  )
  const [selectedEngines, setSelectedEngines] = useState<Set<EngineValue>>(
    new Set()
  )
  const [selectedScenarios, setSelectedScenarios] = useState<Set<string>>(
    new Set()
  )

  const diagnosersQueryKey = ["diagnosers", spaceId, agentUuid, refreshTick]
  const diagnosersQuery = useQuery({
    queryKey: diagnosersQueryKey,
    queryFn: () => listDiagnosers(spaceId, agentUuid),
    enabled: !!spaceId && !!agentUuid,
  })

  const jobsQuery = useQuery({
    queryKey: ["diagnose-jobs", spaceId, agentUuid, refreshTick],
    queryFn: () => listJobs(spaceId, { agent_uuid: agentUuid }),
    enabled: !!spaceId && !!agentUuid,
  })

  const modelConnectionsQuery = useQuery({
    queryKey: ["model-connections", spaceId, refreshTick],
    queryFn: () => listModelConnections(spaceId),
    enabled: !!spaceId,
    staleTime: 30_000,
  })

  const modelConnections = useMemo<ModelConnection[]>(
    () => modelConnectionsQuery.data?.items ?? [],
    [modelConnectionsQuery.data?.items]
  )

  const diagnosers = useMemo(() => {
    if (!diagnosersQuery.data?.items) return []
    return diagnosersQuery.data.items.map((dto) =>
      toDiagnoser(dto, modelConnections, t)
    )
  }, [diagnosersQuery.data, modelConnections, t])

  const scenarioLabels = useMemo<Record<string, string>>(
    () =>
      Object.fromEntries([
        ...diagnosers
          .filter(
            (d) =>
              d.type === "builtin" &&
              !Object.hasOwn(SCENARIO_LABEL_KEYS, d.scenario)
          )
          .map((d) => [
            d.scenario,
            t("diagnose.diagnoser.scenario.legacyBuiltin"),
          ]),
        ...Object.entries(SCENARIO_LABEL_KEYS).map(([scenario, key]) => [
          scenario,
          t(key),
        ]),
      ]),
    [diagnosers, t]
  )

  // 详情翻译仅用于展示，编辑与执行继续使用原始 DTO。
  const detailDiagnoser = detailUuid
    ? (diagnosers.find((d) => d.uuid === detailUuid) ?? null)
    : null
  const detailDto = diagnosersQuery.data?.items.find(
    (d) => d.uuid === detailUuid
  )
  const detailSpec = useMemo(
    () => (detailDto ? toDiagnoserDetailSpec(detailDto, t) : null),
    [detailDto, t]
  )
  const canManageDetail =
    canManageDiagnosers &&
    detailDto?.space_uuid === spaceId &&
    detailDto.agent_uuid === agentUuid
  const canCopyDetail = canManageDetail && canCopyBuiltinRule(detailDto)
  const copiedRule = canManageDetail && !!builtinRuleSource(detailDto.spec)
  const canConfigureDetail =
    copiedRule ||
    (canCopyDetail && !!builtinRuleThreshold(detailDto.spec.executor))
  const detailCore = detailDto ? savedCoreVariant(detailDto) : null

  // 业务场景仅从当前已加载的诊断器中提取，避免展示没有对应诊断器的固定选项。
  const scenarioOptions = useMemo(() => {
    const seen: string[] = []
    for (const d of diagnosers) {
      const sc = (d.scenario ?? "").trim()
      if (sc && !seen.includes(sc)) seen.push(sc)
    }
    return seen
  }, [diagnosers])

  const existingScenarios = scenarioOptions

  const visibleDiagnosers = useMemo(() => {
    return diagnosers.filter((d) => {
      if (
        selectedCategories.size > 0 &&
        !selectedCategories.has(diagnoserCategory(d))
      )
        return false
      if (
        selectedSources.size > 0 &&
        !selectedSources.has(d.type as SourceValue)
      )
        return false
      if (
        selectedEngines.size > 0 &&
        !selectedEngines.has(d.engine as EngineValue)
      )
        return false
      if (selectedScenarios.size > 0 && !selectedScenarios.has(d.scenario))
        return false
      return true
    })
  }, [
    diagnosers,
    selectedCategories,
    selectedSources,
    selectedEngines,
    selectedScenarios,
  ])

  const groupedByCategory = useMemo(() => {
    const groups: Record<string, Diagnoser[]> = {}
    for (const dim of DIM_ORDER) groups[dim] = []
    for (const d of visibleDiagnosers) {
      const dim = diagnoserCategory(d)
      groups[dim]?.push(d)
    }
    return groups
  }, [visibleDiagnosers])

  const categoriesToShow = DIM_ORDER.filter(
    (dim) => (groupedByCategory[dim]?.length ?? 0) > 0
  )

  // 当前筛选 tag 列表
  const activeFilters = useMemo(() => {
    const tags: { dimension: string; value: string; label: string }[] = []
    for (const v of selectedCategories)
      tags.push({ dimension: "category", value: v, label: dimLabels[v] })
    for (const v of selectedSources) {
      tags.push({ dimension: "source", value: v, label: sourceLabels[v] ?? v })
    }
    for (const v of selectedEngines) {
      tags.push({ dimension: "engine", value: v, label: engineLabels[v] ?? v })
    }
    for (const v of selectedScenarios)
      tags.push({
        dimension: "scenario",
        value: v,
        label: scenarioLabels[v] ?? v,
      })
    return tags
  }, [
    selectedCategories,
    selectedSources,
    selectedEngines,
    selectedScenarios,
    dimLabels,
    engineLabels,
    sourceLabels,
    scenarioLabels,
  ])

  const hasFilters = activeFilters.length > 0

  const clearAll = () => {
    setSelectedCategories(new Set())
    setSelectedSources(new Set())
    setSelectedEngines(new Set())
    setSelectedScenarios(new Set())
  }

  const openEditLlm = (uuid: string) => {
    const dto = diagnosersQuery.data?.items.find((item) => item.uuid === uuid)
    if (!dto) return
    setEditor({
      spaceId,
      agentUuid,
      editing: {
        mode: "llm",
        uuid: dto.uuid,
        name: dto.name,
        description: dto.description,
        spec: dto.spec,
      },
    })
  }

  const openModelConfig = (uuid: string) => {
    const dto = diagnosersQuery.data?.items.find((item) => item.uuid === uuid)
    if (
      !canManageDiagnosers ||
      dto?.space_uuid !== spaceId ||
      dto.agent_uuid !== agentUuid
    ) {
      return
    }
    setModelConfigTarget({
      uuid: dto.uuid,
      name: diagnosers.find((item) => item.uuid === uuid)?.name ?? dto.name,
      spec: dto.spec,
      spaceId,
      agentUuid,
      availableModelsOnly:
        currentSpace?.type === SpaceType.Personal && !!savedCoreVariant(dto),
    })
  }

  const openEditRule = (uuid: string, writePython = false) => {
    const dto = diagnosersQuery.data?.items.find((item) => item.uuid === uuid)
    if (!dto) return
    if (writePython) setDetailUuid(null)
    setEditor({
      spaceId,
      agentUuid,
      editing: {
        mode: "rule",
        uuid: dto.uuid,
        name: dto.name,
        description: dto.description,
        spec: writePython
          ? {
              category: dto.spec.category,
              scenario: dto.spec.scenario,
              weight: dto.spec.weight,
              metrics: dto.spec.metrics,
            }
          : dto.spec,
        writePython,
      },
    })
  }

  const openRuleDialog = (mode: "copy" | "configure") => {
    if (!detailDto || !detailDiagnoser) return
    setRuleDialog({
      mode,
      target: detailDto,
      displayName: detailDiagnoser.name,
    })
  }

  const onRuleSaved = (row: DiagnoserDTO) => {
    const isCopy = ruleDialog?.mode === "copy"
    queryClient.setQueryData<{ items: DiagnoserDTO[]; total: number }>(
      diagnosersQueryKey,
      (previous) => {
        const items = previous?.items ?? []
        return items.some((item) => item.uuid === row.uuid)
          ? {
              items: items.map((item) => (item.uuid === row.uuid ? row : item)),
              total: previous?.total ?? items.length,
            }
          : {
              items: [...items, row],
              total: (previous?.total ?? items.length) + 1,
            }
      }
    )
    setRuleDialog(null)
    if (isCopy) {
      setDetailUuid(row.uuid)
      toast.success(t("diagnose.diagnoser.ruleManagement.copied"))
    } else {
      toast.success(t("diagnose.diagnoser.rule.updateSuccess"))
    }
    void queryClient.invalidateQueries({
      queryKey: ["diagnosers", spaceId, agentUuid],
    })
  }

  const isLoading = diagnosersQuery.isLoading || jobsQuery.isLoading

  const affectedDeleteJobs = useMemo(() => {
    if (!deleteTarget) return []
    return (jobsQuery.data?.items ?? []).filter((job) =>
      job.diagnoser_uuids.includes(deleteTarget.uuid)
    )
  }, [deleteTarget, jobsQuery.data])

  const deleteMutation = useMutation({
    mutationFn: async ({
      diagnoserUuid,
      affectedJobs,
    }: {
      diagnoserUuid: string
      affectedJobs: JobDTO[]
    }) => {
      await Promise.all(
        affectedJobs.map((job) => {
          const diagnoserUuids = job.diagnoser_uuids.filter(
            (id) => id !== diagnoserUuid
          )
          return updateJob(spaceId, job.uuid, {
            diagnoser_uuids: diagnoserUuids,
            ...(diagnoserUuids.length === 0 ? { status: "disabled" } : {}),
          })
        })
      )
      await deleteDiagnoser(spaceId, diagnoserUuid, agentUuid)
    },
    onSuccess: () => {
      toast.success(t("diagnose.diagnoser.deleteSuccess"))
      setDeleteTarget(null)
      diagnosersQuery.refetch()
      jobsQuery.refetch()
    },
    onError: () => {
      toast.error(t("diagnose.diagnoser.deleteFail"))
    },
  })

  const handleDelete = (d: Diagnoser) => {
    setDeleteTarget(d)
  }

  const noResults = visibleDiagnosers.length === 0
  const showUnsupportedBanner =
    noBuiltinDiagnosers &&
    (currentSpace?.type !== SpaceType.Personal ||
      (!isLoading &&
        !diagnosersQuery.data?.items.some(
          (dto) =>
            dto.space_uuid === spaceId &&
            dto.agent_uuid === agentUuid &&
            !!savedCoreVariant(dto)
        )))

  return (
    <SpaceAgentGuard>
      <div className="flex min-w-0 flex-col gap-4 overflow-hidden px-4 pt-3 pb-6">
        {/* 标题 + 操作 */}
        <div className="flex items-start justify-between">
          <PageBreadcrumb
            pageTitle={t("diagnose.diagnoser.title")}
            agentProduct={agentUuid}
          />
          <div className="flex items-center gap-2">
            {canTrialSpecialist && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setSpecialistOpen(true)}
              >
                {t("diagnose.specialist.open")}
              </Button>
            )}
            {canManageDiagnosers ? (
              <Button
                size="sm"
                onClick={() => setEditor({ spaceId, agentUuid })}
              >
                <Plus className="mr-1 size-3.5" />
                {t("diagnose.diagnoser.create")}
              </Button>
            ) : (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="inline-flex">
                    <Button size="sm" disabled>
                      <Plus className="mr-1 size-3.5" />
                      {t("diagnose.diagnoser.create")}
                    </Button>
                  </span>
                </TooltipTrigger>
                <TooltipContent>
                  {t("diagnose.diagnoser.createPermissionDenied")}
                </TooltipContent>
              </Tooltip>
            )}
          </div>
        </div>

        {showUnsupportedBanner && (
          <Alert>
            <Info />
            <AlertDescription>
              {t("diagnose.diagnoser.unsupportedBanner", {
                defaultValue:
                  "「{{agent}}」暂未内置诊断器，暂不提供默认诊断任务",
                agent: agentDisplayName,
              })}
            </AlertDescription>
          </Alert>
        )}

        {isLoading ? (
          <div className="flex items-center justify-center py-16 text-sm text-muted-foreground">
            <Loader2 className="mr-2 size-4 animate-spin" />
            {t("common.loading")}
          </div>
        ) : (
          <>
            {/* 横向 chip 筛选条 */}
            <div className="flex flex-wrap items-center gap-x-8 gap-y-3">
              <FilterChipGroup
                title={t("diagnose.diagnoser.filterCategory", {
                  defaultValue: "分类",
                })}
                options={DIM_ORDER}
                labels={dimLabels}
                selected={selectedCategories}
                onChange={setSelectedCategories}
              />
              <FilterChipGroup
                title={t("diagnose.diagnoser.filterSource", {
                  defaultValue: "来源",
                })}
                options={SOURCE_OPTIONS.map((o) => o.value)}
                labels={sourceLabels}
                selected={selectedSources}
                onChange={setSelectedSources}
              />
              <FilterChipGroup
                title={t("diagnose.diagnoser.filterType", {
                  defaultValue: "类型",
                })}
                options={ENGINE_OPTIONS.map((o) => o.value)}
                labels={engineLabels}
                selected={selectedEngines}
                onChange={setSelectedEngines}
              />
              {scenarioOptions.length > 0 && (
                <FilterChipGroup
                  title={t("diagnose.diagnoser.filterScenario", {
                    defaultValue: "业务场景",
                  })}
                  options={scenarioOptions}
                  labels={scenarioLabels}
                  selected={selectedScenarios}
                  onChange={setSelectedScenarios}
                />
              )}
              <div className="flex items-baseline gap-2 pl-2">
                <p className="text-sm text-muted-foreground">
                  {t("diagnose.diagnoser.count", {
                    count: visibleDiagnosers.length,
                  })}
                </p>
                {hasFilters && (
                  <button
                    type="button"
                    className="inline-flex h-6 items-center rounded-md px-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                    onClick={clearAll}
                  >
                    {t("diagnose.diagnoser.clearFilter", {
                      defaultValue: "清除筛选",
                    })}
                  </button>
                )}
              </div>
            </div>

            {/* 按维度分组的卡片 */}
            {noResults ? (
              <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border bg-[var(--color-bg-subtle)] py-20">
                <div className="mb-4 flex size-14 items-center justify-center rounded-2xl bg-muted/60">
                  <Search className="size-7 text-muted-foreground/40" />
                </div>
                <p className="text-sm font-medium text-muted-foreground">
                  {t("diagnose.diagnoser.emptyNoMatch", {
                    defaultValue: "没有匹配的诊断器",
                  })}
                </p>
              </div>
            ) : (
              categoriesToShow.map((dim) => {
                const ops = groupedByCategory[dim] ?? []
                if (ops.length === 0) return null
                return (
                  <section key={dim} className="flex flex-col gap-3">
                    <h3 className="text-sm font-medium text-foreground">
                      {dimLabels[dim]}
                      <span className="ml-1.5 text-muted-foreground">
                        ({ops.length})
                      </span>
                    </h3>
                    <div className={CARD_GRID}>
                      {ops.map((d) => {
                        const dto = diagnosersQuery.data?.items.find(
                          (value) => value.uuid === d.uuid
                        )
                        const canEdit =
                          canManageDiagnosers &&
                          dto?.space_uuid === spaceId &&
                          dto.agent_uuid === agentUuid
                        const isCopy = !!builtinRuleSource(dto?.spec ?? null)
                        const canDelete =
                          canEdit &&
                          (canManageJobs ||
                            (jobsQuery.isSuccess &&
                              !jobsQuery.data.items.some((job) =>
                                job.diagnoser_uuids.includes(d.uuid)
                              )))
                        return (
                          <DiagnoserCard
                            key={d.uuid}
                            diagnoser={d}
                            requireAvailableModel={
                              !!dto &&
                              currentSpace?.type === SpaceType.Personal &&
                              !!savedCoreVariant(dto)
                            }
                            onConfigModel={
                              canEdit &&
                              d.engine === "llm" &&
                              d.type === "builtin" &&
                              genericBuiltinModelConfigAllowed(dto?.spec)
                                ? () => openModelConfig(d.uuid)
                                : undefined
                            }
                            onEdit={
                              canEdit && d.type === "custom"
                                ? () => {
                                    if (isCopy && dto)
                                      setRuleDialog({
                                        mode: "configure",
                                        target: dto,
                                        displayName: d.name,
                                      })
                                    else if (d.engine === "rule")
                                      openEditRule(d.uuid)
                                    else openEditLlm(d.uuid)
                                  }
                                : undefined
                            }
                            onDelete={
                              canDelete && d.type === "custom"
                                ? () => handleDelete(d)
                                : undefined
                            }
                            onOpenDetail={() => setDetailUuid(d.uuid)}
                          />
                        )
                      })}
                    </div>
                  </section>
                )
              })
            )}
          </>
        )}
      </div>

      <Sheet
        open={specialistOpen && canTrialSpecialist}
        onOpenChange={setSpecialistOpen}
      >
        <SheetContent
          aria-describedby={undefined}
          className="flex w-[calc(100vw-24px)] max-w-[calc(100vw-24px)] flex-col gap-0 overflow-hidden p-0 data-[side=right]:!w-[calc(100vw-24px)] data-[side=right]:!max-w-[calc(100vw-24px)] lg:w-[clamp(800px,52vw,1080px)] lg:max-w-[clamp(800px,52vw,1080px)] lg:data-[side=right]:!w-[clamp(800px,52vw,1080px)] lg:data-[side=right]:!max-w-[clamp(800px,52vw,1080px)]"
        >
          <div className="shrink-0 border-b px-6 py-4 pr-14">
            <SheetTitle>{t("diagnose.specialist.open")}</SheetTitle>
          </div>
          <SpecialistConfigurationForm
            open={specialistOpen && canTrialSpecialist}
            spaceId={spaceId}
            agentUuid={agentUuid}
            modelConnections={modelConnections}
          />
        </SheetContent>
      </Sheet>

      {activeModelTarget && (
        <ModelConnectionPickerModal
          key={activeModelTarget.uuid}
          open
          target={activeModelTarget}
          spaceId={spaceId}
          agentUuid={agentUuid}
          modelConnections={modelConnections}
          availableModelsOnly={activeModelTarget.availableModelsOnly}
          onClose={() =>
            setModelConfigTarget((target) =>
              target === activeModelTarget ? null : target
            )
          }
        />
      )}

      <DiagnoserDetailDrawer
        open={!!detailUuid}
        onClose={() => setDetailUuid(null)}
        diagnoser={detailDiagnoser}
        version={detailDto?.version}
        spec={detailSpec}
        coreVariant={detailCore}
        onConfigModel={
          canManageDetail && detailCore
            ? () => openModelConfig(detailDto.uuid)
            : undefined
        }
        onConfigure={
          canConfigureDetail ? () => openRuleDialog("configure") : undefined
        }
        onCopy={canCopyDetail ? () => openRuleDialog("copy") : undefined}
        onWritePython={
          copiedRule ? () => openEditRule(detailDto.uuid, true) : undefined
        }
      />

      {ruleDialog &&
        ruleDialog.target.space_uuid === spaceId &&
        ruleDialog.target.agent_uuid === agentUuid &&
        canManageDiagnosers && (
          <BuiltinRuleConfigurationDialog
            key={`${ruleDialog.mode}:${ruleDialog.target.uuid}`}
            {...ruleDialog}
            onClose={() => setRuleDialog(null)}
            onSaved={onRuleSaved}
          />
        )}

      <CreateDiagnoserModal
        key={`${spaceId}:${agentUuid}`}
        open={canManageDiagnosers && activeEditor !== null}
        editing={activeEditor?.editing}
        spaceId={spaceId}
        agentUuid={agentUuid}
        existingScenarios={existingScenarios}
        modelConnections={modelConnections}
        canCreateRuleDiagnoser={canManageDiagnosers}
        onClose={() => setEditor(null)}
      />

      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open && !deleteMutation.isPending) setDeleteTarget(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("diagnose.diagnoser.deleteTitle")}
            </AlertDialogTitle>
            <AlertDialogDescription className="flex flex-col gap-1">
              <span>{t("diagnose.diagnoser.deleteIrreversible")}</span>
              {affectedDeleteJobs.length > 0 ? (
                <>
                  <span>
                    {t("diagnose.diagnoser.deleteRemoveFromJobs", {
                      count: affectedDeleteJobs.length,
                      name: deleteTarget?.name,
                    })}
                  </span>
                  <span>
                    {affectedDeleteJobs.map((job) => job.name).join(", ")}
                    {affectedDeleteJobs.some(
                      (job) => job.diagnoser_uuids.length === 1
                    )
                      ? t("diagnose.diagnoser.deleteJobsAutoDisable")
                      : "."}
                  </span>
                </>
              ) : (
                <span>
                  {t("diagnose.diagnoser.deleteConfirm", {
                    name: deleteTarget?.name,
                  })}
                </span>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteMutation.isPending}>
              {t("common.cancel")}
            </AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={deleteMutation.isPending || !deleteTarget}
              onClick={(event) => {
                event.preventDefault()
                if (!deleteTarget) return
                deleteMutation.mutate({
                  diagnoserUuid: deleteTarget.uuid,
                  affectedJobs: affectedDeleteJobs,
                })
              }}
            >
              {deleteMutation.isPending ? (
                <Loader2 className="mr-1 size-3 animate-spin" />
              ) : null}
              {t("diagnose.diagnoser.confirmDelete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </SpaceAgentGuard>
  )
}
