import { isTraceHtmlPreviewLink } from "./traceHtmlLinkUrl.ts"
import {
  inferTraceCodeLanguage,
  isTraceCodeField,
  TRACE_CODE_SCAN_LIMIT,
} from "./traceCodeFences.ts"

type TraceHtmlLink = { source: string; path: string }
type TextFragment = { text: string; path: string; offset: number }

const MAX_SCAN_LENGTH = 2 * 1024 * 1024
const MAX_VISITS = 2_000
const MAX_LINKS = 8

const MIME_FIELDS = ["mimeType", "mime_type", "media_type"]
const MEDIA_FIELDS = [
  "url",
  "imageUrl",
  "audioUrl",
  "videoUrl",
  "fileUrl",
  "data",
]
const MEDIA_TYPES = new Set([
  "aivision_attachment",
  "aivision_media_error",
  "image_url",
  "audio_url",
  "video_url",
  "input_image",
  "input_audio",
  "base64",
])
const MEDIA_SOURCE_TYPES = new Set([
  "image",
  "audio",
  "video",
  "document",
  "file",
  "text",
])
const LITERAL_FIELDS =
  /^(?:commandline|shellcommand|stdout|stderr|log|logs|stack|stacktrace|traceback|diff|unifieddiff)$/i
const CODE_TYPES = /^(?:code(?:_?(?:block|snippet))?|script|command)$/i

