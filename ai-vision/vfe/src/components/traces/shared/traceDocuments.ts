export type TraceDocument = {
  format: "html" | "markdown"
  source: string
  path: string
}

const MAX_DOCUMENT_LENGTH = 512 * 1024
const MAX_SCAN_LENGTH = 2 * 1024 * 1024
const MAX_VISITS = 2_000
const MAX_DOCUMENTS = 8

type HtmlTag = {
  name: string
  closing: boolean
  start: number
  attributesStart: number
  end: number
}

const rawTextElements = new Set([
  "script",
  "style",
  "title",
  "textarea",
  "xmp",
  "iframe",
  "noembed",
  "noframes",
])

function* htmlTags(
  text: string,
  offset = 0,
  includeAllTags = false
): Generator<HtmlTag> {
  // Skip ordinary tags without attributes when finding document boundaries.
  // Other tags still need scanning because their attributes can contain ">".
  const pattern = includeAllTags
    ? /<!--|<!doctype[ \t\r\n\f]+html(?=[\s>])|<(\/?)([a-z][\w:-]*)(?=[\s/>])/gi
    : /<!--|<!doctype[ \t\r\n\f]+html(?=[\s>])|<(\/?)(html|body|script|style|title|textarea|xmp|iframe|noembed|noframes)(?=[\s/>])|<\/?[a-z][\w:-]*[ \t\r\n\f]/gi
  const rawTextClose = /<\/([a-z]+)[ \t\r\n\f]*>/gi
  pattern.lastIndex = offset
  for (let match = pattern.exec(text); match; match = pattern.exec(text)) {
    const start = match.index
    if (match[0] === "<!--") {
      const close = text.indexOf("-->", start + 4)
      if (close < 0) return
      pattern.lastIndex = close + 3
      continue
    }

    const attributesStart = pattern.lastIndex
    let cursor = attributesStart
    let quote = ""
    while (cursor < text.length) {
      const character = text[cursor]
      if (quote) {
        if (character === quote) quote = ""
      } else if (character === '"' || character === "'") {
        quote = character
      } else if (character === ">" || character === "<") {
        break
      }
      cursor++
    }
    pattern.lastIndex = cursor
    if (text[cursor] !== ">") continue
    pattern.lastIndex++
    const name =
      match[2]?.toLowerCase() ?? (match[0][1] === "!" ? "!doctype" : undefined)
    if (!name) continue
    const closing = match[1] === "/"
    yield { name, closing, start, attributesStart, end: cursor + 1 }

    // Comments, attribute quotes, and raw-text contents cannot end a document.
    if (!closing && rawTextElements.has(name)) {
      rawTextClose.lastIndex = pattern.lastIndex
      let close: RegExpExecArray | null
      while ((close = rawTextClose.exec(text))) {
        if (close[1].toLowerCase() === name) break
      }
      if (!close) return
      pattern.lastIndex = rawTextClose.lastIndex
    }
  }
}

function nextHtmlDocumentStart(text: string, tags: Generator<HtmlTag>) {
  let doctype: HtmlTag | undefined
  for (let next = tags.next(); !next.done; next = tags.next()) {
    const tag = next.value
    if (tag.name === "html" && !tag.closing) {
      const start =
        doctype && !text.slice(doctype.end, tag.start).trim()
          ? doctype.start
          : tag.start
      return { tag, start }
    }
    doctype = tag.name === "!doctype" ? tag : undefined
  }
}

function readHtmlDocument(text: string, tags: Generator<HtmlTag>) {
  let body: HtmlTag | undefined
  for (let next = tags.next(); !next.done; next = tags.next()) {
    const tag = next.value
    if (!tag.closing && (tag.name === "html" || tag.name === "!doctype"))
      return { body, end: tag.start, closed: false }
    if (tag.name === "body" && !tag.closing) body ??= tag
    if (
      tag.name === "html" &&
      tag.closing &&
      !text.slice(tag.attributesStart, tag.end - 1).trim()
    )
      return { body, end: tag.end, closed: true }
  }
  return { body, end: text.length, closed: false }
}

