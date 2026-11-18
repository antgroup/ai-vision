import {
  memoryEvidenceFromResult,
  memoryResultPayload,
  type MemoryToolCategory,
} from "./memoryDisplay.ts"

export interface MemoryResultEntry {
  content: string
  state?: string
  score?: string
}

export interface MemoryResult {
  type: "search" | "list" | "write" | "note"
  count: number | null
  entries: MemoryResultEntry[]
  partial: boolean
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function entry(value: unknown): MemoryResultEntry[] {
  const item = record(value)
  if (!item) return []
  const memory = record(item.memory) ?? item
  const content = memory.text ?? memory.content
  if (typeof content !== "string" || !content.trim()) return []
  const score = item.score
  return [
    {
      // Do not trim or summarize the content used by the entry's copy action.
      content,
      ...(typeof memory.state === "string" && memory.state
        ? { state: memory.state }
        : {}),
      ...(typeof score === "number" && Number.isFinite(score)
        ? { score: String(score) }
        : {}),
    },
  ]
}

/** Presentation only: identity/counts still use the shared evidence contract. */
export function memoryResultFromOutput(
  raw: unknown,
  category?: MemoryToolCategory | null
): MemoryResult | null {
  const evidence = memoryEvidenceFromResult(raw, category)
  if (!evidence) return null
  const payload = memoryResultPayload(raw)
  const data = record(payload)
  let entries: MemoryResultEntry[] = []
  if (data) {
    const items =
      evidence.type === "search"
        ? data.hits
        : evidence.type === "list"
          ? (data.memories ?? data.memos ?? data.entries ?? data.items)
          : data.records
    if (Array.isArray(items)) entries = items.flatMap(entry)
    else if (evidence.type === "write") entries = entry(data)
  } else if (typeof payload === "string" && evidence.type === "search") {
    // Only parse the verified numbered search format; prose remains available
    // in the original result. Keep continuation lines inside their own entry.
    const numbered =
      /^\s*\d+\.\s+\[([^\]\r\n]+)\][ \t]+([^\r\n]*(?:\r?\n(?!\s*\d+\.\s+\[)[^\r\n]*)*)/gm
    entries = [...payload.matchAll(numbered)].map((match) => {
      const score = /\s+\(score\s+([0-9.]+)\)\s*$/.exec(match[2])
      return {
        state: match[1],
        content: (score ? match[2].slice(0, score.index) : match[2]).trimEnd(),
        ...(score ? { score: score[1] } : {}),
      }
    })
    if (!entries.length && /Found \d+ item\(s\):/.test(payload)) {
      entries = [
        ...payload.matchAll(/^- \[memory (\d+%)\] \S+\n\s+(.+)$/gm),
      ].map((match) => ({ content: match[2], score: match[1] }))
    }
  }
  return {
    type: evidence.type,
    count: evidence.hits ?? null,
    entries,
    partial: Boolean(
      data &&
      ["next_cursor", "has_more", "nextPageToken", "next_page_token"].some(
        (key) => data[key]
      )
    ),
  }
}
