import assert from "node:assert/strict"
import { test } from "node:test"
import {
  readTraceMediaText,
  TRACE_MEDIA_MAX_BYTES,
} from "../src/components/traces/shared/traceMediaText.ts"

function streamed(chunks: Uint8Array[]) {
  let cancelled = false
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      const chunk = chunks.shift()
      if (chunk) controller.enqueue(chunk)
      else controller.close()
    },
    cancel() {
      cancelled = true
    },
  })
  return { response: new Response(body), body, cancelled: () => cancelled }
}

test("decodes Chinese UTF-8 split across network chunks", async () => {
  const bytes = new TextEncoder().encode("你好 Markdown")
  const { response, body } = streamed([
    bytes.slice(0, 1),
    bytes.slice(1, 4),
    bytes.slice(4),
  ])
  assert.equal(await readTraceMediaText(response), "你好 Markdown")
  assert.equal(body.locked, false)
})

test("invalid UTF-8 cancels the stream and releases its reader", async () => {
  const source = streamed([new Uint8Array([255]), new Uint8Array([1])])
  await assert.rejects(readTraceMediaText(source.response))
  assert.equal(source.cancelled(), true)
  assert.equal(source.body.locked, false)
})

test("enforces 16 MiB including responses without Content-Length", async () => {
  const exact = streamed([new Uint8Array(TRACE_MEDIA_MAX_BYTES)])
  assert.equal(
    (await readTraceMediaText(exact.response)).length,
    TRACE_MEDIA_MAX_BYTES
  )
  const large = streamed([
    new Uint8Array(TRACE_MEDIA_MAX_BYTES),
    new Uint8Array([1]),
  ])
  await assert.rejects(readTraceMediaText(large.response), /too large/)
  assert.equal(large.cancelled(), true)
  assert.equal(large.body.locked, false)
  await assert.rejects(
    readTraceMediaText(
      new Response("", {
        headers: { "content-length": String(TRACE_MEDIA_MAX_BYTES + 1) },
      })
    )
  )
  await assert.rejects(readTraceMediaText(new Response("", { status: 404 })))
})

test("an HTML preview can impose a lower byte limit on streamed content", async () => {
  const response = streamed([new Uint8Array([97, 98]), new Uint8Array([99])])
  await assert.rejects(readTraceMediaText(response.response, 2), /too large/)
  assert.equal(response.body.locked, false)
})
