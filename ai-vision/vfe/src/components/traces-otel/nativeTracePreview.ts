import type { NativeTraceIOPreview } from "./types"

/** Missing or unrecognized contracts retain the legacy canonical IO request. */
export function getNativeTraceIOPreview(
  value: unknown
): NativeTraceIOPreview | null {
  if (!value || typeof value !== "object") return null
  const preview = value as Record<string, unknown>
  if (
    preview.version !== "native-trace-preview-v1" ||
    preview.complete !== true ||
    typeof preview.rootObservationId !== "string" ||
    !preview.rootObservationId.trim() ||
    (preview.input !== null && typeof preview.input !== "string") ||
    (preview.output !== null && typeof preview.output !== "string") ||
    typeof preview.inputTruncated !== "boolean" ||
    typeof preview.outputTruncated !== "boolean"
  )
    return null
  return value as NativeTraceIOPreview
}

/** Session previews preserve explicit null/empty values and never power search. */
export function getNativeSessionIOPreview(value: unknown): {
  input: string | null
  output: string | null
} | null {
  if (!value || typeof value !== "object") return null
  const preview = value as Record<string, unknown>
  if (
    preview.version !== "native-session-preview-v1" ||
    preview.complete !== true ||
    (preview.input !== null && typeof preview.input !== "string") ||
    (preview.output !== null && typeof preview.output !== "string") ||
    typeof preview.inputTruncated !== "boolean" ||
    typeof preview.outputTruncated !== "boolean" ||
    [preview.input, preview.output].some(
      (text) => typeof text === "string" && Array.from(text).length > 1024
    )
  )
    return null
  return {
    input: preview.input as string | null,
    output: preview.output as string | null,
  }
}
