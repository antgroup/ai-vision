import { toast } from "sonner"
import { writeClipboardText } from "@/lib/clipboard"
import { useState, type ReactNode } from "react"
import {
  Check,
  ChevronRight,
  ChevronsDownUp,
  ChevronsUpDown,
  Copy,
  Loader2,
} from "lucide-react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { useTranslation } from "react-i18next"

import { cn } from "@/lib/utils"

import type { BadCaseDetail, DiagnosisVerdict } from "../types"

export interface DiagnosisMetricConclusion {
  id: string
  name: string
  verdict?: DiagnosisVerdict
  reason?: string | null
}

export function DiagnosisInputToggle({ prompt }: { prompt?: string | null }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)

  if (!prompt) return null

  return (
    <div className="mt-2">
      <button
        type="button"
        className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        onClick={() => setOpen((v) => !v)}
      >
        <ChevronRight
          className={`size-3 transition-transform ${open ? "rotate-90" : ""}`}
        />
        {t("diagnose.detail.diagnosisInput", {
          defaultValue: "诊断 Prompt",
        })}
      </button>
      {open && (
        <pre className="mt-2 max-h-[400px] overflow-auto rounded-md bg-muted/40 p-3 font-mono text-xs leading-relaxed text-foreground/80">
          {prompt}
        </pre>
      )}
    </div>
  )
}

export function DiagnosisConclusionSection({
  summary,
  metrics,
  diagnosisInput,
}: {
  summary?: string | null
  metrics: DiagnosisMetricConclusion[]
  diagnosisInput?: string | null
}) {
  const { t } = useTranslation()

  return (
    <DetailSection
      title={t("diagnose.detail.conclusion", {
        defaultValue: "诊断依据",
      })}
    >
      <div className="flex flex-col gap-3 pt-1">
        {summary ? (
          <p className="text-sm leading-relaxed break-words whitespace-pre-wrap text-foreground">
            {summary}
          </p>
        ) : null}
        {metrics.length > 1 ? (
          <div className="flex flex-col gap-2">
            <span className="text-xs font-medium text-muted-foreground">
              {t("diagnose.detail.hitMetrics", {
                defaultValue: "命中指标",
              })}
            </span>
            {metrics.map((metric) => (
              <div key={metric.id} className="rounded-md bg-muted/30 px-3 py-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-xs font-medium text-foreground">
                    {metric.name}
                  </span>
                  {metric.verdict === "bad" || metric.verdict === "failed" ? (
                    <span className="rounded-md bg-destructive/10 px-2 py-1 text-xs leading-4 text-destructive">
                      {metric.verdict === "failed"
                        ? t("diagnose.detail.metricFailed", {
                            defaultValue: "诊断失败",
                          })
                        : t("diagnose.detail.metricBad", {
                            defaultValue: "异常",
                          })}
                    </span>
                  ) : null}
                </div>
                {metric.reason ? (
                  <div className="mt-2">
                    <div className="text-xs text-muted-foreground">
                      {t("diagnose.detail.metricReason", {
                        defaultValue: "判定原因",
                      })}
                    </div>
                    <p className="mt-1 text-sm leading-relaxed break-words whitespace-pre-wrap text-foreground">
                      {metric.reason}
                    </p>
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        ) : null}
        <DiagnosisInputToggle prompt={diagnosisInput} />
      </div>
    </DetailSection>
  )
}

export function DetailSection({
  title,
  children,
  action,
  loading,
}: {
  title: string
  children: ReactNode
  action?: ReactNode
  loading?: boolean
}) {
  return (
    <section className="relative rounded-lg border border-border bg-card text-card-foreground">
      <div className="flex items-center justify-between gap-3 px-3 py-1.5">
        <div className="flex items-center gap-2">
          <h4 className="text-sm font-medium text-foreground">{title}</h4>
          {loading && (
            <Loader2 className="size-3.5 animate-spin text-muted-foreground" />
          )}
        </div>
        {action}
      </div>
      <div className="px-3 pb-2">{children}</div>
    </section>
  )
}

export function CopyIconButton({ text }: { text: string }) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    try {
      await writeClipboardText(text)
    } catch {
      setCopied(false)
      toast.error(t("common.copyFailed"))
      return
    }
    setCopied(true)
    window.setTimeout(() => setCopied(false), 2000)
  }

  return (
    <button
      type="button"
      className="inline-flex shrink-0 items-center rounded p-0.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
      title={t("common.copy")}
      aria-label={t("common.copy")}
      onClick={handleCopy}
    >
      {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
    </button>
  )
}

export function IOSection({
  label,
  text,
  variant = "input",
}: {
  label: string
  text: string | null
  variant?: "input" | "output"
}) {
  const { t } = useTranslation()
  const [collapsed, setCollapsed] = useState(false)
  const content = text ?? ""
  const bgClass = variant === "input" ? "bg-blue-500/5" : "bg-green-500/5"
  return (
    <div>
      <div className="mb-1 flex items-center gap-2">
        <h4 className="text-sm font-medium text-foreground">{label}</h4>
        <span className="flex-1" />
        {content && (
          <button
            type="button"
            className="inline-flex shrink-0 items-center rounded p-0.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            title={t(collapsed ? "common.showMore" : "common.collapse")}
            aria-label={t(collapsed ? "common.showMore" : "common.collapse")}
            onClick={() => setCollapsed((v) => !v)}
          >
            {collapsed ? (
              <ChevronsUpDown className="size-3.5" />
            ) : (
              <ChevronsDownUp className="size-3.5" />
            )}
          </button>
        )}
        {content && <CopyIconButton text={content} />}
      </div>
      <div className={cn("rounded-lg p-3", bgClass)}>
        {content ? (
          <div
            className={cn(
              "text-sm break-words text-foreground",
              variant === "input" && "whitespace-pre-wrap",
              collapsed && "line-clamp-4"
            )}
          >
            {variant === "output" ? (
              <div className="prose max-w-none [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {content}
                </ReactMarkdown>
              </div>
            ) : (
              content
            )}
          </div>
        ) : (
          <span className="text-sm text-muted-foreground">—</span>
        )}
      </div>
    </div>
  )
}

export function QuestionAnswerSection({
  userQuestion,
  agentAnswer,
}: {
  userQuestion: string | null
  agentAnswer: string | null
}) {
  const { t } = useTranslation()

  return (
    <section className="rounded-lg border border-border bg-card p-3 text-card-foreground">
      <div className="flex flex-col gap-3">
        <IOSection
          label={t("diagnose.detail.userQuery", {
            defaultValue: "用户问题",
          })}
          text={userQuestion}
          variant="input"
        />
        <IOSection
          label={t("diagnose.detail.agentOutput", {
            defaultValue: "Agent 回答",
          })}
          text={agentAnswer}
          variant="output"
        />
      </div>
    </section>
  )
}

export function SnapshotOnlySections({
  detail,
  evidenceOverview,
  diagnosisMetrics,
  diagnosisInput,
}: {
  detail: BadCaseDetail
  evidenceOverview?: string
  diagnosisMetrics: DiagnosisMetricConclusion[]
  diagnosisInput?: string
}) {
  return (
    <div className="flex flex-col gap-4">
      <DiagnosisConclusionSection
        summary={evidenceOverview || detail.summary}
        metrics={diagnosisMetrics}
        diagnosisInput={diagnosisInput}
      />
      <QuestionAnswerSection
        userQuestion={detail.businessImpact.userQuery}
        agentAnswer={detail.businessImpact.agentOutput}
      />
    </div>
  )
}
