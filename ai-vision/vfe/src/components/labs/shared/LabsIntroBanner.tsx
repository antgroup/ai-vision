import { useId, type ReactNode } from "react"
import {
  CircleHelp,
  ExternalLink,
  Lightbulb,
  type LucideIcon,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { cn } from "@/lib/utils"

export interface IntroLink {
  label: string
  href: string
}

interface LabsIntroBannerProps {
  icon?: LucideIcon
  title: string
  summary: string
  summaryClassName?: string
  links: IntroLink[]
  children?: ReactNode
}

export function LabsIntroBanner({
  icon: Icon = Lightbulb,
  title,
  summary,
  summaryClassName,
  links,
  children,
}: LabsIntroBannerProps) {
  const titleId = useId()

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="size-8 shrink-0 text-muted-foreground"
          aria-label={title}
          title={title}
        >
          <CircleHelp className="size-4" aria-hidden="true" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        collisionPadding={16}
        aria-labelledby={titleId}
        className="max-h-[min(32rem,70dvh)] w-[min(36rem,calc(100vw-2rem))] overflow-y-auto rounded-xl"
      >
        <div className="flex items-start gap-3">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-[var(--color-bg-labs-tag)] text-[var(--color-labs)]">
            <Icon className="size-4" aria-hidden="true" />
          </span>
          <div className="flex min-w-0 flex-1 flex-col gap-2">
            <div className="flex items-center justify-between gap-4">
              <h2
                id={titleId}
                className="text-sm leading-[22px] font-medium text-[var(--color-text-primary)]"
              >
                {title}
              </h2>
            </div>
            <div>
              <div className="flex flex-col gap-2">
                <p
                  className={cn(
                    "w-full max-w-[72ch] text-sm leading-[22px] text-[var(--color-text-secondary)]",
                    summaryClassName
                  )}
                >
                  {summary}
                </p>
                {children}
                <ul className="flex flex-wrap gap-x-4 gap-y-1">
                  {links.map((link) => (
                    <li key={link.href}>
                      <a
                        href={link.href}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 rounded-[6px] text-xs leading-5 text-[var(--color-labs)] underline decoration-[var(--color-border-labs)] underline-offset-4 hover:decoration-current focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-labs)]"
                      >
                        {link.label}
                        <ExternalLink className="size-3" />
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  )
}
