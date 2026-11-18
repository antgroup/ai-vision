import { Rows2, Rows3, Rows4 } from "lucide-react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

export type TraceRowDensity = "compact" | "default" | "relaxed"

interface TraceRowDensityToggleProps {
  density: TraceRowDensity
  onChange: (density: TraceRowDensity) => void
}

const DENSITY_OPTIONS: {
  value: TraceRowDensity
  icon: typeof Rows2
  labelKey: string
}[] = [
  { value: "compact", icon: Rows4, labelKey: "traceRowDensity.compact" },
  { value: "default", icon: Rows3, labelKey: "traceRowDensity.default" },
  { value: "relaxed", icon: Rows2, labelKey: "traceRowDensity.relaxed" },
]

export function TraceRowDensityToggle({
  density,
  onChange,
}: TraceRowDensityToggleProps) {
  const { t } = useTranslation()

  return (
    <div className="flex items-center rounded-lg border bg-background p-0.5">
      {DENSITY_OPTIONS.map(({ value, icon: Icon, labelKey }) => (
        <Tooltip key={value}>
          <TooltipTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={t(labelKey)}
              className={cn(
                "size-7 rounded-md border border-transparent text-muted-foreground",
                density === value && "bg-accent text-foreground"
              )}
              onClick={() => onChange(value)}
            >
              <Icon className="size-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="top">{t(labelKey)}</TooltipContent>
        </Tooltip>
      ))}
    </div>
  )
}
