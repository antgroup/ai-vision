import type { NativeTraceDetailResponse } from "@/components/traces-otel/types"
import { getCanonicalTraceUsageSummary } from "../../components/traces-otel/traceUsage.ts"
import { inspectTraceStructure } from "../../components/traces/shared/traceIntegrity.ts"

export function expectedSpanCount(value: string): number | null {
  return /^\d+$/.test(value.trim()) &&
    Number.isSafeInteger(Number(value)) &&
    Number(value) > 0
    ? Number(value)
    : null
}

const count = (value: unknown): value is number =>
  typeof value === "number" && Number.isSafeInteger(value) && value >= 0

export function buildTraceVerificationReport(
  detail: NativeTraceDetailResponse,
  expected: number | null = null
) {
  const structure = inspectTraceStructure(detail.spans)
  const storageCount = count(detail.meta?.expectedSpanCount)
    ? detail.meta.expectedSpanCount
    : null
  const readState =
    detail.meta?.complete === false ||
    (storageCount !== null && storageCount !== structure.spanCount)
      ? "partial"
      : detail.meta?.complete === true &&
          storageCount !== null &&
          !structure.duplicateSpanIds.length
        ? "complete"
        : "unknown"
  const coverage = detail.semantics?.coverage
  const validCoverage =
    coverage &&
    count(coverage.billableCalls) &&
    count(coverage.knownUsageCalls) &&
    coverage.knownUsageCalls <= coverage.billableCalls
  return {
    ...structure,
    traceId: detail.traceId,
    storageCount,
    expectedCount: expected,
    expectedMatches:
      expected === null ? null : expected === structure.spanCount,
    readState,
    coverage: validCoverage
      ? {
          known: coverage.knownUsageCalls,
          total: coverage.billableCalls,
        }
      : null,
    usage: getCanonicalTraceUsageSummary(detail.semantics),
  }
}

export type TraceVerificationReport = ReturnType<
  typeof buildTraceVerificationReport
>
