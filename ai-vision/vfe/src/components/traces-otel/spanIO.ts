export interface SpanIOValue {
  value: unknown
  attributeKey: string
}

function parseJsonValue(value: unknown): unknown {
  if (typeof value !== "string") return value
  try {
    return JSON.parse(value)
  } catch {
    return value
  }
}

/** The detail panel and process projection share the same original IO ownership. */
export function spanIO(
  attrs: Record<string, unknown>,
  field: "input" | "output",
  { preserveValue = false }: { preserveValue?: boolean } = {}
): SpanIOValue | undefined {
  const valueFor = (value: unknown) =>
    preserveValue ? value : parseJsonValue(value)
  const standard = `gen_ai.${field}.messages`
  const sources = parseJsonValue(attrs["aivision.normalization.sources"])
  const derived =
    sources !== null &&
    typeof sources === "object" &&
    !Array.isArray(sources) &&
    Object.hasOwn(sources, standard)
  // Explicit OTel wins; normalized messages defer to the original SDK payload.
  // Decode the selected transport envelope once without parsing nested content.
  if (
    !derived &&
    Object.hasOwn(attrs, standard) &&
    attrs[standard] !== undefined
  )
    return { value: valueFor(attrs[standard]), attributeKey: standard }
  const toolKey = `gen_ai.tool.call.${field === "input" ? "arguments" : "result"}`
  const key = [
    `langfuse.observation.${field}`,
    `${field}.value`,
    standard,
    toolKey,
    `aivision.request.${field}`,
  ].find(
    (candidate) =>
      attrs[candidate] !== undefined &&
      (preserveValue ||
        attrs[candidate] !== null ||
        (candidate === toolKey && Object.hasOwn(attrs, toolKey)))
  )
  return key === undefined
    ? undefined
    : { value: valueFor(attrs[key]), attributeKey: key }
}
