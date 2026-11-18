import { useTranslation } from "react-i18next"
import type { ComponentProps } from "react"

import { Badge } from "@/components/ui/badge"
import {
  TONE_TAG_CLASS,
  type StatTone,
} from "@/components/labs/shared/labsFormat"
import { cn } from "@/lib/utils"

type TraceBadgeProps = ComponentProps<"span"> & { tone?: StatTone }

/** One metadata treatment across trace views. Color is reserved for status. */
export function TraceBadge({
  tone = "default",
  className,
  children,
  ...props
}: TraceBadgeProps) {
  return (
    <Badge
      variant="secondary"
      data-trace-badge=""
      className={cn(
        "max-w-full min-w-0 shrink font-normal",
        tone !== "default" && TONE_TAG_CLASS[tone],
        className
      )}
      {...props}
    >
      <span className="min-w-0 truncate">{children}</span>
    </Badge>
  )
}

export function ModelBadge({
  model,
  title,
  className,
  children = model,
  ...props
}: TraceBadgeProps & { model: string }) {
  const { t } = useTranslation()
  return (
    <TraceBadge
      title={title ?? `${t("traceLabels.model")}: ${model}`}
      className={cn("font-mono", className)}
      {...props}
    >
      {children}
    </TraceBadge>
  )
}

export function ErrorBadge() {
  const { t } = useTranslation()
  return (
    <TraceBadge tone="error" className="shrink-0">
      {t("traceLabels.error")}
    </TraceBadge>
  )
}