/** Return recorded root attributes, including when capture stops before EOF. */
export function traceHtmlRootAttributes(source: string) {
  const tags = htmlTags(source)
  const start = nextHtmlDocumentStart(source, tags)
  if (!start || source.slice(0, start.start).trim()) return null
  const { body, end } = readHtmlDocument(source, tags)
  if (source.slice(end).trim()) return null
  const attributes = (tag: HtmlTag) =>
    source.slice(tag.attributesStart, tag.end - 1)
  return {
    html: attributes(start.tag),
    body: body ? attributes(body) : "",
  }
}

type DocumentMatch = Omit<TraceDocument, "path"> & {
  start: number
  unfenced?: true
  partial?: true
}

const standaloneHtml =
  /^\s*<(?:!doctype\s+html|article|aside|body|blockquote|div|figure|h[1-6]|header|main|ol|p|pre|section|table|ul)(?=[\s>])/i
const markdownHeading = /^#{1,6}[ \t]+\S[^\r\n]*(?:\r?\n|$)/
const markdownTableHeader = /^\|[^\r\n]+\|\r?\n\|[ \t:|-]*-[ \t:|-]*\|/m

function* documentsInText(text: string): Generator<DocumentMatch> {
  const fences = /^ {0,3}(`{3,}|~{3,})([^\r\n]*)\r?$/gm
  function nextOpeningFence() {
    let match: RegExpExecArray | null
    while ((match = fences.exec(text))) {
      const [, marker, info] = match
      if (marker[0] !== "`" || !info.includes("`")) return match
    }
    return null
  }

  let offset = 0
  let fence = nextOpeningFence()
  let hasFence = false
  let hasDocument = false
  // Complete roots may contain fence-like text. Bound retries across incomplete
  // roots so repeated lookahead cannot repeatedly scan a large suffix.
  let lookahead = 2 * text.length
  while (offset < text.length) {
    const segment = text.slice(offset, fence?.index)
    const html = nextHtmlDocumentStart(segment, htmlTags(segment))
    if (html) {
      const bodyStart = offset + html.tag.end
      let end = fence?.index ?? text.length
      let closed = false
      if (text.length - bodyStart <= lookahead) {
        const document = readHtmlDocument(text, htmlTags(text, bodyStart))
        lookahead -= document.end - bodyStart
        closed = document.closed
        end = closed ? document.end : Math.min(document.end, end)
      }
      const prefix = segment.slice(0, html.start)
      const startsDocument =
        html.start !== html.tag.start ||
        !prefix.trim() ||
        /^ {0,3}$/.test(prefix.slice(prefix.lastIndexOf("\n") + 1))
      if (closed || startsDocument) {
        hasDocument = true
        const start = offset + html.start
        yield {
          format: "html",
          source: text.slice(start, end),
          start,
          partial: closed ? undefined : true,
        }
        offset = end
        if (fence && fence.index < offset) {
          fences.lastIndex = offset
          fence = nextOpeningFence()
        }
        continue
      }
    }
    // Strong block markup at the start of a value can be a fragment; embedded
    // markup in ordinary prose is not enough to identify another document.
    if (standaloneHtml.test(segment)) {
      for (const tag of htmlTags(segment, 0, true)) {
        if (!tag.closing && tag.name !== "!doctype") {
          hasDocument = true
          yield {
            format: "html",
            source: segment,
            start: offset,
            partial: true,
          }
          break
        }
      }
    }
    if (!fence) break

    hasFence = true
    const [, marker, info] = fence
    const language = info.trim().split(/\s+/, 1)[0].toLowerCase()
    const start = fences.lastIndex + 1
    let close: RegExpExecArray | null
    while ((close = fences.exec(text))) {
      const [, endMarker, endInfo] = close
      if (
        endMarker[0] === marker[0] &&
        endMarker.length >= marker.length &&
        !endInfo.trim()
      )
        break
    }
    const source = text.slice(start, close?.index)
    if (language === "html") {
      for (const tag of htmlTags(source, 0, true)) {
        if (tag.closing || tag.name === "!doctype") continue
        hasDocument = true
        yield {
          format: "html",
          source,
          start,
          partial: close ? undefined : true,
        }
        break
      }
    } else if (["md", "markdown"].includes(language) && source.trim()) {
      hasDocument = true
      yield {
        format: "markdown",
        source,
        start,
        partial: close ? undefined : true,
      }
    }

    if (!close) break
    offset = fences.lastIndex
    fence = nextOpeningFence()
  }

  if (hasDocument) return
  // Unfenced Markdown is ambiguous. Look for a document heading with a body,
  // front matter followed by content, or a GFM table with at least one data row.
  const trimmed = text.trim()
  const hasBody =
    /^(?:#{1,6}[ \t]+\S[^\r\n]*\r?\n(?:[ \t]*\r?\n)*\S|---\r?\n[\s\S]{1,16384}?\r?\n---\r?\n\s*\S)/.test(
      trimmed
    ) ||
    (!hasFence &&
      /^\|[^\r\n]+\|\r?\n\|[ \t:|-]+\|\r?\n\|[^\r\n]+\|/m.test(trimmed))
  if (
    hasBody ||
    markdownHeading.test(trimmed) ||
    (!hasFence && markdownTableHeader.test(trimmed))
  )
    yield {
      format: "markdown",
      source: text,
      start: 0,
      unfenced: true,
      partial: hasBody ? undefined : true,
    }
}

type TextFragment = {
  key: "text" | "content"
  text: string
  visitOrder?: number
}

type VisitedTextFragment = {
  text: string
  start: number
  visitOrder: number
}

function traceTextPart(value: unknown): TextFragment | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return
  const part = value as Record<string, unknown>
  if (
    part.type !== "text" &&
    part.type !== "input_text" &&
    part.type !== "output_text"
  )
    return
  if (typeof part.text === "string") return { key: "text", text: part.text }
  if (typeof part.content === "string")
    return { key: "content", text: part.content }
}

type FoundDocument = TraceDocument & {
  visitOrder: number
  offset: number
  partial?: true
}

type SourcePosition = Pick<FoundDocument, "visitOrder" | "offset">

function compareDocumentOrder(left: SourcePosition, right: SourcePosition) {
  return left.visitOrder - right.visitOrder || left.offset - right.offset
}

/** Read received IO only. Deduplicate repeated message/tool payloads and bound discovery. */
export function findTraceDocuments(value: unknown): TraceDocument[] {
  const documents: FoundDocument[] = []
  let remaining = MAX_SCAN_LENGTH
  let visits = 0
  const hasBudget = () =>
    visits < MAX_VISITS && remaining > 0 && documents.length < MAX_DOCUMENTS

  function addDocuments(
    text: string,
    path: string,
    fragments?: VisitedTextFragment[]
  ) {
    let partIndex = 0
    for (const { format, source, start, unfenced, partial } of documentsInText(
      text
    )) {
      let visitOrder = visits
      let offset = start
      if (fragments) {
        while (fragments[partIndex + 1]?.start <= start) partIndex++
        const next = fragments[partIndex + 1]
        if (!next || next.start >= start + source.length) continue
        // Unfenced Markdown has no hard end marker. If a part is already a
        // Markdown document, keep its own preview instead of appending siblings.
        if (
          unfenced &&
          fragments.some(({ text }) => {
            for (const doc of documentsInText(text)) {
              if (doc.format === "markdown" && !doc.partial) return true
            }
            return false
          })
        )
          continue
        const part = fragments[partIndex]
        visitOrder = part.visitOrder
        offset -= part.start
        const end = start + source.length
        let last = partIndex + 1
        while (fragments[last + 1]?.start < end) last++
        const through = {
          visitOrder: fragments[last].visitOrder,
          offset: end - fragments[last].start,
        }
        const from = { visitOrder, offset }
        for (let index = documents.length - 1; index >= 0; index--) {
          const seen = documents[index]
          if (
            seen.partial &&
            seen.format === format &&
            compareDocumentOrder(seen, from) >= 0 &&
            compareDocumentOrder(
              {
                visitOrder: seen.visitOrder,
                offset: seen.offset + seen.source.length,
              },
              through
            ) <= 0
          )
            documents.splice(index, 1)
        }
      }
      const found = { format, source, path, visitOrder, offset, partial }
      if (
        documents.length >= MAX_DOCUMENTS &&
        compareDocumentOrder(found, documents[MAX_DOCUMENTS - 1]) >= 0
      )
        break
      const duplicate = documents.findIndex(
        (seen) => seen.format === format && seen.source === source
      )
      if (duplicate >= 0) {
        if (compareDocumentOrder(found, documents[duplicate]) >= 0) continue
        documents.splice(duplicate, 1)
      }
      documents.push(found)
      documents.sort(compareDocumentOrder)
      if (documents.length > MAX_DOCUMENTS) documents.pop()
    }
  }

  function visitParts(
    parts: unknown[],
    path: string,
    depth: number,
    canJoinText: boolean
  ) {
    const fragments: VisitedTextFragment[] = []
    let length = 0

    function flush() {
      if (fragments.length > 1 && length <= remaining) {
        remaining -= length
        addDocuments(
          fragments.map(({ text }) => text).join(""),
          path,
          fragments
        )
      }
      fragments.length = 0
      length = 0
    }

    for (let index = 0; index < parts.length && hasBudget(); index++) {
      const part = parts[index]
      const fragment = canJoinText ? traceTextPart(part) : undefined
      // Never join across media, tool calls, or separate messages.
      if (!fragment) flush()
      visit(part, `${path}[${index}]`, depth + 1, false, fragment)
      if (
        fragment?.visitOrder &&
        fragment.text &&
        length <= MAX_DOCUMENT_LENGTH
      ) {
        const start = length
        length += fragment.text.length
        if (length > MAX_DOCUMENT_LENGTH) {
          fragments.length = 0
        } else {
          fragments.push({
            text: fragment.text,
            start,
            visitOrder: fragment.visitOrder,
          })
        }
      }
    }
    flush()
  }

  function visit(
    item: unknown,
    path: string,
    depth: number,
    canJoinText: boolean,
    fragment?: TextFragment
  ) {
    if (!hasBudget() || depth > 16) return
    visits++
    if (typeof item === "string") {
      if (item.length > remaining) return
      remaining -= item.length
      const trimmed = item.trimStart()
      if (/^(?:\{\s*"|\[\s*["{[])/.test(trimmed)) {
        // Only read child strings. Number formatting in this display-only
        // traversal does not affect the original IO or its copy action.
        try {
          visit(JSON.parse(trimmed), path, depth + 1, canJoinText)
        } catch {
          // A partial JSON envelope cannot establish document boundaries.
        }
        return
      }
      if (item.length <= MAX_DOCUMENT_LENGTH) addDocuments(item, path)
    } else if (depth < 16 && Array.isArray(item)) {
      visitParts(item, path, depth, canJoinText)
    } else if (depth < 16 && item && typeof item === "object") {
      for (const key of Object.keys(item)) {
        if (!hasBudget()) break
        const child = (item as Record<string, unknown>)[key]
        const childPath = /^[\w-]+$/.test(key)
          ? `${path ? path + "." : ""}${key}`
          : `${path}[${JSON.stringify(key)}]`
        if (fragment && key === fragment.key) fragment.visitOrder = visits + 1
        visit(child, childPath, depth + 1, key === "content" || key === "parts")
      }
    }
  }

  visit(value, "", 0, true)
  return documents.map(({ format, source, path }) => ({ format, source, path }))
}
