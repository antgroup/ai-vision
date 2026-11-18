import type { QueryClient } from "@tanstack/react-query"

export const PRICING_INVENTORY_QUERY = "model-pricing-inventory"

// Session list/detail/usage keys include their semantics version before the
// Space. Match the owning key layout, never any occurrence of the Space ID.
const spacePositions = new Map<string, number>([
  ["model-pricing", 1],
  [PRICING_INVENTORY_QUERY, 1],
  ["model-pricing-catalog", 1],
  ["nativeTraceList", 1],
  ["nativeTraceDetail", 1],
  ["nativeSpanDetail", 1],
  ["nativeSessionList", 2],
  ["nativeSessionDetail", 2],
  ["nativeSessionUsage", 2],
  ["nativeSessionTraces", 1],
  ["frameworkDashboardMetrics", 1],
  ["frameworkDashboardTraces", 1],
  ["frameworkDashboardSessions", 1],
])

// Signed and share queries contain a payload and signature instead of a Space.
const sharedDetails = new Set([
  "traceLinkDetail",
  "observationLinkDetail",
  "shareData",
  "shareTraceDetail",
])

/** Price changes must be visible even when a recently viewed Trace is fresh. */
export async function invalidateModelPricingQueries(
  client: QueryClient,
  spaceId: string,
  scope: "space" | "global" = "space"
) {
  if (!spaceId) return
  const filters = {
    predicate: ({ queryKey }: { queryKey: readonly unknown[] }) => {
      const kind = queryKey[0]
      if (typeof kind !== "string") return false
      const position = spacePositions.get(kind)
      if (scope === "global")
        return position !== undefined || sharedDetails.has(kind)
      return position !== undefined && queryKey[position] === spaceId
    },
  }
  // A response started before a rule or shared catalog change must not
  // overwrite newer evidence. Global scope still matches only pricing keys.
  await client.cancelQueries(filters)
  await client.invalidateQueries({ ...filters, refetchType: "active" })
}
