import { useTranslation } from "react-i18next"
import type { HTMLAttributes, ReactNode } from "react"
import { ArrowUpRight, Info, type LucideIcon } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"

import { cn } from "@/lib/utils"
import {
  observabilityViewDocsUrl,
  type ObservabilityViewFeature,
} from "./observabilityViewDocs"

type LabsSectionProps = Omit<HTMLAttributes<HTMLElement>, "title"> & {
  icon: LucideIcon
  feature: ObservabilityViewFeature
  variant?: "summary" | "detail"
  title: string
  description?: string
  summary?: ReactNode
  /** Feature maturity, independent from the enhanced-view category marker. */
  beta?: boolean
  children: ReactNode
}

/** Summary and detail templates for observability view enhancements. */
export function LabsSection({
  icon: Icon,
  feature,
  variant = "summary",
  title,
  description,
  summary,
  beta = false,
  className,
  children,
  ...sectionProps
}: LabsSectionProps) {
  const { t, i18n } = useTranslation()
  const docsLabel = t("labs.common.enhancedViewDocs", { title })
  return (
    <section
      {...sectionProps}
      data-variant={variant}
      className={cn(
        "flex min-w-0 flex-col gap-2 rounded-lg border border-[var(--color-border-labs)] bg-[var(--color-bg-labs)] p-3",
        className
      )}
    >
      <header className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <h3 className="inline-flex min-w-0 items-center gap-2 text-sm leading-[22px] font-medium text-[var(--color-text-primary)]">
              <Icon className="size-4 shrink-0 text-[var(--color-labs)]" />
              <span className="[overflow-wrap:anywhere]">{title}</span>
            </h3>
            <Badge
              asChild
              variant="secondary"
              className="bg-[var(--color-bg-labs-tag)] font-normal text-[var(--color-labs)] hover:bg-[var(--color-bg-labs-tag)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-labs)]"
            >
              <a
                href={observabilityViewDocsUrl(
                  feature,
                  i18n.resolvedLanguage || i18n.language
                )}
                target="_blank"
                rel="noopener noreferrer"
                aria-label={docsLabel}
                title={docsLabel}
              >
                {t("labs.common.enhancedView")}
                <ArrowUpRight className="size-3" aria-hidden="true" />
              </a>
            </Badge>
            {beta ? (
              <Badge
                variant="outline"
                className="font-normal text-[var(--color-text-secondary)]"
              >
                {t("labs.common.beta")}
              </Badge>
            ) : null}
          </div>
          {description ? (
            <Popover>
              <PopoverTrigger asChild>
                <button
                  type="button"
                  aria-label={t("labs.common.viewHelp", { title })}
                  className="inline-flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                >
                  <Info className="size-3.5" aria-hidden="true" />
                </button>
              </PopoverTrigger>
              <PopoverContent
                align="start"
                className="max-w-[calc(100vw-2rem)] text-xs leading-5"
              >
                {description}
              </PopoverContent>
            </Popover>
          ) : null}
        </div>
        {summary ? (
          <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs leading-5 text-[var(--color-text-description)]">
            {summary}
          </div>
        ) : null}
      </header>
      {children}
    </section>
  )
}
