import { useEffect } from "react"

import { useTheme } from "@/components/layout/theme-provider"
import { parseLinkTheme } from "@/hooks/use-link-theme"

function hasUrlThemeOverride() {
  if (
    window.location.pathname !== "/trace-link" &&
    window.location.pathname !== "/observation-link"
  ) {
    return false
  }

  return parseLinkTheme(new URLSearchParams(window.location.search).get("theme")) !== null
}

/**
 * Toggle between light and dark theme on pressing "D" key.
 * Ignored inside editable fields (input, textarea, contenteditable).
 */
export function useThemeToggle() {
  const { resolvedTheme, setTheme, theme } = useTheme()

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.repeat || e.metaKey || e.ctrlKey || e.altKey) return
      if (e.key.toLowerCase() !== "d") return
      if (hasUrlThemeOverride()) return

      const target = e.target
      if (
        target instanceof HTMLElement &&
        (target.isContentEditable ||
          target.closest(
            "input, textarea, select, [contenteditable='true']"
          ))
      )
        return

      e.preventDefault()
      const currentTheme = resolvedTheme ?? theme
      setTheme(currentTheme === "dark" ? "light" : "dark")
    }

    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [resolvedTheme, setTheme, theme])
}
