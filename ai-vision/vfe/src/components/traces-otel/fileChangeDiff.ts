import type { RecordedChange } from "./spanContent"

export interface FileDiffRow {
  kind: "context" | "remove" | "add" | "hunk" | "omitted"
  text: string
  oldLine?: number
  newLine?: number
  count?: number
  omittedBefore?: number
  omittedAfter?: number
  noNewline?: boolean
}
export interface FileDiffProjection {
  rows: FileDiffRow[]
  added: number
  removed: number
  coarse: boolean
  /** Complete, unabridged recorded diff or a valid derived unified diff. */
  raw: string
}

const CONTEXT = 3
const MAX_MATRIX_CELLS = 40_000
const lines = (text: string) => text.match(/[^\n]*\n|[^\n]+$/g) ?? []
const lineText = (text: string) =>
  text.endsWith("\n") ? text.slice(0, -1) : text

/** Collapse display context only; source text and raw diff remain untouched. */
function contextual(rows: FileDiffRow[], all: boolean): FileDiffRow[] {
  if (all) return rows
  const result: FileDiffRow[] = []
  for (let i = 0; i < rows.length; ) {
    if (rows[i].kind !== "context") {
      result.push({ ...rows[i++] })
      continue
    }
    let end = i
    while (end < rows.length && rows[end].kind === "context") end++
    const keepBefore = i === 0 ? 0 : CONTEXT
    const keepAfter = end === rows.length ? 0 : CONTEXT
    if (end - i <= keepBefore + keepAfter)
      result.push(...rows.slice(i, end).map((row) => ({ ...row })))
    else {
      result.push(...rows.slice(i, i + keepBefore).map((row) => ({ ...row })))
      result.push({
        kind: "omitted",
        text: "",
        count: end - i - keepBefore - keepAfter,
      })
      result.push(
        ...rows.slice(end - keepAfter, end).map((row) => ({ ...row }))
      )
    }
    i = end
  }
  // A change late in a long single line must be visible without scrolling past
  // thousands of identical characters. Omitted characters are explicitly counted.
  for (let i = 0; i < result.length; i++) {
    if (result[i].kind !== "remove") continue
    let middle = i
    while (result[middle]?.kind === "remove") middle++
    let end = middle
    while (result[end]?.kind === "add") end++
    for (let n = 0; n < Math.min(middle - i, end - middle); n++) {
      const old = result[i + n],
        next = result[middle + n]
      let prefix = 0,
        suffix = 0
      while (
        prefix < Math.min(old.text.length, next.text.length) &&
        old.text[prefix] === next.text[prefix]
      )
        prefix++
      while (
        suffix < Math.min(old.text.length, next.text.length) - prefix &&
        old.text[old.text.length - suffix - 1] ===
          next.text[next.text.length - suffix - 1]
      )
        suffix++
      const start = Math.max(0, prefix - 80),
        tail = Math.max(0, suffix - 80)
      for (const row of [old, next]) {
        row.omittedBefore = start
        row.omittedAfter = tail
        row.text = row.text.slice(start, row.text.length - tail)
      }
    }
    i = end - 1
  }
  return result
}

function recorded(diff: string, all: boolean): FileDiffProjection {
  const rows: FileDiffRow[] = []
  let oldLine = 0,
    newLine = 0,
    added = 0,
    removed = 0,
    inHunk = false
  let oldRemaining = Infinity,
    newRemaining = Infinity
  for (const text of diff.split("\n")) {
    const hunk = text.match(/^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/)
    if (hunk || text === "@@") {
      inHunk = true
      oldLine = Number(hunk?.[1] ?? 1)
      newLine = Number(hunk?.[3] ?? 1)
      oldRemaining = hunk ? Number(hunk[2] ?? 1) : Infinity
      newRemaining = hunk ? Number(hunk[4] ?? 1) : Infinity
      rows.push({ kind: "hunk", text })
    } else if (inHunk && text.startsWith("-")) {
      rows.push({ kind: "remove", text: text.slice(1), oldLine: oldLine++ })
      removed++
      oldRemaining--
    } else if (inHunk && text.startsWith("+")) {
      rows.push({ kind: "add", text: text.slice(1), newLine: newLine++ })
      added++
      newRemaining--
    } else if (inHunk && text.startsWith(" ")) {
      rows.push({
        kind: "context",
        text: text.slice(1),
        oldLine: oldLine++,
        newLine: newLine++,
      })
      oldRemaining--
      newRemaining--
    } else if (text) rows.push({ kind: "hunk", text })
    if (oldRemaining === 0 && newRemaining === 0) inHunk = false
  }
  return {
    rows: contextual(rows, all),
    added,
    removed,
    coarse: false,
    raw: diff,
  }
}

