import type { CatalogStatus, CostCoverage, PricingRates } from "./types.ts"

export function catalogProvenance(
  catalog?: Pick<CatalogStatus, "sourceType" | "sourceUrl" | "sourceRevision">
) {
  const source = catalog?.sourceType ?? "unavailable"
  const full = catalog?.sourceRevision
  const commit = source === "repository" && /^[a-f\d]{40}$/i.test(full ?? "")
  const content = /^sha256:[a-f\d]{64}$/i.test(full ?? "")
  // Construct links from the fixed source contract, never a response-provided URL.
  const href =
    source === "api"
      ? "https://models.dev/api.json"
      : source === "repository"
        ? `https://github.com/anomalyco/models.dev${commit ? `/commit/${full}` : ""}`
        : null
  return {
    source,
    href,
    revision: full
      ? {
          kind: commit
            ? ("commit" as const)
            : content
              ? ("content" as const)
              : ("version" as const),
          short: content ? full.slice(0, 19) : full.slice(0, 12),
          full,
        }
      : null,
  }
}

export const rateFields = [
  "input",
  "output",
  "cacheReadInput",
  "cacheWriteInput",
  "reasoning",
] as const
export const emptyRates: PricingRates = {
  input: null,
  output: null,
  cacheReadInput: null,
  cacheWriteInput: null,
  reasoning: null,
}
export function validRate(value: string) {
  return /^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(value.trim())
}
export function validManualRates(rates: PricingRates) {
  return (
    rates.input !== null &&
    rates.output !== null &&
    rateFields.every(
      (field) => rates[field] === null || validRate(rates[field]!)
    )
  )
}
export function costPresentation(
  total: string | null | undefined,
  coverage?: CostCoverage | null
) {
  if (total !== null && total !== undefined)
    return { kind: "total" as const, value: total }
  if (
    coverage?.additive &&
    coverage.knownSubtotal !== null &&
    coverage.knownCostCalls > 0
  )
    return { kind: "subtotal" as const, value: coverage.knownSubtotal }
  return { kind: "unknown" as const, value: null }
}

/** Count reported estimate gaps without treating absent historical estimates as failures. */
export function pricingNeedsAttention(coverage: CostCoverage) {
  return (
    !coverage.additive ||
    coverage.unknownCalls > (coverage.subscriptionCalls ?? 0) ||
    Object.values(coverage.listPriceEquivalentUnknownReasons ?? {}).some(
      (count) => count > 0
    )
  )
}

/** Backend reasons may include tariff details. Present their stable codes, retaining the original counts. */
export function pricingFailureReasons(reasons: Record<string, number>) {
  const counts = new Map<string, number>()
  for (const [reason, count] of Object.entries(reasons)) {
    if (count <= 0) continue
    const codes = new Set(
      reason
        .split(";")
        .map((part) => {
          const code = part.split(":", 1)[0].trim()
          if (
            code.startsWith("unsupported_") &&
            code !== "unsupported_currency"
          )
            return "unsupported_pricing"
          if (code === "missing_prices") return "price_not_configured"
          if (code === "missing_input_or_output_price")
            return "price_bucket_missing"
          return code
        })
        .filter(Boolean)
    )
    for (const code of codes) counts.set(code, (counts.get(code) ?? 0) + count)
  }
  return [...counts].map(([code, count]) => ({ code, count }))
}

const catalogPriceFailures = new Set([
  "price_not_configured",
  "price_not_effective",
  "price_bucket_missing",
  "unsupported_pricing",
])

export function isSubscriptionPriceFailure(code: string) {
  return (
    catalogPriceFailures.has(code) ||
    code === "official_model_price_unavailable"
  )
}

export function pricingModelState(coverage: CostCoverage) {
  if (!coverage.additive) return "missingUsage"
  if (!pricingNeedsAttention(coverage) && (coverage.subscriptionCalls ?? 0) > 0)
    return coverage.subscriptionCalls === coverage.billableCalls
      ? "subscription"
      : "mixed"
  if (!coverage.unknownCalls && coverage.billableCalls > 0) return "complete"
  const failures = pricingFailureReasons(coverage.unknownReasons)
  if (failures.some(({ code }) => catalogPriceFailures.has(code)))
    return "missingPrice"
  const subscriptionFailures = pricingFailureReasons(
    coverage.listPriceEquivalentUnknownReasons ?? {}
  )
  if (
    subscriptionFailures.length > 0 &&
    subscriptionFailures.every(({ code }) =>
      isSubscriptionPriceFailure(code)
    ) &&
    failures.every(({ code }) => code === "subscription_billing")
  )
    return "missingOfficialPrice"
  return "missingUsage"
}
