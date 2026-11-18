import { hasPrivacyMarker } from "../../../labs/privacy/privacyMarkers.ts"

export function isTraceHtmlPreviewLink(source: string): boolean {
  // URL normalizes extra slashes and empty userinfo. Validate the recorded authority.
  const authority = /^https:\/\/([^/?#]+)/i.exec(source)?.[1]
  if (
    !authority ||
    authority.includes("@") ||
    hasPrivacyMarker(source) ||
    source.includes("[FILTERED_")
  ) {
    return false
  }
  try {
    return /\.html?$/i.test(new URL(source).pathname)
  } catch {
    return false
  }
}
