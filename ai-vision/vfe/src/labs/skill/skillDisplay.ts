import { decodeEmbeddedPayload } from "@/components/labs/decision/decisionDisplay"
import { parseTimestampMs } from "@/components/traces-otel/format"
import { resolveToolSpanOutcome } from "@/components/traces-otel/spanContent"
import type { OTelSpan } from "@/components/traces-otel/types"
import { ARGUMENT_KEYS } from "@/labs/mcp/mcpDisplay"

export interface SkillSpanInfo {
  name: string | null
  id: string | null
  /** True when reconstructed from the payload shape rather than native attributes. */
  derived: boolean
}

/**
 * The Agent Skills file layout is the cross-host invariant: codex `exec`,
 * deepagents `read_file` and friends all end up touching
 * `skills/<name>/SKILL.md` when they load a skill.
 */
const SKILL_MD_PATH = /skills\/([A-Za-z0-9._-]+)\/SKILL\.md/

/** Detail rows read the codex SkillInvocation shape out of the arguments JSON. */
const ARGUMENT_JSON_KEYS = [
  "agentic.tool.call.arguments",
  "gen_ai.tool.call.arguments",
] as const

function text(attrs: Record<string, unknown>, key: string): string | null {
  const value = attrs[key]
  return typeof value === "string" && value.trim() ? value.trim() : null
}

function isTrue(value: unknown): boolean {
  return value === true || value === "true" || value === "1"
}

function joinArguments(attrs: Record<string, unknown>): string {
  return ARGUMENT_KEYS.map((key) => attrs[key])
    .filter(
      (value): value is string => typeof value === "string" && !!value.trim()
    )
    .map(decodeEmbeddedPayload)
    .join("\n")
}

// Reader-gate allowlists, mirroring the collector side (skill.rs
// READER_PROGRAMS / WRAPPER_PROGRAMS) and labs_skill.py.
const SKILL_READER_PROGRAMS = new Set([
  "cat",
  "head",
  "tail",
  "sed",
  "awk",
  "grep",
  "rg",
  "less",
  "more",
  "bat",
  "read_file",
  "readfile",
])
const SKILL_WRAPPER_PROGRAMS = new Set(["sh", "bash", "zsh", "dash", "ksh"])
// Tool names that maintain a SKILL.md rather than use the skill: a write
// shape never derives usage even when the path matches.
const SKILL_WRITER_TOOL_NAMES = new Set([
  "write",
  "write_file",
  "writefile",
  "edit",
  "edit_file",
  "apply_patch",
  "multi_edit",
  "create_file",
  "notebook_edit",
  "str_replace",
])

function readArgumentValues(attrs: Record<string, unknown>): string[] {
  // Raw argument values without the embedded-escape round: the JSON-envelope
  // channel needs quotes intact to extract values.
  return ARGUMENT_KEYS.map((key) => attrs[key]).filter(
    (value): value is string => typeof value === "string" && !!value.trim()
  )
}

function splitUnquotedOperators(text: string): string[] {
  // Split on ; | & and newlines outside quotes, so a reader named inside an
  // echo "..." string cannot pass the gate.
  const segments: string[] = []
  let start = 0
  let quote: string | null = null
  let escaped = false
  for (let index = 0; index < text.length; index += 1) {
    const ch = text[index]
    if (escaped) {
      escaped = false
      continue
    }
    if (quote !== null) {
      if (ch === "\\") escaped = true
      else if (ch === quote) quote = null
      continue
    }
    if (ch === "\\") {
      escaped = true
      continue
    }
    if (ch === '"' || ch === "'") {
      quote = ch
      continue
    }
    if (ch === ";" || ch === "|" || ch === "&" || ch === "\n") {
      segments.push(text.slice(start, index))
      start = index + 1
    }
  }
  segments.push(text.slice(start))
  return segments
}

