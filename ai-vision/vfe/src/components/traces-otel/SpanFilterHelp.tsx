import { useRef, useState, type ReactNode } from "react"
import { CircleHelp } from "lucide-react"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"

export function SpanFilterHelp({
  name,
  label,
  children,
}: {
  name: string
  label: string
  children: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const [clicked, setClicked] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const close = () => {
    setOpen(false)
    setClicked(false)
  }
  return (
    <TooltipProvider>
      <Tooltip
        open={open}
        onOpenChange={(next) => {
          setOpen(next)
          if (!next) setClicked(false)
        }}
      >
        <TooltipTrigger asChild>
          <button
            ref={triggerRef}
            type="button"
            data-filter-help={name}
            data-trace-filter-input={open ? "" : undefined}
            aria-label={label}
            aria-expanded={open}
            className="inline-flex size-6 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
            // Radix otherwise closes on pointerdown before click toggles it.
            onPointerDown={(event) => event.preventDefault()}
            onClick={(event) => {
              event.preventDefault()
              event.currentTarget.focus({ preventScroll: true })
              // Focus/hover previews are already open on keyboard activation.
              // The first click keeps them open; a second click closes them.
              setOpen(!clicked)
              setClicked(!clicked)
            }}
            onKeyDown={(event) => {
              if (event.key === "Escape" && open) {
                event.preventDefault()
                event.stopPropagation()
                close()
              }
            }}
          >
            <CircleHelp aria-hidden="true" className="size-3.5" />
          </button>
        </TooltipTrigger>
        <TooltipContent
          side="right"
          align="start"
          sideOffset={6}
          className="block max-w-[min(20rem,calc(100vw-2rem))] space-y-2 px-3 py-2.5 text-xs leading-relaxed"
          onPointerDownOutside={(event) => {
            // The trigger is outside the portaled content. Let its click handler
            // toggle help instead of dismissing it earlier in the pointer sequence.
            if (
              triggerRef.current?.contains(
                event.detail.originalEvent.target as Node | null
              )
            ) {
              event.preventDefault()
            }
          }}
          onEscapeKeyDown={(event) => {
            event.preventDefault()
            event.stopPropagation()
            close()
          }}
        >
          {children}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
