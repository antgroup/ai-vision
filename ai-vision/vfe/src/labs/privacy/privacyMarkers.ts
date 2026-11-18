// Explicit replacement tokens only. Stars, hashes and missing fields are ambiguous.
const MARKER =
  /\[REDACTED(?: PRIVATE KEY|:sha256:[0-9a-f]{12}|_[A-Z][A-Z0-9_]{0,63})?\]/iu

/** A content observation, never proof that a policy ran or covered every value. */
export function hasPrivacyMarker(value: string): boolean {
  return MARKER.test(value)
}

/** Split display text without changing the received value or interpreting markup. */
export function privacyMarkerSegments(
  text: string
): { text: string; marked: boolean }[] {
  const parts: { text: string; marked: boolean }[] = []
  let offset = 0
  for (const match of text.matchAll(new RegExp(MARKER.source, "giu"))) {
    if (match.index > offset)
      parts.push({ text: text.slice(offset, match.index), marked: false })
    parts.push({ text: match[0], marked: true })
    offset = match.index + match[0].length
  }
  if (offset < text.length)
    parts.push({ text: text.slice(offset), marked: false })
  return parts
}

/** Keep a visible replacement token whole when a preview ends inside it. */
export function privacyPreviewBoundary(
  text: string,
  preferredEnd: number
): number {
  const end = Math.min(text.length, Math.max(0, Math.floor(preferredEnd)))
  for (const match of text.matchAll(new RegExp(MARKER.source, "giu"))) {
    if (match.index >= end) break
    const markerEnd = match.index + match[0].length
    if (markerEnd > end) return markerEnd
  }
  return end
}