function shellTokens(segment: string, requireComplete = false): string[] {
  // Whitespace tokens that respect quoting, so "cat a; b" stays one token.
  const tokens: string[] = []
  let start: number | null = null
  let quote: string | null = null
  let escaped = false
  for (let index = 0; index < segment.length; index += 1) {
    const ch = segment[index]
    if (escaped) {
      escaped = false
      continue
    }
    if (quote !== null) {
      if (ch === "\\") escaped = true
      else if (ch === quote) quote = null
      continue
    }
    if (ch === "\\") {
      escaped = true
      if (start === null) start = index
      continue
    }
    if (ch === '"' || ch === "'") {
      quote = ch
      if (start === null) start = index
      continue
    }
    if (/\s/.test(ch)) {
      if (start !== null) {
        tokens.push(segment.slice(start, index))
        start = null
      }
      continue
    }
    if (start === null) start = index
  }
  if (requireComplete && (quote !== null || escaped)) return []
  if (start !== null) tokens.push(segment.slice(start))
  return tokens
}

function unquote(token: string): string {
  const trimmed = token.trim()
  if (
    trimmed.length >= 2 &&
    trimmed[0] === trimmed[trimmed.length - 1] &&
    (trimmed[0] === '"' || trimmed[0] === "'")
  ) {
    return trimmed.slice(1, -1)
  }
  return trimmed
}

function redirectsOntoSkillMd(tokens: string[]): boolean {
  // True when a redirection inside the segment targets the SKILL.md path
  // itself (cat > x/SKILL.md) — maintaining the file, not reading it.
  // Handles attached forms (>>file, 2>file) via the quoted token stream, so
  // a > inside an echo string never matches.
  for (let position = 0; position < tokens.length; position += 1) {
    let bare = unquote(tokens[position])
    if (bare === ">" || bare === ">>") {
      if (
        position + 1 < tokens.length &&
        SKILL_MD_PATH.test(unquote(tokens[position + 1]))
      ) {
        return true
      }
      continue
    }
    while (/^\d/.test(bare)) bare = bare.slice(1)
    for (const operator of [">>", ">"]) {
      if (bare.startsWith(operator) && bare.length > operator.length) {
        if (SKILL_MD_PATH.test(unquote(bare.slice(operator.length)))) {
          return true
        }
        break
      }
    }
  }
  return false
}

function editsInPlace(tokens: string[]): boolean {
  // True for sed -i / sed --in-place / sed -i.bak shapes.
  for (let position = 1; position < tokens.length; position += 1) {
    const bare = unquote(tokens[position])
    if (!bare.startsWith("-")) continue
    if (
      bare.startsWith("--in-place") ||
      (!bare.startsWith("--") && bare[1] === "i")
    ) {
      return true
    }
  }
  return false
}

