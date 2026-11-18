import { useTranslation } from "react-i18next"
import { Link2 } from "lucide-react"

interface ShareBannerProps {
  sharerName: string
  createdAt: string
  expiresAt: string
}

export function ShareBanner({ sharerName, createdAt }: ShareBannerProps) {
  const { t, i18n } = useTranslation()

  const formattedDate = (() => {
    try {
      return new Date(createdAt).toLocaleString(i18n?.resolvedLanguage)
    } catch {
      return createdAt
    }
  })()

  return (
    <div className="flex shrink-0 items-center gap-2 border-b bg-muted/50 px-4 py-2 text-xs text-muted-foreground">
      <Link2 className="size-3.5 shrink-0" />
      <span>
        {t("share.banner", { name: sharerName, time: formattedDate })}
      </span>
    </div>
  )
}
