import { createContext, useContext, useEffect } from "react"

export const DashboardSectionPresentation = createContext<{
  reportError: (failed: boolean) => void
} | null>(null)

/** Embed existing sections without duplicate headings; keep known errors visible when closed. */
export function useDashboardSectionPresentation(errors: readonly unknown[]) {
  const presentation = useContext(DashboardSectionPresentation)
  const reportError = presentation?.reportError
  const failed = errors.some(Boolean)
  useEffect(() => {
    reportError?.(failed)
  }, [failed, reportError])
  return presentation !== null
}
