import { useEffect, useMemo, useRef, useState } from "react"

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { cn } from "@/lib/utils"

const MAX_VISIBLE_TAGS = 2

interface TagButtonProps {
  active?: boolean
  label: string
  truncate?: boolean
  onClick: () => void
}

function TagButton({
  active = false,
  label,
  truncate: shouldTruncate = false,
  onClick,
}: TagButtonProps) {
  return (
    <button
      type="button"
      title={label}
      className={cn(
        "inline-flex items-center rounded-sm border px-2 py-0.5 text-xs leading-normal font-normal transition-colors",
        shouldTruncate && "max-w-[140px]",
        active
          ? "border-primary/50 bg-primary/15 text-primary"
          : "border-transparent bg-accent text-foreground hover:bg-accent/80"
      )}
      onClick={(event) => {
        event.stopPropagation()
        onClick()
      }}
    >
      <span className={shouldTruncate ? "truncate" : ""}>{label}</span>
    </button>
  )
}

export function TraceTagsCell({
  tags,
  activeTag = "",
  onSelectTag,
}: {
  tags: string[]
  activeTag?: string
  onSelectTag?: (tag: string) => void
}) {
  const [open, setOpen] = useState(false)
  const closeTimerRef = useRef<number | null>(null)

  const cancelScheduledClose = () => {
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current)
      closeTimerRef.current = null
    }
  }

  const scheduleClose = () => {
    cancelScheduledClose()
    closeTimerRef.current = window.setTimeout(() => {
      setOpen(false)
      closeTimerRef.current = null
    }, 120)
  }

  useEffect(() => cancelScheduledClose, [])

  const normalizedTags = useMemo(
    () =>
      tags.filter(
        (tag): tag is string => typeof tag === "string" && tag.trim().length > 0
      ),
    [tags]
  )

  if (normalizedTags.length === 0) {
    return <span className="text-muted-foreground">-</span>
  }

  const visibleTags = normalizedTags.slice(0, MAX_VISIBLE_TAGS)
  const hiddenCount = normalizedTags.length - MAX_VISIBLE_TAGS

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <div
        className="flex items-center gap-1"
        onMouseEnter={() => {
          cancelScheduledClose()
          setOpen(true)
        }}
        onMouseLeave={scheduleClose}
      >
        <PopoverTrigger asChild>
          <div className="flex items-center gap-1">
            {visibleTags.map((tag, index) => (
              <TagButton
                key={`${tag}-${index}`}
                active={activeTag === tag}
                label={tag}
                truncate
                onClick={() => onSelectTag?.(tag)}
              />
            ))}
            {hiddenCount > 0 && (
              <span className="shrink-0 rounded-sm bg-secondary px-2 py-0.5 text-xs font-normal text-muted-foreground">
                +{hiddenCount}
              </span>
            )}
          </div>
        </PopoverTrigger>
        <PopoverContent
          align="start"
          side="top"
          className="w-auto max-w-[400px] p-3"
          onOpenAutoFocus={(event) => event.preventDefault()}
          onMouseEnter={cancelScheduledClose}
          onMouseLeave={scheduleClose}
        >
          <div className="flex flex-wrap gap-1.5">
            {normalizedTags.map((tag) => (
              <TagButton
                key={tag}
                active={activeTag === tag}
                label={tag}
                onClick={() => onSelectTag?.(tag)}
              />
            ))}
          </div>
        </PopoverContent>
      </div>
    </Popover>
  )
}
