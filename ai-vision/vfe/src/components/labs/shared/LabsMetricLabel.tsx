import { CircleHelp } from "lucide-react"
import { useTranslation } from "react-i18next"

import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"

/** Shared metric help for overview strips, cards, and tool-table headings. */
export function LabsMetricLabel({
  label,
  help,
}: {
  label: string
  help?: string
}) {
  const { t } = useTranslation()
  return (
    <span className="inline-flex max-w-full min-w-0 items-center gap-1">
      <span className="min-w-0 [overflow-wrap:anywhere]">{label}</span>
      {help ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              className="inline-flex size-4 shrink-0 items-center justify-center rounded-sm text-[var(--color-icon-normal)] hover:text-[var(--color-text-primary)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-text-primary)]"
              aria-label={t("labs.common.metricHelp", { metric: label })}
              onClick={(event) => event.stopPropagation()}
            >
              <CircleHelp className="size-3.5" aria-hidden="true" />
            </button>
          </TooltipTrigger>
          <TooltipContent
            side="top"
            sideOffset={6}
            collisionPadding={12}
            className="max-h-[70dvh] max-w-[min(22rem,calc(100vw-2rem))] overflow-y-auto text-left text-xs leading-5 break-words whitespace-pre-line"
          >
            {help}
          </TooltipContent>
        </Tooltip>
      ) : null}
    </span>
  )
}
