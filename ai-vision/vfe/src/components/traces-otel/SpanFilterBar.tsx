import { useCallback, useEffect, useId, useRef, useState } from "react"
import {
  ChevronDown,
  ChevronRight,
  ChevronUp,
  SlidersHorizontal,
  X,
} from "lucide-react"
import { useTranslation } from "react-i18next"
import { useOptionalUser } from "@/contexts/UserContext"
import { convertUsdAmount } from "@/features/model-pricing/currency"
import { SpanThresholdInput } from "./SpanThresholdInput"
import {
  SpanCostFilterInput,
  type SpanCostFilterInputProps,
} from "./SpanCostFilterInput"
import { SpanFilterHelp } from "./SpanFilterHelp"
import { formatSpanThreshold } from "./spanThresholds"
import { SpanFilterPresets } from "./SpanFilterPresetMenu"
import { spanFilterStorageKey, type SpanFilterQuery } from "./spanFilterPresets"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  createSpanFilters,
  hasSpanFilters,
  type SpanFilterKind,
  type SpanFilters,
} from "./spanFilters"

export interface SpanFilterBarProps {
  filters: SpanFilters
  onChange: (filters: SpanFilters) => void
  onExpandedChange?: (expanded: boolean) => void
  kinds: readonly SpanFilterKind[]
  models: readonly string[]
  toolNames: readonly string[]
  spaceId: string
  search: string
  onApplyQuery: (query: SpanFilterQuery) => void
  matchCount: number
  totalCount: number
  contextCount?: number
  durationDefaultMs?: number
  tokenDefault?: number
  costCurrency?: SpanCostFilterInputProps["defaultCurrency"]
  costRates?: SpanCostFilterInputProps["rates"]
  costRateDate?: SpanCostFilterInputProps["rateDate"]
  costExchangeStatus?: SpanCostFilterInputProps["exchangeStatus"]
  branchScope?: {
    active: { spanId: string; label: string } | null
    candidate: { spanId: string; label: string } | null
    onChange: (spanId: string | null) => void
  }
}

