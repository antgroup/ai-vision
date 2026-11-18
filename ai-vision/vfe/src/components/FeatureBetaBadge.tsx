import { useTranslation } from "react-i18next"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

/** Shared maturity marker for the product's experimental features. */
export function FeatureBetaBadge({ className }: { className?: string }) {
  const { t } = useTranslation()
  return (
    <Badge
      className={cn(
        "h-4 shrink-0 rounded-[6px] border-0 bg-[var(--color-bg-labs-tag)] px-1 py-0 text-[10px] leading-none font-normal text-[var(--color-labs)] shadow-none",
        className
      )}
    >
      {t("labs.common.beta")}
    </Badge>
  )
}
