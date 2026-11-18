import type {
  NativeIOItem,
  NativeIOState,
  NativeTraceListResponse,
} from "./types.ts"

export function sessionContent(raw: unknown, typed = false) {
  let value: unknown = raw
  if (!typed && typeof raw === "string") {
    // API null is missing; the string "null" can be a literal business value.
    try {
      value = JSON.parse(raw) ?? raw
    } catch {
      /* Plain text is also valid IO. */
    }
  }
  const structured = value !== null && typeof value === "object"
  const missing = value === null || value === undefined
  const empty =
    value === "" ||
    (Array.isArray(value) && value.length === 0) ||
    (structured &&
      !Array.isArray(value) &&
      Object.keys(value as object).length === 0)
  const state: NativeIOState = missing
    ? "unknown"
    : empty
      ? "empty"
      : "available"
  return {
    value,
    state,
    structured,
    text: missing
      ? ""
      : typeof value === "string"
        ? value
        : JSON.stringify(value, null, 2),
  }
}

export function sessionFieldContent(
  item: NativeIOItem | undefined,
  field: "input" | "output",
  legacy?: string
) {
  const key = field === "input" ? "inputValue" : "outputValue"
  if (item && Object.hasOwn(item, key)) return sessionContent(item[key], true)
  return sessionContent(item ? item[field] : legacy)
}

export function sessionIOState(
  raw: string | null | undefined,
  state?: NativeIOState
): NativeIOState {
  return state ?? sessionContent(raw).state
}

type Response<T> = { code: number; message?: string; data: T }

function requireIds(expected: string[], actual: string[]) {
  const wanted = new Set(expected)
  if (
    wanted.size !== expected.length ||
    actual.length !== expected.length ||
    new Set(actual).size !== actual.length ||
    actual.some((id) => !wanted.has(id))
  ) {
    throw new Error("Incomplete session traces or IO; retry")
  }
}

export async function loadSessionIO(
  ids: string[],
  fetchBatch: (ids: string[]) => Promise<Response<{ data: NativeIOItem[] }>>
) {
  const rows: NativeIOItem[] = []
  for (let start = 0; start < ids.length; start += 100) {
    const batch = ids.slice(start, start + 100)
    const response = await fetchBatch(batch)
    if (response.code !== 0)
      throw new Error(response.message || "Session IO query failed")
    requireIds(
      batch,
      response.data.data.map((item) => item.id)
    )
    rows.push(...response.data.data)
  }
  return { code: 0, message: "success", data: { data: rows } }
}

export async function loadSessionTraces(
  ids: string[],
  fetchPage: (page: number) => Promise<Response<NativeTraceListResponse>>
) {
  const first = await fetchPage(1)
  if (first.code !== 0)
    throw new Error(first.message || "Session trace query failed")
  const meta = first.data.meta
  if (
    meta.page !== 1 ||
    meta.limit <= 0 ||
    !Number.isInteger(meta.totalPages) ||
    meta.totalItems !== ids.length ||
    meta.totalPages !== Math.ceil(ids.length / meta.limit)
  ) {
    throw new Error("Incomplete session trace pagination; retry")
  }
  const rows = [...first.data.data]
  for (let page = 2; page <= meta.totalPages; page++) {
    const next = await fetchPage(page)
    if (next.code !== 0)
      throw new Error(next.message || "Session trace query failed")
    if (
      next.data.meta.page !== page ||
      next.data.meta.totalItems !== meta.totalItems ||
      next.data.meta.totalPages !== meta.totalPages ||
      next.data.meta.limit !== meta.limit
    ) {
      throw new Error("Session changed during pagination; retry")
    }
    rows.push(...next.data.data)
  }
  requireIds(
    ids,
    rows.map((row) => row.trace_id)
  )
  return { ...first, data: { ...first.data, data: rows } }
}