function segmentSkillName(segment: string, depth = 0): string | null {
  // A segment passes when its first program is a reader, or when it wraps a
  // reader command inside sh/bash/zsh -c|-lc "<inner>".
  const trimmed = segment.trim()
  if (!trimmed || trimmed.startsWith("#") || depth >= 3) return null
  const tokens = shellTokens(trimmed)
  if (tokens.length === 0) return null
  const program = tokens[0].split("/").pop() ?? tokens[0]
  if (SKILL_READER_PROGRAMS.has(program)) {
    // A reader can also maintain the file: redirection onto the path
    // itself or an in-place edit is not usage.
    if (
      redirectsOntoSkillMd(tokens) ||
      (program === "sed" && editsInPlace(tokens))
    ) {
      return null
    }
    return SKILL_MD_PATH.exec(segment.replace(/\\\//g, "/"))?.[1] ?? null
  }
  if (!SKILL_WRAPPER_PROGRAMS.has(program)) return null
  for (let position = 1; position < tokens.length; position += 1) {
    if (
      unquote(tokens[position]) !== "-c" &&
      unquote(tokens[position]) !== "-lc"
    )
      continue
    if (position + 1 >= tokens.length) return null
    const inner = tokens[position + 1]
    if (inner.startsWith("-")) return null
    return shellSkillName(unquote(inner), depth + 1)
  }
  return null
}

function shellSkillName(text: string, depth = 0): string | null {
  for (const segment of splitUnquotedOperators(text)) {
    const name = segmentSkillName(segment, depth)
    if (name) return name
  }
  return null
}

const JSON_STRING_ESCAPES: Record<string, string> = {
  '"': '"',
  "\\": "\\",
  "/": "/",
  n: "\n",
  t: "\t",
  r: "\r",
  "'": "'",
  b: "\b",
  f: "\f",
}

function unescapeJsonString(text: string): string {
  let out = ""
  for (let index = 0; index < text.length; index += 1) {
    const ch = text[index]
    if (ch === "\\" && index + 1 < text.length) {
      const next = text[index + 1]
      out += JSON_STRING_ESCAPES[next] ?? `\\${next}`
      index += 1
      continue
    }
    out += ch
  }
  return out
}

function stringLiterals(text: string): string[] {
  // Quoted string contents, escape-aware, so a JSON envelope's values can be
  // re-judged after one unescape round.
  const literals: string[] = []
  let index = 0
  while (index < text.length) {
    const ch = text[index]
    if (ch === "\\") {
      index += 2
      continue
    }
    if (ch === '"' || ch === "'") {
      const quote = ch
      index += 1
      const start = index
      while (index < text.length) {
        const inner = text[index]
        if (inner === "\\") {
          index += 2
          continue
        }
        if (inner === quote) break
        index += 1
      }
      literals.push(text.slice(start, index))
      index += 1
      continue
    }
    index += 1
  }
  return literals
}

function isPurePathArgument(value: string): boolean {
  const trimmed = value.trim()
  if (!trimmed) return false
  return !/\s/.test(trimmed) && !/[;|&]/.test(trimmed)
}

function readSkillName(rawText: string): string | null {
  // Bind the matched path to its reader command; a path mentioned by an
  // echo beside a separate reader is not evidence that the file was read.
  const commandName = shellSkillName(rawText)
  if (commandName) return commandName
  if (!rawText.trimStart().startsWith("{")) return null
  for (const literal of stringLiterals(rawText)) {
    const value = unescapeJsonString(literal)
    const name = shellSkillName(value)
    if (name) return name
    if (isPurePathArgument(value)) {
      const pathName = SKILL_MD_PATH.exec(value)?.[1]
      if (pathName) return pathName
    }
  }
  return null
}

function singleReadArgv(
  argv: string[],
  allowShellWrapper = true
): string | null {
  const [executable, ...operands] = argv
  const program = executable?.split("/").at(-1) ?? ""
  if (
    allowShellWrapper &&
    SKILL_WRAPPER_PROGRAMS.has(program) &&
    operands.length === 2 &&
    (operands[0] === "-c" || operands[0] === "-lc")
  ) {
    return singleReadCommand(operands[1], false)
  }

  let paths = operands
  if (program === "head" || program === "tail") {
    if (/^(?:-n|-c|--lines|--bytes)$/.test(operands[0] ?? "")) {
      if (!/^[+-]?\d+$/.test(operands[1] ?? "")) return null
      paths = operands.slice(2)
    }
    if (paths[0] === "--") paths = paths.slice(1)
  } else if (program === "sed") {
    if (operands[0] !== "-n" || !/^[0-9,$]*p$/.test(operands[1] ?? ""))
      return null
    paths = operands.slice(2)
    if (paths[0] === "--") paths = paths.slice(1)
  } else if (program === "cat") {
    const optionEnd = operands.indexOf("--")
    paths = operands.filter(
      (operand, index) =>
        operand === "-" ||
        !operand.startsWith("-") ||
        (optionEnd !== -1 && index > optionEnd)
    )
  } else {
    return null
  }
  const path = paths[0]
  if (paths.length !== 1 || !path || path.startsWith("#")) return null
  return /(?:^|\/)skills\/([A-Za-z0-9._-]+)\/SKILL\.md$/.exec(path)?.[1] ?? null
}

function singleReadCommand(
  command: string,
  allowShellWrapper = true
): string | null {
  const trimmed = command.trim()
  if (/[;|&<>\r\n`]|\$\(/.test(trimmed)) return null
  return singleReadArgv(
    shellTokens(trimmed, true).map(unquote),
    allowShellWrapper
  )
}

function singleReadSkillName(value: string): string | null {
  let payload: unknown = value
  for (
    let depth = 0;
    depth < 2 &&
    typeof payload === "string" &&
    /^[{["]/.test(payload.trimStart());
    depth++
  ) {
    try {
      payload = JSON.parse(payload)
    } catch {
      return null
    }
  }
  if (payload && typeof payload === "object" && !Array.isArray(payload)) {
    const fields = payload as Record<string, unknown>
    payload = fields.command ?? fields.cmd
  }
  if (typeof payload === "string") return singleReadCommand(payload)
  if (
    Array.isArray(payload) &&
    payload.every((arg): arg is string => typeof arg === "string")
  )
    return singleReadArgv(payload)
  return null
}

/**
 * Single recognition entry for skill spans, shared by the tree chip, the
 * trace summary and the span card. Native attributes are authoritative; the
 * shape branch only reads this span's own arguments, never its result (tool
 * output may be a mere excerpt — e.g. `cat SKILL.md >/tmp/x` printing just a
 * line count).
 */
export function resolveSkillSpanInfo(
  attrs: Record<string, unknown>
): SkillSpanInfo | null {
  // Native: plugin-reported classification (codex/claudecode/pi/openclaw/...).
  if (text(attrs, "agentic.tool.type")?.toLowerCase() === "skill") {
    if (isFallbackInvocation(attrs)) return null
    return {
      name:
        text(attrs, "agentic.tool.name") ?? text(attrs, "agentic.skill.name"),
      id: text(attrs, "agentic.tool.id"),
      derived: isTrue(attrs["agentic.tool.derived"]),
    }
  }
  // Native: aicoder-style storage columns.
  const storedName = text(attrs, "agentic.skill.name")
  if (storedName) {
    if (isFallbackInvocation(attrs)) return null
    return {
      name: storedName,
      id: text(attrs, "agentic.skill.id"),
      derived: false,
    }
  }

  // Derived shape: a tool whose arguments reference a SKILL.md path. Gated
  // to TOOL spans so middleware narration text never matches, and by the
  // reader gate so printf/echo strings, comments, and script paths never
  // count as usage.
  const kind = text(attrs, "gen_ai.span.kind")?.toUpperCase()
  if (kind === "TOOL") {
    const toolName = (
      text(attrs, "gen_ai.tool.call.name") ??
      text(attrs, "agentic.tool.raw_name") ??
      text(attrs, "agentic.tool.name") ??
      text(attrs, "gen_ai.tool.name") ??
      ""
    ).toLowerCase()
    if (!SKILL_WRITER_TOOL_NAMES.has(toolName)) {
      const name = readSkillName(readArgumentValues(attrs).join("\n"))
      if (name) return { name, id: null, derived: true }
    }
  }

  return null
}

// Plugin-inferred invocations write no command text into their arguments
// JSON (only {skill, invocation, source, byte_range}), so the platform
// cannot re-judge them; the same event re-enters through the gated shape
// branch on the exec TOOL span instead.
const SKILL_FALLBACK_SOURCES = new Set(["command_path_fallback"])

function isFallbackInvocation(attrs: Record<string, unknown>): boolean {
  // Missing, unparsable, or redacted payloads mean a semantic signal —
  // cchooks writes no source at all.
  for (const key of ARGUMENT_KEYS) {
    const value = attrs[key]
    if (typeof value !== "string" || !value.trim()) continue
    try {
      const parsed = JSON.parse(value) as { source?: unknown } | null
      if (parsed && SKILL_FALLBACK_SOURCES.has(String(parsed.source))) {
        return true
      }
    } catch {
      // Plain command text or a redacted payload — not a fallback marker.
    }
  }
  return false
}

/**
 * Name from the plugin's own report (recognition branches 1 and 2 only).
 * Native records are authoritative — except command-path fallbacks, whose
 * arguments carry no command text to re-judge; they re-enter through the
 * gated shape branch on the exec TOOL span instead. Never consult the
 * `agentic.tool.derived` flag here, a future native fallback span may
 * carry that flag too.
 */
export function nativeSkillName(
  attrs: Record<string, unknown> | undefined
): string | null {
  if (!attrs) return null
  let name: string | null = null
  if (text(attrs, "agentic.tool.type")?.toLowerCase() === "skill") {
    name = text(attrs, "agentic.tool.name") ?? text(attrs, "agentic.skill.name")
  } else {
    name = text(attrs, "agentic.skill.name")
  }
  if (!name || isFallbackInvocation(attrs)) return null
  return name
}

type SkillOutcomeSpan = Pick<OTelSpan, "attributes" | "resource" | "status">

function isCodexShell(span: SkillOutcomeSpan, attrs: Record<string, unknown>) {
  const tool = (
    text(attrs, "agentic.tool.raw_name") ??
    text(attrs, "gen_ai.tool.call.name") ??
    text(attrs, "gen_ai.tool.name")
  )?.toLowerCase()
  if (tool === "exec_command") return true
  if (tool !== "bash" && tool !== "shell_command") return false

  const resource = span.resource ?? {}
  return [
    "aivision.agent.product",
    "ant.agent.product",
    "ant.agent.type",
    "agentic.runtime.name",
  ].some((key) =>
    [resource[key], attrs[key]].some(
      (value) => typeof value === "string" && /^codex$/i.test(value.trim())
    )
  )
}

function codexShellFailed(output: unknown): boolean {
  let result = output
  for (
    let depth = 0;
    depth < 2 &&
    typeof result === "string" &&
    result.trimStart().startsWith('"');
    depth++
  ) {
    try {
      result = JSON.parse(result)
    } catch {
      return false
    }
  }
  if (typeof result !== "string") return false
  // Only inspect the transport header, never the command's displayed output.
  const exitCode =
    /^Chunk ID: [A-Za-z0-9_-]+\r?\nWall time: [0-9]+(?:\.[0-9]+)?[ \t]+(?:seconds?|s)\r?\nProcess exited with code (-?[0-9]{1,20})(?:\r?\n|$)/.exec(
      result
    )?.[1] ??
    /^Exit code: (-?[0-9]{1,20})\r?\nOutput:(?:\r?\n|$)/.exec(result)?.[1]
  return exitCode !== undefined && !/^-?0+$/.test(exitCode)
}

/** A file read can fail without invoking the Skill. Keep the evidence on the source span. */
export function failedSkillReadName(
  span: SkillOutcomeSpan,
  output = span.attributes?.["gen_ai.tool.call.result"] ??
    span.attributes?.["langfuse.observation.output"]
): string | null {
  const attrs = span.attributes
  if (!attrs || text(attrs, "gen_ai.span.kind")?.toUpperCase() !== "TOOL")
    return null
  const codexShell = isCodexShell(span, attrs)
  const outcome = resolveToolSpanOutcome(span, output)
  if (outcome !== "error" && !(codexShell && codexShellFailed(output)))
    return null

  const reported = nativeSkillName(attrs)
  const name = reported ?? resolveSkillSpanInfo(attrs)?.name
  if (!name) return null
  if (!codexShell) return reported ? null : name

  // A shell exit applies to the whole command. Attribute it to the read only
  // when the command reads that file alone or the producer marked the read.
  const argumentValues = readArgumentValues(attrs)
  if (argumentValues.some((value) => singleReadSkillName(value) === name))
    return name
  return reported &&
    outcome === "error" &&
    isTrue(attrs["agentic.tool.derived"]) &&
    ARGUMENT_KEYS.every((key) => {
      const value = attrs[key]
      return (
        value === undefined ||
        value === null ||
        (typeof value === "string" &&
          (!value.trim() ||
            /^(?:\[(?:REDACTED|TRUNCATED)\]|"\[(?:REDACTED|TRUNCATED)\]")$/.test(
              value.trim()
            )))
      )
    })
    ? reported
    : null
}

/** Parsed numeric token usage (aicoder columns); null when nothing is set. */
export interface SkillTokenUsage {
  input: number | null
  output: number | null
  total: number | null
}

/** Detail fields for the span card; every row renders only when non-null. */
export interface SkillDetailFields {
  info: SkillSpanInfo
  rawName: string | null
  invocation: string | null
  source: string | null
  byteRange: string | null
  argsSummary: string | null
  input: string | null
  output: string | null
  errorType: string | null
  owner: string | null
  provider: string | null
  tags: string[] | null
  phase: string | null
  tokens: SkillTokenUsage | null
  rawArguments: string | null
}

function parseNumber(
  attrs: Record<string, unknown>,
  key: string
): number | null {
  const value = attrs[key]
  if (typeof value === "number" && Number.isFinite(value)) return value
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return null
}

function parseTags(attrs: Record<string, unknown>): string[] | null {
  const value = attrs["agentic.skill.tags"]
  if (typeof value === "string" && value.trim()) {
    return value
      .split(",")
      .map((tag) => tag.trim())
      .filter(Boolean)
  }
  if (Array.isArray(value)) {
    const tags = value.filter(
      (tag): tag is string => typeof tag === "string" && !!tag.trim()
    )
    return tags.length ? tags : null
  }
  return null
}

function parseTokens(attrs: Record<string, unknown>): SkillTokenUsage | null {
  const tokens = {
    input: parseNumber(attrs, "agentic.skill.input_tokens"),
    output: parseNumber(attrs, "agentic.skill.output_tokens"),
    total: parseNumber(attrs, "agentic.skill.total_tokens"),
  }
  return tokens.input === null &&
    tokens.output === null &&
    tokens.total === null
    ? null
    : tokens
}

/** Per-call detail rows for the span card; keys stay null when absent so the
 * card can hide missing rows instead of showing placeholder dashes. */
export function resolveSkillDetailFields(
  attrs: Record<string, unknown>
): SkillDetailFields | null {
  const info = resolveSkillSpanInfo(attrs)
  if (!info) return null
  const rawArguments = joinArguments(attrs) || null
  let invocation: string | null = null
  let source: string | null = null
  let byteRange: string | null = null
  for (const key of ARGUMENT_JSON_KEYS) {
    const raw = attrs[key]
    if (typeof raw !== "string") continue
    try {
      const parsed = JSON.parse(decodeEmbeddedPayload(raw)) as Record<
        string,
        unknown
      >
      invocation =
        typeof parsed.invocation === "string" ? parsed.invocation : invocation
      source = typeof parsed.source === "string" ? parsed.source : source
      byteRange =
        typeof parsed.byte_range === "string" ? parsed.byte_range : byteRange
    } catch {
      // Plain command text (exec shape) carries no structured fields.
    }
  }
  return {
    info,
    rawName: text(attrs, "agentic.tool.raw_name"),
    invocation,
    source,
    byteRange,
    argsSummary: text(attrs, "gen_ai.tool.call.arguments"),
    input: text(attrs, "agentic.skill.input"),
    output: text(attrs, "agentic.skill.output"),
    errorType: text(attrs, "error.type"),
    owner: text(attrs, "agentic.skill.owner"),
    provider: text(attrs, "agentic.skill.provider"),
    tags: parseTags(attrs),
    phase: text(attrs, "agentic.skill.phase"),
    tokens: parseTokens(attrs),
    rawArguments,
  }
}

export interface CountedSkillSpans<T> {
  /** Skill spans that carry the usage count, in input order. */
  counted: T[]
  /** Failed reads kept as evidence outside usage metrics. */
  failedReads: T[]
  /** Skill names with a native, non-derived report anywhere in the window. */
  nativeSeen: Set<string>
}

/** Best-effort [start, end] ms interval for the usage merge; the reported
 * tool duration backs the end when no usable end timestamp is present. */
function spanIntervalMs(span: {
  start_time_unix_nano?: string | null
  end_time_unix_nano?: string | null
  attributes?: Record<string, unknown>
}): [number, number] {
  const start = span.start_time_unix_nano
    ? (parseTimestampMs(span.start_time_unix_nano) ?? 0)
    : 0
  let end = span.end_time_unix_nano
    ? (parseTimestampMs(span.end_time_unix_nano) ?? 0)
    : 0
  if (end <= start) {
    const duration = span.attributes
      ? parseNumber(span.attributes, "agentic.tool.duration_ms")
      : null
    end = duration !== null ? start + duration : start
  }
  return [start, Math.max(start, end)]
}

function intervalsOverlap(
  first: [number, number],
  second: [number, number]
): boolean {
  const [aStart, aEnd] = first
  const [bStart, bEnd] = second
  return aStart <= bEnd && bStart <= aEnd
}

/**
 * Usage merge over one window (mirrors the backend scan): each read without a
 * reported failure counts as one use, and a native period overlapping those reads
 * is the same usage (the plugin derives that period from the first read),
 * so it merges into the reads instead of counting twice. A native period
 * with no reads of its own still counts once. Recognition itself — tree
 * chips — is unaffected; only the counted list changes.
 */
export function resolveCountedSkillSpans<
  T extends SkillOutcomeSpan & {
    start_time_unix_nano?: string | null
    end_time_unix_nano?: string | null
  },
>(spans: T[]): CountedSkillSpans<T> {
  const failedReads: T[] = []
  const derivedIntervals = new Map<string, [number, number][]>()
  const recognized = []
  for (const span of spans) {
    const attrs = span.attributes
    const info = attrs ? resolveSkillSpanInfo(attrs) : null
    if (!info?.name) continue
    if (failedSkillReadName(span)) {
      failedReads.push(span)
      continue
    }
    const native = Boolean(nativeSkillName(attrs))
    recognized.push({ span, name: info.name, derived: info.derived, native })
    if (native) continue
    const intervals = derivedIntervals.get(info.name) ?? []
    intervals.push(spanIntervalMs(span))
    derivedIntervals.set(info.name, intervals)
  }
  const counted: T[] = []
  const nativeSeen = new Set<string>()
  for (const { span, name, derived, native } of recognized) {
    if (native) {
      if (!derived) nativeSeen.add(name)
      // Covered by a read of the same skill → same usage, drop the period.
      if (
        (derivedIntervals.get(name) ?? []).some((interval) =>
          intervalsOverlap(spanIntervalMs(span), interval)
        )
      )
        continue
    }
    counted.push(span)
  }
  return { counted, failedReads, nativeSeen }
}

export interface SkillMarkdownParts {
  /** Parsed `key: value` pairs from the YAML frontmatter block. */
  meta: Array<{ key: string; value: string }>
  /** Markdown body after the frontmatter block. */
  body: string
}

/** Splits the `---` frontmatter block into simple key/value pairs and the
 * Markdown body. Block scalars (`>` folded, `|` literal) collect their
 * more-indented lines; other nested YAML structures are skipped; a doc
 * without frontmatter yields an empty meta list. */
export function splitSkillFrontmatter(content: string): SkillMarkdownParts {
  const trimmed = content.trimStart()
  const match = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/.exec(trimmed)
  if (!match) return { meta: [], body: trimmed }
  const meta: SkillMarkdownParts["meta"] = []
  const lines = match[1].split(/\r?\n/)
  for (let index = 0; index < lines.length; index += 1) {
    const pair = /^([A-Za-z0-9_-]+):\s*(.*)$/.exec(lines[index])
    if (!pair) continue
    const value = pair[2].trim()
    // A bare `>`/`|` indicator carries the value in the following
    // more-indented lines; join them so the card shows the real text.
    const block = /^([>|])[+-]?$/.exec(value)
    if (block) {
      const indent = lines[index].length - lines[index].trimStart().length
      const collected: string[] = []
      let step = index + 1
      while (step < lines.length) {
        const line = lines[step]
        const lineIndent = line.length - line.trimStart().length
        if (line.trim() && lineIndent <= indent) break
        collected.push(line.trim())
        step += 1
      }
      index = step - 1
      meta.push({
        key: pair[1],
        value:
          block[1] === "|"
            ? collected.join("\n").trimEnd()
            : collected.filter(Boolean).join(" ").trimEnd(),
      })
      continue
    }
    meta.push({
      key: pair[1],
      value: /^"[\s\S]*"$|^'[\s\S]*'$/.test(value) ? value.slice(1, -1) : value,
    })
  }
  return { meta, body: trimmed.slice(match[0].length).trimStart() }
}

/** Drops a leading `# <name>` heading that merely repeats the skill name the
 * drawer already surfaces in its title and the metadata card. */
export function stripRepeatedTitleHeading(body: string, name: string): string {
  const target = name.trim().toLowerCase()
  if (!target) return body
  const match = /^#[ \t]+([^\r\n]*)(?:\r?\n|$)/.exec(body)
  if (!match || match[1].trim().toLowerCase() !== target) return body
  return body.slice(match[0].length).trimStart()
}

/** Only complete counters establish the source split; recentCalls is bounded. */
export function skillProvenanceCounts(value: {
  calls: number
  derived?: boolean
  provenance?: { inferred: number; reported: number }
}): { inferred: number; reported: number } | null {
  const counts = value.provenance
  if (
    counts &&
    Number.isInteger(counts.inferred) &&
    counts.inferred >= 0 &&
    Number.isInteger(counts.reported) &&
    counts.reported >= 0 &&
    counts.inferred + counts.reported === value.calls
  )
    return counts
  // The legacy all-derived flag is proof for this case only. False can mean mixed.
  return value.derived === true ? { inferred: value.calls, reported: 0 } : null
}

/** Preserve list/detail context while discarding an instance filter from another product. */
export function skillTraceHref(
  search: string,
  call: {
    traceId: string
    spanId: string
    agentProduct?: string
    sourceSystem?: string
  },
  currentProduct?: string
): string {
  const current = new URLSearchParams(search)
  const next = new URLSearchParams()
  for (const key of [
    "spaceId",
    "range",
    "from",
    "to",
    "trace_from",
    "trace_to",
  ]) {
    const value = current.get(key)
    if (value) next.set(key, value)
  }
  const product =
    call.agentProduct || currentProduct || current.get("agent") || ""
  if (product) next.set("agent", product)
  const originalProduct = currentProduct || current.get("agent")
  if (originalProduct === product && current.has("agent_uuid"))
    next.set("agent_uuid", current.get("agent_uuid")!)
  const source =
    call.sourceSystem || current.get("source_system") || current.get("source")
  if (source) next.set("source_system", source)
  if (current.get("from") && current.get("to")) {
    next.set("trace_from", current.get("from")!)
    next.set("trace_to", current.get("to")!)
  } else if (current.get("traceId") !== call.traceId) {
    next.delete("trace_from")
    next.delete("trace_to")
  }
  next.set("traceId", call.traceId)
  next.set("focus_span", call.spanId)
  return `/trace?${next.toString()}`
}

export function skillQueryErrorCode(error: unknown): number | null {
  if (
    error &&
    typeof error === "object" &&
    "code" in error &&
    typeof error.code === "number"
  )
    return error.code
  return null
}
