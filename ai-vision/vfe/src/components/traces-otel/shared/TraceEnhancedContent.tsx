import { Activity, type ReactNode } from "react"
import { TraceEnhancedVisibility } from "@/components/traces/shared/traceEnhancedVisibility"

/** Preserve per-trace interaction state without exposing inactive enhancements. */
export function TraceEnhancedContent({
  active,
  children,
}: {
  active: boolean
  children: ReactNode
}) {
  return (
    <TraceEnhancedVisibility value={active}>
      <Activity mode={active ? "visible" : "hidden"}>{children}</Activity>
    </TraceEnhancedVisibility>
  )
}
