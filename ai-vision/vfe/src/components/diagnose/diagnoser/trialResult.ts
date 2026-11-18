import type { TrialResponse } from "../types"

/** Execution success alone does not mean this Trace received a verdict. */
export function isValidatedTrial(result: TrialResponse | null): boolean {
  return Boolean(
    result &&
    result.execution.status === "succeeded" &&
    !result.skipped &&
    (result.verdict === "good" || result.verdict === "bad") &&
    result.metrics.length > 0 &&
    result.metrics.every(
      (metric) => metric.verdict === "good" || metric.verdict === "bad"
    )
  )
}
