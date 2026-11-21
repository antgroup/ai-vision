import { useTranslation } from "react-i18next"
import { ChevronDown } from "lucide-react"
import { Link } from "react-router-dom"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import type { CostCoverage } from "./types"
import {
  costPresentation,
  isSubscriptionPriceFailure,
  pricingFailureReasons,
} from "./presentation"
import { usePricingCurrency } from "./PricingCurrency"

const officialSources = [
  "official_catalog",
  "official_publisher",
  "archived_publisher",
] as const

/** Canonical server evidence only. Never roll up the browser's paginated spans. */
export function CostSummary({
  total,
  coverage,
  compact = false,
  prominent = false,
  amountTrigger = false,
  showCurrencyNote = true,
  simulated = false,
  pricingHref,
}: {
  total?: string | null
  coverage?: CostCoverage | null
  compact?: boolean
  prominent?: boolean
  amountTrigger?: boolean
  showCurrencyNote?: boolean
  simulated?: boolean
  pricingHref?: string
}) {
  const { t } = useTranslation()
  const pricingCurrency = usePricingCurrency()
  if (
    !coverage ||
    (coverage.billableCalls === 0 && (total === null || total === undefined))
  )
    return null

  const cost = costPresentation(total, coverage)
  const format = (amount: string | null | undefined) =>
    coverage.currency === "USD"
      ? pricingCurrency.format(amount)
      : { amount: amount ?? null, currency: coverage.currency }
  const display = format(cost.value)
  const reference = format(coverage.referenceSubtotal)
  const hasReference = coverage.referenceCalls > 0 || reference.amount !== null
  const equivalent = format(coverage.listPriceEquivalentSubtotal)
  const hasEquivalent =
    coverage.listPriceEquivalentSubtotal !== null &&
    coverage.listPriceEquivalentSubtotal !== undefined &&
    equivalent.amount !== null
  const subscriptionCalls = coverage.subscriptionCalls ?? 0
  const estimatedSubscriptions = coverage.listPriceEquivalentCalls ?? 0
  const partialEquivalentCoverage =
    hasEquivalent && estimatedSubscriptions < subscriptionCalls
  const partialEstimateOnly = hasEquivalent && estimatedSubscriptions === 0
  const hasSubscription = subscriptionCalls > 0
  const equivalentSources = coverage.listPriceEquivalentSources ?? {}
  const pricedSources = Object.keys(equivalentSources).filter(
    (source) => equivalentSources[source] > 0
  )
  const officialEquivalent =
    pricedSources.length > 0 &&
    pricedSources.every((source) =>
      officialSources.some((kind) => kind === source)
    )
  const includesArchived = equivalentSources.archived_publisher > 0
  const archivedOnly = includesArchived && pricedSources.length === 1
  const hasConversion =
    coverage.currency === "USD" &&
    pricingCurrency.currency !== "USD" &&
    (display.amount !== null || hasEquivalent || reference.amount !== null)
  const hasExchangeRate = Boolean(
    pricingCurrency.exchangeRates?.rateDate &&
    pricingCurrency.exchangeRates.rates[pricingCurrency.currency]
  )
  const exchangeStatus =
    !showCurrencyNote ||
    !hasConversion ||
    (pricingCurrency.status === "ready" && hasExchangeRate)
      ? null
      : pricingCurrency.status === "loading"
        ? "loadingCompact"
        : !hasExchangeRate
          ? "usingUsdCompact"
          : pricingCurrency.status === "stale"
            ? "staleCompact"
            : "retainedCompact"

  const sourceKinds = [
    "reported",
    "manual",
    "alias",
    "catalog",
    "reference",
  ] as const
  const monetarySources = sourceKinds.filter(
    (kind) =>
      coverage[`${kind}Calls`] > 0 ||
      (cost.kind === "subtotal" &&
        kind !== "reported" &&
        (coverage.partialCostSources?.[kind] ?? 0) > 0)
  )
  const equivalentKinds = [...sourceKinds, ...officialSources] as const
  const subscriptionSources = equivalentKinds.filter(
    (kind) => kind !== "reported" && equivalentSources[kind] > 0
  )
  const onlySubscription =
    hasSubscription && coverage.subscriptionCalls === coverage.billableCalls
  const officialLabel = archivedOnly
    ? "archivedEquivalentLabel"
    : "officialEquivalentLabel"
  const equivalentLabel = partialEstimateOnly
    ? "partialEquivalentLabel"
    : archivedOnly || (officialEquivalent && onlySubscription)
      ? officialLabel
      : "equivalentLabel"
  const equivalentCompact = partialEstimateOnly
    ? "amountTrigger.partialSubscriptionEstimate"
    : archivedOnly
      ? "archivedEquivalentCompact"
      : officialEquivalent && onlySubscription
        ? "officialEquivalentCompact"
        : "equivalentCompact"
  const equivalentIsPrimary =
    onlySubscription &&
    coverage.additive &&
    hasEquivalent &&
    cost.kind === "unknown"
  const primaryDisplay = equivalentIsPrimary ? equivalent : display
  const subscriptionChargeUnknown =
    onlySubscription && coverage.additive && cost.kind === "unknown"
  const monetaryLabel =
    cost.kind === "total" && coverage.knownCostCalls > 0
      ? coverage.reportedCalls === coverage.knownCostCalls
        ? "reportedTotal"
        : coverage.reportedCalls === 0
          ? "estimatedTotal"
          : "total"
      : cost.kind
  const primaryLabel = equivalentIsPrimary
    ? equivalentCompact
    : subscriptionChargeUnknown
      ? "subscriptionActualUnknown"
      : monetaryLabel
  const knownCalls = equivalentIsPrimary
    ? estimatedSubscriptions
    : coverage.knownCostCalls
  const totalCalls = equivalentIsPrimary
    ? subscriptionCalls
    : coverage.billableCalls
  const partialCostCoverage =
    coverage.additive && knownCalls > 0 && knownCalls < totalCalls
  let amountTriggerLabel = "modelPricing.cost.unknown"
  if (amountTrigger && primaryDisplay.amount !== null) {
    if (simulated) {
      amountTriggerLabel = "modelPricing.cost.amountTrigger.simulated"
    } else if (equivalentIsPrimary) {
      amountTriggerLabel = partialEquivalentCoverage
        ? "modelPricing.cost.amountTrigger.partialSubscriptionEstimate"
        : "modelPricing.cost.amountTrigger.subscriptionEstimate"
    } else if (monetaryLabel === "estimatedTotal") {
      amountTriggerLabel = "modelPricing.cost.estimatedTotal"
    } else if (hasReference) {
      amountTriggerLabel = "modelPricing.cost.amountTrigger.referenceSubtotal"
    } else {
      amountTriggerLabel =
        cost.kind === "subtotal"
          ? "modelPricing.cost.amountTrigger.subtotal"
          : "modelPricing.cost.amountTrigger.cost"
    }
  }
  const amountTriggerText = amountTrigger
    ? t(amountTriggerLabel, {
        amount: primaryDisplay.amount,
        currency: primaryDisplay.currency,
      })
    : null
  const detailLabel = t("modelPricing.cost.details")
  const conclusion = !coverage.additive
    ? t("modelPricing.cost.overlap")
    : t(
        `modelPricing.cost.popup.${onlySubscription ? "subscriptionConclusion" : hasSubscription ? "mixedConclusion" : cost.kind === "unknown" ? "unknownConclusion" : coverage.unknownCalls > 0 ? "partialConclusion" : "completeConclusion"}`
      )
  const reasonLabels: Record<string, string> = {
    usage_missing: "usage",
    overlapping_usage_buckets: "buckets",
    price_not_configured: "price",
    official_model_price_unavailable: "officialPrice",
    publisher_exchange_rate_unavailable: "publisherExchangeRate",
    price_not_effective: "price",
    price_bucket_missing: "price",
    provider_unknown: "identity",
    model_unknown: "identity",
    pricing_time_missing: "time",
    invalid_price_configuration: "rules",
    ambiguous_price_configuration: "rules",
    invalid_billing_mode: "invalidBilling",
    unsupported_currency: "currency",
  }
  const plainReasons = (
    reasons: Record<string, number>,
    isEquivalent = false
  ) =>
    pricingFailureReasons(reasons)
      .filter(
        ({ code }) =>
          code !== "subscription_billing" &&
          (code !== "ambiguous_nested_generations" || coverage.additive)
      )
      .map(({ code }) => {
        if (
          isEquivalent &&
          (!hasEquivalent || code !== "price_bucket_missing") &&
          isSubscriptionPriceFailure(code)
        )
          return "officialPrice"
        if (code === "unsupported_pricing") return "unsupported"
        return reasonLabels[code] ?? "unavailable"
      })
  const monetaryReasons = plainReasons(coverage.unknownReasons)
  const equivalentReasons = plainReasons(
    coverage.listPriceEquivalentUnknownReasons ?? {},
    true
  )
  const sourceRow = (
    label: string,
    sources: readonly (typeof equivalentKinds)[number][]
  ) =>
    sources.length > 0 && (
      <>
        <dt className="text-muted-foreground">
          {t(`modelPricing.cost.popup.${label}`)}
        </dt>
        <dd className="flex min-w-0 flex-wrap gap-x-2 gap-y-1">
          {sources.map((source) => (
            <span key={source}>
              {t(`modelPricing.cost.popup.sources.${source}`)}
            </span>
          ))}
        </dd>
      </>
    )

  const details = (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          data-cost-details-trigger
          aria-label={
            amountTrigger
              ? `${amountTriggerText} · ${detailLabel}`
              : detailLabel
          }
          title={amountTrigger ? undefined : detailLabel}
          onClick={(event) => event.stopPropagation()}
          className={
            amountTrigger
              ? "inline-flex min-h-8 max-w-full min-w-0 items-center gap-1 rounded-md px-1.5 py-1 text-right text-xs leading-tight font-medium tabular-nums outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring sm:text-sm"
              : "inline-flex shrink-0 items-center gap-0.5 rounded-sm py-0.5 text-xs font-normal text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
          }
        >
          {amountTrigger ? (
            <span className="min-w-0 [overflow-wrap:anywhere]">
              {amountTriggerText}
            </span>
          ) : (
            t("modelPricing.cost.detailsShort")
          )}
          <ChevronDown
            className={amountTrigger ? "size-3 shrink-0" : "size-3"}
            aria-hidden={amountTrigger || undefined}
          />
        </button>
      </PopoverTrigger>
      <PopoverContent
        data-cost-details-body
        role="dialog"
        aria-label={detailLabel}
        align={amountTrigger ? "end" : "start"}
        className="max-h-[min(28rem,calc(100svh-2rem))] w-80 max-w-[calc(100vw-2rem)] gap-3 overflow-y-auto rounded-xl p-3 text-xs leading-relaxed"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="space-y-3">
          <h3 className="sr-only">{detailLabel}</h3>
          <p
            data-cost-conclusion
            className="text-sm leading-relaxed text-foreground"
          >
            {simulated ? t("modelPricing.cost.demoNote") : conclusion}
          </p>
          {simulated && <p className="text-muted-foreground">{conclusion}</p>}
          <dl
            data-cost-overview
            className="grid grid-cols-[minmax(0,6.5rem)_minmax(0,1fr)] gap-x-3 gap-y-2"
          >
            {coverage.additive && display.amount !== null && (
              <>
                <dt className="text-muted-foreground">
                  {t(
                    cost.kind === "subtotal" || hasSubscription
                      ? "modelPricing.cost.popup.knownSubtotal"
                      : `modelPricing.cost.${monetaryLabel}Label`
                  )}
                </dt>
                <dd className="min-w-0 font-medium break-all tabular-nums">
                  {display.currency} {display.amount}
                </dd>
              </>
            )}
            {coverage.additive && hasSubscription && hasEquivalent && (
              <>
                <dt className="text-muted-foreground">
                  {t(
                    partialEstimateOnly
                      ? "modelPricing.cost.partialEquivalentLabel"
                      : officialEquivalent
                        ? `modelPricing.cost.${officialLabel}`
                        : `modelPricing.cost.popup.${onlySubscription ? "equivalent" : "subscriptionEquivalent"}`
                  )}
                </dt>
                <dd className="min-w-0 font-medium break-all tabular-nums">
                  {equivalent.currency} {equivalent.amount}
                </dd>
              </>
            )}
            {sourceRow(
              hasSubscription ? "costSource" : "source",
              monetarySources
            )}
            {hasSubscription &&
              sourceRow(
                onlySubscription ? "source" : "equivalentSource",
                subscriptionSources
              )}
          </dl>
          {amountTrigger && partialCostCoverage && (
            <p className="text-muted-foreground">
              {t(
                equivalentIsPrimary
                  ? "modelPricing.cost.listPriceEquivalentCoverage"
                  : "modelPricing.cost.coverage",
                { known: knownCalls, total: totalCalls }
              )}
            </p>
          )}
          {coverage.additive && hasSubscription && hasEquivalent && (
            <p className="text-muted-foreground">
              {t(
                includesArchived
                  ? "modelPricing.cost.popup.archivedEquivalentHint"
                  : officialEquivalent
                    ? "modelPricing.cost.popup.officialEquivalentHint"
                    : "modelPricing.cost.popup.equivalentHint"
              )}
              {partialEquivalentCoverage && (
                <> {t("modelPricing.cost.popup.partialEquivalentHint")}</>
              )}
            </p>
          )}
          {(hasReference || equivalentSources.reference > 0) && (
            <p className="text-muted-foreground">
              {t("modelPricing.cost.popup.referenceHint")}
            </p>
          )}
          {[...new Set([...monetaryReasons, ...equivalentReasons])].map(
            (reason) => (
              <p key={reason} className="text-muted-foreground">
                {t(`modelPricing.cost.popup.reasons.${reason}`)}
              </p>
            )
          )}
          {amountTrigger && pricingHref && (
            <Link
              data-cost-pricing-link
              to={pricingHref}
              className="inline-flex min-h-8 w-fit items-center rounded-sm font-medium text-foreground underline underline-offset-4 outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {t("nav.modelPricing")}
            </Link>
          )}
          {!amountTrigger &&
            pricingHref &&
            (monetaryReasons.includes("price") ||
              monetaryReasons.includes("rules")) && (
              <a
                data-cost-pricing-link
                href={pricingHref}
                onClick={(event) => event.stopPropagation()}
                className="inline-flex w-fit rounded-sm font-medium text-foreground underline underline-offset-4 outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {t("modelPricing.cost.resolvePrice")}
              </a>
            )}
          {showCurrencyNote &&
            hasConversion &&
            (!hasExchangeRate || pricingCurrency.status !== "ready") && (
              <p role="status" className="text-muted-foreground">
                {t(
                  `modelPricing.currency.${!hasExchangeRate ? (pricingCurrency.status === "loading" ? "loading" : "fallback") : pricingCurrency.status === "loading" ? "loading" : pricingCurrency.status === "stale" ? "stale" : "error"}`
                )}
              </p>
            )}
          <details data-cost-evidence className="border-t pt-2">
            <summary className="w-fit cursor-pointer rounded-sm py-1 text-muted-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring">
              {t("modelPricing.cost.popup.moreDetails")}
            </summary>
            <div className="space-y-2 pt-2">
              {!onlySubscription && !(amountTrigger && partialCostCoverage) && (
                <p className="text-muted-foreground">
                  {t("modelPricing.cost.coverage", {
                    known: coverage.knownCostCalls,
                    total: coverage.billableCalls,
                  })}
                </p>
              )}
              {hasConversion && hasExchangeRate && (
                <p className="text-muted-foreground">
                  {t("modelPricing.cost.popup.exchangeRate")}
                  {": "}
                  <a
                    href="https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html"
                    target="_blank"
                    rel="noreferrer"
                    onClick={(event) => event.stopPropagation()}
                    className="rounded-sm underline underline-offset-2 outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    ECB {pricingCurrency.exchangeRates!.rateDate}
                  </a>
                </p>
              )}
              <div className="flex flex-wrap gap-x-3 gap-y-1 text-muted-foreground">
                {(
                  [
                    "reported",
                    "manual",
                    "alias",
                    "catalog",
                    "reference",
                    "unknown",
                  ] as const
                )
                  .filter((kind) => coverage[`${kind}Calls`] > 0)
                  .map((kind) => (
                    <span key={kind}>
                      {t(
                        `modelPricing.cost.${kind === "unknown" ? "unknownCalls" : kind}`,
                        { count: coverage[`${kind}Calls`] }
                      )}
                    </span>
                  ))}
              </div>
              {hasReference && (
                <p className="text-muted-foreground">
                  {t("modelPricing.cost.referenceSubtotal", {
                    amount: reference.amount ?? t("traceLabels.unknown"),
                    currency: reference.currency,
                  })}
                </p>
              )}
              {hasSubscription && (
                <div className="space-y-1 text-muted-foreground">
                  <p>
                    {t("modelPricing.cost.subscription", {
                      count: coverage.subscriptionCalls,
                    })}
                  </p>
                  {!hasEquivalent && (
                    <p>
                      {t("modelPricing.cost.listPriceEquivalentUnavailable")}
                    </p>
                  )}
                  {hasEquivalent && officialEquivalent && (
                    <p>{t("modelPricing.cost.popup.officialEstimateMethod")}</p>
                  )}
                  {coverage.listPriceEquivalentCalls !== undefined &&
                    !(
                      amountTrigger &&
                      equivalentIsPrimary &&
                      partialCostCoverage
                    ) && (
                      <p>
                        {t("modelPricing.cost.listPriceEquivalentCoverage", {
                          known: coverage.listPriceEquivalentCalls,
                          total: coverage.subscriptionCalls,
                        })}
                      </p>
                    )}
                  {pricedSources.length > 0 && (
                    <div className="flex flex-wrap gap-x-3 gap-y-1">
                      <span>{t("modelPricing.cost.equivalentSources")}</span>
                      {subscriptionSources.map((kind) => (
                        <span key={kind}>
                          {t(`modelPricing.cost.${kind}`, {
                            count: equivalentSources[kind],
                          })}
                        </span>
                      ))}
                    </div>
                  )}
                  {Object.entries(
                    coverage.listPriceEquivalentUnknownReasons ?? {}
                  )
                    .filter(([, count]) => count > 0)
                    .map(([reason, count]) => (
                      <p key={reason}>
                        {t("modelPricing.cost.equivalentReasonCount", {
                          reason: t(`modelPricing.reasons.${reason}`, {
                            defaultValue: reason,
                          }),
                          count,
                        })}
                      </p>
                    ))}
                </div>
              )}
              {(monetarySources.includes("catalog") ||
                equivalentSources.catalog > 0 ||
                equivalentSources.official_catalog > 0) && (
                <p className="text-muted-foreground">
                  {t("modelPricing.cost.currentCatalog")}
                </p>
              )}
              {Object.entries(coverage.unknownReasons)
                .filter(([, count]) => count > 0)
                .map(([reason, count]) => (
                  <p key={reason} className="text-muted-foreground">
                    {t("modelPricing.cost.reasonCount", {
                      reason: t(`modelPricing.reasons.${reason}`, {
                        defaultValue: reason,
                      }),
                      count,
                    })}
                  </p>
                ))}
            </div>
          </details>
        </div>
      </PopoverContent>
    </Popover>
  )

  const compactStatus = (
    <>
      {hasReference && (
        <span className="text-xs text-muted-foreground">
          {t("modelPricing.cost.referenceIncluded")}
        </span>
      )}
      {!coverage.additive && (
        <span className="text-xs text-muted-foreground">
          {t("modelPricing.cost.overlapCompact")}
        </span>
      )}
      {exchangeStatus && (
        <span
          data-cost-exchange-status
          className="text-xs text-muted-foreground"
        >
          {t(`modelPricing.currency.${exchangeStatus}`)}
        </span>
      )}
    </>
  )

  return (
    <div
      data-cost-summary
      data-demo-cost={simulated || undefined}
      className={
        amountTrigger
          ? "max-w-full min-w-0"
          : compact
            ? "max-w-full space-y-1 text-xs leading-relaxed"
            : prominent
              ? "space-y-2 text-sm"
              : "max-w-full space-y-1 text-sm"
      }
    >
      {amountTrigger ? (
        details
      ) : prominent ? (
        <>
          <div className="space-y-2">
            <p className="text-sm text-muted-foreground">
              {simulated
                ? t("modelPricing.cost.demoNote")
                : t(
                    `modelPricing.cost.${equivalentIsPrimary ? equivalentLabel : subscriptionChargeUnknown ? "subscriptionCostLabel" : `${monetaryLabel}Label`}`
                  )}
            </p>
            <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-3xl leading-tight font-semibold tracking-tight tabular-nums sm:text-4xl">
              {primaryDisplay.amount === null ? (
                <span>{t("traceLabels.unknown")}</span>
              ) : (
                <>
                  <span className="text-base font-medium tracking-normal text-muted-foreground">
                    {primaryDisplay.currency}
                  </span>
                  <span className="break-all">{primaryDisplay.amount}</span>
                </>
              )}
            </p>
          </div>
          <div
            data-cost-row
            className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground"
          >
            {partialCostCoverage && (
              <span>
                {t(
                  `modelPricing.cost.${equivalentIsPrimary ? "equivalentCoverageCompact" : "coverageCompact"}`,
                  { known: knownCalls, total: totalCalls }
                )}
              </span>
            )}
            {compactStatus}
            {details}
          </div>
        </>
      ) : (
        <div
          data-cost-row
          className="flex flex-wrap items-center gap-x-2.5 gap-y-1 leading-relaxed"
        >
          <span className="min-w-0 font-medium break-all tabular-nums">
            {t(
              simulated && primaryDisplay.amount !== null
                ? "modelPricing.cost.demoAmount"
                : `modelPricing.cost.${primaryLabel}`,
              {
                amount: primaryDisplay.amount,
                currency: primaryDisplay.currency,
              }
            )}
          </span>
          {partialCostCoverage && (
            <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
              {t(
                `modelPricing.cost.${equivalentIsPrimary ? "equivalentCoverageCompact" : "coverageCompact"}`,
                { known: knownCalls, total: totalCalls }
              )}
            </span>
          )}
          {compactStatus}
          {details}
        </div>
      )}

      {!amountTrigger &&
        hasSubscription &&
        (!subscriptionChargeUnknown || equivalentIsPrimary) && (
          <div
            data-subscription-equivalent
            className={
              prominent
                ? "space-y-1.5 border-l-2 border-border py-1 pl-4 text-xs text-muted-foreground"
                : "flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs text-muted-foreground"
            }
          >
            {equivalentIsPrimary ? (
              <span>{t("modelPricing.cost.subscriptionActualUnknown")}</span>
            ) : prominent && hasEquivalent ? (
              <>
                <p className="text-sm font-medium text-foreground">
                  {t(`modelPricing.cost.${equivalentLabel}`)}
                </p>
                <p className="flex flex-wrap items-baseline gap-x-1.5 gap-y-1 text-xl leading-tight font-semibold text-foreground tabular-nums">
                  <span className="text-xs font-medium text-muted-foreground">
                    {equivalent.currency}
                  </span>
                  <span className="break-all">{equivalent.amount}</span>
                </p>
                {partialEquivalentCoverage && (
                  <p>
                    {t("modelPricing.cost.listPriceEquivalentCoverage", {
                      known: coverage.listPriceEquivalentCalls,
                      total: coverage.subscriptionCalls,
                    })}
                  </p>
                )}
              </>
            ) : (
              <>
                <span>
                  {hasEquivalent
                    ? t(`modelPricing.cost.${equivalentCompact}`, {
                        amount: equivalent.amount,
                        currency: equivalent.currency,
                      })
                    : t("modelPricing.cost.subscriptionCompact", {
                        count: coverage.subscriptionCalls,
                      })}
                </span>
                {partialEquivalentCoverage && (
                  <span className="tabular-nums">
                    {t("modelPricing.cost.equivalentCoverageCompact", {
                      known: coverage.listPriceEquivalentCalls,
                      total: coverage.subscriptionCalls,
                    })}
                  </span>
                )}
              </>
            )}
          </div>
        )}
    </div>
  )
}