/** Linear prefix/suffix scan, then a bounded LCS; oversized edits use a valid replacement. */
export function buildFileChangeDiff(
  change: RecordedChange,
  all = false
): FileDiffProjection {
  if (change.before === undefined || change.after === undefined)
    return recorded(change.diff ?? "", all)
  const before = lines(change.before),
    after = lines(change.after)
  let prefix = 0,
    suffix = 0
  while (
    prefix < Math.min(before.length, after.length) &&
    before[prefix] === after[prefix]
  )
    prefix++
  while (
    suffix < Math.min(before.length, after.length) - prefix &&
    before[before.length - suffix - 1] === after[after.length - suffix - 1]
  )
    suffix++
  const oldCore = before.slice(prefix, before.length - suffix),
    newCore = after.slice(prefix, after.length - suffix)
  const coarse = (oldCore.length + 1) * (newCore.length + 1) > MAX_MATRIX_CELLS
  const operations: { kind: "context" | "remove" | "add"; value: string }[] = []
  for (let i = 0; i < prefix; i++)
    operations.push({ kind: "context", value: before[i] })
  if (coarse) {
    for (const value of oldCore) operations.push({ kind: "remove", value })
    for (const value of newCore) operations.push({ kind: "add", value })
  } else {
    const width = newCore.length + 1
    const matrix = new Uint32Array((oldCore.length + 1) * width)
    for (let i = oldCore.length - 1; i >= 0; i--)
      for (let j = newCore.length - 1; j >= 0; j--)
        matrix[i * width + j] =
          oldCore[i] === newCore[j]
            ? matrix[(i + 1) * width + j + 1] + 1
            : Math.max(matrix[(i + 1) * width + j], matrix[i * width + j + 1])
    let i = 0,
      j = 0
    while (i < oldCore.length || j < newCore.length) {
      if (
        i < oldCore.length &&
        j < newCore.length &&
        oldCore[i] === newCore[j]
      ) {
        operations.push({ kind: "context", value: oldCore[i++] })
        j++
      } else if (
        i < oldCore.length &&
        (j === newCore.length ||
          matrix[(i + 1) * width + j] >= matrix[i * width + j + 1])
      )
        operations.push({ kind: "remove", value: oldCore[i++] })
      else operations.push({ kind: "add", value: newCore[j++] })
    }
  }
  for (let i = before.length - suffix; i < before.length; i++)
    operations.push({ kind: "context", value: before[i] })
  let oldLine = 1,
    newLine = 1
  const rows: FileDiffRow[] = operations.map(({ kind, value }) => ({
    kind,
    text: lineText(value),
    oldLine: kind === "add" ? undefined : oldLine++,
    newLine: kind === "remove" ? undefined : newLine++,
    noNewline: !value.endsWith("\n"),
  }))
  const added = rows.filter((row) => row.kind === "add").length,
    removed = rows.filter((row) => row.kind === "remove").length
  const start = Math.max(0, prefix - CONTEXT),
    end = rows.length - Math.max(0, suffix - CONTEXT)
  const rawRows = rows.slice(start, end)
  const oldCount = rawRows.filter((row) => row.kind !== "add").length,
    newCount = rawRows.filter((row) => row.kind !== "remove").length
  const header = `@@ -${oldCount ? start + 1 : start},${oldCount} +${newCount ? start + 1 : start},${newCount} @@`
  const raw =
    added || removed
      ? `--- a/${change.path}\n+++ b/${change.path}\n${header}\n${rawRows.map((row) => `${row.kind === "add" ? "+" : row.kind === "remove" ? "-" : " "}${row.text}\n${row.noNewline ? "\\ No newline at end of file\n" : ""}`).join("")}`
      : ""
  return { rows: contextual(rows, all), added, removed, coarse, raw }
}
