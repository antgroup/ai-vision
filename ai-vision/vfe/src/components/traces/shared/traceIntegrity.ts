/** Inspect every returned parent chain without assuming that the source sent all spans. */
export function inspectTraceStructure(
  spans: { span_id: string; parent_span_id?: string }[]
) {
  const parents = new Map<string, string | undefined>()
  const duplicates = new Set<string>()
  for (const span of spans) {
    if (parents.has(span.span_id)) duplicates.add(span.span_id)
    else parents.set(span.span_id, span.parent_span_id)
  }
  const missingParents = [...parents].flatMap(([spanId, parentSpanId]) =>
    parentSpanId && !/^0+$/.test(parentSpanId) && !parents.has(parentSpanId)
      ? [{ spanId, parentSpanId }]
      : []
  )
  const visited = new Set<string>()
  const cycles = new Set<string>()
  for (const id of parents.keys()) {
    const path: string[] = []
    const positions = new Map<string, number>()
    let cursor: string | undefined = id
    while (
      cursor &&
      !/^0+$/.test(cursor) &&
      parents.has(cursor) &&
      !visited.has(cursor)
    ) {
      const position = positions.get(cursor)
      if (position !== undefined) {
        for (const member of path.slice(position)) cycles.add(member)
        break
      }
      positions.set(cursor, path.length)
      path.push(cursor)
      cursor = parents.get(cursor)
    }
    for (const member of path) visited.add(member)
  }
  return {
    spanCount: parents.size,
    missingParents,
    missingParentIds: [
      ...new Set(missingParents.map((item) => item.parentSpanId)),
    ],
    cycleSpanIds: [...cycles],
    duplicateSpanIds: [...duplicates],
    integrityIssues: [
      ...(cycles.size ? ["parent_cycle"] : []),
      ...(duplicates.size ? ["duplicate_span_id"] : []),
    ],
  }
}
