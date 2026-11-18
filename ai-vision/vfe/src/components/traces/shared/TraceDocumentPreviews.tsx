import { lazy, Suspense, useContext, useState, type ReactNode } from "react"
import {
  TraceEnhancedVisibility,
  TraceHtmlPreviewSources,
  type TraceHtmlPreviewSelection,
} from "./traceEnhancedVisibility"

// List routes share detail components but should not load document discovery
// or rendering until enhanced content actually mounts.
const TraceDocumentPreviewContent = lazy(
  () => import("./TraceDocumentPreviewContent")
)

export function TraceDocumentPreviews({
  value,
  allowHtmlLinks = true,
  enabled = true,
  children,
}: {
  value: unknown
  allowHtmlLinks?: boolean
  enabled?: boolean
  children?: ReactNode
}) {
  const active = useContext(TraceEnhancedVisibility) !== false && enabled
  const [selection, setSelection] = useState<TraceHtmlPreviewSelection | null>(
    null
  )
  const sources =
    allowHtmlLinks && selection && Object.is(selection.value, value)
      ? selection.sources
      : null
  return (
    <>
      {active && (
        <Suspense fallback={null}>
          <TraceDocumentPreviewContent
            value={value}
            allowHtmlLinks={allowHtmlLinks}
            onHtmlPreviewSelection={
              children === undefined ? undefined : setSelection
            }
          />
        </Suspense>
      )}
      {children !== undefined && (
        <TraceHtmlPreviewSources.Provider value={active ? sources : undefined}>
          {children}
        </TraceHtmlPreviewSources.Provider>
      )}
    </>
  )
}
