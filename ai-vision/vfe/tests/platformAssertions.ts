import assert from "node:assert/strict"

type Values = Record<string, unknown>
type ExpectedSpan = {
  spanId: string
  parentSpanId: string | null
  name: string
  type: string
  input: unknown
  output: unknown
  inputPresent: boolean
  outputPresent: boolean
  usage: { input: number | null; output: number | null; total: number | null }
  expectedError: boolean
  metadata?: Values
}
type ExpectedTrace = {
  traceId: string
  sdkLanguage: string
  sdkVersion: string
  spans: ExpectedSpan[]
}

/** Decode exactly one SDK wire envelope. Never recursively parse user strings. */
export function wireValue(value: unknown): unknown {
  if (typeof value !== "string") return value
  try {
    return JSON.parse(value)
  } catch {
    return value
  }
}

function ioSource(attributes: Values, field: "input" | "output") {
  const standard = `gen_ai.${field}.messages`
  const sources = wireValue(attributes["aivision.normalization.sources"])
  const derived =
    sources !== null &&
    typeof sources === "object" &&
    Object.hasOwn(sources, standard)
  if (!derived && Object.hasOwn(attributes, standard)) return standard
  for (const name of [
    `langfuse.observation.${field}`,
    `${field}.value`,
    standard,
    `gen_ai.tool.call.${field === "input" ? "arguments" : "result"}`,
    `aivision.request.${field}`,
  ])
    if (attributes[name] !== undefined && attributes[name] !== null) return name
  return null
}

export function spanIO(attributes: Values, field: "input" | "output") {
  const source = ioSource(attributes, field)
  return source === null ? null : wireValue(attributes[source])
}

export function spanMetadata(
  attributes: Values,
  language: string,
  version: string
) {
  const prefix = "langfuse.observation.metadata"
  const major = Number(version.split(".")[0])
  assert(Number.isInteger(major), "Metadata requires an explicit SDK version")
  const legacy = language === "python" ? major < 5 : major < 6
  const base = wireValue(attributes[prefix])
  const result: Values =
    base && typeof base === "object" && !Array.isArray(base) ? { ...base } : {}
  for (const [key, value] of Object.entries(attributes))
    if (key.startsWith(prefix + "."))
      result[key.slice(prefix.length + 1)] = legacy ? value : wireValue(value)
  return result
}

function typeOfSpan(attributes: Values) {
  const explicit = attributes["langfuse.observation.type"]
  if (explicit) return String(explicit).toUpperCase()
  const operation = String(
    attributes["gen_ai.operation.name"] || ""
  ).toLowerCase()
  const operations: Record<string, string> = {
    chat: "GENERATION",
    completion: "GENERATION",
    text_completion: "GENERATION",
    embedding: "EMBEDDING",
    embeddings: "EMBEDDING",
    execute_tool: "TOOL",
    tool: "TOOL",
    invoke_agent: "AGENT",
    retrieval: "RETRIEVER",
  }
  if (operations[operation]) return operations[operation]
  const kind = String(
    attributes["gen_ai.span.kind"] ||
      attributes["openinference.span.kind"] ||
      "SPAN"
  ).toUpperCase()
  return kind === "LLM" ? "GENERATION" : kind
}

function isError(status: unknown, attributes: Values) {
  const code =
    status && typeof status === "object" ? (status as Values).code : status
  return (
    [2, "2", "ERROR", "STATUS_CODE_ERROR"].includes(code as string | number) ||
    attributes["langfuse.observation.level"] === "ERROR"
  )
}

/** Independent oracle over the JSON downloaded by the browser, not an API mock. */
export function assertNativeExport(exported: Values, expected: ExpectedTrace) {
  assert.equal(exported.traceId, expected.traceId, "Export trace identity")
  assert.equal(
    exported.representation,
    "native-span",
    "Export must preserve native spans"
  )
  assert(Array.isArray(exported.spans), "Export has a span inventory")
  const spans = exported.spans as Values[]
  assert.deepEqual(
    spans.map((span) => span.span_id).sort(),
    expected.spans.map((span) => span.spanId).sort(),
    "Exact span inventory"
  )
  for (const target of expected.spans) {
    const span = spans.find((span) => span.span_id === target.spanId)!
    const attributes = span.attributes as Values
    assert(
      attributes && typeof attributes === "object",
      "Exported span attributes"
    )
    assert.equal(span.name, target.name, "Span name")
    assert.equal(
      span.parent_span_id || null,
      target.parentSpanId,
      "Span parent"
    )
    assert.equal(typeOfSpan(attributes), target.type, "Span type")
    assert.equal(
      isError(span.status, attributes),
      target.expectedError,
      "Span error status"
    )
    for (const field of ["input", "output"] as const)
      assert.equal(
        ioSource(attributes, field) !== null,
        target[`${field}Present`],
        `Span ${field} presence`
      )
    assert.deepEqual(
      spanIO(attributes, "input"),
      target.input,
      "Typed span input"
    )
    assert.deepEqual(
      spanIO(attributes, "output"),
      target.output,
      "Typed span output"
    )
    if (target.metadata !== undefined)
      assert.deepEqual(
        spanMetadata(attributes, expected.sdkLanguage, expected.sdkVersion),
        target.metadata,
        "Once-decoded span metadata"
      )
    for (const field of ["input", "output", "total"] as const)
      assert.equal(
        attributes[`gen_ai.usage.${field}_tokens`] ?? null,
        target.usage[field],
        `Span ${field} usage`
      )
  }
}
