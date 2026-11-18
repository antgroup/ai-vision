/* eslint-disable react-refresh/only-export-components */
import { useContext } from "react"
import { DeferredMarkdownMedia } from "@/components/traces/shared/MarkdownMedia"
import { TraceEnhancedVisibility } from "@/components/traces/shared/traceEnhancedVisibility"

function VisibleMarkdownMedia(props: { src?: string; alt?: string }) {
  const active = useContext(TraceEnhancedVisibility)
  return <DeferredMarkdownMedia {...props} active={active} />
}

export const markdownMediaComponents = { img: VisibleMarkdownMedia }
