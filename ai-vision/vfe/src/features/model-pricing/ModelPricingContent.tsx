import { useEffect, useMemo, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import { useSearchParams } from "react-router-dom"
import {
  Check,
  ChevronRight,
  Loader2,
  Plus,
  Search,
  Settings2,
  Trash2,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { FeatureBetaBadge } from "@/components/FeatureBetaBadge"
import { Dialog, DialogDescription, DialogTitle } from "@/components/ui/dialog"
import { DialogContent } from "@/components/dialogs/DialogContent"
import { useTimeFilter } from "@/contexts/TimeFilterContext"
import { getPresetTimeWindow } from "@/lib/timeFilter"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  deletePricingRule,
  getModelPricing,
  getPricingInventory,
  syncPricingCatalog,
} from "./api"
import { CostSummary } from "./CostSummary"
import { CompactCurrencySelector } from "./PricingCurrency"
import { PricingRuleForm, RateSummary, type RuleDraft } from "./PricingRuleForm"
import {
  PricingModelResolver,
  type ModelIdentity,
} from "./PricingModelResolver"
import { PricingSettingsDialog } from "./PricingSettingsDialog"
import { pricingModelState, pricingNeedsAttention } from "./presentation"
import { isValidPricingWindow } from "./timeRange"
import {
  invalidateModelPricingQueries,
  PRICING_INVENTORY_QUERY,
} from "./pricingQueryInvalidation"
import type {
  CostCoverage,
  PricingInventory,
  PricingRule,
  PricingSyncSource,
} from "./types"

type ModelRow = PricingInventory["models"][number]

function needsAttention(row: ModelRow) {
  return pricingNeedsAttention(row.costCoverage)
}

export default function ModelPricingContent({ spaceId }: { spaceId: string }) {
  const { t, i18n } = useTranslation()
  const client = useQueryClient()
  const returnFocus = useRef<HTMLElement | null>(null)
  const openingRule = useRef(false)
  const [searchParams, setSearchParams] = useSearchParams()
  const tab = searchParams.get("tab") === "rules" ? "rules" : "overview"
  const setTab = (next: "overview" | "rules") => {
    setSearchParams(
      (current) => {
        const params = new URLSearchParams(current)
        if (next === "rules") params.set("tab", next)
        else params.delete("tab")
        return params
      },
      { replace: true }
    )
  }
  const { timeRange, isCustom, fromTimestamp, toTimestamp, queryRefreshTick } =
    useTimeFilter()
  const [settings, setSettings] = useState(false)
  const [draft, setDraft] = useState<RuleDraft | null>(null)
  const [resolver, setResolver] = useState<
    (ModelIdentity & { coverage?: CostCoverage }) | null
  >(null)
  const [deleting, setDeleting] = useState<PricingRule | null>(null)
  const [filter, setFilter] = useState<"all" | "attention">("all")
  const criteria = useMemo(
    () => ({
      window: isCustom
        ? { from: fromTimestamp, to: toTimestamp }
        : getPresetTimeWindow(timeRange),
      refreshTick: queryRefreshTick,
    }),
    [timeRange, isCustom, fromTimestamp, toTimestamp, queryRefreshTick]
  )
  const { window } = criteria
  const windowValid = isValidPricingWindow(window)
  const inventoryEnabled = tab === "overview" && windowValid
  const [syncSource, setSyncSource] = useState<PricingSyncSource>("auto")
  const summary = useQuery({
    queryKey: ["model-pricing", spaceId],
    queryFn: () => getModelPricing(spaceId),
  })
  const inventory = useQuery({
    queryKey: [PRICING_INVENTORY_QUERY, spaceId, window],
    queryFn: ({ signal }) => getPricingInventory(spaceId, window, signal),
    enabled: inventoryEnabled,
    retry: false,
  })
  const refetchInventory = inventory.refetch
  const previousCriteria = useRef(criteria)
  useEffect(() => {
    const previous = previousCriteria.current
    previousCriteria.current = criteria
    // A moving preset changes the query key. Refetch fixed or unchanged
    // bounds in place so the last complete result survives a refresh failure.
    if (
      inventoryEnabled &&
      previous.refreshTick !== criteria.refreshTick &&
      previous.window.from === window.from &&
      previous.window.to === window.to
    ) {
      void refetchInventory()
    }
  }, [criteria, inventoryEnabled, refetchInventory, window])
  const canManage = summary.data?.canManage === true
  const refresh = () => invalidateModelPricingQueries(client, spaceId)
  const sync = useMutation({
    mutationFn: (source: PricingSyncSource) =>
      syncPricingCatalog(spaceId, source),
    onSettled: (_data, error) =>
      invalidateModelPricingQueries(
        client,
        spaceId,
        error ? "space" : "global"
      ),
  })
  const remove = useMutation({
    mutationFn: (uuid: string) => deletePricingRule(spaceId, uuid),
    onSuccess: async () => {
      setDeleting(null)
      await refresh()
    },
  })
  const date = (value: string) =>
    new Date(value).toLocaleString(i18n.resolvedLanguage || i18n.language)
  const models = [...(inventory.data?.models ?? [])].sort(
    (a, b) =>
      Number(needsAttention(b)) - Number(needsAttention(a)) || b.calls - a.calls
  )
  const pending = models.filter(needsAttention)
  const visibleModels = filter === "attention" ? pending : models
  const openDraft = (next: RuleDraft) => {
    openingRule.current = true
    setResolver(null)
    setDraft(next)
  }
  const rememberFocus = () => {
    returnFocus.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null
  }
  const restoreFocus = () => {
    if (returnFocus.current?.isConnected) returnFocus.current.focus()
    else document.getElementById(`pricing-tab-${tab}`)?.focus()
  }
  const browse = () => {
    openingRule.current = false
    rememberFocus()
    setResolver({ provider: "", model: "" })
  }
  const openModel = (row: ModelRow) => {
    rememberFocus()
    openingRule.current = false
    setResolver({
      provider: row.provider,
      model: row.model,
      coverage: row.costCoverage,
    })
  }

  return (
    <div className="mx-auto w-full max-w-[90rem] min-w-0 space-y-6 px-4 py-6 sm:px-8 sm:py-8">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex flex-wrap items-center gap-2.5">
          <h1 className="text-2xl font-semibold tracking-tight">
            {t("modelPricing.title")}
          </h1>
          <FeatureBetaBadge />
        </div>
        <Button variant="outline" size="sm" onClick={() => setSettings(true)}>
          <Settings2 className="size-4" />
          {t("modelPricing.workspace.settingsTitle")}
        </Button>
      </header>
      <div
        role="tablist"
        aria-label={t("modelPricing.title")}
        className="flex gap-6 border-b"
      >
        {(["overview", "rules"] as const).map((value) => (
          <button
            key={value}
            type="button"
            role="tab"
            id={`pricing-tab-${value}`}
            aria-controls={`pricing-panel-${value}`}
            aria-selected={tab === value}
            tabIndex={tab === value ? 0 : -1}
            onKeyDown={(event) => {
              if (
                !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)
              )
                return
              event.preventDefault()
              const next =
                event.key === "Home"
                  ? "overview"
                  : event.key === "End"
                    ? "rules"
                    : tab === "overview"
                      ? "rules"
                      : "overview"
              setTab(next)
              document.getElementById(`pricing-tab-${next}`)?.focus()
            }}
            onClick={() => setTab(value)}
            className={`relative -mb-px rounded-t-sm border-b-2 px-0.5 pb-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring ${tab === value ? "border-foreground font-medium text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"}`}
          >
            {t(`modelPricing.workspace.${value}`)}
            {value === "rules" &&
              summary.data &&
              summary.data.rules.length > 0 && (
                <span className="ml-2 text-xs text-muted-foreground tabular-nums">
                  {summary.data.rules.length}
                </span>
              )}
          </button>
        ))}
      </div>
      {summary.isError && (
        <ErrorState
          label={t("modelPricing.loadError")}
          retry={() => void summary.refetch()}
        />
      )}
      {summary.data && !canManage && (
        <p className="text-xs text-muted-foreground">
          {t("modelPricing.readOnly")}
        </p>
      )}
      {tab === "overview" ? (
        <section
          role="tabpanel"
          id="pricing-panel-overview"
          aria-labelledby="pricing-tab-overview"
          className="space-y-5"
        >
          {!windowValid ? (
            <p role="alert" className="text-sm text-muted-foreground">
              {t("modelPricing.workspace.timeError")}
            </p>
          ) : inventory.isLoading ? (
            <p
              role="status"
              className="flex min-h-40 items-center gap-2 text-sm text-muted-foreground"
            >
              <Loader2 className="size-4 animate-spin" />
              {t("common.loading")}
            </p>
          ) : inventory.isError && !inventory.data ? (
            <ErrorState
              label={t("modelPricing.inventory.error")}
              retry={() => void refetchInventory()}
            />
          ) : (
            inventory.data && (
              <>
                {inventory.isError && (
                  <ErrorState
                    label={t("modelPricing.workspace.inventoryStale")}
                    retry={() => void refetchInventory()}
                  />
                )}
                {models.length > 0 ? (
                  <>
                    <div
                      data-pricing-summary-layout
                      className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-3 border-b pb-5 sm:gap-x-6"
                    >
                      <div data-pricing-overview className="min-w-0">
                        <CostSummary
                          coverage={inventory.data.costCoverage}
                          prominent
                          showCurrencyNote={false}
                        />
                      </div>
                      <CompactCurrencySelector />
                    </div>
                    <div id="pricing-models" className="@container space-y-4">
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        <h2 className="font-semibold">
                          {t("modelPricing.workspace.modelsTitle")}
                        </h2>
                        <div
                          className="inline-flex rounded-lg bg-muted/60 p-1"
                          role="group"
                          aria-label={t("modelPricing.workspace.modelFilter")}
                        >
                          {(["all", "attention"] as const).map((value) => (
                            <button
                              type="button"
                              key={value}
                              aria-pressed={filter === value}
                              onClick={() => setFilter(value)}
                              className={`rounded-md px-3 py-1.5 text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring ${filter === value ? "bg-background font-medium shadow-xs" : "text-muted-foreground hover:text-foreground"}`}
                            >
                              {t(
                                value === "all"
                                  ? "modelPricing.workspace.allModels"
                                  : "modelPricing.workspace.needsAttention"
                              )}
                              <span className="ml-1.5 text-muted-foreground tabular-nums">
                                {value === "all"
                                  ? models.length
                                  : pending.length}
                              </span>
                            </button>
                          ))}
                        </div>
                      </div>
                      {visibleModels.length === 0 ? (
                        <EmptyState
                          title={t("modelPricing.workspace.noPending")}
                          action={t("modelPricing.workspace.showAll")}
                          onAction={() => setFilter("all")}
                        />
                      ) : (
                        <>
                          <div className="divide-y @min-[44rem]:hidden">
                            {visibleModels.map((row) => {
                              const state = pricingModelState(row.costCoverage)
                              return (
                                <div
                                  key={JSON.stringify([
                                    row.provider,
                                    row.model,
                                  ])}
                                  data-pricing-model
                                  className="space-y-2.5 py-3"
                                >
                                  <div className="flex items-start justify-between gap-3">
                                    <div className="min-w-0 space-y-1">
                                      <p className="text-sm font-medium break-all">
                                        {row.model ||
                                          t(
                                            "modelPricing.inventory.missingModel"
                                          )}
                                      </p>
                                      <p className="text-xs break-all text-muted-foreground">
                                        {row.provider ||
                                          t(
                                            "modelPricing.inventory.missingProvider"
                                          )}
                                      </p>
                                    </div>
                                    <span className="shrink-0 text-xs text-muted-foreground">
                                      {t("modelPricing.inventory.callCount", {
                                        count: row.calls,
                                        formattedCount:
                                          row.calls.toLocaleString(
                                            i18n.language
                                          ),
                                      })}
                                    </span>
                                  </div>
                                  <div className="flex items-end justify-between gap-3">
                                    <div className="min-w-0 space-y-1">
                                      <CostSummary
                                        coverage={row.costCoverage}
                                        compact
                                        showCurrencyNote={false}
                                      />
                                      <p className="text-xs text-muted-foreground">
                                        {t(
                                          `modelPricing.workspace.states.${state}`
                                        )}
                                      </p>
                                    </div>
                                    <Button
                                      className="shrink-0"
                                      variant="outline"
                                      size="sm"
                                      data-pricing-model-action
                                      onClick={() => openModel(row)}
                                    >
                                      {t(
                                        state === "missingPrice"
                                          ? "modelPricing.inventory.find"
                                          : "modelPricing.workspace.inspect"
                                      )}
                                      <ChevronRight className="size-3.5" />
                                    </Button>
                                  </div>
                                </div>
                              )
                            })}
                          </div>
                          <div className="hidden @min-[44rem]:block">
                            <Table className="table-fixed">
                              <TableHeader className="bg-muted/40 [&_th]:h-10 [&_th]:text-xs [&_th]:text-muted-foreground">
                                <TableRow className="hover:bg-transparent">
                                  <TableHead scope="col">
                                    {t("modelPricing.model")}
                                  </TableHead>
                                  <TableHead scope="col">
                                    {t("modelPricing.inventory.calls")}
                                  </TableHead>
                                  <TableHead scope="col">
                                    {t("modelPricing.workspace.valuation")}
                                  </TableHead>
                                  <TableHead scope="col">
                                    {t("modelPricing.workspace.status")}
                                  </TableHead>
                                  <TableHead
                                    scope="col"
                                    className="w-40 pr-10 text-right"
                                  >
                                    {t("modelPricing.actions")}
                                  </TableHead>
                                </TableRow>
                              </TableHeader>
                              <TableBody>
                                {visibleModels.map((row) => {
                                  const state = pricingModelState(
                                    row.costCoverage
                                  )
                                  return (
                                    <TableRow
                                      key={JSON.stringify([
                                        row.provider,
                                        row.model,
                                      ])}
                                      data-pricing-model
                                    >
                                      <TableCell className="whitespace-normal">
                                        <p className="font-medium break-all">
                                          {row.model ||
                                            t(
                                              "modelPricing.inventory.missingModel"
                                            )}
                                        </p>
                                        <p className="mt-1 text-xs break-all text-muted-foreground">
                                          {row.provider ||
                                            t(
                                              "modelPricing.inventory.missingProvider"
                                            )}
                                        </p>
                                      </TableCell>
                                      <TableCell className="tabular-nums">
                                        {row.calls.toLocaleString(
                                          i18n.language
                                        )}
                                      </TableCell>
                                      <TableCell className="whitespace-normal tabular-nums">
                                        <CostSummary
                                          coverage={row.costCoverage}
                                          compact
                                          showCurrencyNote={false}
                                        />
                                      </TableCell>
                                      <TableCell>
                                        <span
                                          className={`inline-flex items-center gap-1.5 text-xs whitespace-normal ${needsAttention(row) ? "text-foreground" : "text-muted-foreground"}`}
                                        >
                                          {state === "complete" && (
                                            <Check className="size-3.5" />
                                          )}
                                          {t(
                                            `modelPricing.workspace.states.${state}`
                                          )}
                                        </span>
                                      </TableCell>
                                      <TableCell className="text-right">
                                        <Button
                                          size="sm"
                                          variant="ghost"
                                          className="px-2"
                                          data-pricing-model-action
                                          onClick={() => openModel(row)}
                                        >
                                          {t(
                                            state === "missingPrice"
                                              ? "modelPricing.inventory.find"
                                              : "modelPricing.workspace.inspect"
                                          )}
                                          <ChevronRight className="size-3.5" />
                                        </Button>
                                      </TableCell>
                                    </TableRow>
                                  )
                                })}
                              </TableBody>
                            </Table>
                          </div>
                        </>
                      )}
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-muted-foreground"
                        onClick={browse}
                      >
                        <Search className="size-3.5" />
                        {t("modelPricing.workspace.browse")}
                      </Button>
                    </div>
                  </>
                ) : (
                  <EmptyState
                    title={t("modelPricing.workspace.emptyTitle")}
                    action={t("modelPricing.workspace.browse")}
                    onAction={browse}
                  />
                )}
              </>
            )
          )}
        </section>
      ) : (
        <section
          role="tabpanel"
          id="pricing-panel-rules"
          aria-labelledby="pricing-tab-rules"
          className="space-y-5"
        >
          <div className="flex flex-wrap items-start justify-between gap-3">
            <p className="max-w-prose text-sm text-muted-foreground">
              {t("modelPricing.rules.description")}
            </p>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline" onClick={browse}>
                <Search className="size-3.5" />
                {t("modelPricing.workspace.browse")}
              </Button>
              <Button
                size="sm"
                disabled={!canManage}
                onClick={() => {
                  rememberFocus()
                  setDraft({ provider: "", model: "", kind: "manual" })
                }}
              >
                <Plus className="size-4" />
                {t("modelPricing.rule.create")}
              </Button>
            </div>
          </div>
          {summary.isLoading ? (
            <p role="status" className="py-8 text-sm text-muted-foreground">
              {t("common.loading")}
            </p>
          ) : summary.data?.rules.length === 0 ? (
            <EmptyState
              title={t("modelPricing.rules.empty")}
              description={t("modelPricing.workspace.rulesEmptyHint")}
            />
          ) : (
            summary.data && (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t("modelPricing.model")}</TableHead>
                      <TableHead>{t("modelPricing.rules.kind")}</TableHead>
                      <TableHead>
                        {t("modelPricing.workspace.unitPrice")}
                      </TableHead>
                      <TableHead>{t("modelPricing.rule.effective")}</TableHead>
                      <TableHead className="text-right">
                        <span className="sr-only">
                          {t("modelPricing.actions")}
                        </span>
                      </TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {summary.data.rules.map((rule) => (
                      <TableRow key={rule.uuid}>
                        <TableCell className="max-w-80 min-w-44 py-3 whitespace-normal">
                          <p className="font-medium break-all">{rule.model}</p>
                          <p className="mt-1 text-xs break-all text-muted-foreground">
                            {rule.provider}
                          </p>
                          {rule.catalogModel && (
                            <p className="mt-2 text-xs break-all text-muted-foreground">
                              {t("modelPricing.rule.selectedCatalog")}:{" "}
                              {rule.catalogProvider} / {rule.catalogModel}
                            </p>
                          )}
                        </TableCell>
                        <TableCell className="text-xs whitespace-nowrap">
                          {t(`modelPricing.kinds.${rule.kind}`)}
                        </TableCell>
                        <TableCell className="min-w-48">
                          {rule.kind === "subscription" ? (
                            <span className="text-muted-foreground">—</span>
                          ) : (
                            <RateSummary rates={rule.rates} />
                          )}
                        </TableCell>
                        <TableCell className="text-xs whitespace-nowrap">
                          {date(rule.effectiveFrom)}
                        </TableCell>
                        <TableCell className="text-right">
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label={t("modelPricing.rules.deleteLabel", {
                              model: rule.model,
                            })}
                            disabled={!canManage}
                            onClick={() => {
                              remove.reset()
                              rememberFocus()
                              setDeleting(rule)
                            }}
                          >
                            <Trash2 className="size-4" />
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )
          )}
        </section>
      )}
      {settings && (
        <PricingSettingsDialog
          spaceId={spaceId}
          catalog={summary.data?.catalog}
          canManage={canManage}
          syncSource={syncSource}
          onSourceChange={setSyncSource}
          syncPending={sync.isPending}
          syncError={sync.isError}
          onSync={() => sync.mutate(syncSource)}
          onClose={() => setSettings(false)}
        />
      )}
      {resolver && (
        <PricingModelResolver
          key={JSON.stringify([spaceId, resolver.provider, resolver.model])}
          spaceId={spaceId}
          identity={resolver}
          coverage={resolver.coverage}
          canManage={canManage}
          onClose={() => setResolver(null)}
          onDraft={openDraft}
          onReturnFocus={() => {
            if (!openingRule.current) restoreFocus()
          }}
        />
      )}
      {draft && (
        <PricingRuleForm
          spaceId={spaceId}
          draft={draft}
          onClose={() => setDraft(null)}
          onSaved={refresh}
          onReturnFocus={restoreFocus}
        />
      )}
      {deleting && (
        <Dialog
          open
          onOpenChange={(open) => {
            if (!open && !remove.isPending) setDeleting(null)
          }}
        >
          <DialogContent
            size="compact"
            onCloseAutoFocus={(event) => {
              event.preventDefault()
              restoreFocus()
            }}
          >
            <DialogTitle>{t("modelPricing.rules.deleteTitle")}</DialogTitle>
            <DialogDescription>
              {t("modelPricing.rules.deleteDescription")}
            </DialogDescription>
            <p className="text-sm font-medium break-all">
              {deleting.provider} / {deleting.model}
            </p>
            {remove.isError && (
              <p role="alert" className="text-sm text-destructive">
                {t("modelPricing.rules.deleteError")}
              </p>
            )}
            <div className="flex justify-end gap-2">
              <Button
                variant="outline"
                disabled={remove.isPending}
                onClick={() => setDeleting(null)}
              >
                {t("common.cancel")}
              </Button>
              <Button
                variant="destructive"
                disabled={remove.isPending}
                onClick={() => remove.mutate(deleting.uuid)}
              >
                {t("modelPricing.rules.delete")}
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      )}
    </div>
  )
}

function EmptyState({
  title,
  description,
  action,
  onAction,
}: {
  title: string
  description?: string
  action?: string
  onAction?: () => void
}) {
  return (
    <div className="space-y-2 rounded-lg border border-dashed px-6 py-10 text-center">
      <h3 className="text-sm font-medium">{title}</h3>
      {description && (
        <p className="mx-auto max-w-prose text-sm leading-relaxed text-muted-foreground">
          {description}
        </p>
      )}
      {action && (
        <Button className="mt-2" variant="outline" size="sm" onClick={onAction}>
          {action}
        </Button>
      )}
    </div>
  )
}
function ErrorState({ label, retry }: { label: string; retry: () => void }) {
  const { t } = useTranslation()
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-destructive/20 bg-destructive/5 p-4"
    >
      <p className="text-sm">{label}</p>
      <Button size="sm" variant="outline" onClick={retry}>
        {t("common.retry")}
      </Button>
    </div>
  )
}
