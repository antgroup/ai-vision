import { messageRole } from "./spanContent.ts"

export interface SessionMessage {
  role: string
  content: unknown
  hasContent: boolean
  toolCalls: unknown[]
  reasoning: string | null
  name: string | null
  fields: Record<string, unknown>
}

export interface SessionMessageGroup {
  messages: SessionMessage[]
  fields: Record<string, unknown>
}

type RecordValue = Record<string, unknown>
type LocatedRecord = { value: RecordValue; path: string[] }
type LocatedValue = { value: unknown; path: string[] }

function record(value: unknown): value is RecordValue {
  return value !== null && typeof value === "object" && !Array.isArray(value)
}

function hasContent(value: RecordValue): boolean {
  return Object.hasOwn(value, "content") || Object.hasOwn(value, "parts")
}

function validRole(value: string | null): value is string {
  return value !== null && value.trim().length > 0
}

function hasOtherEvidence(value: RecordValue): boolean {
  const additional = value.additional_kwargs
  return (
    (Array.isArray(value.tool_calls) && value.tool_calls.length > 0) ||
    (record(additional) &&
      Array.isArray(additional.tool_calls) &&
      additional.tool_calls.length > 0) ||
    typeof value.reasoning_content === "string"
  )
}

/** Remove only displayed leaves, cloning every traversed record first. */
function omitDisplayed(fields: RecordValue, path: string[]): void {
  const [key, ...rest] = path
  if (rest.length === 0) {
    delete fields[key]
    return
  }
  const nested = fields[key]
  if (!record(nested)) return
  const remainder = { ...nested }
  omitDisplayed(remainder, rest)
  // An originally empty object is never traversed. Only a structural wrapper
  // emptied by displaying all of its children can disappear here.
  if (Object.keys(remainder).length === 0) delete fields[key]
  else fields[key] = remainder
}

function firstField(
  locations: LocatedRecord[],
  key: string,
  accepts: (value: unknown) => boolean
): LocatedValue | null {
  for (const location of locations) {
    if (Object.hasOwn(location.value, key) && accepts(location.value[key])) {
      return { value: location.value[key], path: [...location.path, key] }
    }
  }
  return null
}

function parseMessage(value: unknown): SessionMessage | null {
  if (!record(value)) return null
  const candidates: LocatedRecord[] = [{ value, path: [] }]
  for (const key of ["data", "kwargs"]) {
    const nested = value[key]
    if (record(nested)) candidates.push({ value: nested, path: [key] })
  }
  // Prefer a real content field over a wrapper carrying only metadata.
  const body =
    candidates.find((candidate) => hasContent(candidate.value)) ??
    candidates.find((candidate) => hasOtherEvidence(candidate.value)) ??
    candidates[0]
  const containers = body.path.length ? [candidates[0], body] : [body]
  const additional = containers.flatMap((container): LocatedRecord[] => {
    const kwargs = container.value.additional_kwargs
    return record(kwargs)
      ? [{ value: kwargs, path: [...container.path, "additional_kwargs"] }]
      : []
  })
  const calls = firstField(
    [...containers, ...additional],
    "tool_calls",
    Array.isArray
  )
  const reasoning = firstField(
    containers,
    "reasoning_content",
    (item) => typeof item === "string"
  )
  const name = firstField(
    containers,
    "name",
    (item) => typeof item === "string"
  )
  const rawRole = messageRole(value)
  const nestedRole = messageRole(body.value)
  const role = validRole(rawRole)
    ? rawRole
    : validRole(nestedRole)
      ? nestedRole
      : null
  const contentPresent = hasContent(body.value)
  if (
    role === null ||
    (!contentPresent &&
      (calls === null || (calls.value as unknown[]).length === 0) &&
      reasoning === null)
  )
    return null

  const fields = { ...value }
  for (const container of containers) {
    if (container.value.role === role)
      omitDisplayed(fields, [...container.path, "role"])
  }
  const contentKey = Object.hasOwn(body.value, "content") ? "content" : "parts"
  if (contentPresent) omitDisplayed(fields, [...body.path, contentKey])
  for (const field of [calls, reasoning, name]) {
    if (field !== null) omitDisplayed(fields, field.path)
  }
  return {
    role,
    content: contentPresent ? body.value[contentKey] : undefined,
    hasContent: contentPresent,
    toolCalls: calls === null ? [] : (calls.value as unknown[]),
    reasoning: reasoning === null ? null : (reasoning.value as string),
    name: name === null ? null : (name.value as string),
    fields,
  }
}

function sequence(value: unknown): SessionMessage[] | null {
  if (!Array.isArray(value)) {
    const message = parseMessage(value)
    return message === null ? null : [message]
  }
  const items = value.length === 1 && Array.isArray(value[0]) ? value[0] : value
  if (items.length === 0) return null
  const messages: SessionMessage[] = []
  for (const item of items) {
    const message = parseMessage(item)
    if (message === null) return null
    messages.push(message)
  }
  return messages
}

/** Recognize structured message evidence only; typed strings stay literal. */
export function sessionMessages(value: unknown): SessionMessageGroup | null {
  const messages = sequence(value)
  if (messages !== null) return { messages, fields: {} }
  if (!record(value) || !Object.hasOwn(value, "messages")) return null
  const wrapped = sequence(value.messages)
  if (wrapped === null) return null
  const fields = { ...value }
  delete fields.messages
  return { messages: wrapped, fields }
}

function readableText(value: unknown): string | null {
  if (typeof value === "string") return value
  const blocks = Array.isArray(value) ? value : [value]
  const texts = blocks.flatMap((block): string[] => {
    if (typeof block === "string") return [block]
    if (
      !record(block) ||
      !["text", "input_text", "output_text"].includes(String(block.type))
    )
      return []
    if (typeof block.text === "string") return [block.text]
    if (typeof block.content === "string") return [block.content]
    return []
  })
  return texts.length > 0 ? texts.join("\n") : null
}

/** Prefer the last explicit user message; do not fabricate text for media. */
export function sessionMessagePreview(value: unknown): string | null {
  const group = sessionMessages(value)
  if (group === null) return null
  const messages = group.messages
  let selected = messages[messages.length - 1]
  for (let index = messages.length - 1; index >= 0; index--) {
    if (messages[index].role === "user") {
      selected = messages[index]
      break
    }
  }
  return readableText(selected.content)
}
