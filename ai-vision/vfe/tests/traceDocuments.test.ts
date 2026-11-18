import assert from "node:assert/strict"
import { test } from "node:test"
import {
  findTraceDocuments,
  traceHtmlRootAttributes,
} from "../src/components/traces/shared/traceDocuments.ts"

test("keeps exact HTML when an attribute mentions a closing document tag", () => {
  const report =
    '<!doctype html><html><body><h1 title="Example of a closing tag: </html>">Regional report</h1><p>First quarter figures.</p></body></html>'
  const value = { params: JSON.stringify({ content: report }) }
  assert.deepEqual(findTraceDocuments(value), [
    { format: "html", path: "params.content", source: report },
  ])
})

test("keeps fenced examples inside an HTML report and finds a following Markdown document", () => {
  const html = [
    "<!doctype html><html><body>",
    "<h1>Release notes</h1><pre>Example:",
    "```sh",
    "printf hello",
    "```",
    "</pre><p>First quarter figures.</p></body></html>",
  ].join("\n")
  const markdown = "# Reading notes\n\nReview the examples.\n"
  const value = [html, "", "```markdown", markdown + "```"].join("\n")
  assert.deepEqual(findTraceDocuments(value), [
    { format: "html", path: "", source: html },
    { format: "markdown", path: "", source: markdown },
  ])
})

test("keeps a Markdown code fence and a later HTML report distinct", () => {
  const markdown = "# Reading notes\n\n    <html>\n"
  const html = "<html><body>Complete report.</body></html>"
  const value = ["```md", markdown + "```", "", html].join("\n")
  assert.deepEqual(findTraceDocuments(value), [
    { format: "markdown", path: "", source: markdown },
    { format: "html", path: "", source: html },
  ])
})

test("bounds preview discovery on a large report and keeps adjacent small documents", () => {
  const large =
    "<html><body>" + "<p>月次報告の本文</p>".repeat(40_000) + "</body></html>"
  const small = "<html><body><p>Summary</p></body></html>"
  assert.ok(large.length > 512 * 1024)
  assert.deepEqual(findTraceDocuments({ large, small }), [
    { format: "html", path: "small", source: small },
  ])
})

test("recovers HTML and body attributes from the complete report", () => {
  const html =
    '<!DOCTYPE HTML><HTML lang="ja" class="report"><head><title>Body &lt;body&gt;</title></head><BODY style="margin: 2rem" title="Quarter > month"><p>本文</p></BODY></HTML>'
  assert.deepEqual(traceHtmlRootAttributes(html), {
    html: ' lang="ja" class="report"',
    body: ' style="margin: 2rem" title="Quarter > month"',
  })
  assert.equal(traceHtmlRootAttributes("<body>Only a fragment</body>"), null)
  assert.deepEqual(
    traceHtmlRootAttributes('<html lang="ja"><body class="report">Draft'),
    { html: ' lang="ja"', body: ' class="report"' }
  )
})

test("offers partial HTML documents and standalone fragments without rewriting the source", () => {
  const html = '<!doctype html><html lang="en"><body><h1>Work in progress'
  const fragment = "<article><p>First draft"
  assert.deepEqual(findTraceDocuments({ html, fragment }), [
    { format: "html", path: "html", source: html },
    { format: "html", path: "fragment", source: fragment },
  ])
  assert.deepEqual(
    findTraceDocuments("In HTML, <article> marks a section."),
    []
  )
})

test("offers partial fenced HTML and Markdown up to the received end", () => {
  const html = "<section><h2>Overview</h2><p>Pending"
  const md = "# Draft\n\n- First item\n"
  const value = {
    html: "Report follows.\n\n```html\n" + html,
    md: "~~~markdown\r\n" + md,
  }
  assert.deepEqual(findTraceDocuments(value), [
    { format: "html", path: "html", source: html },
    { format: "markdown", path: "md", source: md },
  ])
  assert.deepEqual(findTraceDocuments("```md\n"), [])
  assert.deepEqual(findTraceDocuments("```js\nconsole.log('example')"), [])
})

test("offers a heading-only Markdown draft and a table before its first row", () => {
  const heading = "# Draft title"
  const table = "| Region | Status |\n| --- | --- |"
  assert.deepEqual(findTraceDocuments({ heading, table }), [
    { format: "markdown", path: "heading", source: heading },
    { format: "markdown", path: "table", source: table },
  ])
})

test("keeps Markdown with an indented HTML example as one preview", () => {
  const markdown = "# HTML notes\n\n    <html>\n    <head>\n"
  assert.deepEqual(findTraceDocuments(markdown), [
    { format: "markdown", path: "", source: markdown },
  ])
})

test("keeps an unfinished HTML report separate from a following document", () => {
  const partial = "<html><body><p>First draft\n\n"
  const complete = "<html><body><p>Second report</p></body></html>"
  const markdown = "# Notes\n"
  assert.deepEqual(
    findTraceDocuments(partial + complete + "\n\n```md\n" + markdown),
    [
      { format: "html", path: "", source: partial },
      { format: "html", path: "", source: complete },
      { format: "markdown", path: "", source: markdown },
    ]
  )
})

test("same-message continuation replaces covered partial previews", () => {
  const first = "<html><body>"
  const second = "<h1>Updated report"
  const third = "</h1><p>Draft details"
  const value = [first, second, third].map((text) => ({
    type: "output_text",
    text,
  }))
  assert.deepEqual(findTraceDocuments(value), [
    { format: "html", path: "", source: first + second + third },
  ])

  const parts = ["# Draft notes", "\n\n- Latest section"]
  assert.deepEqual(
    findTraceDocuments(parts.map((text) => ({ type: "output_text", text }))),
    [{ format: "markdown", path: "", source: parts.join("") }]
  )
})

