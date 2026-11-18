type ModelOperation = "chat" | "embedding"

interface ModelSpanInput {
  name?: string | null
  attributes?: Record<string, unknown>
  type?: string | null
  model?: string | null
}

const OPERATIONS: Record<string, ModelOperation> = {
  chat: "chat",
  embed: "embedding",
  embedding: "embedding",
  embeddings: "embedding",
  create_embedding: "embedding",
}

function operationFor(value: string): ModelOperation | null {
  return Object.hasOwn(OPERATIONS, value) ? OPERATIONS[value] : null
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : ""
}

/**
 * Separate an SDK's conventional operation/model name for display only.
 * Match the complete name against reported models; never infer a model from
 * arbitrary span text or remove a custom suffix (for example an agent name).
 */
export function getModelSpanPresentation(span: ModelSpanInput) {
  const attrs = span.attributes ?? {}
  const models = [
    text(attrs["gen_ai.response.model"]),
    text(attrs["gen_ai.request.model"]),
    text(span.model),
  ].filter(Boolean)
  // Some providers report "unknown" until a response model is available.
  // Keep it for conventional-name matching, but prefer an actual identifier.
  const model =
    models.find((candidate) => candidate.toLowerCase() !== "unknown") ?? null
  const name = text(span.name)
  const declaredOperation = text(attrs["gen_ai.operation.name"]).toLowerCase()
  const kind = (
    text(attrs["gen_ai.span.kind"]) ||
    text(span.type) ||
    text(attrs["langfuse.observation.type"])
  ).toLowerCase()
  const nameOperation = Object.entries(OPERATIONS).find(([prefix]) =>
    models.some(
      (candidate) =>
        name.slice(0, prefix.length).toLowerCase() === prefix &&
        name.slice(prefix.length) === ` ${candidate}`
    )
  )?.[1]

  // Explicit operations win. In particular, CHAT can mean an entire agent
  // turn, and invoke_agent/execute_tool must not become a model request.
  let operation: ModelOperation | null = null
  if (declaredOperation) {
    operation = operationFor(declaredOperation)
  } else if (kind === "embedding") {
    operation = "embedding"
  } else if (kind === "llm" || kind === "generation") {
    operation = "chat"
  } else if (!kind || kind === "span") {
    operation = nameOperation ?? null
  }

  const standardName =
    !name ||
    operationFor(name.toLowerCase()) === operation ||
    nameOperation === operation
  const labelKey =
    operation && standardName ? (`modelSpan.${operation}` as const) : null

  return { operation, model, labelKey }
}
