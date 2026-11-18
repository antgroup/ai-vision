import { useTranslation } from "react-i18next"

import type { ReviewStatus } from "../types"

/** 状态 pill 标签 — 对齐原型 demo StatusBadge:h-5 全圆角 + 底色 + 圆点 + 文字。 */
const STATUS_CONFIG: Record<
  ReviewStatus,
  { badgeClass: string; dotClass: string; textClass: string; labelKey: string }
> = {
  pending: {
    badgeClass: "bg-muted/60",
    textClass: "text-muted-foreground",
    dotClass: "bg-muted-foreground",
    labelKey: "diagnose.badcase.statusPending",
  },
  confirmed: {
    badgeClass: "bg-[var(--color-bg-error)]",
    textClass: "text-[var(--color-error)]",
    dotClass: "bg-[var(--color-error)]",
    labelKey: "diagnose.badcase.statusConfirmed",
  },
  falsePositive: {
    badgeClass: "bg-[var(--color-bg-success)]",
    textClass: "text-[var(--color-success)]",
    dotClass: "bg-[var(--color-success)]",
    labelKey: "diagnose.badcase.statusFalsePositive",
  },
  skipped: {
    badgeClass: "bg-[var(--color-bg-auxiliary)]",
    textClass: "text-[var(--color-auxiliary)]",
    dotClass: "bg-[var(--color-auxiliary)]",
    labelKey: "diagnose.badcase.statusSkipped",
  },
  cannotJudge: {
    badgeClass: "bg-[var(--color-bg-auxiliary)]",
    textClass: "text-[var(--color-auxiliary)]",
    dotClass: "bg-[var(--color-auxiliary)]",
    labelKey: "diagnose.badcase.statusCannotJudge",
  },
}

const NO_ACTION_CONFIG = {
  badgeClass: "bg-[var(--color-bg-success)]",
  textClass: "text-[var(--color-success)]",
  dotClass: "bg-[var(--color-success)]",
  labelKey: "diagnose.badcase.statusNoAction",
}

interface BadCaseStatusBadgeProps {
  status: ReviewStatus
  tone?: "review" | "noAction"
}

export function BadCaseStatusBadge({
  status,
  tone = "review",
}: BadCaseStatusBadgeProps) {
  const { t } = useTranslation()
  const config = tone === "noAction" ? NO_ACTION_CONFIG : STATUS_CONFIG[status]

  return (
    <span
      className={`inline-flex h-5 items-center gap-1 rounded-3xl px-2 text-xs font-normal whitespace-nowrap ${config.badgeClass} ${config.textClass}`}
    >
      <span className={`size-1.5 shrink-0 rounded-full ${config.dotClass}`} />
      {t(config.labelKey)}
    </span>
  )
}
