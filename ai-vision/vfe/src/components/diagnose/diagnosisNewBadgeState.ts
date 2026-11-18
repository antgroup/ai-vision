const DIAGNOSIS_NEW_BADGE_VERSION = "2026-08-11"
const DIAGNOSIS_NEW_BADGE_RELEASED_AT = Date.parse("2026-08-11T00:00:00+08:00")
const DIAGNOSIS_NEW_BADGE_DURATION_MS = 7 * 24 * 60 * 60 * 1000
const DIAGNOSIS_NEW_BADGE_STORAGE_PREFIX = "ai-vision:feature:diagnosis-new:"
const DIAGNOSIS_NEW_BADGE_CHANGE_EVENT = "ai-vision:diagnosis-new-badge-change"

const sessionSeenUsers = new Set<string>()

function storageKey(userKey: string): string {
  return `${DIAGNOSIS_NEW_BADGE_STORAGE_PREFIX}${DIAGNOSIS_NEW_BADGE_VERSION}:${userKey}`
}

function expiresAt(): number {
  return DIAGNOSIS_NEW_BADGE_RELEASED_AT + DIAGNOSIS_NEW_BADGE_DURATION_MS
}

export function isDiagnosisPath(pathname: string): boolean {
  return ["/badcase", "/diagnosers", "/diagnose-jobs"].some(
    (path) => pathname === path || pathname.startsWith(`${path}/`)
  )
}

export function shouldShowDiagnosisNewBadge(
  userKey?: string,
  now = Date.now()
): boolean {
  if (!userKey || typeof window === "undefined") return false
  if (now < DIAGNOSIS_NEW_BADGE_RELEASED_AT || now >= expiresAt()) return false
  if (sessionSeenUsers.has(userKey)) return false

  try {
    return window.localStorage.getItem(storageKey(userKey)) !== "done"
  } catch {
    return true
  }
}

export function markDiagnosisNewBadgeSeen(userKey: string): void {
  if (typeof window === "undefined") return
  sessionSeenUsers.add(userKey)
  try {
    window.localStorage.setItem(storageKey(userKey), "done")
  } catch {
    // Keep the in-memory state so the badge still hides for this session.
  }
  window.dispatchEvent(new Event(DIAGNOSIS_NEW_BADGE_CHANGE_EVENT))
}

export function subscribeDiagnosisNewBadgeState(
  listener: () => void
): () => void {
  if (typeof window === "undefined") return () => {}
  const handleChange = () => listener()
  const handleStorage = (event: StorageEvent) => {
    if (event.key?.startsWith(DIAGNOSIS_NEW_BADGE_STORAGE_PREFIX)) {
      listener()
    }
  }
  const remaining = expiresAt() - Date.now()
  const expiryTimer =
    remaining > 0 ? window.setTimeout(listener, remaining) : undefined

  window.addEventListener(DIAGNOSIS_NEW_BADGE_CHANGE_EVENT, handleChange)
  window.addEventListener("storage", handleStorage)
  return () => {
    if (expiryTimer !== undefined) window.clearTimeout(expiryTimer)
    window.removeEventListener(DIAGNOSIS_NEW_BADGE_CHANGE_EVENT, handleChange)
    window.removeEventListener("storage", handleStorage)
  }
}
