/** The same JSON payload has one identity regardless of object insertion order. */
export function stableQueryIdentity(value: unknown): string {
  return (
    JSON.stringify(value, (_key, item: unknown) => {
      if (item === null || typeof item !== "object" || Array.isArray(item))
        return item
      return Object.fromEntries(
        Object.entries(item).sort(([left], [right]) =>
          left < right ? -1 : left > right ? 1 : 0
        )
      )
    }) ?? "null"
  )
}
