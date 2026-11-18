import { hasPrivacyMarker } from "@/labs/privacy/privacyMarkers"

export type TraceMediaPart =
  | { kind: "text"; text: string }
  | { kind: "media"; mime: string; source: string; sha256?: string }
  | { kind: "unknown"; value: unknown }
  | {
      kind: "invalid"
      reason:
        "filtered" | "invalid" | "budget" | "unsupported" | "externalReference"
    }

const supported =
  /^(image\/[a-z0-9.+-]+|audio\/[a-z0-9.+-]+|video\/[a-z0-9.+-]+|application\/(pdf|json)|text\/(plain|markdown|html))$/i
const maxInline = 4 * 1024 * 1024
// Avoid nested repetition: V8 can exhaust its call stack on large Base64 input.
const validBase64 = (value: string) =>
  value.length % 4 === 0 &&
  /^[A-Za-z0-9+/]*={0,2}$/.test(value) &&
  !/=/.test(value.slice(0, -2))
const obj = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v)

/** Only explicit SDK blocks and typed data are interpreted. Unknown blocks stay visible. */
export function traceMediaParts(value: unknown): TraceMediaPart[] | null {
  const blocks = Array.isArray(value) ? value : [value]
  let found = false
  const parts = blocks.map((block): TraceMediaPart => {
    if (typeof block === "string") {
      const data = /^data:([^;,]+);base64,([\s\S]*)$/i.exec(block)
      if (!data || !supported.test(data[1]))
        return { kind: "text", text: block }
      found = true
      if (block.length > maxInline || !validBase64(data[2]))
        return {
          kind: "invalid",
          reason: block.length > maxInline ? "budget" : "invalid",
        }
      return { kind: "media", mime: data[1], source: block }
    }
    if (!obj(block)) return { kind: "unknown", value: block }
    if (
      ["text", "input_text", "output_text"].includes(String(block.type)) &&
      typeof block.text === "string"
    )
      return { kind: "text", text: block.text }
    const type = String(block.type ?? "")
    if (type === "aivision_media_error") {
      found = true
      return {
        kind: "invalid",
        reason:
          block.reason === "budget"
            ? "budget"
            : block.reason === "filtered"
              ? "filtered"
              : "invalid",
      }
    }
    let source: unknown
    let mime: unknown
    if (
      type === "aivision_attachment" &&
      typeof block.id === "string" &&
      /^(?:[0-9a-f]{64}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i.test(
        block.id
      )
    ) {
      source = `aivision-attachment:${block.id}`
      if (
        typeof block.spaceId === "string" &&
        typeof block.agentId === "string"
      ) {
        source += `?${new URLSearchParams({ space_id: block.spaceId, agent_uuid: block.agentId })}`
      }
      mime = block.mimeType
    } else if (["image_url", "audio_url", "video_url"].includes(type)) {
      const value = block[type]
      source = obj(value) ? value.url : value
      mime =
        type === "image_url"
          ? "image/png"
          : type === "audio_url"
            ? "audio/mpeg"
            : "video/mp4"
    } else if (type === "input_image") {
      source = block.image_url
      mime = "image/png"
    } else if (type === "input_audio" && obj(block.input_audio)) {
      source = block.input_audio.data
      mime =
        block.input_audio.format === "mp3"
          ? "audio/mpeg"
          : block.input_audio.format === "wav"
            ? "audio/wav"
            : undefined
    } else if (type === "base64") {
      source = block.data
      mime = block.media_type ?? block.mime_type
    } else if (
      [block.mimeType, block.mime_type, block.media_type].some(
        (value) => typeof value === "string"
      ) &&
      [
        block.url,
        block.imageUrl,
        block.audioUrl,
        block.videoUrl,
        block.fileUrl,
        block.data,
      ].some((value) => typeof value === "string")
    ) {
      source = [
        block.url,
        block.imageUrl,
        block.audioUrl,
        block.videoUrl,
        block.fileUrl,
        block.data,
      ].find((value) => typeof value === "string")
      mime = [block.mimeType, block.mime_type, block.media_type].find(
        (value) => typeof value === "string"
      )
    } else if (
      ["image", "audio", "video", "document", "file", "text"].includes(type) &&
      obj(block.source)
    ) {
      source = block.source.data ?? block.source.url
      mime = block.source.media_type
    }
    if (typeof source !== "string") return { kind: "unknown", value: block }
    if (hasPrivacyMarker(source) || source.includes("[FILTERED_")) {
      found = true
      return { kind: "invalid", reason: "filtered" }
    }
    if (/^@@@langfuseMedia:.*@@@$/.test(source)) {
      found = true
      return { kind: "invalid", reason: "externalReference" }
    }
    if (source.startsWith("aivision-attachment:")) {
      found = true
      // The native attachment service does not accept the active HTML MIME.
      return typeof mime === "string" &&
        mime.toLowerCase() !== "text/html" &&
        supported.test(mime)
        ? {
            kind: "media",
            mime,
            source,
            ...(typeof block.sha256 === "string" &&
            /^[0-9a-f]{64}$/i.test(block.sha256)
              ? { sha256: block.sha256.toLowerCase() }
              : {}),
          }
        : { kind: "invalid", reason: "unsupported" }
    }
    const data = /^data:([^;,]+);base64,([\s\S]*)$/i.exec(source)
    if (data) mime = data[1]
    if (typeof mime !== "string" || !supported.test(mime)) {
      found = true
      return { kind: "invalid", reason: "unsupported" }
    }
    if (
      ["image_url", "input_image"].includes(type) &&
      !data &&
      !/^https?:\/\//i.test(source)
    ) {
      found = true
      return { kind: "invalid", reason: "invalid" }
    }
    if (!data && !/^https?:\/\//i.test(source)) {
      if (source.length > maxInline || !validBase64(source)) {
        found = true
        return {
          kind: "invalid",
          reason: source.length > maxInline ? "budget" : "invalid",
        }
      }
      source = `data:${mime};base64,${source}`
    }
    if (data && (data[0].length > maxInline || !validBase64(data[2]))) {
      found = true
      return {
        kind: "invalid",
        reason: data[0].length > maxInline ? "budget" : "invalid",
      }
    }
    found = true
    return { kind: "media", mime, source: source as string }
  })
  return found ? parts : null
}

export function traceMediaKind(mime: string): string {
  if (mime.startsWith("image/")) return "image"
  if (mime.startsWith("audio/")) return "audio"
  if (mime.startsWith("video/")) return "video"
  if (mime.toLowerCase() === "text/html") return "html"
  return mime === "application/pdf" ? "pdf" : "text"
}

type MediaSummarySource = { id: string; name: string; parts: TraceMediaPart[] }

/** Collapse copies across spans, while keeping every owning source accessible. */
export function collectTraceMedia(sources: MediaSummarySource[]) {
  const entries: Array<{
    part: Extract<TraceMediaPart, { kind: "media" | "invalid" }>
    sources: Array<{ id: string; name: string }>
  }> = []
  const seen = new Map<string, number>()
  for (const source of sources) {
    for (const part of source.parts) {
      if (part.kind !== "media" && part.kind !== "invalid") continue
      // An invalid descriptor has no content identity. Never merge unrelated failures.
      const identity =
        part.kind === "media"
          ? `${part.mime.toLowerCase()}:${part.sha256 ?? part.source}`
          : null
      const index = identity === null ? undefined : seen.get(identity)
      if (index !== undefined) {
        if (!entries[index].sources.some((item) => item.id === source.id))
          entries[index].sources.push({ id: source.id, name: source.name })
      } else {
        if (identity !== null) seen.set(identity, entries.length)
        entries.push({ part, sources: [{ id: source.id, name: source.name }] })
      }
    }
  }
  return entries
}
