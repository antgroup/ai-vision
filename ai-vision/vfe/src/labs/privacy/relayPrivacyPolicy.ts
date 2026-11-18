// Producer-declared configuration, never an execution receipt or a hit counter.
export const RELAY_PRIVACY_POLICY = "relay-observability/v1"
export const RELAY_PRIVACY_POLICY_KEY = "aivision.privacy.policy"
export const RELAY_OMITTED_PATHS_KEY = "aivision.privacy.omitted_paths"

export type PathError = "invalid" | "too_many" | "too_long"

export function omittedPathError(paths: string[]): PathError | null {
  if (paths.length > 20) return "too_many"
  if (paths.some((path) => path.length > 512)) return "too_long"
  if (
    paths.some(
      (path) =>
        !path.startsWith("/") ||
        path === "/" ||
        Array.from(path).some((character) => {
          const point = character.codePointAt(0)!
          return (
            character === "*" ||
            point < 32 ||
            (point >= 127 && point <= 159) ||
            (point >= 0xd800 && point <= 0xdfff)
          )
        }) ||
        /~(?:[^01]|$)/u.test(path)
    )
  )
    return "invalid"
  return null
}

export function relayPrivacyResourceAttributes(
  paths: string[]
): Record<string, string> {
  return {
    [RELAY_PRIVACY_POLICY_KEY]: RELAY_PRIVACY_POLICY,
    [RELAY_OMITTED_PATHS_KEY]: JSON.stringify([...new Set(paths)]),
  }
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

/** Read only the selected record's explicit policy declaration. */
export function reportedOmittedPaths(resource: unknown): string[] {
  const raw = record(resource)
  const attrs = record(raw?.attributes) ?? raw
  if (attrs?.[RELAY_PRIVACY_POLICY_KEY] !== RELAY_PRIVACY_POLICY) return []
  const value = attrs[RELAY_OMITTED_PATHS_KEY]
  // Every allowed character may need two JSON characters (quotes/backslashes).
  const maxDeclarationLength = 20 * (512 * 2 + 3) + 2
  if (typeof value !== "string" || value.length > maxDeclarationLength)
    return []
  try {
    const paths: unknown = JSON.parse(value)
    return Array.isArray(paths) &&
      paths.every((path) => typeof path === "string") &&
      !omittedPathError(paths)
      ? [...new Set(paths as string[])]
      : []
  } catch {
    return []
  }
}
