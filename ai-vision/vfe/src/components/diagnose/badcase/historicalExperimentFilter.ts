/** Preserve the existing numeric, exact multi-value history filter after retirement. */
export function historicalExperimentFilter(
  requested: string[],
  currentOptions: string[]
) {
  const normalized = requested.map((value) => value.trim())
  const invalid = normalized.some((value) => !/^[0-9]+$/.test(value))
  const selected = normalized.filter((value) => /^[0-9]+$/.test(value))
  return {
    invalid,
    selected,
    options: Array.from(new Set([...currentOptions, ...selected])),
  }
}

/** Read experiment groups by their shared schema without rewriting stored specs. */
export function historicalExperimentOptions(
  spec: Record<string, unknown> | null | undefined
): string[] {
  const selector = spec?.selector
  if (!selector || typeof selector !== "object" || Array.isArray(selector))
    return []
  const selectorRecord = selector as Record<string, unknown>
  const experimentGroups = selectorRecord.experiment_groups
  if (!Array.isArray(experimentGroups)) return []
  return Array.from(
    new Set(
      experimentGroups
        .filter((value): value is string => typeof value === "string")
        .map((value) => value.trim())
        .filter(Boolean)
    )
  )
}
