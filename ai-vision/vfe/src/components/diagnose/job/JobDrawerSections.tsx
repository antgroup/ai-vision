import { useCallback, useMemo, useState, type ReactNode } from "react"
import { useTranslation } from "react-i18next"
import { ChevronDown, ChevronRight, Loader2, Search, X } from "lucide-react"

import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"

import {
  isPlatformManagedJob,
  PLATFORM_MANAGED_ACTOR_NAME,
} from "../resourceScopes"
import type { Diagnoser, JobDTO } from "../types"
import {
  formatTraceTimeOffset,
  formatTraceTimeRange,
  formatNullableAbsoluteTime,
  getFrequencyLabel,
  type JobFormData,
} from "./jobFormModel"

const DIM_ORDER = ["outcome", "process", "safety", "system"] as const
type DimKey = (typeof DIM_ORDER)[number]
function diagnoserDimension(diagnoser: Diagnoser): DimKey {
  const dim = diagnoser.category ?? "outcome"
  if (DIM_ORDER.includes(dim as DimKey)) return dim as DimKey
  return "outcome"
}

export function JobDetailView({
  job,
  form,
  diagnosers,
  diagnosersLoading,
  creatorNameMap,
}: {
  job: JobDTO
  form: JobFormData
  diagnosers: Diagnoser[]
  diagnosersLoading: boolean
  creatorNameMap: Map<string, string>
}) {
  const { t, i18n } = useTranslation()
  const diagnoserByUuid = new Map(
    diagnosers.map((diagnoser) => [diagnoser.uuid, diagnoser])
  )
  const selectedDiagnosers = form.diagnoserUuids
    .map((uuid) => diagnoserByUuid.get(uuid))
    .filter((diagnoser): diagnoser is Diagnoser => !!diagnoser)
  const scheduleText =
    job.trigger_type === "manual"
      ? "手动触发"
      : `定时触发 · ${getFrequencyLabel(form.frequency)}`

  return (
    <div className="flex flex-col gap-5">
      <div className="grid grid-cols-2 gap-x-6 gap-y-3">
        <DetailItem label="创建人">
          {resolveJobActorName(job, "creator", creatorNameMap)}
        </DetailItem>
        <DetailItem label="创建时间" className="tabular-nums">
          {formatNullableAbsoluteTime(job.gmt_create, i18n?.resolvedLanguage)}
        </DetailItem>
        <DetailItem label="最后修改人">
          {resolveJobActorName(job, "modifier", creatorNameMap)}
        </DetailItem>
        <DetailItem label="最后修改时间" className="tabular-nums">
          {formatNullableAbsoluteTime(job.gmt_modified, i18n?.resolvedLanguage)}
        </DetailItem>
      </div>

      {job.description ? (
        <div className="flex flex-col gap-1">
          <span className="text-xs font-medium text-muted-foreground">
            描述
          </span>
          <p className="text-sm text-foreground">{job.description}</p>
        </div>
      ) : null}

      <DetailItem label="调度方式">{scheduleText}</DetailItem>

      <div className="grid grid-cols-2 gap-x-6 gap-y-4">
        <DetailItem label="最大诊断条数">{form.maxTraceCount}</DetailItem>
        <DetailItem label="取数时间范围">
          {formatTraceTimeRange(form.timeRange)}
        </DetailItem>
        <DetailItem label="数据延迟">
          {formatTraceTimeOffset(form.timeOffset)}
        </DetailItem>
        <DetailItem label="过滤条件">{form.whereFilter || "全量"}</DetailItem>
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-xs font-medium text-muted-foreground">
          已选诊断器（{form.diagnoserUuids.length}）
        </span>
        {diagnosersLoading ? (
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Loader2 className="size-3 animate-spin" />
            {t("diagnose.job.drawer.loading")}
          </div>
        ) : selectedDiagnosers.length > 0 ? (
          <div className="flex flex-wrap gap-1.5">
            {selectedDiagnosers.map((diagnoser) => (
              <span
                key={diagnoser.uuid}
                className="inline-flex h-6 items-center gap-1.5 rounded-md border border-border px-2 text-xs text-foreground"
              >
                <span
                  className={cn(
                    "inline-flex h-4 items-center rounded px-1 text-xs font-medium",
                    diagnoser.engine === "llm"
                      ? "bg-[var(--color-bg-auxiliary)] text-[var(--color-auxiliary)]"
                      : "bg-[var(--color-bg-success)] text-[var(--color-success)]"
                  )}
                >
                  {diagnoser.engine === "llm" ? "LLM" : "规则"}
                </span>
                {diagnoser.name}
              </span>
            ))}
          </div>
        ) : (
          <span className="text-sm text-muted-foreground">-</span>
        )}
      </div>
    </div>
  )
}

