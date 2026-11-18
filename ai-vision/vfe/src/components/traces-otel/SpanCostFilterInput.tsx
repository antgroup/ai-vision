import { useEffect, useId, useState } from "react"
import { useTranslation } from "react-i18next"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"
import { convertUsdAmount } from "@/features/model-pricing/currency"
import { SpanFilterHelp } from "./SpanFilterHelp"
import { parseSpanCostAmount, type SpanCostThreshold } from "./spanCostFilter"

type CostCurrency = SpanCostThreshold["currency"]
export interface SpanCostFilterInputProps {
  value: SpanCostThreshold | null
  onChange: (next: SpanCostThreshold | null) => void
  defaultCurrency?: CostCurrency
  rates?: Partial<Record<CostCurrency, string>>
  rateDate?: string | null
  exchangeStatus?: "ready" | "stale" | "error" | "empty" | "loading"
  resetKey?: number
}

export function SpanCostFilterInput({
  value,
  onChange,
  defaultCurrency = "USD",
  rates,
  rateDate,
  exchangeStatus = "empty",
  resetKey,
}: SpanCostFilterInputProps) {
  const { t } = useTranslation()
  const id = useId()
  const [currency, setCurrency] = useState<CostCurrency>(
    value?.currency ?? defaultCurrency
  )
  const [metric, setMetric] = useState<SpanCostThreshold["metric"]>(
    value?.metric ?? "cost"
  )
  const [draft, setDraft] = useState<string | null>(null)
  const [invalid, setInvalid] = useState(false)
  const enabled = value !== null
  const hasRate = (candidate: CostCurrency) => {
    if (candidate === "USD") return true
    return convertUsdAmount("0", candidate, rates).currency === candidate
  }
  const available = hasRate(currency)
  const fxStatus =
    currency === "USD"
      ? null
      : !available
        ? "missing"
        : exchangeStatus === "ready"
          ? null
          : exchangeStatus === "loading"
            ? "loadingCompact"
            : exchangeStatus === "stale"
              ? "staleCompact"
              : "retainedCompact"

  /* eslint-disable react-hooks/set-state-in-effect -- applying saved conditions replaces a local uncommitted draft */
  useEffect(() => {
    setCurrency(value?.currency ?? defaultCurrency)
    setMetric(value?.metric ?? "cost")
    setDraft(null)
    setInvalid(false)
  }, [value, defaultCurrency, resetKey])
  /* eslint-enable react-hooks/set-state-in-effect */

  const publish = (next: SpanCostThreshold | null) => {
    setDraft(null)
    setInvalid(false)
    if (JSON.stringify(next) !== JSON.stringify(value)) onChange(next)
  }
  const commit = () => {
    if (!enabled || draft === null) return
    const amount = parseSpanCostAmount(draft)
    if (amount === null) {
      setInvalid(true)
      return
    }
    publish({ amount, currency, metric })
  }

  return (
    <fieldset
      data-threshold-kind="cost"
      aria-labelledby={`${id}-label`}
      className={cn(
        "min-w-0 space-y-2 rounded-md border p-2.5",
        enabled
          ? "border-border bg-background/60"
          : "border-border/60 bg-muted/20"
      )}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <div className="flex min-w-0 flex-1 items-center gap-0.5">
          <label
            id={`${id}-label`}
            className="flex min-w-0 items-center gap-1.5 text-xs font-medium"
          >
            <input
              type="checkbox"
              className="size-3.5 shrink-0 accent-primary"
              aria-controls={`${id}-amount`}
              checked={enabled}
              disabled={!enabled && !available}
              onChange={(event) => {
                if (!event.target.checked) publish(null)
                else if (available)
                  publish({ amount: "0.01", currency, metric })
              }}
            />
            <span>{t("nativeSpanFilters.costMinimum")}</span>
          </label>
          <SpanFilterHelp
            name="cost"
            label={t("nativeSpanFilters.costHelpLabel")}
          >
            <p>{t("nativeSpanFilters.costHelp")}</p>
            <p>{t("nativeSpanFilters.costEquivalentHelp")}</p>
            <p>{t("nativeSpanFilters.costInputHint")}</p>
            {currency !== "USD" && rateDate && available && (
              <p>
                {t("modelPricing.currency.reference", { date: rateDate })} ECB
              </p>
            )}
            {fxStatus && (
              <p>
                {t(
                  fxStatus === "missing"
                    ? "nativeSpanFilters.costFxUnavailable"
                    : `modelPricing.currency.${fxStatus}`
                )}
              </p>
            )}
          </SpanFilterHelp>
        </div>
        <div className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
          <span aria-hidden="true">≥</span>
          <Input
            id={`${id}-amount`}
            data-testid="span-filter-cost-amount"
            data-trace-filter-input
            type="text"
            inputMode="decimal"
            autoComplete="off"
            spellCheck={false}
            disabled={!enabled}
            aria-label={t("nativeSpanFilters.costAmount")}
            aria-describedby={`${id}-hint${invalid ? ` ${id}-error` : ""}${fxStatus === "missing" ? ` ${id}-fx` : ""}`}
            aria-invalid={invalid}
            className="h-7 w-20 px-2 text-xs tabular-nums disabled:bg-muted/60"
            value={draft ?? value?.amount ?? "0.01"}
            onChange={(event) => {
              setDraft(event.target.value)
              setInvalid(false)
            }}
            onBlur={commit}
            onKeyDown={(event) => {
              if (event.nativeEvent.isComposing) return
              if (event.key === "Enter") {
                event.preventDefault()
                event.stopPropagation()
                commit()
              } else if (event.key === "Escape") {
                event.preventDefault()
                event.stopPropagation()
                setDraft(null)
                setInvalid(false)
              }
            }}
          />
          <select
            data-testid="span-filter-cost-currency"
            data-trace-filter-input
            aria-label={t("nativeSpanFilters.costCurrency")}
            className="h-7 rounded-md border border-input bg-background pr-1 pl-1.5 text-xs text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
            value={currency}
            onChange={(event) => {
              const next = event.target.value as CostCurrency
              if (!["USD", "CNY", "JPY"].includes(next) || !hasRate(next))
                return
              setCurrency(next)
              if (value) publish({ ...value, currency: next })
            }}
          >
            {(["USD", "CNY", "JPY"] as const).map((candidate) => (
              <option
                key={candidate}
                value={candidate}
                disabled={!hasRate(candidate)}
              >
                {candidate}
              </option>
            ))}
          </select>
        </div>
      </div>
      <select
        data-testid="span-filter-cost-metric"
        data-trace-filter-input
        aria-label={t("nativeSpanFilters.costMetric")}
        className="h-7 w-full min-w-0 rounded-md border border-input bg-background px-2 text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring"
        value={metric}
        onChange={(event) => {
          const next = event.target.value as SpanCostThreshold["metric"]
          if (next !== "cost" && next !== "listPriceEquivalent") return
          setMetric(next)
          if (value) publish({ ...value, metric: next })
        }}
      >
        <option value="cost">{t("nativeSpanFilters.costMetricValue")}</option>
        <option value="listPriceEquivalent">
          {t("nativeSpanFilters.costMetricEquivalent")}
        </option>
      </select>
      {fxStatus && (
        <p
          id={`${id}-fx`}
          data-testid="span-filter-cost-fx-warning"
          role="status"
          className="text-[11px] leading-relaxed text-muted-foreground"
        >
          {t(
            fxStatus === "missing"
              ? "nativeSpanFilters.costFxUnavailable"
              : `modelPricing.currency.${fxStatus}`
          )}
        </p>
      )}
      <p id={`${id}-hint`} className="sr-only">
        {t("nativeSpanFilters.costInputHint")}
      </p>
      {invalid && (
        <p
          id={`${id}-error`}
          role="alert"
          className="text-[11px] leading-relaxed text-destructive"
        >
          {t("nativeSpanFilters.costInvalid")}
        </p>
      )}
    </fieldset>
  )
}
