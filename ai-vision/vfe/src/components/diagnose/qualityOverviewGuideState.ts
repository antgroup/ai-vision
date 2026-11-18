const QUALITY_OVERVIEW_GUIDE_VERSION = "v2"
const QUALITY_OVERVIEW_GUIDE_STORAGE_PREFIX =
  "ai-vision:onboarding:quality-overview:"
const QUALITY_OVERVIEW_GUIDE_CHANGE_EVENT =
  "ai-vision:quality-overview-guide-change"

function storageKey(userKey: string): string {
  return `${QUALITY_OVERVIEW_GUIDE_STORAGE_PREFIX}${QUALITY_OVERVIEW_GUIDE_VERSION}:${userKey}`
}

export function hasSeenQualityOverviewGuide(userKey?: string): boolean {
  if (!userKey || typeof window === "undefined") return true
  try {
    return window.localStorage.getItem(storageKey(userKey)) === "done"
  } catch {
    return false
  }
}

export function markQualityOverviewGuideSeen(userKey: string): void {
  if (typeof window === "undefined") return
  try {
    window.localStorage.setItem(storageKey(userKey), "done")
  } catch {
    // The guide still works when storage is unavailable; it may show again.
  }
  window.dispatchEvent(new Event(QUALITY_OVERVIEW_GUIDE_CHANGE_EVENT))
}

export function subscribeQualityOverviewGuideState(
  listener: () => void
): () => void {
  if (typeof window === "undefined") return () => {}
  const handleChange = () => listener()
  const handleStorage = (event: StorageEvent) => {
    if (event.key?.startsWith(QUALITY_OVERVIEW_GUIDE_STORAGE_PREFIX)) {
      listener()
    }
  }
  window.addEventListener(QUALITY_OVERVIEW_GUIDE_CHANGE_EVENT, handleChange)
  window.addEventListener("storage", handleStorage)
  return () => {
    window.removeEventListener(
      QUALITY_OVERVIEW_GUIDE_CHANGE_EVENT,
      handleChange
    )
    window.removeEventListener("storage", handleStorage)
  }
}
