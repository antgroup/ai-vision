import { useState } from "react"
import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { PricingLabel as Label } from "./PricingLabel"
import { Badge } from "@/components/ui/badge"
import { Dialog, DialogTitle } from "@/components/ui/dialog"
import { DialogContent } from "@/components/dialogs/DialogContent"
import { createPricingRule } from "./api"
import {
  emptyRates,
  knownPricingIdentity,
  rateFields,
  validManualRates,
} from "./presentation"
import type { PricingCandidate, PricingRates, PricingRuleKind } from "./types"
import { usePricingCurrency } from "./PricingCurrency"

export interface RuleDraft {
  provider: string
  model: string
  kind: PricingRuleKind
  candidate?: PricingCandidate
  catalogSnapshot?: string | null
}
export function PricingRuleForm({
  spaceId,
  draft,
  onClose,
  onSaved,
  onReturnFocus,
}: {
  spaceId: string
  draft: RuleDraft
  onClose: () => void
  onSaved: () => Promise<void>
  onReturnFocus?: () => void
}) {
  const { t, i18n } = useTranslation()
  const [provider, setProvider] = useState(draft.provider)
  const [model, setModel] = useState(draft.model)
  const [kind, setKind] = useState(draft.kind)
  const [effective, setEffective] = useState("")
  const [rates, setRates] = useState<PricingRates>(
    draft.candidate?.rates ?? { ...emptyRates }
  )
  const [review, setReview] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(false)
  const effectiveDate = effective ? new Date(effective) : null
  const valid = Boolean(
    knownPricingIdentity(provider) &&
    knownPricingIdentity(model) &&
    effectiveDate &&
    Number.isFinite(effectiveDate.getTime()) &&
    (kind !== "manual" || validManualRates(rates))
  )
  const save = async () => {
    if (!valid || !review || saving) return
    setSaving(true)
    setError(false)
    try {
      await createPricingRule(spaceId, {
        provider: provider.trim(),
        model: model.trim(),
        kind,
        effectiveFrom: effectiveDate!.toISOString(),
        ...(kind === "manual"
          ? { rates }
          : kind === "alias" || kind === "reference"
            ? {
                catalogProvider: draft.candidate!.provider,
                catalogModel: draft.candidate!.model,
                catalogSnapshot: draft.catalogSnapshot,
              }
            : {}),
      })
      await onSaved()
      onClose()
    } catch {
      setError(true)
    } finally {
      setSaving(false)
    }
  }
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !saving) onClose()
      }}
    >
      <DialogContent
        size="form"
        className="overflow-y-auto"
        aria-describedby={undefined}
        showCloseButton={!saving}
        onCloseAutoFocus={
          onReturnFocus
            ? (event) => {
                event.preventDefault()
                onReturnFocus()
              }
            : undefined
        }
      >
        <div className="flex flex-wrap items-center gap-2 pr-6">
          <DialogTitle>{t("modelPricing.rule.title")}</DialogTitle>
          <Badge variant="secondary">{t(`modelPricing.kinds.${kind}`)}</Badge>
        </div>
        {kind === "reference" && (
          <p className="rounded-lg border p-3 text-sm text-muted-foreground">
            {t("modelPricing.rule.referenceWarning")}
          </p>
        )}
        {review ? (
          <div
            data-testid="pricing-rule-review"
            className="space-y-3 rounded-xl border bg-muted/30 p-4"
          >
            <h3 className="font-medium">{t("modelPricing.rule.review")}</h3>
            <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">
              <dt className="text-muted-foreground">
                {t("modelPricing.provider")}
              </dt>
              <dd className="font-mono break-all">{provider.trim()}</dd>
              <dt className="text-muted-foreground">
                {t("modelPricing.model")}
              </dt>
              <dd className="font-mono break-all">{model.trim()}</dd>
              <dt className="text-muted-foreground">
                {t("modelPricing.rule.effective")}
              </dt>
              <dd>
                {effectiveDate?.toLocaleString(
                  i18n.resolvedLanguage || i18n.language
                )}
              </dd>
            </dl>
            <p className="text-xs text-muted-foreground">
              {t(
                kind === "subscription"
                  ? "modelPricing.rule.subscriptionRange"
                  : "modelPricing.rule.range"
              )}
            </p>
            {draft.candidate && (
              <p className="text-xs break-all">
                {t("modelPricing.rule.selectedCatalog")}:{" "}
                <span className="font-mono">
                  {draft.candidate.provider} / {draft.candidate.model}
                </span>
              </p>
            )}
            {kind !== "subscription" && (
              <RateSummary rates={rates} preserveUsd={kind === "manual"} />
            )}
          </div>
        ) : (
          <div className="space-y-4">
            {(draft.kind === "manual" || draft.kind === "subscription") && (
              <div className="space-y-2">
                <Label htmlFor="pricing-rule-kind">
                  {t("modelPricing.rule.billingType")}
                </Label>
                <select
                  id="pricing-rule-kind"
                  value={kind}
                  onChange={(event) =>
                    setKind(event.target.value as "manual" | "subscription")
                  }
                  className="h-9 w-full min-w-0 rounded-md border border-input bg-background px-3 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
                >
                  <option value="manual">
                    {t("modelPricing.kinds.manual")}
                  </option>
                  <option value="subscription">
                    {t("modelPricing.kinds.subscription")}
                  </option>
                </select>
              </div>
            )}
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="pricing-provider">
                  {t("modelPricing.provider")}
                </Label>
                <Input
                  id="pricing-provider"
                  value={provider}
                  onChange={(event) => setProvider(event.target.value)}
                  placeholder={t("modelPricing.rule.providerPlaceholder")}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="pricing-model">{t("modelPricing.model")}</Label>
                <Input
                  id="pricing-model"
                  value={model}
                  onChange={(event) => setModel(event.target.value)}
                  placeholder={t("modelPricing.rule.modelPlaceholder")}
                />
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              {t("modelPricing.rule.identityHint")}
            </p>
            {draft.candidate && (
              <div className="space-y-2 rounded-lg border p-3">
                <p className="text-xs break-all">
                  {t("modelPricing.rule.selectedCatalog")}:{" "}
                  <span className="font-mono">
                    {draft.candidate.provider} / {draft.candidate.model}
                  </span>
                </p>
                <RateSummary rates={rates} />
              </div>
            )}
            <div className="space-y-2">
              <Label htmlFor="pricing-effective">
                {t("modelPricing.rule.effective")}
              </Label>
              <Input
                id="pricing-effective"
                type="datetime-local"
                value={effective}
                onChange={(event) => setEffective(event.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                {t("modelPricing.rule.effectiveHint")}
              </p>
            </div>
            {kind === "manual" && (
              <fieldset className="space-y-3">
                <legend className="mb-3 text-sm font-medium">
                  {t("modelPricing.rates.title")}
                </legend>
                <div className="grid gap-4 sm:grid-cols-2">
                  {rateFields.map((field) => (
                    <div className="space-y-2" key={field}>
                      <Label htmlFor={`rate-${field}`}>
                        {t(`modelPricing.rates.${field}`)}
                        {["input", "output"].includes(field) ? " *" : ""}
                      </Label>
                      <Input
                        id={`rate-${field}`}
                        inputMode="decimal"
                        placeholder={
                          ["input", "output"].includes(field)
                            ? "0.00"
                            : t("modelPricing.rates.optional")
                        }
                        value={rates[field] ?? ""}
                        onChange={(event) =>
                          setRates({
                            ...rates,
                            [field]:
                              event.target.value === ""
                                ? null
                                : event.target.value,
                          })
                        }
                      />
                    </div>
                  ))}
                </div>
                <p className="text-xs text-muted-foreground">
                  {t("modelPricing.rates.hint")}
                </p>
              </fieldset>
            )}
          </div>
        )}
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {t("modelPricing.rule.saveError")}
          </p>
        )}
        <div className="flex flex-wrap justify-end gap-2">
          <Button
            variant="outline"
            disabled={saving}
            onClick={() => (review ? setReview(false) : onClose())}
          >
            {t(review ? "modelPricing.rule.back" : "common.cancel")}
          </Button>
          <Button
            disabled={!valid || saving}
            onClick={() => (review ? void save() : setReview(true))}
          >
            {t(
              saving
                ? "modelPricing.rule.saving"
                : review
                  ? "modelPricing.rule.confirm"
                  : "modelPricing.rule.review"
            )}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
export function RateSummary({
  rates,
  preserveUsd = false,
}: {
  rates: Partial<PricingRates>
  preserveUsd?: boolean
}) {
  const { t } = useTranslation()
  const pricingCurrency = usePricingCurrency()
  const format = (amount: string) =>
    preserveUsd ? { amount, currency: "USD" } : pricingCurrency.format(amount)
  const firstRate = rateFields
    .map((field) => rates[field])
    .find((value) => value !== null && value !== undefined)
  const currency = firstRate ? format(firstRate).currency : "USD"
  return (
    <div className="space-y-1 text-xs">
      <p className="text-muted-foreground">
        {t("modelPricing.rates.displayTitle", { currency })}
      </p>
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        {rateFields.map(
          (field) =>
            rates[field] !== null &&
            rates[field] !== undefined && (
              <span key={field}>
                {t(`modelPricing.rates.${field}`)}:{" "}
                <span className="font-mono">{format(rates[field]).amount}</span>
              </span>
            )
        )}
      </div>
    </div>
  )
}
