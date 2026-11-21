/** Bounded, strict UTF-8 decoding for Trace attachment and Markdown previews. */
export const TRACE_MEDIA_MAX_BYTES = 16 * 1024 * 1024
export const TRACE_HTML_PREVIEW_MAX_BYTES = 512 * 1024

export class TraceHtmlRequestError extends Error {}

export async function fetchTraceHtml(
  source: string,
  signal: AbortSignal,
  redirect: RequestRedirect = "follow"
): Promise<string> {
  const url = new URL(source)
  if (
    !["http:", "https:"].includes(url.protocol) ||
    url.username ||
    url.password
  ) {
    throw new Error("Unsupported HTML source")
  }
  const response = await fetch(source, {
    signal,
    credentials: "omit",
    redirect,
    referrerPolicy: "no-referrer",
  }).catch((cause: unknown) => {
    if (signal.aborted) throw cause
    throw new TraceHtmlRequestError("Could not read the HTML response", {
      cause,
    })
  })
  if (
    response.headers.get("content-type")?.split(";")[0].trim().toLowerCase() !==
    "text/html"
  ) {
    await response.body?.cancel()
    throw new Error("Unexpected HTML media type")
  }
  return readTraceMediaText(response, TRACE_HTML_PREVIEW_MAX_BYTES)
}

export async function readTraceMediaText(
  response: Response,
  maxBytes = TRACE_MEDIA_MAX_BYTES
): Promise<string> {
  if (
    !response.ok ||
    Number(response.headers.get("content-length")) > maxBytes
  ) {
    await response.body?.cancel()
    throw new Error("File unavailable or too large")
  }
  if (!response.body) throw new Error("File unavailable")
  const reader = response.body.getReader()
  const decoder = new TextDecoder("utf-8", { fatal: true })
  const chunks: string[] = []
  let size = 0
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      size += value.byteLength
      if (size > maxBytes) throw new Error("File too large")
      chunks.push(decoder.decode(value, { stream: true }))
    }
    return chunks.join("") + decoder.decode()
  } finally {
    try {
      await reader.cancel()
    } finally {
      reader.releaseLock()
    }
  }
}
