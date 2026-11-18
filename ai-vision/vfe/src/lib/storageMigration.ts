const STORAGE_VERSION_KEY = "aivision:vfe-storage-version"
const CURRENT_STORAGE_VERSION = "2026-05-auth-space-reset"

const VERSIONED_SELECTION_KEYS = [
  "selectedSpaceId",
  "selectedAgentProduct",
  "ai-vision:last-agent-by-workspace",
] as const

export function migrateLocalStorage(): void {
  const storedVersion = localStorage.getItem(STORAGE_VERSION_KEY)

  if (storedVersion === CURRENT_STORAGE_VERSION) {
    return
  }

  for (const key of VERSIONED_SELECTION_KEYS) {
    localStorage.removeItem(key)
  }

  localStorage.setItem(STORAGE_VERSION_KEY, CURRENT_STORAGE_VERSION)
}
