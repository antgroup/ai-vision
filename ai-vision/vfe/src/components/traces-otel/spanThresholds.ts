export type SpanThresholdKind = "tokens" | "duration"

export const SPAN_THRESHOLD_PRESETS: Record<
  SpanThresholdKind,
  readonly number[]
> = {
  tokens: [1000, 5000, 10000, 50000, 100000],
  duration: [1000, 5000, 10000, 30000, 60000, 300000],
}

/** Unitless values retain the existing base units: Token counts or milliseconds. */
export function parseSpanThreshold(
  input: string,
  kind: SpanThresholdKind
): number | null {
  const match = input
    .trim()
    .match(/^((?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)\s*(k|ms|s|m|min)?$/i)
  if (!match) return null
  const suffix = match[2]?.toLowerCase() ?? ""
  if (kind === "tokens" ? suffix !== "" && suffix !== "k" : suffix === "k")
    return null
  const [significand, exponent = "0"] = match[1].toLowerCase().split("e")
  const scale = Number(exponent) + (suffix === "k" || suffix === "s" ? 3 : 0)
  // Shift the decimal before parsing, avoiding 1.001 * 1000 becoming 1000.999….
  const [whole, fraction = ""] = significand.split(".")
  // A minute is 6 × 10^4 ms. Multiply the original decimal digits, not an
  // already-rounded float: 1.001 * 60000 otherwise becomes 60059.999….
  const value =
    suffix === "m" || suffix === "min"
      ? Number(
          `${BigInt(whole + fraction) * 6n}e${Number(exponent) + 4 - fraction.length}`
        )
      : Number(`${significand}e${scale}`)
  if (!Number.isFinite(value) || value < 0) return null
  if (value === 0 && /[1-9]/.test(significand)) return null
  if (kind === "tokens") {
    if (!Number.isSafeInteger(value)) return null
    const fractionalPlaces = fraction.length - scale
    // Validate the original digits as well: floating-point rounding must not
    // turn a large fractional count into an apparently valid integer.
    if (
      fractionalPlaces > 0 &&
      /[1-9]/.test((whole + fraction).slice(-fractionalPlaces))
    )
      return null
  }
  return value
}

/** Keep exact values editable: compact only when converting back loses no precision. */
export function formatSpanThreshold(
  value: number,
  kind: SpanThresholdKind
): string {
  if (!Number.isFinite(value) || value < 0) return ""
  if (kind === "duration" && value >= 60000) {
    const minutes = `${value / 60000}m`
    if (
      /^\d+(?:\.\d{1,3})?m$/.test(minutes) &&
      parseSpanThreshold(minutes, kind) === value
    )
      return minutes
  }
  const scaled = value / 1000
  const compact = `${scaled}${kind === "tokens" ? "K" : "s"}`
  if (value >= 1000 && parseSpanThreshold(compact, kind) === value)
    return compact
  return `${value}${kind === "tokens" ? "" : "ms"}`
}
