import { Loader2, Search, type LucideIcon } from "lucide-react"

export function ImportLookupStatus({
  message,
  icon: Icon = Search,
  busy = false,
  className = "",
}: {
  message: string | null
  icon?: LucideIcon
  busy?: boolean
  className?: string
}) {
  const StatusIcon = busy ? Loader2 : Icon
  return (
    <div role="status">
      {message ? (
        <p
          className={`flex items-center gap-2.5 rounded-[10px] bg-[var(--color-bg-info)] px-3.5 py-3 text-sm leading-[22px] text-[var(--color-text-secondary)] ${className}`}
        >
          <StatusIcon
            aria-hidden="true"
            className={`size-4 shrink-0 text-[var(--color-text-description)] ${busy ? "motion-safe:animate-spin" : ""}`}
          />
          {message}
        </p>
      ) : null}
    </div>
  )
}
