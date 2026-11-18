import type { TFunction } from "i18next"

export function diagnosisExecutionMessage(
  execution: {
    code?: string
    msg?: string
    evidence?: Record<string, unknown>
  },
  t: TFunction
): string {
  if (execution.code === "projection_unavailable") {
    const failure = execution.evidence?.projection_failure
    const cause =
      failure && typeof failure === "object" && "cause" in failure
        ? failure.cause
        : null
    const causes = [
      "framework_root_missing",
      "framework_root_ambiguous",
      "source_spans_empty",
    ]
    return t(
      typeof cause === "string" && causes.includes(cause)
        ? `diagnose.execution.${cause}`
        : "diagnose.execution.projection_unavailable"
    )
  }
  if (
    execution.code === "executor_exception" ||
    (execution.code === "legacy_execution_failed" &&
      (!execution.msg ||
        ["诊断器执行失败", "历史诊断执行失败"].includes(execution.msg)))
  ) {
    return t("diagnose.execution.executor_exception")
  }
  if (execution.code === "sandbox_plugin_upgrade_required") {
    return t("enhancedRule.pluginUpgradeRequired")
  }
  if (execution.code === "sandbox_input_limit") {
    const details = execution.evidence?.sandbox_input
    if (details && typeof details === "object") {
      const { actual_bytes: actual, allowed_bytes: allowed } =
        details as Record<string, unknown>
      if (
        typeof actual === "number" &&
        Number.isSafeInteger(actual) &&
        actual >= 0 &&
        typeof allowed === "number" &&
        Number.isSafeInteger(allowed) &&
        allowed > 0
      ) {
        return t("enhancedRule.inputLimitDetails", { actual, allowed })
      }
    }
    return t("enhancedRule.inputLimit")
  }
  return execution.msg || t("diagnose.diagnoser.form.trialFailed")
}
