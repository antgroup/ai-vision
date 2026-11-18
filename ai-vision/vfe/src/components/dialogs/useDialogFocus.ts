import { useRef } from "react"

type AutoFocusHandler = (event: Event) => void

/** Controlled dialogs also need a return target when no DialogTrigger exists. */
export function useDialogFocus(
  onOpenAutoFocus?: AutoFocusHandler,
  onCloseAutoFocus?: AutoFocusHandler
) {
  const opener = useRef<HTMLElement | null>(null)
  return {
    onOpenAutoFocus(event: Event) {
      const active = document.activeElement
      const menu = active?.closest('[role="menu"]')
      const triggerId = menu?.getAttribute("aria-labelledby")
      // A selected menu item unmounts as its dialog opens. Return to its trigger.
      const target = (triggerId && document.getElementById(triggerId)) || active
      opener.current = target instanceof HTMLElement ? target : null
      onOpenAutoFocus?.(event)
    },
    onCloseAutoFocus(event: Event) {
      if (onCloseAutoFocus) {
        onCloseAutoFocus(event)
        return
      }
      const target = opener.current
      if (target?.isConnected && target !== document.body) {
        target.focus({ preventScroll: true })
        if (document.activeElement === target) event.preventDefault()
      }
    },
  }
}
