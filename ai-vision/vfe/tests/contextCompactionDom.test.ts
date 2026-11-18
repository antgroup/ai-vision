import assert from "node:assert/strict"
import { createRequire } from "node:module"
import test from "node:test"
import { act, createElement, type ComponentType } from "react"
import { createRoot } from "react-dom/client"
import { JSDOM } from "jsdom"
import { nativeUiHarness } from "./nativeUiHarness.ts"
import { collectContextCompactions } from "../src/labs/context-compaction/contextCompaction.ts"
import {
  compactionEvent,
  compactionSpan,
} from "./fixtures/contextCompaction.ts"

const require = createRequire(import.meta.url)
const locales = Object.fromEntries(
  ["zh-CN", "en-US", "ja-JP"].map((language) => [
    language,
    require(`../src/i18n/locales/${language}.json`),
  ])
)

async function fixture(language = "en-US") {
  const messages = locales[language]
  const dom = new JSDOM("<div id='root'></div>")
  const originals = new Map<string, PropertyDescriptor | undefined>()
  for (const [key, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    originals.set(key, Object.getOwnPropertyDescriptor(globalThis, key))
    Object.defineProperty(globalThis, key, { configurable: true, value })
  }
  const h = nativeUiHarness({
    react: require("react"),
    "@/components/ui/badge": {
      Badge: ({ children, ...props }: Record<string, unknown>) =>
        createElement("span", props, children as never),
    },
    "react-i18next": {
      useTranslation: () => ({
        t: (key: string, params: Record<string, unknown> = {}) => {
          const value = key.split(".").reduce((v, k) => v?.[k], messages)
          return typeof value === "string"
            ? value.replace(/{{(\w+)}}/g, (_, key) => String(params[key] ?? ""))
            : (params.defaultValue ?? key)
        },
        i18n: { resolvedLanguage: language },
      }),
    },
    "./contextCompaction": { collectContextCompactions },
    "@/components/labs/shared/LabsSection": {
      LabsSection: ({
        children,
        title,
        summary,
        ...props
      }: Record<string, unknown>) =>
        createElement(
          "section",
          { "data-testid": props["data-testid"] },
          createElement("h3", {}, title as never),
          summary as never,
          children as never
        ),
    },
  })
  const { ContextCompactionCard: Card } = h.load<{
    ContextCompactionCard: ComponentType<Record<string, unknown>>
  }>("labs/context-compaction/ContextCompactionCard.tsx")
  const root = createRoot(dom.window.document.getElementById("root")!)
  return {
    doc: dom.window.document,
    labels: messages.labs.compaction,
    recordLabels: messages.traceRecords,
    render: async (props: Record<string, unknown>) => {
      await act(async () => root.render(createElement(Card, props)))
    },
    click: async (element: Element) => {
      await act(async () =>
        element.dispatchEvent(
          new dom.window.MouseEvent("click", { bubbles: true })
        )
      )
    },
    close: async () => {
      await act(async () => root.unmount())
      for (const [key, descriptor] of originals) {
        if (descriptor) Object.defineProperty(globalThis, key, descriptor)
        else Reflect.deleteProperty(globalThis, key)
      }
      dom.window.close()
    },
  }
}

for (const language of ["zh-CN", "en-US", "ja-JP"]) {
  test(`skipped compaction explains its reason while technical records stay in details: ${language}`, async () => {
    const f = await fixture(language)
    try {
      const selected: unknown[] = []
      await f.render({
        spans: [
          compactionSpan([
            compactionEvent(
              {
                outcome: "skipped",
                method: "none",
                failure_class: "insufficient_messages",
                split_status: "aborted",
                tokens_after: 0,
                tokens_reclaimed: 0,
                messages_after: 0,
                duration_ms: 0,
              },
              "compaction.attempt"
            ),
          ]),
        ],
        onViewSource: (source: unknown) => selected.push(source),
      })
      const record = f.doc.querySelector("[data-compaction-record]")!
      const details = record.querySelector("details")!
      assert.equal(details.open, false)
      const explanation = record.querySelector("[data-compaction-reason]")!
      assert.equal(
        explanation.textContent,
        f.labels.reasons.insufficient_messages
      )
      assert.equal(explanation.closest("details"), null)
      assert.equal(record.getAttribute("data-compaction-outcome"), "skipped")
      assert.equal(record.querySelector("[data-compaction-comparison]"), null)
      assert.equal(record.querySelector("[data-compaction-savings]"), null)
      for (const field of [
        "method",
        "trigger",
        "failure_class",
        "split_status",
      ]) {
        const value = record.querySelector(
          `[data-compaction-field="${field}"]`
        )!
        assert.equal(value.closest("details"), details)
      }
      assert.match(details.textContent!, /insufficient_messages/)
      assert.equal(
        record.querySelector("[data-compaction-source]")!.closest("details"),
        details
      )
      for (const field of [
        "tokens_after",
        "tokens_reclaimed",
        "messages_after",
        "duration_ms",
      ]) {
        const value = details.querySelector(
          `[data-compaction-field="${field}"]`
        )!
        assert.equal(
          value.querySelector("dd")?.textContent,
          field === "duration_ms" ? "0 ms" : "0"
        )
      }
      await f.click(details.querySelector("summary")!)
      assert.equal(details.open, true)
      await f.click(details.querySelector("[data-compaction-source]")!)
      assert.equal(selected.length, 1)
    } finally {
      await f.close()
    }
  })
}

test("method, trigger and unfamiliar technical values remain in collapsed details", async () => {
  const f = await fixture()
  try {
    await f.render({
      spans: [
        compactionSpan([
          compactionEvent({
            failure_class: "upstream_failure_v2",
            token_count_method: "upstream_measurement_v2",
          }),
        ]),
      ],
    })
    const record = f.doc.querySelector("[data-compaction-record]")!
    const details = record.querySelector("details")!
    assert.equal(details.open, false)
    for (const field of [
      "method",
      "trigger",
      "failure_class",
      "token_count_method",
    ]) {
      assert.equal(
        record
          .querySelector(`[data-compaction-field="${field}"]`)!
          .closest("details"),
        details
      )
    }
    assert.equal(record.querySelector("[data-compaction-reason]"), null)
    assert.equal(record.getAttribute("data-compaction-outcome"), "committed")
    assert.match(
      record.querySelector("details")!.textContent!,
      /upstream_failure_v2/
    )
    assert.match(
      record.querySelector("details")!.textContent!,
      /upstream_measurement_v2/
    )
  } finally {
    await f.close()
  }
})

test("partial comparison retains the reported count and exact source without inventing savings", async () => {
  const f = await fixture()
  try {
    const selected: unknown[] = []
    await f.render({
      spans: [compactionSpan([compactionEvent({ tokens_after: null })])],
      onViewSource: (source: unknown) => selected.push(source),
    })
    const comparison = f.doc.querySelector("[data-compaction-comparison]")
    assert.ok(comparison, "a committed result retains its one reported count")
    assert.ok(comparison.textContent?.includes(f.labels.comparison.estimated))
    assert.match(comparison.textContent!, /12,000/)
    assert.deepEqual(
      [...comparison.querySelectorAll("dt")].map((label) => label.textContent),
      [f.labels.comparison.before]
    )
    assert.doesNotMatch(comparison.textContent!, /(?<!\d)0(?!\d)/)
    assert.equal(f.doc.querySelector("[data-compaction-savings]"), null)
    assert.equal(
      f.doc.querySelector('[data-compaction-field="tokens_before"] dd')
        ?.textContent,
      "12,000"
    )
    assert.equal(
      f.doc.querySelector('[data-compaction-field="tokens_after"]'),
      null
    )
    assert.equal(
      f.doc.querySelector('details [data-compaction-field="queue_wait_ms"] dd')
        ?.textContent,
      "0 ms"
    )
    assert.equal(
      f.doc.querySelector(
        'details [data-compaction-field="session_rotated"] dd'
      )?.textContent,
      f.labels.no
    )
    await f.click(f.doc.querySelector("[data-compaction-source]")!)
    assert.deepEqual(selected, [
      {
        traceId: "compaction-trace",
        spanId: "turn-span",
        eventIndex: 0,
        eventName: "compaction",
        markId: "mark-committed",
        time: "2026-11-02T02:00:01Z",
        categoryProfile: undefined,
      },
    ])
  } finally {
    await f.close()
  }
})

test("only a compatible decrease earns a savings claim", async () => {
  const f = await fixture()
  try {
    for (const [data, after, savings] of [
      [{}, "4,000", "8,000"],
      [{ tokens_after: 0, tokens_reclaimed: 12000 }, "0", "12,000"],
      [{ tokens_reclaimed: null }, "4,000", "8,000"],
      [{ tokens_reclaimed: 7000 }, "4,000", null],
      [{ tokens_after: 12000, tokens_reclaimed: 0 }, "12,000", null],
      [{ tokens_after: 13000, tokens_reclaimed: null }, "13,000", null],
    ] as const) {
      await f.render({ spans: [compactionSpan([compactionEvent(data)])] })
      const comparison = f.doc.querySelector("[data-compaction-comparison]")
      assert.ok(comparison, "committed attempts with counts show a comparison")
      const { before, after: afterLabel, unit } = f.labels.comparison
      assert.deepEqual(
        [...comparison.querySelectorAll("dt")].map((label) => [
          label.textContent,
          label.nextElementSibling?.textContent,
        ]),
        [
          [before, `12,000 ${unit}`],
          [afterLabel, `${after} ${unit}`],
        ]
      )
      const displayedSavings = f.doc.querySelector("[data-compaction-savings]")
      if (savings === null) {
        assert.equal(displayedSavings, null)
      } else {
        assert.ok(displayedSavings?.textContent?.includes(savings))
      }
    }
  } finally {
    await f.close()
  }
})

test("an unreported committed comparison does not substitute zero", async () => {
  const f = await fixture()
  try {
    await f.render({
      spans: [
        compactionSpan([
          compactionEvent({ tokens_before: null, tokens_after: null }),
        ]),
      ],
    })
    const record = f.doc.querySelector('[data-compaction-outcome="committed"]')!
    assert.ok(record.textContent?.includes(f.labels.comparison.unreported))
    assert.equal(record.querySelector("[data-compaction-comparison]"), null)
    assert.equal(record.querySelector("[data-compaction-savings]"), null)
  } finally {
    await f.close()
  }
})

test("malformed token counts cannot advertise a rounded saving", async () => {
  const f = await fixture()
  try {
    await f.render({
      spans: [
        compactionSpan([
          compactionEvent({
            tokens_before: 1.0004,
            tokens_after: 1.0003,
            tokens_reclaimed: null,
          }),
        ]),
      ],
    })
    const record = f.doc.querySelector('[data-compaction-outcome="committed"]')!
    assert.equal(
      Boolean(record.querySelector("[data-compaction-comparison]")),
      false
    )
    assert.equal(
      Boolean(record.querySelector("[data-compaction-savings]")),
      false
    )
    assert.ok(
      record.querySelector('details [data-compaction-field="tokens_before"]')
    )
  } finally {
    await f.close()
  }
})

test("noncommitted and conflicting attempts keep reported numbers out of the comparison", async () => {
  const f = await fixture()
  try {
    for (const outcome of [
      "failed",
      "skipped",
      "aborted",
      "blocked",
      "unknown",
      "conflict",
    ]) {
      await f.render({
        spans: [
          compactionSpan([
            compactionEvent(
              { outcome: outcome === "conflict" ? "committed" : outcome },
              "compaction.attempt"
            ),
          ]),
        ],
      })
      const record = f.doc.querySelector("[data-compaction-record]")!
      assert.equal(record.getAttribute("data-compaction-outcome"), outcome)
      assert.ok(record.textContent?.includes(f.labels.outcomes[outcome]))
      assert.equal(record.querySelector("[data-compaction-comparison]"), null)
      assert.equal(record.querySelector("[data-compaction-savings]"), null)
      assert.equal(
        record.querySelector(
          'details [data-compaction-field="tokens_reclaimed"] dd'
        )?.textContent,
        "8,000"
      )
    }
  } finally {
    await f.close()
  }
})

test("record window is bounded and reversible and resets for a different trace", async () => {
  const f = await fixture()
  try {
    const span = compactionSpan(
      Array.from({ length: 7 }, (_, n) =>
        compactionEvent(
          { attempt_id: `attempt-${n}` },
          "compaction",
          `mark-${n}`
        )
      )
    )
    await f.render({ spans: [span] })
    assert.equal(f.doc.querySelectorAll("[data-compaction-record]").length, 5)
    await f.click(
      [...f.doc.querySelectorAll("button")].find(
        (b) => b.textContent === f.recordLabels.all
      )!
    )
    assert.equal(f.doc.querySelectorAll("[data-compaction-record]").length, 7)
    await f.click(
      [...f.doc.querySelectorAll("button")].find(
        (b) => b.textContent === f.recordLabels.collapse
      )!
    )
    assert.equal(f.doc.querySelectorAll("[data-compaction-record]").length, 5)
    await f.click(
      [...f.doc.querySelectorAll("button")].find(
        (b) => b.textContent === f.recordLabels.all
      )!
    )
    await f.render({ spans: [{ ...span, trace_id: "another-trace" }] })
    assert.equal(f.doc.querySelectorAll("[data-compaction-record]").length, 5)
    await f.render({ spans: [] })
    assert.equal(f.doc.querySelector("section"), null)
  } finally {
    await f.close()
  }
})

test("duplicate source histories also use a reversible five-event window", async () => {
  const f = await fixture()
  try {
    await f.render({
      spans: [
        compactionSpan(Array.from({ length: 7 }, () => compactionEvent())),
      ],
      onViewSource: () => {},
    })
    assert.equal(f.doc.querySelectorAll("[data-compaction-record]").length, 1)
    assert.equal(f.doc.querySelectorAll("[data-compaction-source]").length, 5)
    await f.click(
      [...f.doc.querySelectorAll("button")].find(
        (b) => b.textContent === f.recordLabels.all
      )!
    )
    assert.equal(f.doc.querySelectorAll("[data-compaction-source]").length, 7)
    await f.click(
      [...f.doc.querySelectorAll("button")].find(
        (b) => b.textContent === f.recordLabels.collapse
      )!
    )
    assert.equal(f.doc.querySelectorAll("[data-compaction-source]").length, 5)
  } finally {
    await f.close()
  }
})
