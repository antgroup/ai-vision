/**
 * 诊断器卡片 — 对齐 demo(DetectorsPage.DetectorCard)。
 * 结构：圆角卡片 + 引擎色淡底头部(规则=绿、LLM=紫)、
 * 标题 + 更多菜单、两行描述、底栏标签(规则/LLM + 内置/自定义 + 场景 + 模型)。
 */
import { useId, type CSSProperties } from "react"
import { useTranslation } from "react-i18next"
import { MoreHorizontal } from "lucide-react"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { cn } from "@/lib/utils"
import { SCENARIO_LABEL_KEYS, type Diagnoser } from "../types"
import { getDimensionTheme } from "./diagnoserColors"

interface DiagnoserCardProps {
  diagnoser: Diagnoser
  requireAvailableModel?: boolean
  onConfigModel?: () => void
  onEdit?: () => void
  onDelete?: () => void
  onOpenDetail?: () => void
}

export function DiagnoserCard({
  diagnoser,
  requireAvailableModel = false,
  onConfigModel,
  onEdit,
  onDelete,
  onOpenDetail,
}: DiagnoserCardProps) {
  const { t } = useTranslation()
  const titleId = useId()
  const requiresLlm = diagnoser.engine === "llm"
  const isBuiltin = diagnoser.type === "builtin"
  const isCustom = diagnoser.type === "custom"
  const modelConnection = diagnoser.modelConnection
  const modelReady =
    !!diagnoser.modelConnectionUuid &&
    modelConnection?.status === "available" &&
    (!requireAvailableModel || modelConnection.apiKeyConfigured)
  const llmNotReady = requiresLlm && !modelReady
  const theme = getDimensionTheme(diagnoser.category)

  const gradientStyle: CSSProperties = { background: theme.gradientCss }

  const modelAction = onConfigModel ?? (isCustom ? onEdit : undefined)

  const hasMenu =
    (requiresLlm && onConfigModel) ||
    (isCustom && onEdit) ||
    (isCustom && onDelete)

  return (
    <div
      onClick={
        onOpenDetail
          ? (e) => {
              // 点到按钮/菜单项等交互元素时不打开详情抽屉（避免配置模型/编辑/删除同时弹抽屉）
              if (
                (e.target as HTMLElement).closest?.(
                  'button, a, [role="menuitem"], [role="menu"]'
                )
              ) {
                return
              }
              onOpenDetail()
            }
          : undefined
      }
      className={cn(
        "group relative flex w-full flex-col overflow-hidden rounded-4xl border border-border bg-card shadow-sm transition-all",
        onOpenDetail && "cursor-pointer",
        llmNotReady && !requireAvailableModel
          ? "opacity-55"
          : "hover:border-border hover:shadow-[0_1px_6px_0_rgba(0,0,0,.04)]"
      )}
    >
      <div className="relative flex min-w-0 flex-1 flex-col gap-2.5 px-4 pt-4 pb-3">
        <div
          className="absolute inset-0 dark:opacity-0"
          style={gradientStyle}
        />
        {/* 标题行：名称 + 菜单 */}
        <div className="relative flex items-center gap-2">
          <h3
            id={titleId}
            className="min-w-0 flex-1 truncate text-sm font-medium text-foreground"
          >
            {diagnoser.name}
          </h3>
          {hasMenu && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  type="button"
                  onClick={(e) => e.stopPropagation()}
                  className="flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground/50 transition-colors hover:bg-muted hover:text-foreground"
                >
                  <MoreHorizontal className="size-4" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-32">
                {requiresLlm && onConfigModel && (
                  <DropdownMenuItem onSelect={onConfigModel}>
                    {t("diagnose.diagnoser.configModel")}
                  </DropdownMenuItem>
                )}
                {isCustom && onEdit && (
                  <DropdownMenuItem onSelect={onEdit}>
                    {t("common.edit")}
                  </DropdownMenuItem>
                )}
                {isCustom && onDelete && (
                  <DropdownMenuItem
                    className="text-destructive"
                    onSelect={onDelete}
                  >
                    {t("common.delete")}
                  </DropdownMenuItem>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>

        {/* 描述 */}
        <p className="relative line-clamp-2 min-h-10 text-xs leading-relaxed text-muted-foreground">
          {diagnoser.description}
        </p>
      </div>

      {/* 底栏：标签沉底，对齐 demo */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/50 px-4 py-2.5">
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          <span
            className={cn(
              "inline-flex h-5 items-center rounded px-1.5 text-xs font-medium",
              requiresLlm
                ? "bg-violet-500/8 text-violet-600 dark:text-violet-400"
                : "bg-emerald-500/8 text-emerald-600 dark:text-emerald-400"
            )}
          >
            {requiresLlm ? "LLM" : t("diagnose.diagnoser.engineRule")}
          </span>
          <span
            className={cn(
              "inline-flex h-5 items-center rounded px-1.5 text-xs",
              isBuiltin
                ? "bg-muted/60 text-muted-foreground"
                : "bg-sky-500/8 text-sky-600 dark:text-sky-400"
            )}
          >
            {isBuiltin
              ? t("diagnose.diagnoser.sourceBuiltin")
              : t("diagnose.diagnoser.sourceCustom")}
          </span>
          {diagnoser.scenario && (
            <span
              key="scenario"
              className="inline-flex h-5 items-center rounded bg-muted/50 px-1.5 text-xs text-muted-foreground"
            >
              {Object.hasOwn(SCENARIO_LABEL_KEYS, diagnoser.scenario)
                ? t(SCENARIO_LABEL_KEYS[diagnoser.scenario])
                : isBuiltin
                  ? t("diagnose.diagnoser.scenario.legacyBuiltin")
                  : diagnoser.scenario}
            </span>
          )}
          {requiresLlm &&
            (modelConnection ? (
              <span
                className={cn(
                  "inline-flex h-5 items-center gap-1 rounded px-1.5 text-xs",
                  modelReady
                    ? "bg-muted/50 text-muted-foreground"
                    : "bg-red-500/8 text-red-500"
                )}
              >
                <span
                  className={cn(
                    "size-1.5 rounded-full",
                    modelReady ? "bg-emerald-500" : "bg-red-500"
                  )}
                />
                {modelConnection.name}
              </span>
            ) : (
              <span className="inline-flex h-5 items-center rounded bg-amber-500/8 px-1.5 text-xs text-amber-600 dark:text-amber-400">
                {t("diagnose.diagnoser.modelUnconfigured")}
              </span>
            ))}
        </div>
        <div className="ml-auto flex items-center gap-3">
          {llmNotReady && modelAction && (
            <button
              type="button"
              className="text-sm text-primary hover:underline"
              onClick={modelAction}
            >
              {onConfigModel
                ? t("diagnose.diagnoser.configModel")
                : t("common.edit")}
            </button>
          )}
          {onOpenDetail && (
            <button
              type="button"
              className="text-sm font-medium text-primary hover:underline"
              aria-describedby={titleId}
              onClick={onOpenDetail}
            >
              {t("diagnose.diagnoser.detail.view")}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
