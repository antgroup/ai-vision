import { Play } from "lucide-react"
import { useTranslation } from "react-i18next"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { useProductGuide } from "./useProductGuide"

export function DemoDataBadge() {
  const { t } = useTranslation()
  return (
    <Badge
      variant="secondary"
      title={t("productGuide.demo.dataBadgeHint")}
      data-product-guide="demo-data"
    >
      {t("productGuide.demo.dataBadge")}
    </Badge>
  )
}

export function DemoExamplesButton({ className }: { className?: string }) {
  const guide = useProductGuide()
  const { t } = useTranslation()
  if (!guide?.canStartDemo) return null

  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      className={className}
      onClick={guide.openDemo}
      aria-haspopup="dialog"
    >
      <Play aria-hidden="true" />
      {t("productGuide.demo.headerEntry")}
    </Button>
  )
}
