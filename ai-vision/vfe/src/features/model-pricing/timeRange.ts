import { isTimeRangeLongerThanDays } from "@/lib/timeFilter"

export const PRICING_MAX_RANGE_DAYS = 31
const OFFSET_SUFFIX = /(?:Z|[+-]\d{2}:?\d{2})$/i

export function isValidPricingWindow({
  from,
  to,
}: {
  from: string
  to: string
}) {
  const start = Date.parse(from)
  const end = Date.parse(to)
  return (
    OFFSET_SUFFIX.test(from) &&
    OFFSET_SUFFIX.test(to) &&
    start < end &&
    !isTimeRangeLongerThanDays(from, to, PRICING_MAX_RANGE_DAYS, "elapsed")
  )
}
