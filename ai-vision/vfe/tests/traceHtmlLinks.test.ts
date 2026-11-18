import assert from "node:assert/strict"
import test from "node:test"

import { findTraceHtmlLinks } from "../src/components/traces/shared/traceHtmlLinks.ts"

test("discovers explicit HTTPS HTML links in order and deduplicates exact source URLs", () => {
  const signed =
    "https://example.com/report.HTM?path=a%2Fb&signature=A+b%3D#summary"
  const proof = "https://example.com/proof.html"
  const raw = "https://example.com/raw.html"
  assert.deepEqual(
    findTraceHtmlLinks({
      messages: [
        { "text value": `[Report](${signed}) and <${proof}>.` },
        { text: `See ${raw}. Again: ${signed}` },
      ],
    }),
    [
      { source: signed, path: 'messages[0]["text value"]' },
      { source: proof, path: 'messages[0]["text value"]' },
      { source: raw, path: "messages[1].text" },
    ]
  )
})

test("discovers report links in prose that starts with Select and contains from", () => {
  const source = "https://example.com/report.html?signature=a%2B#results"
  for (const destination of [`[report](${source})`, `<${source}>`]) {
    assert.deepEqual(
      findTraceHtmlLinks(`Select the record from ${destination}.`),
      [{ source, path: "" }]
    )
  }
  assert.deepEqual(
    findTraceHtmlLinks({
      content: [
        { type: "output_text", text: "Select the record from [report](" },
        { type: "output_text", text: `${source}).` },
      ],
    }),
    [{ source, path: "content[1].text" }]
  )
})

test("keeps HTML links in SQL expressions, comments and indented SQL inert", () => {
  const url = "https://example.com/report.html"
  const prose = `Select the record from [report](${url}).`
  for (const sql of [
    `SELECT '${prose}' AS note FROM reports;`,
    `SELECT the record FROM reports -- ${prose}`,
    `SELECT the record FROM reports /* ${prose} */`,
    `SELECT url FROM [reports] WHERE url = '${url}';`,
    `    ${prose}`,
    `~~~sql\n${prose}\n~~~`,
    { type: "code_block", language: "sql", source: prose },
  ]) {
    assert.deepEqual(findTraceHtmlLinks(sql), [])
  }
})

test("only offers previews for explicit HTML URL pathnames with a valid HTTPS authority", () => {
  const rejected = [
    "https://example.com/report",
    "https://example.com/report?filename=real.html",
    "https://example.com/report#page.html",
    "http://example.com/report.html",
    "//example.com/report.html",
    "./report.html",
    "file:///report.html",
    "javascript:https://example.com/report.html",
    "https://user:pass@example.com/report.html",
    "https://@example.com/report.html",
    "https:///example.com/report.html",
    "https://example.com/report.html/summary",
    "https://example.com\\report.html",
  ]
  assert.deepEqual(findTraceHtmlLinks(rejected), [])
})

test("links inside code and HTML markup stay inert alongside an eligible link", () => {
  const hidden = "https://example.com/hidden.html"
  const shown = "https://example.com/shown.html"
  const trace = [
    `Inline code: \`fetch("${hidden}")\``,
    `Inline code with varying markers: \`\`x \` ${hidden}\`\``,
    `Multiline inline: \`x\n${hidden}\``,
    "",
    "   ~~~sh",
    `curl '${hidden}'`,
    "   ~~~~",
    "",
    `    curl '${hidden}'`,
    `\tcurl '${hidden}'`,
    `<a data-title="> ${hidden}"\nhref="${hidden}">report</a>`,
    `<!-- ${hidden} -->`,
    `<script>const source = "${hidden}";</script>`,
    `<pre><code>${hidden}</code></pre>`,
    `![Image](${hidden})`,
    `Use the [real report](${shown}).`,
    "```",
    hidden,
  ].join("\n")
  assert.deepEqual(findTraceHtmlLinks(trace), [{ source: shown, path: "" }])
})