test("preserves a report at the inclusive discovery boundary", () => {
  const prefix = "<html><body><p>"
  const suffix = "</p></body></html>"
  const html =
    prefix + "A".repeat(512 * 1024 - prefix.length - suffix.length) + suffix
  const documents = findTraceDocuments(html)
  assert.equal(documents.length, 1)
  assert.equal(documents[0].source, html)
})

test("does not read unfinished tag examples across a Markdown fence", () => {
  const markdown = "# CSS notes\n\n    <style>\n"
  const html = "<html><body>Actual complete report.</body></html>"
  const value = ["```md", markdown + "```", "", html].join("\n")
  assert.deepEqual(findTraceDocuments(value), [
    { format: "markdown", path: "", source: markdown },
    { format: "html", path: "", source: html },
  ])
})

test("recovers a report spanning three typed parts from the same assistant output", () => {
  const chunks = [
    "<!doctype html><ht",
    "ml><body><h1>Quarterly report</h1><p>42 items.</p></body>",
    "</html>",
  ]
  const output = JSON.stringify([
    {
      role: "assistant",
      content: chunks.map((text) => ({ type: "output_text", text })),
    },
  ])
  assert.deepEqual(findTraceDocuments(output), [
    { format: "html", path: "[0].content", source: chunks.join("") },
  ])
})

test("recovers Markdown from adjacent typed user input parts without inserting whitespace", () => {
  const chunks = ["# Market no", "tes\n\nRead the summary."]
  const input = {
    role: "user",
    parts: chunks.map((content) => ({ type: "input_text", content })),
  }
  assert.deepEqual(findTraceDocuments(input), [
    { format: "markdown", path: "parts", source: chunks.join("") },
  ])
})

test("does not extend a partial preview across a separate message, tool call, or media block", () => {
  const start = "<html><body><h1>Quarterly report"
  const finish = "</h1></body></html>"
  const text = (source: string) => ({ type: "text", text: source })
  const value = {
    messages: [
      { role: "assistant", content: [text(start)] },
      { role: "assistant", content: [text(finish)] },
    ],
    toolCalls: [
      { name: "write", args: { path: "report.html", content: [text(start)] } },
      { name: "write", args: { path: "report.html", content: [text(finish)] } },
    ],
    mixed: {
      role: "assistant",
      content: [
        text(start),
        { type: "image", source: "fixture" },
        text(finish),
      ],
    },
    fields: [start, finish],
  }
  assert.deepEqual(findTraceDocuments(value), [
    { format: "html", path: "messages[0].content[0].text", source: start },
  ])
  for (const record of [
    value.messages,
    value.toolCalls,
    value.mixed,
    value.fields,
  ]) {
    assert.deepEqual(
      findTraceDocuments(record).map(({ source }) => source),
      [start]
    )
  }
})

test("keeps a standalone document alongside a reconstructed document", () => {
  const first = "<html><body>Already complete</body></html>"
  const second = "<html><body>Split report</body></html>"
  const value = [
    { type: "text", text: first },
    { type: "text", text: "<html><body>Split " },
    { type: "text", text: "report</body></html>" },
  ]
  assert.deepEqual(findTraceDocuments(value), [
    { format: "html", path: "[0].text", source: first },
    { format: "html", path: "", source: second },
  ])
})

test("limits reconstruction while keeping the bounded partial preview and adjacent fields", () => {
  const partial = "<html><body>" + "a".repeat(256 * 1024)
  const value = {
    content: [
      { type: "output_text", text: partial },
      { type: "output_text", text: "b".repeat(256 * 1024) + "</body></html>" },
    ],
    summary: "# Summary\n\nThe report exceeded the preview limit.",
  }
  assert.deepEqual(findTraceDocuments(value), [
    { format: "html", path: "content[0].text", source: partial },
    { format: "markdown", path: "summary", source: value.summary },
  ])
})

test("retains two standalone Markdown documents as separate previews", () => {
  const parts = ["# First\n\nRead page one.\n", "# Second\n\nRead page two."]
  const value = parts.map((text) => ({ type: "text", text }))
  assert.deepEqual(findTraceDocuments(value), [
    { format: "markdown", path: "[0].text", source: parts[0] },
    { format: "markdown", path: "[1].text", source: parts[1] },
  ])
})

test("orders recovered reports between standalone HTML in the same record", () => {
  const first = "<html><body>Before</body></html>"
  const middle = "<html><body>Split report</body></html>"
  const last = "<html><body>After</body></html>"
  const value = [
    { type: "text", text: first + "\n<html><body>Split " },
    { type: "output_text", text: "report</body></html>" },
    { type: "input_text", content: last },
  ]
  assert.deepEqual(
    findTraceDocuments(value).map(({ source }) => source),
    [first, middle, last]
  )
})

test("recovers mixed supported part shapes at the inclusive document boundary", () => {
  const start = "<html><body><p>"
  const end = "</p></body></html>"
  const middle = "a".repeat(512 * 1024 - start.length - end.length)
  const value = [
    { type: "text", text: start },
    { type: "output_text", text: middle },
    { type: "input_text", content: end },
  ]
  assert.deepEqual(findTraceDocuments(value), [
    { format: "html", path: "", source: start + middle + end },
  ])
})
