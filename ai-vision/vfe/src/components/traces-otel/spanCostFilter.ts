import {
  convertUsdAmount,
  type DisplayCurrency,
} from "../../features/model-pricing/currency.ts"
import type { OTelSpan } from "./types"

export type SpanCostMetric = "cost" | "listPriceEquivalent"
export interface SpanCostThreshold {
  amount: string
  currency: DisplayCurrency
  metric: SpanCostMetric
}
export type SpanCostExchangeRates = Partial<Record<DisplayCurrency, string>>

/** Exact, bounded input text. No exponent, grouping, unit, or currency guessing. */
export function parseSpanCostAmount(input: string): string | null {
  if (input.length > 256) return null
  const text = input.trim()
  if (!/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(text)) return null
  const [whole, fraction = ""] = text.split(".")
  const integer = (whole || "0").replace(/^0+(?=\d)/, "")
  const decimals = fraction.replace(/0+$/, "")
  const amount = decimals ? `${integer}.${decimals}` : integer
  return amount.length <= 256 ? amount : null
}

export function parseSpanCostThreshold(
  value: unknown
): SpanCostThreshold | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null
  const candidate = value as Record<string, unknown>
  if (
    Object.keys(candidate).length !== 3 ||
    !["amount", "currency", "metric"].every((key) =>
      Object.hasOwn(candidate, key)
    ) ||
    typeof candidate.amount !== "string" ||
    (candidate.currency !== "USD" &&
      candidate.currency !== "CNY" &&
      candidate.currency !== "JPY") ||
    (candidate.metric !== "cost" && candidate.metric !== "listPriceEquivalent")
  )
    return null
  const amount = parseSpanCostAmount(candidate.amount)
  return amount === null
    ? null
    : {
        amount,
        currency: candidate.currency as DisplayCurrency,
        metric: candidate.metric,
      }
}

/** Only a server-owned, disjoint single call establishes a filterable amount. */
export function getSpanCostAmount(
  span: OTelSpan,
  metric: SpanCostMetric
): string | null {
  const coverage = span.costCoverage
  const cost = span.usage?.cost
  if (
    coverage?.additive !== true ||
    coverage.billableCalls !== 1 ||
    coverage.currency !== "USD" ||
    !cost
  )
    return null
  let amount: string | null | undefined
  let currency: string | undefined
  if (metric === "listPriceEquivalent") {
    if (
      coverage.subscriptionCalls !== 1 ||
      coverage.listPriceEquivalentCalls !== 1
    )
      return null
    amount = cost.listPriceEquivalent?.total
    currency =
      cost.listPriceEquivalent?.currency ?? cost.currency ?? coverage.currency
  } else {
    if (
      coverage.knownCostCalls !== 1 ||
      (coverage.subscriptionCalls ?? 0) > 0 ||
      cost.billingMode === "subscription"
    )
      return null
    amount = cost.total
    currency = cost.currency ?? coverage.currency
  }
  return currency === "USD" && typeof amount === "string"
    ? parseSpanCostAmount(amount)
    : null
}

function decimalParts(text: string) {
  // Conversion can multiply two 256-character decimals. Bound the result too.
  if (text.length > 512 || !/^\d+(?:\.\d+)?$/.test(text)) return null
  const [whole, fraction = ""] = text.split(".")
  return { coefficient: BigInt(whole + fraction), scale: fraction.length }
}

/** Compare raw values, never their rounded/localized display strings. */
export function matchesSpanCostThreshold(
  span: OTelSpan,
  value: SpanCostThreshold,
  rates: SpanCostExchangeRates = {}
): boolean {
  const threshold = parseSpanCostThreshold(value)
  if (!threshold) return false
  const amount = getSpanCostAmount(span, threshold.metric)
  if (amount === null) return false
  const converted = convertUsdAmount(amount, threshold.currency, rates)
  // Display formatting can fall back to USD; a saved CNY/JPY condition cannot.
  if (converted.currency !== threshold.currency || converted.amount === null)
    return false
  const left = decimalParts(converted.amount)
  const right = decimalParts(threshold.amount)
  if (!left || !right) return false
  const scale = Math.max(left.scale, right.scale)
  return (
    left.coefficient * 10n ** BigInt(scale - left.scale) >=
    right.coefficient * 10n ** BigInt(scale - right.scale)
  )
}
