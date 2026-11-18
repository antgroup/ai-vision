/** Read the authoritative total; incomplete buckets must not overwrite it. */
export function reportedTotal(
  usage:
    | {
        totalTokens?: unknown
        inputTokens?: unknown
        outputTokens?: unknown
        usageDetails?: { total?: unknown } | null
      }
    | null
    | undefined
): number | null {
  const value = usage?.totalTokens ?? usage?.usageDetails?.total
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : null
}
