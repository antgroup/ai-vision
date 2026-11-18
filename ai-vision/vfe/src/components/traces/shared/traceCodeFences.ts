export const TRACE_CODE_SCAN_LIMIT = 512 * 1024
export const TRACE_CODE_PREVIEW_PAGE_SIZE = 8

export interface TraceCodeFence {
  start: number
  end: number
  bodyStart: number
  bodyEnd: number
  language: string
  closed: boolean
}

export function traceCodeFences(text: string): readonly TraceCodeFence[] {
  if (text.length > TRACE_CODE_SCAN_LIMIT) return []

  const markers = /^ {0,3}(`{3,}|~{3,})([^\r\n]*)(?:\r\n|[\r\n]|$)/gm
  const fences: TraceCodeFence[] = []
  let open: (TraceCodeFence & { marker: string }) | undefined

  for (const match of text.matchAll(markers)) {
    const [line, marker, info] = match
    const start = match.index
    const end = start + line.length

    if (open) {
      if (
        marker[0] === open.marker[0] &&
        marker.length >= open.marker.length &&
        !info.trim()
      ) {
        fences.push({
          start: open.start,
          end,
          bodyStart: open.bodyStart,
          bodyEnd: start,
          language: open.language,
          closed: true,
        })
        open = undefined
      }
    } else if (marker[0] === "~" || !info.includes("`")) {
      open = {
        marker,
        start,
        end: text.length,
        bodyStart: end,
        bodyEnd: text.length,
        language: info.trim().split(/\s+/, 1)[0].toLowerCase(),
        closed: false,
      }
    }
  }

  if (open) {
    const { start, end, bodyStart, bodyEnd, language, closed } = open
    fences.push({ start, end, bodyStart, bodyEnd, language, closed })
  }
  return fences
}

const languageAliases: Record<string, string> = {
  bash: "shell",
  "c#": "csharp",
  "c++": "cpp",
  dotenv: "shell",
  golang: "go",
  htm: "html",
  js: "javascript",
  md: "markdown",
  patch: "diff",
  py: "python",
  rb: "ruby",
  rs: "rust",
  sh: "shell",
  ts: "typescript",
  yml: "yaml",
  zsh: "shell",
}

export function normalizeTraceCodeLanguage(value: string): string {
  const language = value.trim().toLowerCase()
  if (!/^[a-z0-9+#-]{1,24}$/.test(language)) return ""
  return Object.hasOwn(languageAliases, language)
    ? languageAliases[language]
    : language
}

export function isTraceCodeField(field?: string): boolean {
  return (
    typeof field === "string" &&
    /^(?:code|code_?snippet|source_?code|script|command|cmd)$/i.test(field)
  )
}

export function traceCodeFieldLanguage(
  record: Record<string, unknown>,
  field: string,
  text: string
): string | null {
  const typed =
    typeof record.type === "string" &&
    /^(?:code|code_block|code_snippet|script|command)$/i.test(record.type) &&
    /^(?:source|content|text)$/i.test(field)
  if (!typed && !isTraceCodeField(field)) return null
  const language = record.language ?? record.lang
  return typeof language === "string"
    ? language
    : inferTraceCodeLanguage(text, field.toLowerCase())
}

export function inferTraceCodeLanguage(text: string, field?: string): string {
  const source = text.slice(0, 8192).trimStart()
  const prefix = source.replace(
    /^(?:(?:\/\/[^\r\n]*(?:\r\n|[\r\n])|\/\*[\s\S]*?\*\/)\s*)+/,
    ""
  )
  if (
    field === "diff" ||
    field === "unified_diff" ||
    /^diff --git |^@@ -/.test(prefix)
  )
    return "diff"
  if (
    /^#!\/[^\r\n]*python\b/.test(prefix) ||
    /^(?:def \w+\(|from \w+(?:\.\w+)* import |import \w+(?:\.\w+)*(?: +as +\w+)?[ \t]*(?=[\r\n]|$))/.test(
      prefix
    )
  )
    return "python"
  if (/^(?:interface |type \w+\s*=|(?:const|let) \w+\s*:)/.test(prefix))
    return "typescript"
  if (
    /^#!\/[^\r\n]*(?:node|deno)\b/.test(prefix) ||
    /^(?:const |let |var |function |export (?:default |const |function )|import .* from )/.test(
      prefix
    )
  )
    return "javascript"
  if (
    /^#!\/[^\r\n]*(?:bash|zsh|sh)\b/.test(prefix) ||
    field === "command" ||
    field === "cmd"
  )
    return "shell"
  if (
    /^(?:select\b[\s\S]+?\bfrom\b|create\s+table\b|insert\s+into\b|update\s+\w+\s+set\b|delete\s+from\b)/i.test(
      prefix
    )
  )
    return "sql"
  return ""
}
