import type { TFunction } from "i18next"
import type { ReportedSpanUsage } from "./spanUsage"
import type { NativeTraceUsageEvidence } from "./types"

export type TraceUsageSummary = ReportedSpanUsage & {
  hasUsage: boolean
  knownSubtotal: number | null
  reportedCalls?: number
  modelCalls?: number
  unknownReason?: string | null
}

/** Read server evidence without summing spans or deriving a coverage denominator. */
export function getCanonicalTraceUsageSummary(
  evidence?: NativeTraceUsageEvidence
): TraceUsageSummary | null {
  const canonical = evidence?.usage
  if (!canonical) return null
  const reported = evidence.reportedUsage?.total
  return {
    inputTokens: canonical.input ?? null,
    outputTokens: canonical.output ?? null,
    totalTokens: canonical.total,
    cacheReadTokens: canonical.cacheReadInput ?? null,
    cacheCreationTokens: canonical.cacheWriteInput ?? null,
    hasUsage:
      (evidence.coverage?.billableCalls ?? canonical.observationCount) !== 0 ||
      [
        canonical.input,
        canonical.output,
        canonical.total,
        canonical.cacheReadInput,
        canonical.cacheWriteInput,
      ].some((value) => value !== null && value !== undefined),
    knownSubtotal:
      canonical.total === null && reported && reported.reportedCalls > 0
        ? reported.value
        : null,
    reportedCalls: reported?.reportedCalls,
    modelCalls: evidence.coverage?.billableCalls,
    unknownReason: canonical.unknownReason,
  }
}

/** Shared list/detail wording: reported calls are independent of trace completeness. */
export function traceUsageText(usage: TraceUsageSummary, t: TFunction): string {
  const value =
    usage.totalTokens !== null
      ? t("traceLabels.tokenCountValue", { count: usage.totalTokens })
      : usage.knownSubtotal !== null
        ? t("metrics.framework.knownSubtotal", {
            tokens: usage.knownSubtotal.toLocaleString(),
          })
        : t("metrics.framework.totalUnknown")
  const coverage =
    usage.modelCalls !== undefined &&
    usage.modelCalls > 0 &&
    usage.reportedCalls !== undefined
      ? t("nativeSpanUsage.reportedCalls", {
          count: usage.modelCalls,
          reported: usage.reportedCalls,
          total: usage.modelCalls,
        })
      : null
  const ambiguity =
    usage.unknownReason === "ambiguous_nested_generations"
      ? t("nativeSpanUsage.overlappingReportedUsage")
      : null
  return [value, coverage, ambiguity].filter(Boolean).join(" · ")
}
