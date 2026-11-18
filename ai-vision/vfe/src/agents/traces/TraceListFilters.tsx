import { useCallback, useEffect, useId, useState } from "react"
import { useTranslation } from "react-i18next"
import { ChevronDown, CircleHelp, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import {
  formatSpanThreshold,
  parseSpanThreshold,
} from "@/components/traces-otel/spanThresholds"
import type {
  TraceFacetValue,
  TraceListFilters as TraceListFilterValues,
} from "@/agents/types/langfuse"
import {
  traceToolbarActiveClassName,
  traceToolbarControlClassName,
  traceToolbarInputClassName,
  traceToolbarRemoveClassName,
  traceToolbarSplitTriggerClassName,
} from "./traceToolbarStyles"
import {
  isValidTraceListThreshold,
  traceListThresholdMinimum,
  type TraceListThresholdField,
} from "./traceListThresholds"

interface FacetOptionsState {
  requestKey: string
  onOpenChange: (open: boolean) => void
  loading: boolean
  error: boolean
  onRetry: () => void
}

interface TraceListFiltersProps {
  facets: {
    env?: TraceFacetValue[]
    scene?: TraceFacetValue[]
    username?: TraceFacetValue[]
    tags?: TraceFacetValue[]
  }
  loading: boolean
  error: boolean
  onRetry: () => void
  filters: TraceListFilterValues
  onFilterChange: (filters: TraceListFilterValues) => void
  enabledDims: ("env" | "username" | "scene" | "tags")[]
  nativeMetrics?: boolean
  sessionMetrics?: boolean
  tokenMetrics?: boolean
  usernameSemantics?: "task" | "collector"
  hasExternalSearch?: boolean
  onClearAll?: () => void
  environmentOptions?: FacetOptionsState
  usernameOptions?: FacetOptionsState
}

/** 分组 tags 候选项：按 : 前缀分组为 skill/mcp/其他，分組内按 count 降序。 */
function groupTags(
  items: TraceFacetValue[]
): { label: string; group: string; items: TraceFacetValue[] }[] {
  const groups: Record<string, TraceFacetValue[]> = {}
  for (const item of items) {
    const colonIdx = item.value.indexOf(":")
    const prefix = colonIdx > 0 ? item.value.slice(0, colonIdx) : "other"
    if (!groups[prefix]) groups[prefix] = []
    groups[prefix].push(item)
  }
  return Object.entries(groups)
    .sort(([a], [b]) => {
      if (a === "other") return 1
      if (b === "other") return -1
      return a.localeCompare(b)
    })
    .map(([key, vals]) => ({
      label: key.toUpperCase(),
      group: key,
      items: vals.sort((a, b) => b.count - a.count),
    }))
}

function FilterChip({
  label,
  value,
  onClear,
}: {
  label: string
  value: string
  onClear: () => void
}) {
  const { t } = useTranslation()
  return (
    <div className={traceToolbarActiveClassName}>
      <span className="flex min-w-0 items-center gap-1.5 px-2.5">
        <span className="shrink-0 text-muted-foreground">{label}:</span>
        <span className="max-w-40 truncate" title={value}>
          {value}
        </span>
      </span>
      <button
        type="button"
        aria-label={t("traceFilters.clear", { label })}
        className={traceToolbarRemoveClassName}
        onClick={onClear}
      >
        <X className="size-3" />
      </button>
    </div>
  )
}

function FacetPicker({
  label,
  items = [],
  selected = [],
  loading,
  error,
  onRetry,
  onSelect,
  onOpenChange,
  multiple = false,
}: {
  label: string
  items?: TraceFacetValue[]
  selected?: string[]
  loading: boolean
  error: boolean
  onRetry: () => void
  onSelect: (value: string) => void
  onOpenChange?: (open: boolean) => void
  multiple?: boolean
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState("")
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>(
    {}
  )
  useEffect(() => () => onOpenChange?.(false), [onOpenChange])
  const changeOpen = (next: boolean) => {
    setOpen(next)
    setSearch("")
    onOpenChange?.(next)
  }
  // Search the complete tag candidate set before applying the default collapse.
  const query = search.trim().toLocaleLowerCase()
  const matching =
    multiple && query
      ? items.filter((item) =>
          `${item.value} ${item.label ?? ""}`
            .toLocaleLowerCase()
            .includes(query)
        )
      : items
  const groups = multiple
    ? groupTags(matching)
    : [{ group: "all", label: "", items: matching }]
  return (
    <Popover open={open} onOpenChange={changeOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className={traceToolbarControlClassName}
          aria-label={label}
        >
          {label}
          <ChevronDown className="size-3" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-72 p-0" align="start">
        {loading ? (
          <div role="status" className="p-3 text-sm text-muted-foreground">
            {t("traceFilters.loadingValues", { label })}
          </div>
        ) : error ? (
          <div role="alert" className="space-y-2 p-3 text-sm">
            <p>{t("traceFilters.loadValuesError", { label })}</p>
            <Button type="button" variant="outline" size="sm" onClick={onRetry}>
              {t("common.retry")}
            </Button>
          </div>
        ) : (
          <Command shouldFilter={!multiple}>
            <CommandInput
              aria-label={t("traceFilters.search", { label })}
              placeholder={t("traceFilters.search", { label })}
              value={search}
              onValueChange={setSearch}
            />
            <CommandList>
              <CommandEmpty>
                {t(
                  items.length === 0
                    ? "traceFilters.noValues"
                    : "traceFilters.noMatch",
                  { label }
                )}
              </CommandEmpty>
              {groups.map((group) => {
                const expanded =
                  !!query || expandedGroups[group.group] || !multiple
                const visible = expanded ? group.items : group.items.slice(0, 5)
                return (
                  <CommandGroup
                    key={group.group}
                    heading={
                      multiple
                        ? group.group === "other"
                          ? t("traceFilters.other")
                          : group.label
                        : undefined
                    }
                  >
                    {visible.map((item) => (
                      <CommandItem
                        key={item.value}
                        value={item.value}
                        keywords={[item.label ?? item.value]}
                        data-checked={selected.includes(item.value)}
                        onSelect={() => {
                          onSelect(item.value)
                          if (!multiple) changeOpen(false)
                        }}
                      >
                        <span
                          className="flex-1 truncate"
                          title={item.label ?? item.value}
                        >
                          {item.label ?? item.value}
                        </span>
                        <span className="text-xs text-muted-foreground">
                          ({item.count})
                        </span>
                      </CommandItem>
                    ))}
                    {multiple && !query && group.items.length > 5 && (
                      <CommandItem
                        value={`expand-${group.group}`}
                        onSelect={() =>
                          setExpandedGroups((previous) => ({
                            ...previous,
                            [group.group]: !previous[group.group],
                          }))
                        }
                        className="text-xs text-muted-foreground"
                      >
                        {expanded
                          ? t("common.collapse")
                          : t("common.showAll", {
                              count: group.items.length - 5,
                            })}
                      </CommandItem>
                    )}
                  </CommandGroup>
                )
              })}
            </CommandList>
          </Command>
        )}
      </PopoverContent>
    </Popover>
  )
}

function ThresholdField({
  label,
  value,
  kind,
  onChange,
  hintKey,
  restoredInput,
  inputLabel,
}: {
  label: string
  value?: number
  kind: "duration" | "nodes" | "turns" | "tokens"
  hintKey?: string
  restoredInput?: string
  inputLabel?: string
  onChange: (value: number | undefined) => void
}) {
  const { t } = useTranslation()
  const id = useId()
  const thresholdKind = kind === "duration" ? "duration" : "tokens"
  const formatted =
    value === undefined ? "" : formatSpanThreshold(value, thresholdKind)
  const [draft, setDraft] = useState<string | null>(null)
  const [invalid, setInvalid] = useState(false)
  // A restored URL or cleared list replaces any uncommitted local draft.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    setDraft(null)
    setInvalid(false)
  }, [value, restoredInput])
  /* eslint-enable react-hooks/set-state-in-effect */
  const commit = () => {
    if (draft === null) return
    const parsed =
      draft.trim() === "" ? undefined : parseSpanThreshold(draft, thresholdKind)
    if (
      parsed === null ||
      (parsed !== undefined && !isValidTraceListThreshold(parsed, kind))
    ) {
      setInvalid(true)
      return
    }
    setDraft(null)
    setInvalid(false)
    if (parsed !== value || restoredInput !== undefined) onChange(parsed)
  }
  const showInvalid =
    invalid ||
    (draft === null &&
      (restoredInput !== undefined ||
        (value !== undefined && !isValidTraceListThreshold(value, kind))))
  return (
    <form
      className="space-y-2"
      onSubmit={(event) => {
        event.preventDefault()
        commit()
      }}
    >
      <label htmlFor={id} className="text-xs font-medium">
        {label}
      </label>
      <div className="flex items-center gap-2">
        <span aria-hidden="true" className="text-muted-foreground">
          ≥
        </span>
        <Input
          id={id}
          type="text"
          inputMode={kind === "duration" ? "text" : "decimal"}
          autoComplete="off"
          aria-label={
            inputLabel ??
            t(
              kind === "duration"
                ? "traceFilters.minimumDuration"
                : kind === "nodes"
                  ? "traceFilters.minimumNodes"
                  : `sessionFilters.minimum${kind === "turns" ? "Traces" : "Tokens"}`
            )
          }
          aria-describedby={`${id}-hint${showInvalid ? ` ${id}-error` : ""}`}
          aria-invalid={showInvalid}
          className={traceToolbarInputClassName}
          value={draft ?? restoredInput ?? formatted}
          placeholder={
            kind === "duration"
              ? "1m"
              : kind === "turns"
                ? "2"
                : kind === "tokens"
                  ? "1K"
                  : "30"
          }
          onChange={(event) => {
            setDraft(event.target.value)
            setInvalid(false)
          }}
          onBlur={commit}
          onKeyDown={(event) => {
            if (event.nativeEvent.isComposing) return
            if (event.key === "Enter") {
              event.preventDefault()
              commit()
            }
            if (event.key === "Escape") {
              setDraft(null)
              setInvalid(false)
            }
          }}
        />
        <Button
          type="submit"
          variant="outline"
          size="sm"
          className={traceToolbarControlClassName}
        >
          {t("traceFilters.apply")}
        </Button>
      </div>
      <p id={`${id}-hint`} className="text-xs text-muted-foreground">
        {t(
          hintKey ??
            (kind === "duration"
              ? "traceFilters.durationHint"
              : kind === "nodes"
                ? "traceFilters.nodeCountHint"
                : kind === "turns"
                  ? "sessionFilters.traceCountHint"
                  : "sessionFilters.tokensHint")
        )}
      </p>
      {(kind === "turns" || kind === "tokens" || hintKey) && (
        <div className="flex flex-wrap gap-1.5">
          {(kind === "turns"
            ? [2, 5, 10, 20]
            : kind === "tokens"
              ? [1000, 5000, 10000, 50000, 100000]
              : [1000, 5000, 30000, 60000, 300000]
          ).map((preset) => (
            <Button
              key={preset}
              type="button"
              size="sm"
              variant="outline"
              className={traceToolbarControlClassName}
              onClick={() => {
                setDraft(null)
                setInvalid(false)
                onChange(preset)
              }}
            >
              {formatSpanThreshold(preset, thresholdKind)}
            </Button>
          ))}
        </div>
      )}
      {showInvalid && (
        <p id={`${id}-error`} role="alert" className="text-xs text-destructive">
          {t(
            kind === "duration"
              ? "traceFilters.invalidDuration"
              : kind === "nodes"
                ? "traceFilters.invalidNodes"
                : "sessionFilters.invalidThreshold",
            {
              minimum: traceListThresholdMinimum(kind),
              maximum: Number.MAX_SAFE_INTEGER,
            }
          )}
        </p>
      )}
    </form>
  )
}

function StatusFilter({
  value,
  onChange,
  session = false,
}: {
  value?: TraceListFilterValues["status"]
  onChange: (value: TraceListFilterValues["status"]) => void
  session?: boolean
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  return (
    <div className={value ? traceToolbarActiveClassName : "inline-flex"}>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            type="button"
            variant="outline"
            size="sm"
            aria-label={t(
              session ? "sessionFilters.status" : "traceFilters.status"
            )}
            className={cn(
              traceToolbarControlClassName,
              value && traceToolbarSplitTriggerClassName
            )}
          >
            {t(session ? "sessionFilters.status" : "traceFilters.status")}
            {value &&
              `: ${t(`${session ? "sessionFilters" : "traceFilters"}.statusValues.${value}`)}`}
            <ChevronDown className="size-3" />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-64 p-0" align="start">
          <p className="px-3 pt-3 text-xs text-muted-foreground">
            {t(
              session ? "sessionFilters.statusHint" : "traceFilters.statusHint"
            )}
          </p>
          <Command>
            <CommandList>
              <CommandGroup>
                {(["error", "ok", "unset"] as const).map((status) => (
                  <CommandItem
                    key={status}
                    value={status}
                    data-checked={value === status}
                    onSelect={() => {
                      onChange(status)
                      setOpen(false)
                    }}
                  >
                    {t(
                      `${session ? "sessionFilters" : "traceFilters"}.statusValues.${status}`
                    )}
                  </CommandItem>
                ))}
              </CommandGroup>
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
      {value && (
        <button
          type="button"
          aria-label={t("traceFilters.clear", {
            label: t(session ? "sessionFilters.status" : "traceFilters.status"),
          })}
          className={traceToolbarRemoveClassName}
          onClick={() => onChange(undefined)}
        >
          <X className="size-3" />
        </button>
      )}
    </div>
  )
}

export function TraceListFilters({
  facets,
  loading,
  error,
  onRetry,
  filters,
  onFilterChange,
  enabledDims,
  environmentOptions,
  usernameOptions,
  nativeMetrics = false,
  sessionMetrics = false,
  tokenMetrics = false,
  usernameSemantics = "task",
  hasExternalSearch = false,
  onClearAll,
}: TraceListFiltersProps) {
  const { t } = useTranslation()
  const active =
    hasExternalSearch ||
    !!(
      filters.env ||
      filters.scene ||
      filters.username ||
      filters.tags?.length ||
      filters.status ||
      filters.minDurationMs !== undefined ||
      filters.minSpanCount !== undefined ||
      filters.minTraceCount !== undefined ||
      filters.minTotalTokens !== undefined ||
      Object.keys(filters.thresholdInputs ?? {}).length
    )
  const clearAll = useCallback(() => {
    if (onClearAll) onClearAll()
    else onFilterChange({})
  }, [onClearAll, onFilterChange])
  const changeThreshold = (
    field: TraceListThresholdField,
    value: number | undefined
  ) => {
    const thresholdInputs = { ...filters.thresholdInputs }
    delete thresholdInputs[field]
    const next = { ...filters, [field]: value }
    if (Object.keys(thresholdInputs).length)
      next.thresholdInputs = thresholdInputs
    else delete next.thresholdInputs
    onFilterChange(next)
  }
  const showTokens = tokenMetrics || sessionMetrics
  const tokens = filters.minTotalTokens
  const tokenChipValue =
    filters.thresholdInputs?.minTotalTokens ??
    (tokens === undefined ? undefined : formatSpanThreshold(tokens, "tokens"))
  const showMore =
    nativeMetrics || showTokens || enabledDims.some((dim) => dim !== "scene")
  const usernameLabel = t(
    usernameSemantics === "collector"
      ? "traceFilters.collectorLabel"
      : "traceFilters.username"
  )
  const usernameHint = t(
    usernameSemantics === "collector"
      ? "traceFilters.collectorHint"
      : "traceFilters.usernameHint"
  )
  const username =
    facets.username?.find((item) => item.value === filters.username)?.label ??
    filters.username
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2">
      {(nativeMetrics || sessionMetrics) && (
        <>
          <StatusFilter
            session={sessionMetrics}
            value={filters.status}
            onChange={(status) => onFilterChange({ ...filters, status })}
          />
          <div
            className={
              filters.minDurationMs !== undefined
                ? traceToolbarActiveClassName
                : "inline-flex"
            }
          >
            <Popover>
              <PopoverTrigger asChild>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  aria-label={t(
                    sessionMetrics
                      ? "sessionFilters.duration"
                      : "traceFilters.duration"
                  )}
                  className={cn(
                    traceToolbarControlClassName,
                    filters.minDurationMs !== undefined &&
                      traceToolbarSplitTriggerClassName
                  )}
                >
                  {t(
                    sessionMetrics
                      ? "sessionFilters.duration"
                      : "traceFilters.duration"
                  )}
                  {filters.minDurationMs !== undefined &&
                    ` ≥ ${filters.thresholdInputs?.minDurationMs ?? formatSpanThreshold(filters.minDurationMs, "duration")}`}
                  <ChevronDown className="size-3" />
                </Button>
              </PopoverTrigger>
              <PopoverContent className="w-80" align="start">
                <ThresholdField
                  label={t(
                    sessionMetrics
                      ? "sessionFilters.duration"
                      : "traceFilters.duration"
                  )}
                  hintKey={
                    sessionMetrics ? "sessionFilters.durationHint" : undefined
                  }
                  value={filters.minDurationMs}
                  restoredInput={filters.thresholdInputs?.minDurationMs}
                  kind="duration"
                  onChange={(minDurationMs) =>
                    changeThreshold("minDurationMs", minDurationMs)
                  }
                />
              </PopoverContent>
            </Popover>
            {filters.minDurationMs !== undefined && (
              <button
                type="button"
                aria-label={t("traceFilters.clear", {
                  label: t(
                    sessionMetrics
                      ? "sessionFilters.duration"
                      : "traceFilters.duration"
                  ),
                })}
                className={traceToolbarRemoveClassName}
                onClick={() => changeThreshold("minDurationMs", undefined)}
              >
                <X className="size-3" />
              </button>
            )}
          </div>
        </>
      )}
      {showMore && (
        <Popover>
          <PopoverTrigger asChild>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className={traceToolbarControlClassName}
            >
              {t("traceFilters.more")}
              <ChevronDown className="size-3" />
            </Button>
          </PopoverTrigger>
          <PopoverContent
            className={cn(
              "w-80 max-w-[calc(100vw-2rem)]",
              showTokens &&
                "max-h-[min(70vh,640px)] overflow-y-auto [scrollbar-gutter:stable]"
            )}
            align="start"
          >
            {tokenMetrics && !sessionMetrics && (
              <ThresholdField
                label={t("sessionFilters.tokens")}
                inputLabel={t("traceFilters.minimumTokens")}
                hintKey="traceFilters.tokensHint"
                value={filters.minTotalTokens}
                restoredInput={filters.thresholdInputs?.minTotalTokens}
                kind="tokens"
                onChange={(value) => changeThreshold("minTotalTokens", value)}
              />
            )}
            {sessionMetrics && (
              <div className="mb-3 space-y-4 border-b border-border pb-3">
                <ThresholdField
                  label={t("sessionFilters.traceCount")}
                  value={filters.minTraceCount}
                  restoredInput={filters.thresholdInputs?.minTraceCount}
                  kind="turns"
                  onChange={(minTraceCount) =>
                    changeThreshold("minTraceCount", minTraceCount)
                  }
                />
                <ThresholdField
                  label={t("sessionFilters.tokens")}
                  value={filters.minTotalTokens}
                  restoredInput={filters.thresholdInputs?.minTotalTokens}
                  kind="tokens"
                  onChange={(minTotalTokens) =>
                    changeThreshold("minTotalTokens", minTotalTokens)
                  }
                />
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      className="inline-flex items-center gap-1.5 text-xs text-muted-foreground"
                    >
                      <CircleHelp className="size-3.5" />
                      {t("sessionFilters.scopeTitle")}
                    </button>
                  </TooltipTrigger>
                  <TooltipContent className="max-w-72">
                    {t("sessionFilters.scopeHint")}
                  </TooltipContent>
                </Tooltip>
              </div>
            )}
            {nativeMetrics && (
              <ThresholdField
                label={t("traceFilters.nodeCount")}
                value={filters.minSpanCount}
                restoredInput={filters.thresholdInputs?.minSpanCount}
                kind="nodes"
                onChange={(minSpanCount) =>
                  changeThreshold("minSpanCount", minSpanCount)
                }
              />
            )}
            {enabledDims.includes("username") && (
              <div className="space-y-1.5">
                <FacetPicker
                  key={usernameOptions?.requestKey}
                  label={usernameLabel}
                  items={facets.username}
                  selected={filters.username ? [filters.username] : []}
                  loading={usernameOptions?.loading ?? loading}
                  error={usernameOptions?.error ?? error}
                  onRetry={usernameOptions?.onRetry ?? onRetry}
                  onOpenChange={usernameOptions?.onOpenChange}
                  onSelect={(username) =>
                    onFilterChange({ ...filters, username })
                  }
                />
                <p className="text-xs text-muted-foreground">{usernameHint}</p>
              </div>
            )}
            {enabledDims.includes("tags") && (
              <div className="space-y-1.5">
                <FacetPicker
                  label={t("traceFilters.tags")}
                  items={facets.tags}
                  selected={filters.tags}
                  multiple
                  loading={loading}
                  error={error}
                  onRetry={onRetry}
                  onSelect={(tag) => {
                    const tags = filters.tags?.includes(tag)
                      ? filters.tags.filter((value) => value !== tag)
                      : [...(filters.tags ?? []), tag]
                    onFilterChange({
                      ...filters,
                      tags: tags.length ? tags : undefined,
                    })
                  }}
                />
                <p className="text-xs text-muted-foreground">
                  {t("traceFilters.tagsHint")}
                </p>
              </div>
            )}
            {enabledDims.includes("env") && (
              <div className="space-y-1.5">
                <FacetPicker
                  key={environmentOptions?.requestKey}
                  label={t("traceFilters.environment")}
                  items={facets.env}
                  selected={filters.env ? [filters.env] : []}
                  loading={environmentOptions?.loading ?? loading}
                  error={environmentOptions?.error ?? error}
                  onRetry={environmentOptions?.onRetry ?? onRetry}
                  onOpenChange={environmentOptions?.onOpenChange}
                  onSelect={(env) => onFilterChange({ ...filters, env })}
                />
                <p className="text-xs text-muted-foreground">
                  {t("traceFilters.environmentHint")}
                </p>
              </div>
            )}
          </PopoverContent>
        </Popover>
      )}
      {sessionMetrics && filters.minTraceCount !== undefined && (
        <FilterChip
          label={t("sessionFilters.traceCount")}
          value={`≥ ${filters.minTraceCount}`}
          onClear={() => changeThreshold("minTraceCount", undefined)}
        />
      )}
      {showTokens && tokenChipValue !== undefined && (
        <FilterChip
          label={t("sessionFilters.tokens")}
          value={`≥ ${tokenChipValue}`}
          onClear={() => changeThreshold("minTotalTokens", undefined)}
        />
      )}
      {filters.env && (
        <FilterChip
          label={t("traceFilters.environment")}
          value={filters.env}
          onClear={() => onFilterChange({ ...filters, env: undefined })}
        />
      )}
      {filters.username && (
        <FilterChip
          label={usernameLabel}
          value={username!}
          onClear={() => onFilterChange({ ...filters, username: undefined })}
        />
      )}
      {filters.tags?.length ? (
        <FilterChip
          label={t("traceFilters.tags")}
          value={filters.tags.join(", ")}
          onClear={() => onFilterChange({ ...filters, tags: undefined })}
        />
      ) : null}
      {filters.scene && (
        <FilterChip
          label={t("traceFilters.scene")}
          value={filters.scene}
          onClear={() => onFilterChange({ ...filters, scene: undefined })}
        />
      )}
      {nativeMetrics && filters.minSpanCount !== undefined && (
        <FilterChip
          label={t("traceFilters.nodeCount")}
          value={`≥ ${filters.minSpanCount}`}
          onClear={() => changeThreshold("minSpanCount", undefined)}
        />
      )}
      {active && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className={cn(
            traceToolbarControlClassName,
            "text-muted-foreground hover:text-foreground"
          )}
          onClick={clearAll}
        >
          {t("traceFilters.clearAll")}
        </Button>
      )}
    </div>
  )
}
