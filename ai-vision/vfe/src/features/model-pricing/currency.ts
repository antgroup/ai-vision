export type DisplayCurrency = "USD" | "CNY" | "JPY"
export type CurrencySetting = DisplayCurrency | "auto"
export const displayCurrencies: DisplayCurrency[] = ["USD", "CNY", "JPY"]

export interface ExchangeRateStatus {
  baseCurrency: "USD"
  supportedCurrencies: DisplayCurrency[]
  rates: Partial<Record<DisplayCurrency, string>>
  snapshotId: string | null
  rateDate: string | null
  fetchedAt: string | null
  sourceName: string
  sourceUrl: string
  status: "empty" | "ready" | "stale" | "error"
  lastError: string | null
  scope: "global"
  canManage: boolean
}

export type DisplayExchangeRates = Pick<
  ExchangeRateStatus,
  "rates" | "rateDate" | "status"
>

export function defaultDisplayCurrency(language: string): DisplayCurrency {
  if (language.toLowerCase().startsWith("zh")) return "CNY"
  if (language.toLowerCase().startsWith("ja")) return "JPY"
  return "USD"
}

function decimalParts(value: string) {
  if (value.length > 256 || !/^\d+(?:\.\d+)?$/.test(value)) return null
  const [whole, fraction = ""] = value.split(".")
  return { coefficient: BigInt(whole + fraction), scale: fraction.length }
}

function decimalText(coefficient: bigint, scale: number): string {
  if (coefficient === 0n) return "0"
  const text = coefficient.toString().padStart(scale + 1, "0")
  return scale
    ? `${text.slice(0, -scale)}.${text.slice(-scale)}`.replace(/0+$/, "").replace(/\.$/, "")
    : text
}

/** Exact display conversion over decimal text; never price or total in a browser. */
export function multiplyDecimalStrings(amount: string, rate: string): string | null {
  const left = decimalParts(amount)
  const right = decimalParts(rate)
  if (!left || !right) return null
  return decimalText(left.coefficient * right.coefficient, left.scale + right.scale)
}

export function convertUsdAmount(
  amount: string | null | undefined,
  currency: DisplayCurrency,
  rates: Partial<Record<DisplayCurrency, string>> = {}
) {
  if (amount === null || amount === undefined) return { amount: null, currency, converted: false }
  if (!decimalParts(amount)) return { amount: null, currency: "USD" as const, converted: false }
  if (currency === "USD") return { amount, currency, converted: false }
  const rate = rates[currency]
  const parsed = rate ? decimalParts(rate) : null
  if (!rate || !parsed || parsed.coefficient <= 0n)
    return { amount, currency: "USD" as const, converted: false }
  return { amount: multiplyDecimalStrings(amount, rate), currency, converted: true }
}

/** Localized grouping without Number conversion; positive tiny values stay positive. */
export function formatDecimalAmount(value: string, currency: DisplayCurrency, language: string): string {
  const parsed = decimalParts(value)
  if (!parsed) return value
  const minimum = currency === "JPY" ? 0 : 2
  const [, fraction = ""] = value.split(".")
  const first = fraction.search(/[1-9]/)
  const belowOne = parsed.coefficient > 0n && BigInt(value.split(".")[0]) === 0n
  const digits = belowOne && first >= 0 ? Math.max(minimum, first + 6) : minimum
  let coefficient = parsed.coefficient
  let scale = parsed.scale
  if (scale > digits) {
    const divisor = 10n ** BigInt(scale - digits)
    coefficient = (coefficient + divisor / 2n) / divisor
    scale = digits
  }
  const text = decimalText(coefficient, scale)
  const [whole, rawFraction = ""] = text.split(".")
  const decimal = new Intl.NumberFormat(language).formatToParts(1.1).find((part) => part.type === "decimal")?.value ?? "."
  const grouped = new Intl.NumberFormat(language, { maximumFractionDigits: 0 }).format(BigInt(whole))
  const displayedFraction = rawFraction.padEnd(minimum, "0")
  return displayedFraction ? `${grouped}${decimal}${displayedFraction}` : grouped
}
