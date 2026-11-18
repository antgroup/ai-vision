import { useTranslation } from "react-i18next"
import { Check, CircleHelp } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"

interface DiagnoserFormSectionProps {
  title: string
  help?: React.ReactNode
  status?: string
  children: React.ReactNode
}

/** Matches the demo's unnumbered section rhythm inside the diagnoser drawer. */
export function DiagnoserFormSection({
  title,
  help,
  status,
  children,
}: DiagnoserFormSectionProps) {
  const { t } = useTranslation()
  return (
    <section className="flex flex-col gap-4">
      <div className="flex min-h-6 items-center justify-between gap-3">
        <div className="flex items-center gap-0.5">
          <h3 className="text-sm font-medium text-foreground">{title}</h3>
          {help ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  className="size-4 p-0 text-muted-foreground hover:bg-transparent hover:text-foreground"
                  aria-label={t("diagnose.diagnoser.form.sectionHelp", {
                    title,
                  })}
                >
                  <CircleHelp className="size-3.5" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top" className="max-w-xs">
                {help}
              </TooltipContent>
            </Tooltip>
          ) : null}
        </div>
        {status ? (
          <span className="flex items-center gap-1 text-xs text-[var(--color-success)]">
            <Check className="size-3.5" />
            {status}
          </span>
        ) : null}
      </div>
      <div className="flex min-w-0 flex-col gap-5">{children}</div>
    </section>
  )
}
