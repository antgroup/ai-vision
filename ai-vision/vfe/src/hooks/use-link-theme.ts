import { useEffect, useMemo, useRef } from "react"

import { useTheme } from "@/components/layout/theme-provider"

type LinkTheme = "light" | "dark" | "system"

export function parseLinkTheme(value: string | null): LinkTheme | null {
  if (value === "light" || value === "dark" || value === "system") {
    return value
  }
  return null
}

export function useLinkTheme(themeParam: string | null) {
  const { setTheme, theme } = useTheme()
  const requestedTheme = useMemo(() => parseLinkTheme(themeParam), [themeParam])
  const previousThemeRef = useRef<string | undefined>(undefined)
  const setThemeRef = useRef(setTheme)

  useEffect(() => {
    setThemeRef.current = setTheme
  }, [setTheme])

  useEffect(() => {
    if (!requestedTheme) return
    if (previousThemeRef.current === undefined) {
      previousThemeRef.current = theme || "system"
    }
    setTheme(requestedTheme)
  }, [requestedTheme, setTheme, theme])

  useEffect(() => {
    return () => {
      if (previousThemeRef.current) {
        setThemeRef.current(previousThemeRef.current)
      }
    }
  }, [])
}