function DetailItem({
  label,
  className,
  children,
}: {
  label: string
  className?: string
  children: ReactNode
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      <span className={cn("text-sm text-foreground", className)}>
        {children}
      </span>
    </div>
  )
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

export function DetectorSelector({
  selectedIds,
  diagnosers,
  loading,
  readonly,
  error,
  onToggle,
  onToggleGroup,
}: {
  selectedIds: string[]
  diagnosers: Diagnoser[]
  loading: boolean
  readonly: boolean
  error?: string
  onToggle: (uuid: string) => void
  onToggleGroup: (uuids: string[], checked: boolean) => void
}) {
  const [search, setSearch] = useState("")
  const { t } = useTranslation()
  const [collapsed, setCollapsed] = useState<Set<DimKey>>(
    () => new Set(["process", "safety", "system"])
  )
  const keyword = search.trim().toLowerCase()
  const isSearching = keyword.length > 0

  const grouped = useMemo(() => {
    const next: Record<DimKey, Diagnoser[]> = {
      outcome: [],
      process: [],
      safety: [],
      system: [],
    }
    diagnosers.forEach((diagnoser) => {
      next[diagnoserDimension(diagnoser)].push(diagnoser)
    })
    return next
  }, [diagnosers])

  const toggleCollapse = useCallback((key: DimKey) => {
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }, [])

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <label className="text-xs font-medium text-muted-foreground">
          {t("diagnose.job.drawer.selectDiagnosers")}
          {!readonly ? (
            <span className="text-[var(--color-error)]"> *</span>
          ) : null}
        </label>
        <span className="text-xs text-muted-foreground">
          {t("diagnose.job.drawer.selected", { count: selectedIds.length })}
        </span>
      </div>
      {!readonly ? (
        <div className="relative">
          <Search className="absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder={t("diagnose.job.drawer.searchDiagnosers")}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="h-8 !pr-8 !pl-8 text-xs font-normal"
          />
          {search ? (
            <button
              type="button"
              className="absolute top-1/2 right-2.5 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              onClick={() => setSearch("")}
            >
              <X className="size-3.5" />
            </button>
          ) : null}
        </div>
      ) : null}

      <div
        className={cn(
          "rounded-lg border border-border",
          readonly && "opacity-70"
        )}
      >
        {loading ? (
          <div className="flex items-center gap-1.5 p-3 text-xs text-muted-foreground">
            <Loader2 className="size-3 animate-spin" />
            {t("diagnose.job.drawer.loading")}
          </div>
        ) : diagnosers.length === 0 ? (
          <div className="p-3 text-xs text-muted-foreground">
            {t("diagnose.job.drawer.noDiagnosers")}
          </div>
        ) : (
          DIM_ORDER.map((dim) => {
            const items = grouped[dim].filter((item) =>
              keyword ? item.name.toLowerCase().includes(keyword) : true
            )
            if (items.length === 0) return null
            const isCollapsed = !isSearching && collapsed.has(dim)
            const uuids = items.map((item) => item.uuid)
            const selectableUuids = uuids
            const selectedCount = uuids.filter((uuid) =>
              selectedIds.includes(uuid)
            ).length
            const allSelected =
              selectableUuids.length > 0 &&
              selectedCount === selectableUuids.length

            return (
              <div key={dim} className="border-b border-border last:border-b-0">
                <div className="flex items-center gap-2 bg-muted/30 px-3 py-2">
                  {!readonly ? (
                    <Checkbox
                      checked={allSelected}
                      disabled={selectableUuids.length === 0}
                      onCheckedChange={() =>
                        onToggleGroup(selectableUuids, !allSelected)
                      }
                    />
                  ) : null}
                  <button
                    type="button"
                    className="flex flex-1 items-center gap-1 text-left"
                    onClick={() => toggleCollapse(dim)}
                  >
                    {isCollapsed ? (
                      <ChevronRight className="size-3.5 text-muted-foreground" />
                    ) : (
                      <ChevronDown className="size-3.5 text-muted-foreground" />
                    )}
                    <span className="text-xs font-medium text-foreground">
                      {t(
                        `diagnose.diagnoser.llm.dimension${dim[0].toUpperCase()}${dim.slice(1)}`
                      )}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {selectedCount > 0
                        ? `(${selectedCount}/${items.length})`
                        : `(${items.length})`}
                    </span>
                  </button>
                </div>
                {!isCollapsed ? (
                  <div className="grid grid-cols-2 gap-x-2 px-1 py-1">
                    {items.map((diagnoser) => {
                      return (
                        <label
                          key={diagnoser.uuid}
                          className={cn(
                            "flex items-center gap-2 rounded-md px-2 py-1.5 transition-colors",
                            !readonly && "cursor-pointer hover:bg-muted/50"
                          )}
                        >
                          <Checkbox
                            checked={selectedIds.includes(diagnoser.uuid)}
                            disabled={readonly}
                            onCheckedChange={() => onToggle(diagnoser.uuid)}
                          />
                          <span className="flex-1 truncate text-sm">
                            {diagnoser.name}
                          </span>
                          <span
                            className={cn(
                              "inline-flex h-5 shrink-0 items-center rounded px-1.5 text-xs font-medium",
                              diagnoser.engine === "llm"
                                ? "bg-[var(--color-bg-auxiliary)] text-[var(--color-auxiliary)]"
                                : "bg-[var(--color-bg-success)] text-[var(--color-success)]"
                            )}
                          >
                            {diagnoser.engine === "llm" ? "LLM" : "Rule"}
                          </span>
                        </label>
                      )
                    })}
                  </div>
                ) : null}
              </div>
            )
          })
        )}
      </div>
      {error ? (
        <p className="text-xs text-[var(--color-error)]">{error}</p>
      ) : null}
    </div>
  )
}

export function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div className="pt-1">
      <span className="text-sm font-medium text-foreground">{children}</span>
    </div>
  )
}

export function Field({
  label,
  labelSuffix,
  required,
  error,
  hint,
  children,
  className,
}: {
  label: string
  labelSuffix?: React.ReactNode
  required?: boolean
  error?: string
  hint?: string
  children: React.ReactNode
  className?: string
}) {
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <label className="flex items-center gap-1 text-xs font-medium text-muted-foreground">
        {label}
        {required ? (
          <span className="text-[var(--color-error)]"> *</span>
        ) : null}
        {labelSuffix}
      </label>
      {children}
      {error ? (
        <p className="text-xs text-[var(--color-error)]">{error}</p>
      ) : hint ? (
        <p className="text-xs text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  )
}