const HTML_CONTENT_TAGS = new Set([
  "html",
  "script",
  "style",
  "title",
  "textarea",
  "xmp",
  "iframe",
  "noembed",
  "noframes",
  "pre",
  "code",
  "samp",
  "kbd",
])
const LINK_START = /[[\s(<"'“‘：；，。！？、（【]/
const LINK_END = /[\s<>"'`\\，。；！？、（）【】“”‘’]/
const SELECT_LINK_PROSE =
  /^\s*select(?:\s+[\w,]+)+\s+from\s+(?:\[[^\]\r\n]+\]\([ \t]*<?|<)https:\/\//i

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

function isTypedContent(item: Record<string, unknown>) {
  const type = typeof item.type === "string" ? item.type : ""
  return (
    MEDIA_TYPES.has(type) ||
    CODE_TYPES.test(type) ||
    (MIME_FIELDS.some((name) => typeof item[name] === "string") &&
      MEDIA_FIELDS.some((name) => typeof item[name] === "string")) ||
    (MEDIA_SOURCE_TYPES.has(type) &&
      isRecord(item.source) &&
      typeof item.source.media_type === "string" &&
      typeof (item.source.data ?? item.source.url) === "string")
  )
}

function linkEnd(text: string, start: number): number {
  let parentheses = 0
  let brackets = 0
  let braces = 0
  let end = start + "https://".length
  for (; end < text.length; end++) {
    const character = text[end]
    const code = text.charCodeAt(end)
    if (code < 32 || code === 127 || LINK_END.test(character)) break
    if (character === "(") parentheses++
    if (character === "[") brackets++
    if (character === "{") braces++
    if (character === ")" && --parentheses < 0) break
    if (character === "]" && --brackets < 0) break
    if (character === "}" && --braces < 0) break
  }
  return end
}

function endOfTag(text: string, start: number) {
  let quote = ""
  for (let end = start; end < text.length; end++) {
    const character = text[end]
    if (quote) {
      if (character === quote) quote = ""
    } else if (character === ">" || character === "<") {
      return end + Number(character === ">")
    } else if (character === '"' || character === "'") {
      quote = character
    }
  }
  return text.length
}

function* linksInText(
  text: string
): Generator<{ source: string; offset: number }> {
  if (!/https:\/\//i.test(text)) return
  const language = inferTraceCodeLanguage(text)
  // An unquoted Markdown link in the FROM position is evidence of prose.
  const selectLinkProse = language === "sql" && SELECT_LINK_PROSE.test(text)
  if (
    (language && !selectLinkProse) ||
    /^\s*(?:#!\/|(?:curl|wget)\s)/.test(text)
  )
    return

  const tag = /<(\/?)([a-z][\w:-]*)(?=[\s/>])|<!doctype\b/iy
  const quote = /(?: {0,3}>[ \t]?)+/y
  const list = / {0,3}(?:[-*+]|\d{1,9}[.)])[ \t]+/y
  const fence = / {0,3}(`{3,}|~{3,})([^\r\n]*)/y
  const indent = / {4}| {0,3}\t/y
  const line = /[^\r\n]*/y
  const imageLabels: boolean[] = []
  let imageLabelDepth = 0
  let inlineMarkerLength = 0
  let openFence: { marker: string; quoted: boolean } | undefined

  for (let index = 0; index < text.length; ) {
    const character = text[index]
    if (index === 0 || text[index - 1] === "\n" || text[index - 1] === "\r") {
      quote.lastIndex = index
      const quoted = quote.test(text)
      const start = quoted ? quote.lastIndex : index
      list.lastIndex = start
      const listed = list.test(text)
      fence.lastIndex = listed ? list.lastIndex : start
      const marker = fence.exec(text)
      indent.lastIndex = start

      if (openFence?.quoted && !quoted) openFence = undefined
      if (openFence) {
        const closing =
          marker &&
          !listed &&
          marker[1][0] === openFence.marker[0] &&
          marker[1].length >= openFence.marker.length &&
          !marker[2].trim()
        if (closing) openFence = undefined
      } else if (marker && (marker[1][0] === "~" || !marker[2].includes("`"))) {
        openFence = { marker: marker[1], quoted }
        inlineMarkerLength = 0
      }

      if (openFence || marker || (!inlineMarkerLength && indent.test(text))) {
        line.lastIndex = index
        line.test(text)
        index = line.lastIndex
        if (text[index] === "\r") index++
        if (text[index] === "\n") index++
        continue
      }
    }
    if (character === "`") {
      const start = index
      while (text[index] === "`") index++
      const mark = index - start
      if (!inlineMarkerLength) inlineMarkerLength = mark
      else if (inlineMarkerLength === mark) inlineMarkerLength = 0
      continue
    }
    if (inlineMarkerLength) {
      index++
      continue
    }
    if (character === "<") {
      if (text.startsWith("<!--", index)) {
        const end = text.indexOf("-->", index + 4)
        index = end < 0 ? text.length : end + 3
        continue
      }
      if (text.slice(index + 1, index + 9).toLowerCase() !== "https://") {
        tag.lastIndex = index
        const markup = tag.exec(text)
        if (markup) {
          index = endOfTag(text, tag.lastIndex)
          const name = markup[2]?.toLowerCase()
          if (!markup[1] && name && HTML_CONTENT_TAGS.has(name)) {
            const close = new RegExp(`</${name}[\\t\\n\\f\\r ]*>`, "gi")
            close.lastIndex = index
            index = close.test(text) ? close.lastIndex : text.length
          }
          continue
        }
      }
    }
    if (character === "[") {
      const image = text[index - 1] === "!"
      imageLabels.push(image)
      if (image) imageLabelDepth++
    }
    if (character === "]" && imageLabels.pop()) {
      imageLabelDepth--
      if (text[index + 1] === "(") {
        // Image destinations are resources of the recorded Markdown, not preview links.
        let nest = 1
        for (index += 2; index < text.length && nest; index++) {
          const part = text[index]
          if (part === "\r" || part === "\n") break
          if (part === "\\") index++
          if (part === "(") nest++
          if (part === ")") nest--
        }
        continue
      }
    }
    if (
      (character === "h" || character === "H") &&
      !imageLabelDepth &&
      (index === 0 || LINK_START.test(text[index - 1])) &&
      text.slice(index, index + 8).toLowerCase() === "https://"
    ) {
      const end = linkEnd(text, index)
      if (text[end] !== "\\") {
        let candidate = text.slice(index, end)
        const opening = text[index - 1]
        if (opening !== "<" && opening !== "(" && !/[?#]/.test(candidate)) {
          let tail = candidate.length
          while (tail > 0 && /[.,;!:]/.test(candidate[tail - 1])) tail--
          candidate = candidate.slice(0, tail)
        }
        if (isTraceHtmlPreviewLink(candidate)) {
          yield { source: candidate, offset: index }
        }
      }
      index = end
      continue
    }
    index++
  }
}

function textPart(item: unknown) {
  if (
    !isRecord(item) ||
    (item.type !== "text" &&
      item.type !== "input_text" &&
      item.type !== "output_text")
  )
    return
  if (typeof item.text === "string") return { key: "text", text: item.text }
  if (typeof item.content === "string")
    return { key: "content", text: item.content }
}

/** Discover click-to-preview HTML links in recorded IO without loading any URL. */
export function findTraceHtmlLinks(value: unknown): TraceHtmlLink[] {
  const links: TraceHtmlLink[] = []
  const seen = new Set<string>()
  const visited = new WeakSet<object>()
  let remaining = MAX_SCAN_LENGTH
  let visits = 0
  const hasBudget = () =>
    visits < MAX_VISITS && remaining > 0 && links.length < MAX_LINKS

  function addLinks(
    item: string,
    path: string,
    depth: number,
    joinParts: boolean,
    fragments: TextFragment[] = []
  ) {
    const trimmed = item.trimStart()
    if (/^(?:\{\s*"|\[\s*["{[])/.test(trimmed)) {
      try {
        visit(JSON.parse(trimmed), path, depth + 1, joinParts)
      } catch {
        // Partial serialized input has no trustworthy field boundaries.
      }
      return
    }
    if (item.length > TRACE_CODE_SCAN_LIMIT) return
    let part = 0
    for (const { source, offset } of linksInText(item)) {
      if (seen.has(source)) continue
      while (fragments[part + 1]?.offset <= offset) part++
      seen.add(source)
      links.push({ source, path: fragments[part]?.path ?? path })
      if (links.length === MAX_LINKS) break
    }
  }

  function visitParts(
    items: unknown[],
    path: string,
    depth: number,
    joinParts: boolean
  ) {
    const fragments: TextFragment[] = []
    let length = 0
    function flush() {
      if (fragments.length && length <= TRACE_CODE_SCAN_LIMIT) {
        addLinks(
          fragments.map(({ text }) => text).join(""),
          fragments[0].path,
          depth + 1,
          false,
          fragments
        )
      }
      fragments.length = 0
      length = 0
    }
    let index = 0
    for (; index < items.length && hasBudget(); index++) {
      const item = items[index]
      const part = joinParts ? textPart(item) : undefined
      const itemPath = `${path}[${index}]`
      if (!part) {
        flush()
        visit(item, itemPath, depth + 1)
        continue
      }
      visits++
      if (part.text.length > remaining) {
        length = TRACE_CODE_SCAN_LIMIT + 1
        continue
      }
      remaining -= part.text.length
      const offset = length
      length += part.text.length
      if (length <= TRACE_CODE_SCAN_LIMIT) {
        // Adjacent text deltas can split both the URL and its surrounding code
        // markers. Scan them as one value and never join across other blocks.
        fragments.push({
          text: part.text,
          path: `${itemPath}.${part.key}`,
          offset,
        })
      }
    }
    if (index === items.length) flush()
  }

  function visit(
    item: unknown,
    path: string,
    depth: number,
    joinParts = false
  ) {
    if (!hasBudget() || depth > 16) return
    visits++
    if (typeof item === "string") {
      if (item.length > remaining) return
      remaining -= item.length
      addLinks(item, path, depth, joinParts)
    } else if (
      depth < 16 &&
      item &&
      typeof item === "object" &&
      !visited.has(item)
    ) {
      visited.add(item)
      if (Array.isArray(item)) {
        visitParts(item, path, depth, joinParts)
      } else if (isRecord(item) && !isTypedContent(item)) {
        for (const key in item) {
          if (Object.hasOwn(item, key)) {
            if (!hasBudget()) break
            const field = key.replace(/[_-]/g, "")
            if (
              key.length > remaining ||
              isTraceCodeField(field) ||
              LITERAL_FIELDS.test(field)
            ) {
              visits++
              continue
            }
            remaining -= key.length
            const childPath = /^[\w-]+$/.test(key)
              ? `${path ? path + "." : ""}${key}`
              : `${path}[${JSON.stringify(key)}]`
            visit(
              item[key],
              childPath,
              depth + 1,
              key === "content" || key === "parts"
            )
          }
        }
      }
    }
  }

  visit(value, "", 0, true)
  return links
}
