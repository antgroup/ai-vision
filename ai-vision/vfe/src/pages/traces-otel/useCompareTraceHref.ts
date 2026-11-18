import { useSearchParams } from "react-router-dom"

/** Keep the comparison's scope and time window when opening a source call. */
export function useCompareTraceHref(from: string, to: string) {
  const [searchParams] = useSearchParams()
  return (traceId: string, spanId?: string) => {
    const params = new URLSearchParams(searchParams)
    params.delete("a")
    params.delete("b")
    params.delete("showMcp")
    params.delete("focus_span")
    params.set("traceId", traceId)
    if (spanId) params.set("focus_span", spanId)
    if (from) params.set("from", from)
    if (to) params.set("to", to)
    return `/trace?${params}`
  }
}
