/** Missing telemetry is not a reported zero. */
export function measurementNumber(value: unknown): number | null {
  if (typeof value !== "number" && typeof value !== "string") return null
  if (typeof value === "string" && !value.trim()) return null
  const number = Number(value)
  return Number.isFinite(number) && number >= 0 ? number : null
}

export function completeMeasurementSum(...values: unknown[]): number | null {
  const measured = values.map(measurementNumber)
  if (measured.some((value) => value === null)) return null
  const total = measured.reduce<number>((sum, value) => sum + value!, 0)
  return Number.isFinite(total) ? total : null
}

export function measurementSeconds(value: unknown): number | null {
  const number = measurementNumber(value)
  return number === null ? null : number / 1000
}