test("discovers links in recorded JSON envelopes while skipping typed media and code fields", () => {
  const hidden = "https://example.com/hidden.html"
  const shown = "https://example.com/shown.html"
  assert.deepEqual(
    findTraceHtmlLinks(
      JSON.stringify({
        blocks: [
          { type: "aivision_attachment", url: hidden, mimeType: "text/html" },
          { type: "image_url", image_url: { url: hidden } },
          {
            type: "document",
            source: { type: "url", url: hidden, media_type: "text/html" },
          },
          {
            code: hidden,
            code_snippet: hidden,
            source_code: hidden,
            command: hidden,
            cmd: hidden,
            stdout: hidden,
            stderr: hidden,
            diff: hidden,
            unified_diff: hidden,
          },
          { type: "code_block", source: hidden, content: hidden, text: hidden },
          { type: "file", url: shown },
        ],
      })
    ),
    [{ source: shown, path: "blocks[5].url" }]
  )
})

test("ignores quoted and list-nested fenced code without hiding adjacent prose", () => {
  const hidden = "https://example.com/hidden.html"
  const shown = "https://example.com/shown.html"
  const trace = [
    "> ~~~js",
    `> const source = "${hidden}";`,
    "> ~~~",
    `>     ${hidden}`,
    "",
    "- ~~~sh",
    `  curl '${hidden}'`,
    "  ~~~",
    "",
    `See ${shown}.`,
  ].join("\n")
  assert.deepEqual(findTraceHtmlLinks(trace), [{ source: shown, path: "" }])
})

test("keeps explicit destination punctuation and tolerates prose punctuation", () => {
  const exact =
    "https://example.com/report.html?hash=a+b%3D&next=/a(b)#literal."
  assert.deepEqual(
    findTraceHtmlLinks(
      `[Report](${exact}) or <https://example.com/missing.html.>.`
    ),
    [{ source: exact, path: "" }]
  )
})

test("carries code boundaries and explicit destinations across typed text parts", () => {
  const known = "https://example.com/known.html"
  const continuation = "https://example.com/continued.html?signature=a%2B"
  const result = findTraceHtmlLinks({
    content: [
      { type: "output_text", text: "``" },
      { type: "output_text", text: "`sh\n" },
      { type: "output_text", text: "curl https://example.com/hidden.html" },
      {
        type: "output_text",
        text: "\n```\n[Continued](https://example.com/con",
      },
      { type: "output_text", text: "tinued.html?signature=a%2B)" },
      {
        type: "image_url",
        image_url: { url: "https://example.com/image.html" },
      },
      { type: "output_text", text: `See ${known}` },
    ],
  })
  assert.deepEqual(result, [
    { source: continuation, path: "content[3].text" },
    { source: known, path: "content[6].text" },
  ])
})

test("bounds the number of URL previews and skips oversized values", () => {
  const URLs = Array.from(
    { length: 20 },
    (_, index) => `https://example.com/${index}.html`
  )
  assert.deepEqual(
    findTraceHtmlLinks(["a".repeat(512 * 1024 + 1) + URLs[8], ...URLs]),
    URLs.slice(0, 8).map((source, index) => ({
      source,
      path: `[${index + 1}]`,
    }))
  )
  const cyclic: unknown[] = []
  cyclic.push(cyclic)
  assert.deepEqual(findTraceHtmlLinks(cyclic), [])
})

test("keeps inferred code inert and discovers eligible links after a long malformed URL", () => {
  const link = "https://example.com/report.html"
  const malformed = `https://example.com/report.html${".".repeat(480 * 1024)}x`
  assert.deepEqual(
    findTraceHtmlLinks([
      `const report = "${link}";`,
      `// Recorded comment\r\n/* Metadata */\r\nconst report = "${link}";`,
      `curl "${link}"`,
      `${malformed}\nRead ${link}.`,
    ]),
    [{ source: link, path: "[3]" }]
  )
})
