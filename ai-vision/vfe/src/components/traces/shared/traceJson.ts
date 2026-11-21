/** Compare decimal values while ignoring equivalent notation such as 0.10 and 1e-1. */
function decimalValue(token: string): string | null {
  const match = /^(-?)(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$/.exec(token)
  if (!match) return null
  const fraction = match[3] ?? ""
  const digits = (match[2] + fraction).replace(/^0+/, "")
  if (!digits) return "0"
  const significant = digits.replace(/0+$/, "")
  const exponent =
    Number(match[4] ?? 0) - fraction.length + digits.length - significant.length
  return Number.isFinite(exponent)
    ? `${match[1]}${significant}e${exponent}`
    : null
}

/** JSON.parse rounds valid decimals too; retain the raw view when that changes a value. */
function hasLosslessNumbers(text: string): boolean {
  // Consume quoted strings first so their contents never count as JSON numbers.
  const tokens = /"(?:[^"\\]|\\[\s\S])*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g
  for (const match of text.matchAll(tokens)) {
    const token = match[0]
    if (token[0] === '"') continue
    const value = Number(token)
    if (
      !Number.isFinite(value) ||
      (Number.isInteger(value) && !Number.isSafeInteger(value)) ||
      Object.is(value, -0) ||
      decimalValue(token) !== decimalValue(String(value))
    )
      return false
  }
  return true
}

/** A display projection only. The received value remains the copy/export source. */
export function parseStructuredTraceJson(
  value: unknown
): Record<string, unknown> | unknown[] | null {
  let current = value
  for (let layer = 0; layer < 4; layer += 1) {
    if (typeof current !== "string" || current.length > 300_000) return null
    const text = current.trim()
    if (text[0] !== "{" && text[0] !== "[" && text[0] !== '"') return null
    try {
      current = JSON.parse(text) as unknown
      if (!hasLosslessNumbers(text)) return null
    } catch {
      return null
    }
    if (current !== null && typeof current === "object")
      return current as Record<string, unknown> | unknown[]
  }
  return null
}

/** Project a bounded JSON string for a tree while preserving it for copy. */
export function structuredTracePreview(value: unknown): unknown {
  if (typeof value !== "string" || value.length > 65536) return value
  const first = value.trimStart()[0]
  return first === "{" || first === "["
    ? (parseStructuredTraceJson(value) ?? value)
    : value
}
