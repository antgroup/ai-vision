import assert from "node:assert/strict"
import test from "node:test"
import {
  sessionContent,
  sessionIOState,
  sessionFieldContent,
  loadSessionIO,
  loadSessionTraces,
} from "../src/components/traces-otel/sessionIO.ts"

test("typed IO preserves numbers, literal JSON strings, quotes and null without guessing", () => {
  for (const value of [0, "0", { x: 1 }, '{"x": 1}', '"quoted"', null]) {
    const item = {
      id: "t",
      input: "legacy fallback",
      output: "legacy fallback",
      inputValue: value,
      outputValue: value,
    }
    for (const field of ["input", "output"] as const) {
      const content = sessionFieldContent(item, field)
      assert.deepEqual(content.value, value)
      assert.equal(typeof content.value, typeof value)
      assert.equal(
        content.text,
        value === null
          ? ""
          : typeof value === "string"
            ? value
            : JSON.stringify(value, null, 2)
      )
    }
  }
  assert.equal(
    sessionFieldContent({ id: "t", input: '{"x": 1}', output: null }, "input")
      .structured,
    true
  )
})

test("structured output preserves tool calls and arbitrary business JSON", () => {
  const value = {
    content: "",
    tool_calls: [{ name: "lookup", arguments: { city: "Beijing" } }],
  }
  assert.equal(
    sessionContent(JSON.stringify(value)).text,
    JSON.stringify(value, null, 2)
  )
  assert.equal(sessionContent('""').state, "empty")
  assert.equal(sessionContent(null).state, "unknown")
  assert.equal(sessionIOState('""', "cancelled"), "cancelled")
  assert.equal(sessionIOState(null, "unfinished"), "unfinished")
  assert.equal(sessionContent("0").text, "0")
  assert.equal(sessionContent("null").text, "null")
  assert.equal(sessionContent("false").text, "false")
})

test("session IO uses batches of 100 and requires exactly every requested ID", async () => {
  const ids = Array.from({ length: 201 }, (_, index) => `t${index}`)
  const batches: number[] = []
  const result = await loadSessionIO(ids, async (batch: string[]) => {
    batches.push(batch.length)
    return {
      code: 0,
      message: "success",
      data: {
        data: batch.map((id) => ({
          id,
          input: null,
          output: null,
          outputState: "unfinished",
        })),
      },
    }
  })
  assert.deepEqual(batches, [100, 100, 1])
  assert.equal(result.data.data.length, 201)
  assert.equal(result.data.data[0].outputState, "unfinished")
  await assert.rejects(
    loadSessionIO(
      ["a", "b"],
      async () => ({ code: 0, data: { data: [{ id: "a" }] } }) as never
    ),
    /Incomplete/
  )
  await assert.rejects(
    loadSessionIO(["a"], async () => {
      throw new Error("HTTP 503")
    }),
    /503/
  )
})

test("session pagination rejects failure, changing totals and wrong trace IDs", async () => {
  const envelope = (page: number, total = 201) => ({
    code: 0,
    message: "success",
    data: {
      data: Array.from({ length: page === 1 ? 200 : 1 }, (_, i) => ({
        trace_id: `t${page === 1 ? i : 200}`,
      })),
      meta: { page, limit: 200, totalItems: total, totalPages: 2 },
    },
  })
  const ids = Array.from({ length: 201 }, (_, i) => `t${i}`)
  assert.equal(
    (await loadSessionTraces(ids, async (page) => envelope(page))).data.data
      .length,
    201
  )
  await assert.rejects(
    loadSessionTraces(ids, async (page) =>
      envelope(page, page === 1 ? 201 : 202)
    ),
    /changed|Incomplete/
  )
  await assert.rejects(
    loadSessionTraces(["different"], async () => ({
      ...envelope(1),
      data: {
        data: [{ trace_id: "wrong" }],
        meta: { page: 1, limit: 200, totalItems: 1, totalPages: 1 },
      },
    })),
    /Incomplete/
  )
})
