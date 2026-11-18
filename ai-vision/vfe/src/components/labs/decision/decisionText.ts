import type { TFunction } from "i18next"

import { decisionTypeKey, type DecisionSpanInfo } from "./decisionDisplay"

export function decisionTypeLabel(type: string, t: TFunction): string {
  return t(`labs.decision.type.${decisionTypeKey(type)}`)
}

/** Column label for the question's output kind. Shown as the canonical
 * English name (Choice / Score / Noul) in every locale; unknown kinds
 * capitalize too. */
export function decisionKindLabel(kind: string | null): string {
  if (!kind) return "-"
  return kind.charAt(0).toUpperCase() + kind.slice(1)
}

export function decisionOptionLabel(
  info: DecisionSpanInfo,
  key: string,
  t: TFunction
): string {
  if (info.kind === "noul" && (key === "yes" || key === "no"))
    return key === "yes" ? "Yes" : "No"
  return info.type === "tool_approval"
    ? t(`labs.decision.choice.${key}`, { defaultValue: key })
    : key
}

/** One-line outcome, e.g. "→ claude-haiku-4-5", "自动放行", "technical"
 * or the unrounded Score/Noul value. */
export function decisionOutcome(info: DecisionSpanInfo, t: TFunction): string {
  if (info.action === "error") {
    return t("labs.decision.chip.error")
  }
  if (info.type === "model_routing") {
    return info.action === "fallback"
      ? t("labs.decision.chip.fallback", { model: info.applied ?? "-" })
      : t("labs.decision.chip.routed", { model: info.applied ?? "-" })
  }
  if (info.type === "tool_approval" && info.action)
    return t(`labs.decision.chip.${info.action}`, { defaultValue: info.action })
  // Preserve fractional Score and Noul values for the summary and raw-data
  // comparison. Their card options have separate presentation semantics.
  if (!info.choice) return "-"
  return decisionOptionLabel(info, info.choice, t)
}

export function decisionActionText(
  info: DecisionSpanInfo,
  t: TFunction
): string | null {
  if (!info.action) return null
  return t(`labs.decision.action.${info.action}`, {
    target: info.applied ?? "-",
    defaultValue: info.action,
  })
}
