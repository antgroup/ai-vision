import { Menu, PanelLeftClose, PanelLeftOpen, X } from "lucide-react"
import { useEffect, useRef } from "react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import { useSidebar } from "@/components/ui/sidebar"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"

export function SidebarToggle({ inDrawer = false }: { inDrawer?: boolean }) {
  const { t } = useTranslation()
  const { isMobile, open, openMobile, toggleSidebar } = useSidebar()
  const buttonRef = useRef<HTMLButtonElement>(null)
  const wasMobileOpen = useRef(false)
  const expanded = isMobile ? openMobile : open
  const label = t(expanded ? "nav.collapseSidebar" : "nav.expandSidebar")
  const Icon = isMobile
    ? expanded
      ? X
      : Menu
    : expanded
      ? PanelLeftClose
      : PanelLeftOpen

  useEffect(() => {
    if (inDrawer) return
    const restoreFocus = isMobile && wasMobileOpen.current && !openMobile
    wasMobileOpen.current = openMobile
    if (!restoreFocus) return

    // The sheet is controlled by the sidebar provider and has no SheetTrigger.
    const frame = window.requestAnimationFrame(() => buttonRef.current?.focus())
    return () => window.cancelAnimationFrame(frame)
  }, [inDrawer, isMobile, openMobile])

  if (inDrawer && !isMobile) return null

  const button = (
    <Button
      ref={buttonRef}
      type="button"
      variant={inDrawer ? "ghost" : "outline"}
      size="icon"
      className="rounded-lg"
      data-sidebar={inDrawer ? undefined : "trigger"}
      aria-expanded={inDrawer ? undefined : expanded}
      aria-keyshortcuts={inDrawer ? undefined : "Control+B Meta+B"}
      aria-label={label}
      onClick={toggleSidebar}
    >
      <Icon className="size-[18px]" aria-hidden="true" />
    </Button>
  )

  return isMobile ? (
    button
  ) : (
    <Tooltip delayDuration={350}>
      <TooltipTrigger asChild>{button}</TooltipTrigger>
      <TooltipContent side="bottom" sideOffset={6}>
        {label}
      </TooltipContent>
    </Tooltip>
  )
}
