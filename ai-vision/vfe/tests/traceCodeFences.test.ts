import assert from "node:assert/strict"
import { test } from "node:test"

import {
  TRACE_CODE_SCAN_LIMIT,
  inferTraceCodeLanguage,
  normalizeTraceCodeLanguage,
  traceCodeFences,
  traceCodeFieldLanguage,
} from "../src/components/traces/shared/traceCodeFences.ts"
import { traceCodeTokens } from "../src/components/traces/shared/traceCodeHighlighter.ts"

test("code fence boundaries preserve raw source and ignore shorter inner fences", () => {
  const fenced =
    "  ````ts trace-demo\r\n  const x = 'demo';\r\n```\r\n\t\r\n  ````  \r\n"
  const text = "before\r\n" + fenced + "after"
  const [block] = traceCodeFences(text)
  assert.equal(block.language, "ts")
  assert.equal(block.closed, true)
  assert.equal(text.slice(block.start, block.end), fenced)
  assert.equal(
    text.slice(block.bodyStart, block.bodyEnd),
    "  const x = 'demo';\r\n```\r\n\t\r\n"
  )
})

test("fences retain exact empty and partial source, and require a line boundary", () => {
  const text = " inline `text`\n~~~\n~~~\n```PYTHON\n    print('hello')\n    "
  const fences = traceCodeFences(text)
  assert.equal(fences.length, 2)
  assert.deepEqual(
    fences.map(({ bodyStart, bodyEnd, language, closed }) => [
      text.slice(bodyStart, bodyEnd),
      language,
      closed,
    ]),
    [
      ["", "", true],
      ["    print('hello')\n    ", "python", false],
    ]
  )
})

test("bare carriage returns are preserved in a multiline fence", () => {
  const text = "```js\r  const value = 1;\r```\r prose"
  const [block] = traceCodeFences(text)
  assert.equal(
    text.slice(block.bodyStart, block.bodyEnd),
    "  const value = 1;\r"
  )
  assert.equal(block.closed, true)
})

test("scanning is capped for very large text", () => {
  const text = "```js\n" + " ".repeat(TRACE_CODE_SCAN_LIMIT)
  assert.deepEqual(traceCodeFences(text), [])
})

test("explicit fence labels map to language grammars and unknown labels remain plain", () => {
  assert.equal(normalizeTraceCodeLanguage(" Ts "), "typescript")
  assert.equal(normalizeTraceCodeLanguage("c++"), "cpp")
  assert.equal(normalizeTraceCodeLanguage("PY"), "python")
  assert.equal(normalizeTraceCodeLanguage("invalid<language>"), "")
  assert.deepEqual(traceCodeTokens("const value = 1;", "other-language"), [])
  assert.deepEqual(traceCodeTokens("plain", "constructor"), [])
})

test("language highlighting retains exact offsets, including CRLF and literal URLs", () => {
  const value =
    '  const url: string = "https://example.test/guide.html";\r\n    const n = 10;\r\n'
  const tokens = traceCodeTokens(value, "typescript")
  assert.deepEqual(
    tokens
      .filter(({ classes }) => classes.includes("tok-keyword"))
      .map(({ from, to }) => value.slice(from, to)),
    ["const", "const"]
  )
  assert.ok(
    tokens.some(
      ({ from, to, classes }) =>
        classes.includes("tok-string") &&
        value.slice(from, to) === '"https://example.test/guide.html"'
    )
  )
})

test("language inference is bounded and ambiguous source remains plain", () => {
  assert.equal(
    inferTraceCodeLanguage("const enabled: boolean = true;"),
    "typescript"
  )
  assert.equal(inferTraceCodeLanguage("const enabled = true;"), "javascript")
  assert.equal(
    inferTraceCodeLanguage(
      "// Recorded comment\r\n/* Metadata */\r\nconst enabled = true;"
    ),
    "javascript"
  )
  assert.equal(inferTraceCodeLanguage("def greeting():\n  pass"), "python")
  assert.equal(
    inferTraceCodeLanguage("printf '%s\\n' hello", "command"),
    "shell"
  )
  assert.equal(
    inferTraceCodeLanguage("SELECT title FROM guide", "query"),
    "sql"
  )
  assert.equal(inferTraceCodeLanguage("https://example.test/guide.html"), "")
})

test("only explicit code fields and typed code sources carry recorded languages", () => {
  const code = "https://example.test/guide.html"
  assert.equal(traceCodeFieldLanguage({ language: "py" }, "code", code), "py")
  assert.equal(
    traceCodeFieldLanguage({ language: "py" }, "code_snippet", code),
    "py"
  )
  assert.equal(
    traceCodeFieldLanguage({ language: "py" }, "source_code", code),
    "py"
  )
  assert.equal(
    traceCodeFieldLanguage({ lang: "sql", type: "code_block" }, "source", code),
    "sql"
  )
  assert.equal(traceCodeFieldLanguage({ language: "py" }, "source", code), null)
  assert.equal(traceCodeFieldLanguage({ type: "code" }, "text", code), "")
})
