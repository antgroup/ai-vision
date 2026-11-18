import assert from "node:assert/strict"
import { test } from "node:test"
import { readFileSync } from "node:fs"
import ts from "typescript"

// Load the projection with its privacy dependency, as the DOM harness does.
const source = readFileSync(
  new URL(
    "../src/components/traces/shared/traceMediaProjection.ts",
    import.meta.url
  ),
  "utf8"
)
const js = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText
const module = {
  exports: {} as { traceMediaParts: (value: unknown) => unknown },
}
new Function("module", "exports", "require", js)(
  module,
  module.exports,
  () => ({ hasPrivacyMarker: (v: string) => /\[REDACTED/.test(v) })
)
const project = module.exports.traceMediaParts

test("explicit media preserves order and rejects filtered, invalid, unsupported and oversized data", () => {
  const result = project([
    { type: "text", text: "first" },
    { type: "input_image", image_url: "https://example.test/a" },
    { type: "input_audio", input_audio: { data: "broken!", format: "wav" } },
    {
      type: "document",
      source: {
        media_type: "application/pdf",
        data: "[FILTERED_INLINE_IMAGE]",
      },
    },
    { type: "document", source: { media_type: "application/x-unknown", data: "AAAA" } },
    {
      type: "document",
      source: {
        media_type: "application/pdf",
        data: "A".repeat(4 * 1024 * 1024 + 4),
      },
    },
    { type: "text", text: "last" },
  ]) as Array<{ kind: string; reason?: string }>
  assert.deepEqual(
    result.map((v) => v.kind),
    ["text", "media", "invalid", "invalid", "invalid", "invalid", "text"]
  )
  assert.deepEqual(
    result.slice(2, 6).map((v) => v.reason),
    ["invalid", "filtered", "unsupported", "budget"]
  )
  assert.equal(project("SGVsbG8="), null)
  assert.equal(project({ arbitrary: "data:image/png;base64,AAAA" }), null)
})

test("SDK private media references remain unavailable and are never loaded", () => {
  const result = project([
    {
      type: "image_url",
      image_url: {
        url: "@@@langfuseMedia:type=image/jpeg|id=example|source=base64_data_uri@@@",
      },
    },
  ]) as Array<{ kind: string; reason?: string }>
  assert.deepEqual(result, [{ kind: "invalid", reason: "externalReference" }])
})

test("large valid inline media reports its budget without exhausting the call stack", () => {
  const encoded = "A".repeat(7 * 1024 * 1024)
  assert.deepEqual(project({ type: "image", mime_type: "image/png", data: encoded }), [
    { kind: "invalid", reason: "budget" },
  ])
  assert.deepEqual(project(`data:image/png;base64,${encoded}`), [
    { kind: "invalid", reason: "budget" },
  ])
})

test("typed tool output URLs are recognized without guessing untyped URLs", () => {
  const value = {
    summary: "Generated image",
    imageUrl: "https://example.invalid/image",
    mimeType: "image/png",
    width: 512,
  }
  assert.deepEqual(project(value), [
    { kind: "media", mime: "image/png", source: value.imageUrl },
  ])
  assert.equal(project({ imageUrl: value.imageUrl }), null)
  assert.deepEqual(
    project({ imageUrl: value.imageUrl, mimeType: "image/svg+xml" }),
    [{ kind: "media", mime: "image/svg+xml", source: value.imageUrl }]
  )
})

test("typed input and output fields share the same media types", () => {
  const samples = [
    ["imageUrl", "image/png"],
    ["audioUrl", "audio/wav"],
    ["videoUrl", "video/webm"],
    ["fileUrl", "application/pdf"],
    ["url", "text/plain"],
    ["url", "text/markdown"],
  ] as const
  for (const [field, mime] of samples) {
    const source = "https://example.invalid/resource"
    assert.deepEqual(project({ [field]: source, mimeType: mime }), [
      { kind: "media", mime, source },
    ])
    assert.deepEqual(
      project({ type: "file", source: { url: source, media_type: mime } }),
      [{ kind: "media", mime, source }]
    )
  }
})

test("HTML file bodies and declared URLs reach the static preview path", () => {
  const mime = "text/html"
  const data = Buffer.from("<h1>Example</h1>").toString("base64")
  const source = "https://example.invalid/report.html"
  assert.deepEqual(
    project([
      { type: "file", source: { media_type: mime, data } },
      { type: "file", mime_type: mime, url: source },
    ]),
    [
      { kind: "media", mime, source: `data:${mime};base64,${data}` },
      { kind: "media", mime, source },
    ]
  )
  assert.deepEqual(
    project({ type: "aivision_attachment", mimeType: mime, id: "b".repeat(64) }),
    [{ kind: "invalid", reason: "unsupported" }]
  )
})

test("server-side unavailable media renders an error without parsing source Base64", () => {
  assert.deepEqual(project([{ type: "aivision_media_error", reason: "budget" }]), [{ kind: "invalid", reason: "budget" }])
  assert.deepEqual(project([{ type: "aivision_media_error", reason: "invalid" }]), [{ kind: "invalid", reason: "invalid" }])
})
