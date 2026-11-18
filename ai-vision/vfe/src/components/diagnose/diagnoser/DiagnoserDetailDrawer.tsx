/**
 * 诊断器详情抽屉：
 * - LLM 诊断器直接渲染完整 Prompt Markdown，不解析业务结构。
 * - 规则诊断器统一展示描述、诊断逻辑和诊断指标。
 *
 * 数据全部来自 Diagnoser 和 spec。
 */
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { cn } from "@/lib/utils"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { useTranslation } from "react-i18next"

import type { Diagnoser } from "../types"
import { builtinRuleSource } from "../builtinDiagnoserPolicy"
import { LazyCodeMirrorEditor } from "./LazyCodeMirrorEditor"

const CATEGORY_LABEL_KEYS: Record<string, string> = {
  outcome: "diagnose.diagnoser.catOutcome",
  process: "diagnose.diagnoser.catProcess",
  safety: "diagnose.diagnoser.catSafety",
  system: "diagnose.diagnoser.catSystem",
}

function MarkdownBody({ children }: { children: string }) {
  return (
    <div className="overflow-x-auto text-sm leading-relaxed text-muted-foreground [&_a]:underline [&_code]:rounded-sm [&_code]:bg-muted [&_code]:px-1 [&_code]:py-1 [&_h1]:my-4 [&_h1]:text-xl [&_h1]:font-medium [&_h1]:text-foreground [&_h2]:my-4 [&_h2]:text-base [&_h2]:font-medium [&_h2]:text-foreground [&_h3]:my-3 [&_h3]:text-sm [&_h3]:font-medium [&_h3]:text-foreground [&_h4]:my-2 [&_h4]:text-sm [&_h4]:font-medium [&_h4]:text-foreground [&_li]:my-1 [&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5 [&_p]:my-2 [&_pre]:my-3 [&_pre]:overflow-x-auto [&_pre]:rounded-xl [&_pre]:bg-muted [&_pre]:p-4 [&_pre]:text-xs [&_pre]:text-foreground [&_table]:my-3 [&_table]:w-full [&_table]:border-collapse [&_td]:border [&_td]:p-2 [&_th]:border [&_th]:bg-muted [&_th]:p-2 [&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  )
}

export interface DiagnoserDetailDrawerProps {
  open: boolean
  onClose: () => void
  diagnoser: Diagnoser | null
  version?: string
  coreVariant?: string | null
  onConfigModel?: () => void
  onConfigure?: () => void
  onCopy?: () => void
  onWritePython?: () => void
  /** 展示用 spec，内置规则的说明已按当前语言转换。 */
  spec: Record<string, unknown> | null
}

export function DiagnoserDetailDrawer({
  open,
  onClose,
  diagnoser,
  version,
  coreVariant,
  onConfigModel,
  onConfigure,
  onCopy,
  onWritePython,
  spec,
}: DiagnoserDetailDrawerProps) {
  const { t, i18n } = useTranslation()
  if (!diagnoser) return null

  const isBuiltin = diagnoser.type === "builtin"
  const source = builtinRuleSource(spec)
  const modelConnection = diagnoser.modelConnection
  const coreModelKeyMissing =
    !!coreVariant && modelConnection?.apiKeyConfigured === false
  const modelConnectionStatus = !diagnoser.modelConnectionUuid
    ? t("diagnose.diagnoser.detail.modelNotConfigured")
    : modelConnection?.status === "available" && !coreModelKeyMissing
      ? t("diagnose.diagnoser.form.connected")
      : modelConnection?.status === "unavailable" || coreModelKeyMissing
        ? t("diagnose.diagnoser.form.connectionFailed")
        : t("diagnose.diagnoser.form.untested")
  const categoryKey = CATEGORY_LABEL_KEYS[diagnoser.category ?? ""]
  const dimLabel = categoryKey ? t(categoryKey) : (diagnoser.category ?? "")

  const prompt = typeof spec?.prompt === "string" ? spec.prompt : ""
  const attentionPoints = Array.isArray(spec?.attention_points)
    ? (spec!.attention_points as string[])
    : []

  // spec.detail 是规则诊断器的结构化展示元数据。
  const detail =
    (spec?.detail as Record<string, unknown> | undefined) ?? undefined
  const detailScoring = detail?.scoring as Record<string, unknown> | undefined
  const detailScoringText =
    typeof detailScoring?.text === "string" ? detailScoring.text : undefined
  const detailLogic = Array.isArray(detail?.logic)
    ? (detail.logic as string[])
    : undefined
  const detailMaterials = Array.isArray(detail?.materials)
    ? detail.materials.filter(
        (item): item is { key: string; desc?: unknown; label?: unknown } =>
          typeof item?.key === "string" && !!item.key
      )
    : []
  const hasLogic = !!detailLogic?.length
  const hasDistinctScoring =
    hasLogic && detailScoringText && !detailLogic.includes(detailScoringText)

  const isCurrentBuiltinRule =
    (isBuiltin || !!source) &&
    diagnoser.engine === "rule" &&
    (source?.version ?? version) === "3.0.0"
  const toolSuccessMinimum =
    typeof spec?.tool_success_min_rate === "number"
      ? spec.tool_success_min_rate
      : null
  const latencyBudget =
    typeof spec?.latency_budget_ms === "number" ? spec.latency_budget_ms : null
  const policyKey = "diagnose.diagnoser.detail.policy"
  let policy: { label: string; value: string; help?: string } | null = null
  if (isCurrentBuiltinRule && spec?.executor === "tool_success") {
    policy = {
      label: t(`${policyKey}.minimumToolSuccess`),
      value:
        toolSuccessMinimum === null
          ? t(`${policyKey}.defaultToolSuccess`)
          : new Intl.NumberFormat(i18n.resolvedLanguage, {
              style: "percent",
              maximumSignificantDigits: 15,
            }).format(toolSuccessMinimum),
      help:
        toolSuccessMinimum === null
          ? undefined
          : t(`${policyKey}.toolSuccessHelp`),
    }
  } else if (isCurrentBuiltinRule && spec?.executor === "total_latency") {
    policy = {
      label: t(`${policyKey}.latencyBudget`),
      value:
        latencyBudget === null
          ? t(`${policyKey}.defaultLatency`)
          : t(`${policyKey}.latencyValue`, {
              value: new Intl.NumberFormat(i18n.resolvedLanguage, {
                maximumSignificantDigits: 15,
              }).format(latencyBudget),
            }),
      help: latencyBudget === null ? undefined : t(`${policyKey}.latencyHelp`),
    }
  }

  const ruleCode = typeof spec?.code === "string" ? spec.code : ""
  const ruleMetrics = Array.isArray(spec?.metrics)
    ? (spec.metrics as Array<Record<string, unknown>>)
    : []

  return (
    <Sheet open={open} onOpenChange={(v) => !v && onClose()}>
      <SheetContent
        showCloseButton={false}
        className="flex flex-col gap-0 overflow-hidden p-0 data-[side=right]:w-full data-[side=right]:sm:max-w-[clamp(600px,50vw,900px)]"
      >
        {/* Header */}
        <div className="shrink-0 border-b px-4 py-4 sm:px-6">
          <div className="flex items-start justify-between gap-2">
            <SheetTitle className="min-w-0 text-xl font-medium wrap-anywhere">
              {diagnoser.name}
            </SheetTitle>
            <Button
              variant="ghost"
              size="sm"
              className="shrink-0"
              onClick={onClose}
            >
              {t("common.close")}
            </Button>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {dimLabel && <Badge variant="secondary">{dimLabel}</Badge>}
            <Badge
              variant="secondary"
              className={cn(
                diagnoser.engine === "rule"
                  ? "bg-[var(--color-bg-success)] text-[var(--color-success)]"
                  : "bg-[var(--color-bg-auxiliary)] text-[var(--color-auxiliary)]"
              )}
            >
              {diagnoser.engine === "rule"
                ? t("diagnose.diagnoser.engineRule")
                : "LLM"}
            </Badge>
            <Badge variant="secondary">
              {t(
                isBuiltin
                  ? "diagnose.diagnoser.sourceBuiltin"
                  : "diagnose.diagnoser.sourceCustom"
              )}
            </Badge>
            {diagnoser.engine === "llm" && (
              <Badge variant="secondary">
                {modelConnection
                  ? `${modelConnection.name} · ${modelConnection.modelName} · ${modelConnectionStatus}`
                  : modelConnectionStatus}
              </Badge>
            )}
          </div>
          {(onConfigModel || onConfigure || onCopy || onWritePython) && (
            <div className="mt-3 flex flex-wrap gap-2">
              {onConfigModel && (
                <Button variant="outline" size="sm" onClick={onConfigModel}>
                  {t("diagnose.diagnoser.configModel")}
                </Button>
              )}
              {onConfigure && (
                <Button variant="outline" size="sm" onClick={onConfigure}>
                  {t(
                    isBuiltin
                      ? "diagnose.diagnoser.ruleManagement.editThreshold"
                      : "diagnose.diagnoser.editConfig"
                  )}
                </Button>
              )}
              {onCopy && (
                <Button variant="outline" size="sm" onClick={onCopy}>
                  {t("diagnose.diagnoser.ruleManagement.copy")}
                </Button>
              )}
              {onWritePython && (
                <Button variant="outline" size="sm" onClick={onWritePython}>
                  {t("diagnose.diagnoser.ruleManagement.writePython")}
                </Button>
              )}
            </div>
          )}
        </div>

        {/* Content */}
        <div className="min-h-0 min-w-0 flex-1 overflow-y-auto px-4 py-6 sm:px-6">
          <div className="flex flex-col gap-6">
            {coreVariant && (
              <div className="flex flex-col gap-4 text-sm">
                <p>{diagnoser.description}</p>
                <section className="flex flex-col gap-2">
                  <h4 className="text-base font-medium">
                    {t("diagnose.diagnoser.detail.materials")}
                  </h4>
                  <p className="text-muted-foreground">
                    {t(
                      `diagnose.diagnoser.core.variants.${coreVariant}.materials`
                    )}
                  </p>
                  <p className="text-muted-foreground">
                    {t("diagnose.diagnoser.core.materialHint")}
                  </p>
                </section>
                {prompt && (
                  <details className="rounded-lg border p-3">
                    <summary className="cursor-pointer font-medium focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
                      {t("diagnose.diagnoser.core.prompt")}
                    </summary>
                    <div className="mt-3">
                      <MarkdownBody>{prompt}</MarkdownBody>
                    </div>
                  </details>
                )}
              </div>
            )}

            {diagnoser.engine === "llm" &&
              !coreVariant &&
              (prompt ? (
                <MarkdownBody>{prompt}</MarkdownBody>
              ) : (
                <p className="text-sm text-muted-foreground">—</p>
              ))}

            {diagnoser.engine === "rule" && (
              <>
                {diagnoser.description && (
                  <p className="text-sm leading-relaxed text-muted-foreground">
                    {diagnoser.description}
                  </p>
                )}

                {source && (
                  <p className="text-sm text-muted-foreground">
                    {t("diagnose.diagnoser.ruleManagement.copySource", {
                      name: t(
                        `diagnose.diagnoser.builtinNamesV3.${source.key}`,
                        { defaultValue: source.key }
                      ),
                    })}
                  </p>
                )}
                {(isBuiltin || source) && (source?.version ?? version) && (
                  <dl className="flex flex-wrap gap-x-2 text-sm">
                    <dt className="text-muted-foreground">
                      {t("diagnose.diagnoser.detail.ruleVersion")}
                    </dt>
                    <dd>{source?.version ?? version}</dd>
                  </dl>
                )}

                <section className="flex flex-col gap-2">
                  <h4 className="text-base font-medium text-foreground">
                    {t("diagnose.diagnoser.form.logic")}
                  </h4>
                  {ruleCode ? (
                    <LazyCodeMirrorEditor
                      value={ruleCode}
                      language="python"
                      readOnly
                      height="420px"
                      label="Python"
                      allowFullscreen
                      title={t("diagnose.diagnoser.detail.fullscreenPython")}
                    />
                  ) : hasLogic ? (
                    <ul className="flex flex-col gap-2 pl-4">
                      {detailLogic.map((item, index) => (
                        <li
                          key={index}
                          className="list-disc text-sm leading-relaxed text-muted-foreground"
                        >
                          {item}
                        </li>
                      ))}
                    </ul>
                  ) : detailScoringText ? (
                    <p className="text-sm leading-relaxed whitespace-pre-line text-muted-foreground">
                      {detailScoringText}
                    </p>
                  ) : attentionPoints.length > 0 ? (
                    <ul className="flex flex-col gap-2 pl-4">
                      {attentionPoints.map((item, index) => (
                        <li
                          key={index}
                          className="list-disc text-sm leading-relaxed text-muted-foreground"
                        >
                          {item}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      {t("diagnose.diagnoser.detail.logicUnavailable")}
                    </p>
                  )}
                  {hasDistinctScoring && (
                    <div className="flex flex-col gap-1">
                      <h5 className="text-sm font-medium">
                        {t("diagnose.diagnoser.detail.scoring")}
                      </h5>
                      <p className="text-sm whitespace-pre-line text-muted-foreground">
                        {detailScoringText}
                      </p>
                    </div>
                  )}
                  {policy && (
                    <dl className="space-y-1 rounded-lg border p-3 text-sm">
                      <dt className="font-medium">{policy.label}</dt>
                      <dd>{policy.value}</dd>
                      {policy.help && (
                        <dd className="text-muted-foreground">{policy.help}</dd>
                      )}
                    </dl>
                  )}
                </section>

                {detailMaterials.length > 0 && (
                  <section className="flex flex-col gap-2">
                    <h4 className="text-base font-medium text-foreground">
                      {t("diagnose.diagnoser.detail.materials")}
                    </h4>
                    <dl className="space-y-2 text-sm">
                      {detailMaterials.map(({ key, desc, label }) => {
                        const description =
                          typeof desc === "string" && desc
                            ? desc
                            : typeof label === "string" && label
                        return (
                          <div key={key}>
                            <dt className="font-mono wrap-anywhere">{key}</dt>
                            {description && (
                              <dd className="text-muted-foreground">
                                {description}
                              </dd>
                            )}
                          </div>
                        )
                      })}
                    </dl>
                  </section>
                )}

                {/* 3. 诊断指标 */}
                <section className="flex flex-col gap-2">
                  <h4 className="text-base font-medium text-foreground">
                    {t("diagnose.diagnoser.metrics.title")}
                  </h4>
                  {ruleMetrics.length > 0 ? (
                    <div className="overflow-hidden rounded-xl border border-[var(--color-border)]">
                      <Table>
                        <TableHeader>
                          <TableRow className="hover:bg-transparent">
                            <TableHead className="bg-[var(--color-bg-subtle)] text-[var(--color-text-secondary)]">
                              {t("diagnose.diagnoser.metrics.name")}
                            </TableHead>
                            <TableHead className="bg-[var(--color-bg-subtle)] text-[var(--color-text-secondary)]">
                              {t("diagnose.diagnoser.metrics.description")}
                            </TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {ruleMetrics.map((metric, index) => (
                            <TableRow key={index}>
                              <TableCell className="font-mono text-sm">
                                {(metric.name as string) || "—"}
                              </TableCell>
                              <TableCell className="text-sm">
                                {(metric.desc as string) || "—"}
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">—</p>
                  )}
                </section>
              </>
            )}
          </div>
        </div>
      </SheetContent>
    </Sheet>
  )
}
