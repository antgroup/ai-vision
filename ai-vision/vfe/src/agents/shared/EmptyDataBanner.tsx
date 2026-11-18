import { TriangleAlert } from "lucide-react"
import { Trans, useTranslation } from "react-i18next"
import { Link } from "react-router-dom"

import { cn } from "@/lib/utils"

const LINK_CLASS_NAME = "text-blue-600 hover:underline dark:text-blue-400"

export interface EmptyDataMessageProps {
  className?: string
}

export function EmptyDataMessage({ className }: EmptyDataMessageProps) {
  const { t } = useTranslation()

  return (
    <div className={cn("min-w-0 leading-relaxed", className)}>
      <span className="font-medium text-foreground">
        {t("emptyData.title")}
      </span>
      <span className="mx-1.5 text-muted-foreground">—</span>
      <span className="text-muted-foreground">
        <Trans
          i18nKey="emptyData.description"
          components={{
            access: <Link to="/access" className={LINK_CLASS_NAME} />,
          }}
        />
      </span>
    </div>
  )
}

export interface EmptyDataBannerProps {
  agentKey: string
  agentProduct?: string
  visible: boolean
  className?: string
  actionHref?: string
  actionLabel?: string
  message?: string
}

export function EmptyDataBanner({
  visible,
  className,
  actionHref = "/access",
  actionLabel,
  message,
}: EmptyDataBannerProps) {
  const { t } = useTranslation()

  if (!visible) return null

  const defaultActionLabel = (
    <Trans
      i18nKey="emptyData.description"
      components={{
        access: <Link to={actionHref} className={LINK_CLASS_NAME} />,
      }}
    />
  )

  return (
    <div
      className={cn(
        "mx-4 flex items-start gap-3 overflow-hidden rounded-lg border border-amber-500/30 bg-amber-500/5 px-4 py-3 text-sm dark:border-amber-400/30 dark:bg-amber-400/5",
        className
      )}
    >
      <TriangleAlert className="mt-0.5 size-4 shrink-0 text-amber-600 dark:text-amber-400" />
      {message ? (
        <div className="min-w-0 leading-relaxed">
          <span className="font-medium text-foreground">
            {t("emptyData.title")}
          </span>
          <span className="mx-1.5 text-muted-foreground">—</span>
          <span className="text-muted-foreground">{message}</span>
          <Link
            to={actionHref}
            className="ml-2 font-medium text-blue-600 hover:underline dark:text-blue-400"
          >
            {actionLabel}
          </Link>
        </div>
      ) : (
        defaultActionLabel
      )}
    </div>
  )
}
