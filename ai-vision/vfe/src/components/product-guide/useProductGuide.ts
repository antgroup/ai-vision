import { createContext, useContext } from "react"
export type GuideControls = {
  active: boolean
  canBrowse: boolean
  canStartDemo: boolean
  isDemoAgent: boolean
  start: () => void
  restart: () => void
  browse: () => void
  openDemo: () => void
  openRuleEvidence: (ruleUuid: string, traceId: string, spanId: string) => void
  resumable: boolean
}
export const ProductGuideContext = createContext<GuideControls | null>(null)
/** Public trace links intentionally have no authenticated application walkthrough. */
export function useProductGuide() {
  return useContext(ProductGuideContext)
}
