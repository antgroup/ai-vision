import { createContext } from "react"

/** Media must be removed while its preserved enhancement view is inactive. */
export const TraceEnhancedVisibility = createContext(true)

/** Undefined outside formatted IO. Null while its preview actions are pending. */
export const TraceHtmlPreviewSources = createContext<
  ReadonlySet<string> | null | undefined
>(undefined)

export type TraceHtmlPreviewSelection = {
  value: unknown
  sources: ReadonlySet<string>
}
