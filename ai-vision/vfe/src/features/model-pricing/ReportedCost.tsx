import { useTranslation } from "react-i18next"
import { ChevronDown } from "lucide-react"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { usePricingCurrency } from "./PricingCurrency"

/** External sources own their monetary amounts; no price or coverage is inferred. */
export function ReportedCost({
  value,
  className = "",
}: {
  value: unknown
  className?: string
}) {
  const { t } = useTranslation()
  const currency = usePricingCurrency()
  let amount: string | null = null
  if (typeof value === "number" && Number.isFinite(value) && value >= 0) {
    const text = String(value)
    const [coefficient, exponent] = text.toLowerCase().split("e")
    if (exponent === undefined) amount = text
    else {
      const [whole, fraction = ""] = coefficient.split(".")
      const digits = whole + fraction
      const position = whole.length + Number(exponent)
      amount =
        position <= 0
          ? `0.${"0".repeat(-position)}${digits}`
          : position >= digits.length
            ? digits + "0".repeat(position - digits.length)
            : `${digits.slice(0, position)}.${digits.slice(position)}`
    }
  }
  const display = currency.format(amount)
  const conversion = amount !== null && currency.currency !== "USD"
  const rateDate = currency.exchangeRates?.rateDate
  const hasRate = Boolean(
    rateDate && currency.exchangeRates?.rates[currency.currency]
  )
  const fxStatus =
    !conversion || (currency.status === "ready" && hasRate)
      ? null
      : currency.status === "loading"
        ? "loadingCompact"
        : !hasRate
          ? "usingUsdCompact"
          : currency.status === "stale"
            ? "staleCompact"
            : "retainedCompact"
  const fxEvidence = conversion
    ? hasRate
      ? `${t("modelPricing.currency.reference", { date: rateDate })} ECB`
      : t("modelPricing.currency.fallback")
    : null
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          data-reported-cost
          onClick={(event) => event.stopPropagation()}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ")
              event.stopPropagation()
          }}
          className={`inline-flex max-w-full flex-wrap items-baseline gap-x-1 rounded-sm text-left text-xs leading-relaxed tabular-nums hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none ${className}`}
        >
          <span className="text-muted-foreground">
            {t("modelPricing.observability.recordedCost")}:
          </span>
          <span className="min-w-0 font-medium break-all text-foreground">
            {amount === null
              ? t("traceLabels.unknown")
              : display.amount === null
                ? `USD ${String(value)}`
                : `${display.currency} ${display.amount}`}
          </span>
          {fxStatus && (
            <span data-reported-cost-fx className="text-muted-foreground">
              {t(`modelPricing.currency.${fxStatus}`)}
            </span>
          )}
          <ChevronDown
            aria-hidden="true"
            className="size-3 self-center text-muted-foreground"
          />
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        aria-label={t("modelPricing.cost.details")}
        className="max-w-[calc(100vw-2rem)] gap-2 rounded-xl p-3 text-xs leading-relaxed"
        onClick={(event) => event.stopPropagation()}
      >
        <p>{t("modelPricing.observability.sourceCostsHint")}</p>
        {fxEvidence && <p className="text-muted-foreground">{fxEvidence}</p>}
      </PopoverContent>
    </Popover>
  )
}