export function SpanFilterBar({
  filters,
  onChange,
  onExpandedChange,
  kinds,
  models,
  toolNames,
  spaceId,
  search,
  onApplyQuery,
  matchCount,
  totalCount,
  contextCount = 0,
  durationDefaultMs = 1000,
  tokenDefault = 10000,
  costCurrency = "USD",
  costRates,
  costRateDate,
  costExchangeStatus,
  branchScope,
}: SpanFilterBarProps) {
  const { t } = useTranslation()
  const viewer = useOptionalUser()?.user
  const [expanded, setExpanded] = useState(false)
  const [toolSearch, setToolSearch] = useState("")
  const [thresholdRevision, setThresholdRevision] = useState(0)
  const panelId = useId()
  const controlsRef = useRef<HTMLDivElement>(null)
  const controlsContentRef = useRef<HTMLDivElement>(null)
  const [scrollState, setScrollState] = useState({
    overflow: false,
    atEnd: false,
  })
  const updateScrollState = useCallback(() => {
    const viewport = controlsRef.current
    if (!viewport) return
    const overflow =
      viewport.clientHeight > 0 &&
      viewport.scrollHeight > viewport.clientHeight + 1
    const atEnd =
      viewport.scrollTop + viewport.clientHeight >= viewport.scrollHeight - 2
    setScrollState((previous) =>
      previous.overflow === overflow && previous.atEnd === atEnd
        ? previous
        : { overflow, atEnd }
    )
  }, [])
  useEffect(() => {
    if (!expanded) return
    const viewport = controlsRef.current
    const content = controlsContentRef.current
    if (!viewport || !content) return
    updateScrollState()
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(updateScrollState)
    observer?.observe(viewport)
    observer?.observe(content)
    window.addEventListener("resize", updateScrollState)
    return () => {
      observer?.disconnect()
      window.removeEventListener("resize", updateScrollState)
    }
  }, [expanded, updateScrollState])
  const activeCostCurrency = filters.minCost?.currency
  const costFxStatus =
    !activeCostCurrency || activeCostCurrency === "USD"
      ? null
      : convertUsdAmount("0", activeCostCurrency, costRates).currency !==
          activeCostCurrency
        ? "missing"
        : costExchangeStatus === "ready"
          ? null
          : costExchangeStatus === "loading"
            ? "loadingCompact"
            : costExchangeStatus === "stale"
              ? "staleCompact"
              : "retainedCompact"
  const activeScope = branchScope?.active
  const scopeCandidate = branchScope?.candidate
  const active = hasSpanFilters(filters) || Boolean(activeScope)
  const tokenMetric = filters.tokenMetric ?? "total"
  const tokenConditionKey =
    tokenMetric === "input"
      ? "nativeSpanFilters.inputTokenCondition"
      : tokenMetric === "output"
        ? "nativeSpanFilters.outputTokenCondition"
        : "nativeSpanFilters.tokenCondition"
  const tokenLabelKey =
    tokenMetric === "input"
      ? "nativeSpanFilters.inputTokens"
      : tokenMetric === "output"
        ? "nativeSpanFilters.outputTokens"
        : "nativeSpanFilters.tokens"
  const availableTools = [
    ...new Set([...toolNames, ...filters.toolNames]),
  ].sort()
  const availableKinds = [...new Set([...kinds, ...filters.kinds])]
  const conditions: { key: string; label: string; clear: () => void }[] = []
  if (filters.errorOnly)
    conditions.push({
      key: "error",
      label: t("nativeSpanFilters.errors"),
      clear: () => onChange({ ...filters, errorOnly: false }),
    })
  if (filters.hideCompletedTurnToolErrors)
    conditions.push({
      key: "completedTurnToolErrors",
      label: t("nativeSpanFilters.hideCompletedTurnToolErrors"),
      clear: () => onChange({ ...filters, hideCompletedTurnToolErrors: false }),
    })
  if (filters.lowConfidenceOnly)
    conditions.push({
      key: "confidence",
      label: t("nativeSpanFilters.lowConfidence"),
      clear: () => onChange({ ...filters, lowConfidenceOnly: false }),
    })
  if (filters.minDurationMs !== null)
    conditions.push({
      key: "duration",
      label: t("nativeSpanFilters.durationCondition", {
        value: formatSpanThreshold(filters.minDurationMs, "duration"),
      }),
      clear: () => onChange({ ...filters, minDurationMs: null }),
    })
  if (filters.minTokens !== null)
    conditions.push({
      key: "tokens",
      label: t(tokenConditionKey, {
        value: formatSpanThreshold(filters.minTokens, "tokens"),
      }),
      clear: () => onChange({ ...filters, minTokens: null }),
    })
  if (filters.minCost)
    conditions.push({
      key: "cost",
      label: t("nativeSpanFilters.costCondition", {
        amount: filters.minCost.amount,
        currency: filters.minCost.currency,
        metric: t(
          `nativeSpanFilters.${filters.minCost.metric === "listPriceEquivalent" ? "costMetricEquivalent" : "costMetricValue"}`
        ),
      }),
      clear: () => onChange({ ...filters, minCost: null }),
    })
  if (filters.model)
    conditions.push({
      key: "model",
      label: filters.model,
      clear: () => onChange({ ...filters, model: "" }),
    })
  for (const name of filters.toolNames)
    conditions.push({
      key: `tool:${name}`,
      label:
        filters.toolNameMode === "exclude"
          ? t("nativeSpanFilters.excludedToolCondition", { name })
          : name,
      clear: () =>
        onChange({
          ...filters,
          toolNames: filters.toolNames.filter((item) => item !== name),
        }),
    })

  for (const kind of filters.kinds)
    conditions.push({
      key: kind,
      label: t(`nativeSpanFilters.kinds.${kind}`),
      clear: () =>
        onChange({
          ...filters,
          kinds: filters.kinds.filter((item) => item !== kind),
        }),
    })

  const conditionCount = conditions.length + (activeScope ? 1 : 0)

  return (
    <div
      className="flex min-h-0 flex-col overflow-y-auto border-b px-3 py-2 max-lg:shrink-0"
      data-testid="span-filter-bar"
    >
      <div className="flex min-w-0 shrink-0 flex-wrap items-center gap-x-2 gap-y-1">
        <Button
          variant={active || expanded ? "secondary" : "outline"}
          size="sm"
          className="h-8 shrink-0 gap-1.5 px-2 text-xs"
          aria-expanded={expanded}
          aria-controls={panelId}
          onClick={() => {
            const next = !expanded
            setExpanded(next)
            onExpandedChange?.(next)
          }}
        >
          <SlidersHorizontal className="size-3.5" />
          {t("nativeSpanFilters.title")}
          {conditionCount > 0 && (
            <span className="rounded bg-muted px-1 tabular-nums">
              {conditionCount}
            </span>
          )}
          {expanded ? (
            <ChevronDown className="size-3" />
          ) : (
            <ChevronRight className="size-3" />
          )}
        </Button>
        <SpanFilterHelp name="rules" label={t("nativeSpanFilters.rulesHelp")}>
          <p>{t("nativeSpanFilters.combineHint")}</p>
        </SpanFilterHelp>
        {viewer?.user_uuid && spaceId && (
          <div className="ml-auto min-w-0">
            <SpanFilterPresets
              key={spanFilterStorageKey(viewer.user_uuid, spaceId)}
              storageKey={spanFilterStorageKey(viewer.user_uuid, spaceId)}
              query={{ filters, search }}
              onApply={(query) => {
                setThresholdRevision((value) => value + 1)
                onApplyQuery(query)
              }}
            />
          </div>
        )}
      </div>
      {(!expanded ||
        (scopeCandidate && scopeCandidate.spanId !== activeScope?.spanId)) && (
        <div
          className="mt-2 flex shrink-0 flex-wrap gap-1"
          aria-label={t("nativeSpanFilters.quickConditions")}
        >
          {!expanded &&
            [
              {
                key: "errors",
                enabled: filters.errorOnly,
                label: t("nativeSpanFilters.errors"),
                toggle: () =>
                  onChange({ ...filters, errorOnly: !filters.errorOnly }),
              },
              {
                key: "duration",
                enabled: filters.minDurationMs !== null,
                label: t("nativeSpanFilters.quickDuration", {
                  value: formatSpanThreshold(
                    filters.minDurationMs ?? durationDefaultMs,
                    "duration"
                  ),
                }),
                toggle: () =>
                  onChange({
                    ...filters,
                    minDurationMs:
                      filters.minDurationMs === null ? durationDefaultMs : null,
                  }),
              },
              {
                key: "tokens",
                enabled: filters.minTokens !== null,
                label: t(
                  tokenMetric === "total"
                    ? "nativeSpanFilters.quickTokens"
                    : tokenConditionKey,
                  {
                    value: formatSpanThreshold(
                      filters.minTokens ?? tokenDefault,
                      "tokens"
                    ),
                  }
                ),
                toggle: () =>
                  onChange({
                    ...filters,
                    minTokens: filters.minTokens === null ? tokenDefault : null,
                  }),
              },
            ].map((item) => (
              <button
                key={item.key}
                type="button"
                data-testid={`span-quick-${item.key}`}
                aria-pressed={item.enabled}
                onClick={item.toggle}
                className="rounded-md border px-2 py-1 text-[11px] hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none aria-pressed:border-foreground/40 aria-pressed:bg-accent aria-pressed:font-medium"
                title={t("nativeSpanFilters.quickHint")}
              >
                {item.label}
              </button>
            ))}
          {scopeCandidate && scopeCandidate.spanId !== activeScope?.spanId && (
            <button
              type="button"
              data-testid="span-filter-scope-candidate"
              onClick={() => branchScope?.onChange(scopeCandidate.spanId)}
              className="rounded-md border px-2 py-1 text-[11px] hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
              title={scopeCandidate.label}
            >
              {t("nativeSpanFilters.onlyThisBranch")}
            </button>
          )}
        </div>
      )}
      <div className="mt-1.5 flex shrink-0 flex-wrap items-center gap-1 text-[11px] text-muted-foreground">
        <span role="status">
          {t("nativeSpanFilters.matchCount", {
            count: matchCount,
            total: totalCount,
          })}
          {contextCount > 0 &&
            ` · ${t("nativeSpanFilters.contextCount", { count: contextCount })}`}
        </span>
        {active && (
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto h-6 px-1 text-[11px]"
            onClick={() => {
              onChange(createSpanFilters())
              branchScope?.onChange(null)
            }}
          >
            {t("nativeSpanFilters.clear")}
          </Button>
        )}
      </div>
      {conditionCount > 0 && (
        <div
          className="mt-1 flex max-h-20 min-w-0 shrink-0 flex-wrap gap-1 overflow-y-auto"
          aria-label={t("nativeSpanFilters.activeConditions")}
        >
          {activeScope && (
            <span
              data-testid="span-filter-scope-chip"
              className="inline-flex max-w-full items-center gap-0.5 rounded border bg-muted/40 pl-1.5 text-[11px]"
              title={`${t("nativeSpanFilters.branchScopeCondition", { label: activeScope.label })} · ${t("nativeSpanFilters.branchScopeHint")}`}
            >
              <span className="min-w-0 truncate">
                {t("nativeSpanFilters.branchScopeCondition", {
                  label: activeScope.label,
                })}
              </span>
              <button
                type="button"
                onClick={() => branchScope?.onChange(null)}
                className="shrink-0 rounded p-1 hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                aria-label={t("nativeSpanFilters.removeBranchScope")}
              >
                <X className="size-3" />
              </button>
            </span>
          )}
          {conditions.map((condition) => (
            <span
              key={condition.key}
              className="inline-flex max-w-full items-center gap-0.5 rounded border bg-muted/40 pl-1.5 text-[11px]"
              title={condition.label}
            >
              <span className="min-w-0 truncate">{condition.label}</span>
              <button
                type="button"
                onClick={condition.clear}
                className="shrink-0 rounded p-1 hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                aria-label={t("nativeSpanFilters.removeCondition", {
                  label: condition.label,
                })}
              >
                <X className="size-3" />
              </button>
            </span>
          ))}
        </div>
      )}
      {!expanded && costFxStatus && (
        <p
          data-testid="span-filter-cost-condition-fx-warning"
          role="status"
          className="mt-1 text-[11px] leading-relaxed text-muted-foreground"
        >
          {t(
            costFxStatus === "missing"
              ? "nativeSpanFilters.costFxUnavailable"
              : `modelPricing.currency.${costFxStatus}`
          )}
        </p>
      )}
      {expanded && (
        <div className="mt-2 flex max-h-[min(40vh,18rem)] min-h-0 shrink flex-col border-t">
          <div
            id={panelId}
            ref={controlsRef}
            onScroll={updateScrollState}
            className="min-h-0 overflow-y-auto overscroll-y-contain pt-2 pr-1 pb-1"
            data-testid="span-filter-controls"
          >
            <div ref={controlsContentRef} className="space-y-3">
              <fieldset className="min-w-0 rounded-md border border-border/60 px-2.5 pb-2.5">
                <legend className="mb-1 px-1 text-xs font-medium">
                  {t("nativeSpanFilters.status")}
                </legend>
                <div className="flex flex-wrap gap-x-4 gap-y-2">
                  <label className="flex items-center gap-1.5 text-xs">
                    <input
                      type="checkbox"
                      className="size-3.5 accent-primary"
                      checked={filters.errorOnly}
                      onChange={(event) =>
                        onChange({
                          ...filters,
                          errorOnly: event.target.checked,
                        })
                      }
                    />
                    {t("nativeSpanFilters.errors")}
                  </label>
                  <label className="flex items-center gap-1.5 text-xs">
                    <input
                      type="checkbox"
                      className="size-3.5 accent-primary"
                      checked={filters.lowConfidenceOnly}
                      onChange={(event) =>
                        onChange({
                          ...filters,
                          lowConfidenceOnly: event.target.checked,
                        })
                      }
                    />
                    {t("nativeSpanFilters.lowConfidence")}
                  </label>
                  <label className="flex min-w-0 items-start gap-1.5 text-xs">
                    <input
                      type="checkbox"
                      className="mt-0.5 size-3.5 shrink-0 accent-primary"
                      checked={filters.hideCompletedTurnToolErrors === true}
                      onChange={(event) =>
                        onChange({
                          ...filters,
                          hideCompletedTurnToolErrors: event.target.checked,
                        })
                      }
                    />
                    <span className="text-pretty">
                      {t("nativeSpanFilters.hideCompletedTurnToolErrors")}
                    </span>
                  </label>
                </div>
              </fieldset>
              <div className="space-y-2">
                <SpanThresholdInput
                  key={`duration:${thresholdRevision}`}
                  label={t("nativeSpanFilters.duration")}
                  kind="duration"
                  value={filters.minDurationMs}
                  defaultValue={durationDefaultMs}
                  onChange={(value) =>
                    onChange({ ...filters, minDurationMs: value })
                  }
                />
                <SpanThresholdInput
                  key={`tokens:${thresholdRevision}`}
                  resetKey={tokenMetric}
                  label={t(tokenLabelKey)}
                  helpText={t("nativeSpanFilters.usageHint")}
                  kind="tokens"
                  value={filters.minTokens}
                  defaultValue={tokenDefault}
                  onChange={(value) =>
                    onChange({ ...filters, minTokens: value })
                  }
                  headerAccessory={
                    <div
                      role="group"
                      aria-label={t("nativeSpanFilters.tokenMetric")}
                      data-testid="span-filter-token-metric"
                      className="flex rounded-md border border-input bg-muted/30 p-0.5"
                    >
                      {(["total", "input", "output"] as const).map((metric) => {
                        const label = t(
                          `nativeSpanFilters.${metric === "total" ? "tokenMetricTotal" : metric === "input" ? "tokenMetricInput" : "tokenMetricOutput"}`
                        )
                        return (
                          <button
                            key={metric}
                            type="button"
                            data-token-metric={metric}
                            aria-label={t(
                              "nativeSpanFilters.tokenMetricChoice",
                              {
                                metric: label,
                              }
                            )}
                            aria-pressed={tokenMetric === metric}
                            onClick={() =>
                              onChange({ ...filters, tokenMetric: metric })
                            }
                            className="h-6 min-w-0 flex-1 rounded px-2 text-[11px] text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none aria-pressed:bg-background aria-pressed:font-medium aria-pressed:text-foreground aria-pressed:shadow-sm"
                          >
                            {label}
                          </button>
                        )
                      })}
                    </div>
                  }
                />
              </div>
              <SpanCostFilterInput
                value={filters.minCost ?? null}
                onChange={(minCost) => onChange({ ...filters, minCost })}
                defaultCurrency={costCurrency}
                rates={costRates}
                rateDate={costRateDate}
                exchangeStatus={costExchangeStatus}
                resetKey={thresholdRevision}
              />
              <label className="block text-xs">
                <span className="mb-1 block">
                  {t("nativeSpanFilters.model")}
                </span>
                <span className="relative block">
                  <select
                    data-testid="span-filter-model"
                    className="h-8 w-full min-w-0 appearance-none rounded-md border border-input bg-background pr-7 pl-2 text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    value={filters.model}
                    onChange={(event) =>
                      onChange({ ...filters, model: event.target.value })
                    }
                  >
                    <option value="">{t("nativeSpanFilters.anyModel")}</option>
                    {filters.model && !models.includes(filters.model) && (
                      <option value={filters.model}>
                        {filters.model} ·{" "}
                        {t("nativeSpanFilters.unavailableOption")}
                      </option>
                    )}
                    {models.map((model) => (
                      <option key={model} value={model}>
                        {model}
                      </option>
                    ))}
                  </select>
                  <ChevronDown
                    aria-hidden="true"
                    className="pointer-events-none absolute top-1/2 right-2 size-3.5 -translate-y-1/2 text-muted-foreground"
                  />
                </span>
              </label>
              {availableTools.length > 0 && (
                <fieldset className="min-w-0">
                  <legend className="mb-1.5 flex w-full items-center justify-between gap-2 text-xs">
                    <span>{t("nativeSpanFilters.toolNames")}</span>
                    <span className="relative inline-flex shrink-0">
                      <select
                        data-testid="span-filter-tool-mode"
                        aria-label={t("nativeSpanFilters.toolNameMode")}
                        className="h-7 min-w-0 appearance-none rounded-md border border-input bg-background pr-6 pl-2 text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        value={filters.toolNameMode ?? "include"}
                        onChange={(event) =>
                          onChange({
                            ...filters,
                            toolNameMode: event.target
                              .value as SpanFilters["toolNameMode"],
                          })
                        }
                      >
                        <option value="include">
                          {t("nativeSpanFilters.toolModeInclude")}
                        </option>
                        <option value="exclude">
                          {t("nativeSpanFilters.toolModeExclude")}
                        </option>
                      </select>
                      <ChevronDown
                        aria-hidden="true"
                        className="pointer-events-none absolute top-1/2 right-1.5 size-3.5 -translate-y-1/2 text-muted-foreground"
                      />
                    </span>
                  </legend>
                  {(availableTools.length > 6 || toolSearch.trim()) && (
                    <Input
                      data-trace-filter-input
                      value={toolSearch}
                      onChange={(event) => setToolSearch(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.nativeEvent.isComposing) return
                        if (event.key === "Escape") {
                          event.preventDefault()
                          event.stopPropagation()
                          setToolSearch("")
                        }
                      }}
                      aria-label={t("nativeSpanFilters.searchTools")}
                      placeholder={t("nativeSpanFilters.searchTools")}
                      className="mb-1.5 h-7 text-xs"
                    />
                  )}
                  <div className="max-h-28 space-y-1.5 overflow-y-auto">
                    {availableTools
                      .filter((name) =>
                        name.toLowerCase().includes(toolSearch.toLowerCase())
                      )
                      .map((name) => (
                        <label
                          key={name}
                          className="flex min-w-0 items-center gap-1.5 text-xs"
                        >
                          <input
                            type="checkbox"
                            className="size-3.5 shrink-0 accent-primary"
                            data-tool-name={name}
                            checked={filters.toolNames.includes(name)}
                            onChange={(event) =>
                              onChange({
                                ...filters,
                                toolNames: event.target.checked
                                  ? [...filters.toolNames, name]
                                  : filters.toolNames.filter(
                                      (item) => item !== name
                                    ),
                              })
                            }
                          />
                          <span className="min-w-0 break-all">{name}</span>
                          {!toolNames.includes(name) && (
                            <span className="text-muted-foreground">
                              {t("nativeSpanFilters.unavailableOption")}
                            </span>
                          )}
                        </label>
                      ))}
                    {!availableTools.some((name) =>
                      name.toLowerCase().includes(toolSearch.toLowerCase())
                    ) && (
                      <p className="text-xs text-muted-foreground">
                        {t("nativeSpanFilters.noTools")}
                      </p>
                    )}
                  </div>
                </fieldset>
              )}
              <fieldset className="min-w-0">
                <legend className="mb-1.5 text-xs">
                  {t("nativeSpanFilters.nodeTypes")}
                </legend>
                <div className="flex flex-wrap gap-x-3 gap-y-2">
                  {availableKinds.map((kind) => (
                    <label
                      key={kind}
                      className="flex items-center gap-1.5 text-xs"
                    >
                      <input
                        type="checkbox"
                        className="size-3.5 shrink-0 accent-primary"
                        checked={filters.kinds.includes(kind)}
                        onChange={(event) =>
                          onChange({
                            ...filters,
                            kinds: event.target.checked
                              ? [...filters.kinds, kind]
                              : filters.kinds.filter((item) => item !== kind),
                          })
                        }
                      />
                      {t(`nativeSpanFilters.kinds.${kind}`)}
                    </label>
                  ))}
                </div>
              </fieldset>
            </div>
          </div>
          {scrollState.overflow && (
            <button
              type="button"
              data-testid="span-filter-scroll-cue"
              aria-controls={panelId}
              onClick={() => {
                const viewport = controlsRef.current
                if (!viewport) return
                if (scrollState.atEnd)
                  viewport.scrollTo({ top: 0, behavior: "auto" })
                else
                  viewport.scrollBy({
                    top: Math.max(120, viewport.clientHeight * 0.8),
                    behavior: "auto",
                  })
              }}
              className="flex min-h-7 shrink-0 items-center justify-center gap-1.5 rounded-b border-t bg-muted/20 px-1 py-1 text-xs font-medium text-foreground/80 hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
            >
              {scrollState.atEnd ? (
                <>
                  {t("nativeSpanFilters.filtersTop")}
                  <ChevronUp aria-hidden="true" className="size-3 shrink-0" />
                </>
              ) : (
                <>
                  {t("nativeSpanFilters.moreFilters", {
                    groups: [
                      t("nativeSpanFilters.model"),
                      ...(availableTools.length
                        ? [t("nativeSpanFilters.toolsShort")]
                        : []),
                      t("nativeSpanFilters.typesShort"),
                    ].join(t("nativeSpanFilters.groupSeparator")),
                  })}
                  <ChevronDown aria-hidden="true" className="size-3 shrink-0" />
                </>
              )}
            </button>
          )}
        </div>
      )}
    </div>
  )
}
