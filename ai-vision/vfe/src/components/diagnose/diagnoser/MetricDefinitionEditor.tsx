import { Plus, Trash2 } from "lucide-react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

import {
  createMetricDefinitionDraft,
  DEFAULT_METRIC_NAME,
} from "./metricDefinitionModel"
import type { MetricDefinitionDraft } from "./metricDefinitionModel"

function metricNameError(
  metrics: MetricDefinitionDraft[],
  index: number,
  emptyMessage: string,
  duplicateMessage: string
): string | null {
  const name = metrics[index]?.name.trim() ?? ""
  if (!name) return emptyMessage
  const duplicate = metrics.some(
    (metric, metricIndex) =>
      metricIndex !== index && metric.name.trim() === name
  )
  return duplicate ? duplicateMessage : null
}

export function MetricDefinitionEditor({
  mode,
  metrics = [],
  onChange,
}: {
  mode: "llm" | "rule"
  metrics?: MetricDefinitionDraft[]
  onChange?: (metrics: MetricDefinitionDraft[]) => void
}) {
  const { t } = useTranslation()

  const updateMetric = (
    index: number,
    field: "name" | "desc",
    value: string
  ) => {
    onChange?.(
      metrics.map((metric, metricIndex) =>
        metricIndex === index ? { ...metric, [field]: value } : metric
      )
    )
  }

  const emptyMessage = t("diagnose.diagnoser.metrics.nameRequired", {
    defaultValue: "请输入指标名称",
  })
  const duplicateMessage = t("diagnose.diagnoser.metrics.nameDuplicate", {
    defaultValue: "指标名称不能重复",
  })
  const displayedMetrics =
    mode === "llm" && metrics.length === 0
      ? [
          {
            id: "llm-default-metric",
            name: DEFAULT_METRIC_NAME,
            desc: t("diagnose.diagnoser.form.quality"),
          },
        ]
      : metrics

  return (
    <div className="overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-page)]">
      <Table className="table-fixed">
        <colgroup>
          <col className="w-[40%]" />
          <col />
          {mode === "rule" ? <col className="w-14" /> : null}
        </colgroup>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="h-10 bg-[var(--color-bg-subtle)] px-3 text-xs text-[var(--color-text-secondary)]">
              {t("diagnose.diagnoser.metrics.name", {
                defaultValue: "指标名称",
              })}
              {mode === "rule" ? (
                <span className="ml-1 text-[var(--color-error)]">*</span>
              ) : null}
            </TableHead>
            <TableHead className="h-10 bg-[var(--color-bg-subtle)] px-3 text-xs text-[var(--color-text-secondary)]">
              {t("diagnose.diagnoser.metrics.description", {
                defaultValue: "指标说明",
              })}
            </TableHead>
            {mode === "rule" ? (
              <TableHead className="h-10 bg-[var(--color-bg-subtle)] px-2 text-center text-xs text-[var(--color-text-secondary)]">
                {t("common.actions", { defaultValue: "操作" })}
              </TableHead>
            ) : null}
          </TableRow>
        </TableHeader>
        <TableBody>
          {displayedMetrics.map((metric, index) => {
            const error =
              mode === "rule"
                ? metricNameError(
                    metrics,
                    index,
                    emptyMessage,
                    duplicateMessage
                  )
                : null
            return (
              <TableRow key={metric.id} className="hover:bg-transparent">
                <TableCell className="p-2 align-top">
                  <Input
                    value={metric.name}
                    disabled={mode === "llm"}
                    aria-invalid={!!error}
                    onChange={(event) =>
                      updateMetric(index, "name", event.target.value)
                    }
                    placeholder={t(
                      "diagnose.diagnoser.metrics.namePlaceholder",
                      {
                        defaultValue: "例如：quality",
                      }
                    )}
                    className="h-8 text-xs"
                  />
                  {error ? (
                    <p className="mt-1 text-xs text-destructive">{error}</p>
                  ) : null}
                </TableCell>
                <TableCell className="p-2 align-top">
                  <Input
                    value={metric.desc}
                    disabled={mode === "llm"}
                    onChange={(event) =>
                      updateMetric(index, "desc", event.target.value)
                    }
                    placeholder={t(
                      "diagnose.diagnoser.metrics.descriptionPlaceholder",
                      { defaultValue: "说明该指标判断的内容" }
                    )}
                    className="h-8 text-xs"
                  />
                </TableCell>
                {mode === "rule" ? (
                  <TableCell className="p-2 text-center align-top">
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      className="size-8 text-muted-foreground hover:text-destructive"
                      disabled={metrics.length === 1}
                      title={t("diagnose.diagnoser.metrics.remove", {
                        defaultValue: "删除指标",
                      })}
                      aria-label={t("diagnose.diagnoser.metrics.remove", {
                        defaultValue: "删除指标",
                      })}
                      onClick={() =>
                        onChange?.(
                          metrics.filter(
                            (_, metricIndex) => metricIndex !== index
                          )
                        )
                      }
                    >
                      <Trash2 className="size-3.5" />
                    </Button>
                  </TableCell>
                ) : null}
              </TableRow>
            )
          })}
        </TableBody>
        {mode === "rule" ? (
          <TableFooter className="border-t bg-[var(--color-bg-subtle)]">
            <TableRow className="hover:bg-transparent">
              <TableCell colSpan={3} className="p-2">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-8 gap-1.5 px-2 text-xs font-normal"
                  onClick={() =>
                    onChange?.([...metrics, createMetricDefinitionDraft()])
                  }
                >
                  <Plus className="size-3.5" />
                  {t("diagnose.diagnoser.metrics.add", {
                    defaultValue: "添加指标",
                  })}
                </Button>
              </TableCell>
            </TableRow>
          </TableFooter>
        ) : null}
      </Table>
    </div>
  )
}
