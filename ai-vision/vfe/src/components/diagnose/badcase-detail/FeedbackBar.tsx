import { useEffect, useRef, useState, type ReactNode } from "react"
import { useTranslation } from "react-i18next"
import {
  CheckCircle2,
  XCircle,
  HelpCircle,
  Loader2,
  SkipForward,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { formatDateTime } from "@/lib/formatters"
import type { DiagnosisFeedback, ReviewStatus } from "../types"

type FeedbackResult = Exclude<ReviewStatus, "pending">

interface FeedbackBarProps {
  canSubmit?: boolean
  status: ReviewStatus
  feedback?: DiagnosisFeedback | null
  onSubmit: (result: FeedbackResult, remark: string) => void
  onClose: () => void
  isSubmitting?: boolean
  reviewedBy?: string | null
  reviewedAt?: string | null
}

const CONCLUSIONS: {
  key: FeedbackResult
  labelKey: string
  icon: typeof CheckCircle2
}[] = [
  {
    key: "confirmed",
    labelKey: "diagnose.badcase.feedbackConfirm",
    icon: CheckCircle2,
  },
  {
    key: "falsePositive",
    labelKey: "diagnose.badcase.feedbackFalsePositive",
    icon: XCircle,
  },
  {
    key: "cannotJudge",
    labelKey: "diagnose.badcase.feedbackCannotJudge",
    icon: HelpCircle,
  },
  {
    key: "skipped",
    labelKey: "diagnose.badcase.feedbackSkip",
    icon: SkipForward,
  },
]

const ACTIVE_CLASS = "border-primary bg-primary text-primary-foreground"

function formatTime(s: string | null | undefined): string {
  return s ? formatDateTime(s) : ""
}

function readString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null
}

function TruncatedRemark({ remark }: { remark: string }) {
  const textRef = useRef<HTMLSpanElement>(null)
  const [isTruncated, setIsTruncated] = useState(false)

  useEffect(() => {
    const text = textRef.current
    if (!text) return

    const check = () => setIsTruncated(text.scrollWidth > text.clientWidth)
    check()
    const observer = new ResizeObserver(check)
    observer.observe(text)
    return () => observer.disconnect()
  }, [remark])

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span ref={textRef} className="min-w-0 truncate text-foreground">
          {remark}
        </span>
      </TooltipTrigger>
      {isTruncated && (
        <TooltipContent className="max-w-[480px] text-xs break-words whitespace-pre-wrap">
          {remark}
        </TooltipContent>
      )}
    </Tooltip>
  )
}

function ResolvedFooter({
  feedback,
  reviewedBy,
  reviewedAt,
}: Pick<FeedbackBarProps, "feedback" | "reviewedBy" | "reviewedAt">) {
  const { t } = useTranslation()
  const remark = readString(feedback?.remark)

  if (!remark && !reviewedBy && !reviewedAt) return null

  return (
    <dl className="flex min-h-12 shrink-0 flex-wrap items-center gap-x-5 gap-y-1 border-t bg-muted/20 px-4 py-3 text-xs">
      {remark && (
        <div className="flex min-w-0 items-baseline gap-1.5">
          <dt className="shrink-0 text-muted-foreground">
            {t("diagnose.badcase.feedbackRemark")}
          </dt>
          <dd className="flex min-w-0">
            <TruncatedRemark remark={remark} />
          </dd>
        </div>
      )}
      {reviewedBy && (
        <div className="flex min-w-0 items-baseline gap-1.5">
          <dt className="shrink-0 text-muted-foreground">
            {t("diagnose.badcase.feedbackReviewer")}
          </dt>
          <dd className="break-all text-foreground">{reviewedBy}</dd>
        </div>
      )}
      {reviewedAt && (
        <div className="flex items-baseline gap-1.5">
          <dt className="shrink-0 text-muted-foreground">
            {t("diagnose.badcase.feedbackTime")}
          </dt>
          <dd className="text-foreground tabular-nums">
            {formatTime(reviewedAt)}
          </dd>
        </div>
      )}
    </dl>
  )
}

export function FeedbackBar({
  status,
  feedback,
  onSubmit,
  onClose,
  isSubmitting = false,
  canSubmit = true,
  reviewedBy,
  reviewedAt,
}: FeedbackBarProps) {
  const { t } = useTranslation()
  const [active, setActive] = useState<FeedbackResult | null>(null)
  const [remark, setRemark] = useState("")

  const isTerminal =
    status === "confirmed" ||
    status === "falsePositive" ||
    (status !== "pending" && !canSubmit)
  if (isTerminal) {
    return (
      <ResolvedFooter
        feedback={feedback}
        reviewedBy={reviewedBy}
        reviewedAt={reviewedAt}
      />
    )
  }

  if (!canSubmit)
    return (
      <div className="border-t p-4 text-xs text-muted-foreground">
        {t("diagnose.quick.feedbackReadOnly")}
      </div>
    )

  const submit = () => {
    if (!active || isSubmitting) return
    onSubmit(active, remark)
    setActive(null)
    setRemark("")
  }

  const onBtnClick = (c: FeedbackResult) => {
    setActive((cur) => (cur === c ? null : c))
  }

  return (
    <div className="shrink-0 border-t p-4">
      <div className="flex flex-col gap-3">
        {/* 行1:标题 + 提交操作 */}
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium text-muted-foreground">
            {t("diagnose.badcase.feedbackTitle", { defaultValue: "你的判断" })}
          </span>
          {active && (
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                size="sm"
                className="h-8"
                disabled={isSubmitting}
                onClick={onClose}
              >
                {t("common.cancel")}
              </Button>
              <Button
                size="sm"
                className="h-8 gap-1.5"
                disabled={isSubmitting}
                onClick={submit}
              >
                {isSubmitting ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : null}
                {t("diagnose.badcase.feedbackSubmit", {
                  defaultValue: "提交反馈",
                })}
              </Button>
            </div>
          )}
        </div>

        {/* 行2:4 个判断按钮(常驻,选中态) */}
        <div className="flex flex-wrap gap-1.5">
          {CONCLUSIONS.map((c) => {
            const Icon = c.icon
            const isActive = active === c.key
            return (
              <Button
                key={c.key}
                variant="outline"
                size="sm"
                className={cn(
                  "h-8 gap-1.5 px-3 text-xs font-normal transition-colors",
                  isActive && ACTIVE_CLASS
                )}
                disabled={isSubmitting}
                onClick={() => onBtnClick(c.key)}
              >
                <Icon className="size-3.5" />
                {t(c.labelKey)}
              </Button>
            )
          })}
        </div>

        {active && (
          <div className="flex flex-col gap-3 border-t border-border/60 pt-3">
            <Field
              label={t("diagnose.badcase.feedbackRemark", {
                defaultValue: "备注",
              })}
            >
              <Input
                placeholder={
                  active === "falsePositive"
                    ? t("diagnose.badcase.fpRemarkPlaceholder")
                    : active === "cannotJudge"
                      ? t("diagnose.badcase.cjRemarkPlaceholder")
                      : t("diagnose.badcase.remarkPlaceholder")
                }
                value={remark}
                onChange={(e) => setRemark(e.target.value)}
                className="h-8 text-sm"
              />
            </Field>
          </div>
        )}
      </div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      {children}
    </div>
  )
}
