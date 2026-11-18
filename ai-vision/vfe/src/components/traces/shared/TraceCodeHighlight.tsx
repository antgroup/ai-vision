import { useMemo, type ReactNode } from "react"
import { traceCodeTokens } from "./traceCodeHighlighter"

export default function TraceCodeHighlight({
  value,
  language,
}: {
  value: string
  language: string
}) {
  return useMemo(() => {
    const nodes: ReactNode[] = []
    let cursor = 0
    for (const { from, to, classes } of traceCodeTokens(value, language)) {
      if (from > cursor) nodes.push(value.slice(cursor, from))
      nodes.push(
        <span key={from} className={classes}>
          {value.slice(from, to)}
        </span>
      )
      cursor = to
    }
    if (cursor < value.length) nodes.push(value.slice(cursor))
    return nodes
  }, [value, language])
}
