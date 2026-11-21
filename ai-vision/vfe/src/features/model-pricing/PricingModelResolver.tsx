import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import { ArrowRight, ChevronDown, Loader2, Search } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Dialog, DialogTitle } from "@/components/ui/dialog"
import { DialogContent } from "@/components/dialogs/DialogContent"
import { PricingLabel } from "./PricingLabel"
import { searchPricingCatalog } from "./api"
import { RateSummary, type RuleDraft } from "./PricingRuleForm"
import { CostSummary } from "./CostSummary"
import { knownPricingIdentity, pricingFailureReasons } from "./presentation"
import type { CostCoverage } from "./types"

export interface ModelIdentity {
  provider: string | null
  model: string | null
}

/** Matching is a model-scoped task; candidates never change a price by themselves. */
export function PricingModelResolver({
  spaceId,
  identity,
  coverage,
  canManage,
  onClose,
  onDraft,
  onReturnFocus,
}: {
  spaceId: string
  identity: ModelIdentity
  coverage?: CostCoverage
  canManage: boolean
  onClose: () => void
  onDraft: (draft: RuleDraft) => void
  onReturnFocus: () => void
}) {
  const { t } = useTranslation()
  const observedProvider = knownPricingIdentity(identity.provider)
  const observedModel = knownPricingIdentity(identity.model)
  const [provider, setProvider] = useState(observedProvider ?? "")
  const [model, setModel] = useState(observedModel ?? "")
  const [query, setQuery] = useState("")
  const [reference, setReference] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [filters, setFilters] = useState<
    Parameters<typeof searchPricingCatalog>[1] | null
  >(
    observedProvider && observedModel
      ? { provider: observedProvider, model: observedModel }
      : null
  )
  const candidates = useQuery({
    queryKey: ["model-pricing-catalog", spaceId, filters],
    queryFn: () => searchPricingCatalog(spaceId, filters ?? {}),
    enabled: filters !== null,
  })
  const hasIdentity = Boolean(
    knownPricingIdentity(provider) && knownPricingIdentity(model)
  )
  const search = () => {
    setExpanded(false)
    setFilters({
      provider: provider.trim(),
      model: model.trim(),
      q: query.trim(),
      reference,
    })
  }
  // Inputs may be edited after a search. Never attach an older candidate to a
  // different billing identity without explicitly searching it again.
  const currentSearch = Boolean(
    filters &&
    filters.provider === provider.trim() &&
    filters.model === model.trim() &&
    (filters.q ?? "") === query.trim() &&
    Boolean(filters.reference) === reference
  )
  const chooseDraft = (kind: "manual" | "subscription") =>
    onDraft({ provider: provider.trim(), model: model.trim(), kind })
  const rows = candidates.data?.candidates ?? []
  const visible = expanded ? rows : rows.slice(0, 5)
  const monetaryGaps = pricingFailureReasons(
    coverage?.unknownReasons ?? {}
  ).filter(({ code }) => code !== "subscription_billing")
  const equivalentGaps = pricingFailureReasons(
    coverage?.listPriceEquivalentUnknownReasons ?? {}
  )

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        size="detail"
        className="overflow-y-auto"
        aria-describedby={undefined}
        onCloseAutoFocus={(event) => {
          event.preventDefault()
          onReturnFocus()
        }}
      >
        <div className="pr-6">
          <DialogTitle>
            {t(
              coverage
                ? "modelPricing.workspace.modelDetailsTitle"
                : "modelPricing.workspace.resolveTitle"
            )}
          </DialogTitle>
        </div>
        {coverage && (
          <section
            data-pricing-model-result
            className="space-y-3 rounded-lg border bg-muted/20 p-4"
          >
            <CostSummary coverage={coverage} compact showCurrencyNote={false} />
            {monetaryGaps.length > 0 && (
              <div className="space-y-1 text-xs leading-relaxed text-muted-foreground">
                {monetaryGaps.map(({ code, count }) => (
                  <p key={code}>
                    {t("modelPricing.cost.reasonCount", {
                      count,
                      reason: t(`modelPricing.reasons.${code}`, {
                        defaultValue: t("modelPricing.reasons.unavailable"),
                      }),
                    })}
                  </p>
                ))}
              </div>
            )}
            {equivalentGaps.length > 0 && (
              <div
                data-pricing-equivalent-gaps
                className="space-y-1 text-xs leading-relaxed text-muted-foreground"
              >
                <h4 className="font-medium text-foreground">
                  {t("modelPricing.workspace.equivalentGapTitle")}
                </h4>
                {equivalentGaps.map(({ code, count }) => (
                  <p key={code}>
                    {t("modelPricing.cost.reasonCount", {
                      count,
                      reason: t(`modelPricing.reasons.${code}`, {
                        defaultValue: t("modelPricing.reasons.unavailable"),
                      }),
                    })}
                  </p>
                ))}
              </div>
            )}
          </section>
        )}
        {coverage && (!observedProvider || !observedModel) && (
          <p className="text-xs leading-relaxed text-muted-foreground">
            {t("modelPricing.workspace.missingIdentityHint")}
          </p>
        )}
        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault()
            search()
          }}
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <PricingLabel htmlFor="catalog-provider">
                {t("modelPricing.provider")}
              </PricingLabel>
              <Input
                id="catalog-provider"
                value={provider}
                onChange={(event) => setProvider(event.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <PricingLabel htmlFor="catalog-model">
                {t("modelPricing.model")}
              </PricingLabel>
              <Input
                id="catalog-model"
                value={model}
                onChange={(event) => setModel(event.target.value)}
              />
            </div>
          </div>
          <div className="flex flex-wrap items-end gap-2">
            <div className="min-w-0 flex-1 space-y-1.5">
              <PricingLabel htmlFor="catalog-query">
                {t("modelPricing.search.query")}
              </PricingLabel>
              <Input
                id="catalog-query"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </div>
            <Button
              type="submit"
              variant="outline"
              disabled={candidates.isFetching}
            >
              <Search className="size-4" />
              {t("modelPricing.search.search")}
            </Button>
          </div>
          <label className="flex cursor-pointer items-start gap-2 text-xs leading-relaxed text-muted-foreground">
            <input
              type="checkbox"
              className="mt-0.5 size-4 shrink-0 accent-primary"
              checked={reference}
              onChange={(event) => setReference(event.target.checked)}
            />
            <span>{t("modelPricing.search.crossProvider")}</span>
          </label>
        </form>
        <div className="min-h-24 space-y-2" aria-live="polite">
          {candidates.isFetching ? (
            <p
              role="status"
              className="flex items-center gap-2 py-5 text-sm text-muted-foreground"
            >
              <Loader2 className="size-4 animate-spin" />
              {t("common.loading")}
            </p>
          ) : candidates.isError ? (
            <div role="alert" className="space-y-2 py-4 text-sm">
              <p>{t("modelPricing.search.error")}</p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => void candidates.refetch()}
              >
                {t("common.retry")}
              </Button>
            </div>
          ) : filters && rows.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              {t("modelPricing.search.empty")}
            </p>
          ) : (
            <div className="divide-y rounded-lg border">
              {visible.map((candidate) => (
                <div
                  key={`${candidate.provider}:${candidate.model}`}
                  data-pricing-candidate
                  className="space-y-3 p-4"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 flex-1 space-y-1">
                      <h3 className="text-sm font-medium break-all">
                        {candidate.model}
                      </h3>
                      <p className="text-xs text-muted-foreground">
                        {candidate.provider}
                      </p>
                    </div>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={
                        !canManage ||
                        !candidate.supported ||
                        !hasIdentity ||
                        !currentSearch
                      }
                      onClick={() =>
                        onDraft({
                          provider: provider.trim(),
                          model: model.trim(),
                          kind:
                            candidate.provider.toLowerCase() ===
                            provider.trim().toLowerCase()
                              ? "alias"
                              : "reference",
                          candidate,
                          catalogSnapshot:
                            candidate.catalogSnapshot ??
                            candidates.data?.catalog.snapshotId,
                        })
                      }
                    >
                      {t(
                        candidate.provider.toLowerCase() ===
                          provider.trim().toLowerCase()
                          ? "modelPricing.search.alias"
                          : "modelPricing.search.reference"
                      )}
                      <ArrowRight className="size-3.5" />
                    </Button>
                  </div>
                  <RateSummary rates={candidate.rates} />
                  {!candidate.supported && (
                    <p className="text-xs text-muted-foreground">
                      {t("modelPricing.search.unsupported")}
                    </p>
                  )}
                  <details className="text-xs text-muted-foreground">
                    <summary className="w-fit cursor-pointer rounded-sm focus-visible:outline-2 focus-visible:outline-ring">
                      {t("modelPricing.workspace.matchDetails")}
                    </summary>
                    <div className="space-y-1 pt-2">
                      {candidate.reasons.map((reason) => (
                        <p key={reason}>
                          {t(`modelPricing.matches.${reason}`, {
                            defaultValue: t("modelPricing.matches.candidate"),
                          })}
                        </p>
                      ))}
                      {candidate.unsupportedReasons.map((reason) => (
                        <p key={reason}>
                          {t(`modelPricing.reasons.${reason}`, {
                            defaultValue: t(
                              "modelPricing.reasons.unsupported_pricing"
                            ),
                          })}
                        </p>
                      ))}
                    </div>
                  </details>
                </div>
              ))}
            </div>
          )}
          {!expanded && rows.length > 5 && (
            <Button
              variant="ghost"
              size="sm"
              className="w-full"
              onClick={() => setExpanded(true)}
            >
              {t("modelPricing.workspace.moreCandidates", {
                count: rows.length - 5,
              })}
              <ChevronDown className="size-3.5" />
            </Button>
          )}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-4">
          <span className="text-xs text-muted-foreground">
            {t("modelPricing.workspace.otherOptions")}
          </span>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={!canManage}
              onClick={() => chooseDraft("manual")}
            >
              {t("modelPricing.workspace.manualAction")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!canManage}
              onClick={() => chooseDraft("subscription")}
            >
              {t("modelPricing.workspace.subscriptionAction")}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
