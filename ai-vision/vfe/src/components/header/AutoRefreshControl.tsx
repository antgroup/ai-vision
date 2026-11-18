import { useTranslation } from "react-i18next"
import { RefreshCw } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select"
import { useTimeFilter } from "@/contexts/TimeFilterContext"

const REFRESH_OPTIONS = [
  { value: "0", labelKey: "autoRefresh.off" },
  { value: "30000", labelKey: "autoRefresh.thirtySeconds" },
  { value: "60000", labelKey: "autoRefresh.oneMinute" },
  { value: "300000", labelKey: "autoRefresh.fiveMinutes" },
  { value: "900000", labelKey: "autoRefresh.fifteenMinutes" },
] as const

export function AutoRefreshControl({
  refreshPending = false,
}: {
  refreshPending?: boolean
}) {
  const { t } = useTranslation()
  const {
    refreshInterval,
    refreshDisabled,
    setRefreshInterval,
    triggerRefresh,
  } = useTimeFilter()
  const selected = REFRESH_OPTIONS.find(
    (option) => option.value === String(refreshInterval)
  )

  return (
    <div className="flex items-center">
      <Button
        variant="outline"
        size="icon"
        className="size-9 !rounded-l-full !rounded-r-none border-r-0"
        onClick={triggerRefresh}
        aria-label={t("common.refresh")}
        aria-busy={refreshPending || undefined}
        disabled={refreshPending}
      >
        <RefreshCw
          className={
            refreshPending ? "size-3.5 motion-safe:animate-spin" : "size-3.5"
          }
        />
      </Button>
      <Select
        value={String(refreshInterval)}
        onValueChange={(value) => setRefreshInterval(Number(value))}
        disabled={refreshDisabled}
      >
        <SelectTrigger
          aria-label={t("autoRefresh.label")}
          className="h-9 w-auto min-w-[60px] gap-1.5 !rounded-l-none !rounded-r-full border border-input bg-background text-sm"
          title={refreshDisabled ? t("autoRefresh.disabledHint") : undefined}
        >
          <span>{t(selected?.labelKey ?? "autoRefresh.off")}</span>
        </SelectTrigger>
        <SelectContent position="popper" className="!rounded-lg">
          {REFRESH_OPTIONS.map((option) => (
            <SelectItem
              key={option.value}
              value={option.value}
              className="!rounded-md"
            >
              {t(option.labelKey)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}
