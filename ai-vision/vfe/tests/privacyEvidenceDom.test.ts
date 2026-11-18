import assert from "node:assert/strict"
import { createRequire } from "node:module"
import test from "node:test"
import { act, createElement, type ComponentType } from "react"
import { createRoot } from "react-dom/client"
import { JSDOM } from "jsdom"
import { nativeUiHarness } from "./nativeUiHarness.ts"
import { relayPrivacyResourceAttributes } from "../src/labs/privacy/relayPrivacyPolicy.ts"

const require = createRequire(import.meta.url)
const en = require("../src/i18n/locales/en-US.json")

async function fixture() {
  const dom = new JSDOM("<div id='root'></div>")
  const originals = new Map<string, PropertyDescriptor | undefined>()
  const copied: string[] = []
  for (const [key, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: {
      clipboard: {
        writeText: async (text: string) => {
          copied.push(text)
        },
      },
    },
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    originals.set(key, Object.getOwnPropertyDescriptor(globalThis, key))
    Object.defineProperty(globalThis, key, { configurable: true, value })
  }
  const h = nativeUiHarness({
    react: require("react"),
    "lucide-react": require("lucide-react"),
    "react-markdown": require("react-markdown"),
    "remark-gfm": require("remark-gfm"),
    "@/components/ui/button": {
      Button: ({ children, ...props }: Record<string, unknown>) =>
        createElement(
          "button",
          { type: "button", ...props },
          children as never
        ),
    },
    "react-i18next": {
      useTranslation: () => ({
        t: (key: string) => key.split(".").reduce((v, k) => v?.[k], en) ?? key,
      }),
    },
  })
  const { TraceTextContent: Text } = h.load<{
    TraceTextContent: ComponentType<Record<string, unknown>>
  }>("components/traces/shared/TraceTextContent.tsx")
  const { PrivacyMarkdown: Markdown } = h.load<{
    PrivacyMarkdown: ComponentType<Record<string, unknown>>
  }>("labs/privacy/PrivacyMarkdown.tsx")
  const { ReportedFieldOmissions: Omissions } = h.load<{
    ReportedFieldOmissions: ComponentType<Record<string, unknown>>
  }>("labs/privacy/ReportedFieldOmissions.tsx")
  const root = createRoot(dom.window.document.getElementById("root")!)
  return {
    doc: dom.window.document,
    copied,
    Text,
    Markdown,
    Omissions,
    render: async (
      Component: ComponentType<Record<string, unknown>>,
      props: Record<string, unknown>
    ) => {
      await act(async () => root.render(createElement(Component, props)))
    },
    click: async (element: Element) => {
      await act(async () =>
        element.dispatchEvent(
          new dom.window.MouseEvent("click", { bubbles: true })
        )
      )
    },
    toggle: async (details: HTMLDetailsElement, open = true) => {
      await act(async () => {
        details.open = open
        details.dispatchEvent(new dom.window.Event("toggle"))
      })
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

test("received placeholders are visibly marked without modifying text or copied values", async () => {
  const f = await fixture()
  try {
    const content =
      "<script>alert(1)</script> Contact [REDACTED_EMAIL], token [REDACTED]. ****"
    await f.render(f.Text, {
      content,
      copyLabel: "Copy exact received content",
    })
    assert.equal(f.doc.querySelector("pre")?.textContent, content)
    assert.deepEqual(
      [...f.doc.querySelectorAll("mark[data-privacy-marker]")].map(
        (node) => node.textContent
      ),
      ["[REDACTED_EMAIL]", "[REDACTED]"]
    )
    assert.match(f.doc.querySelector("mark")?.className ?? "", /bg-amber/)
    assert.equal(f.doc.querySelector("script"), null)
    await f.click(
      f.doc.querySelector('button[aria-label="Copy exact received content"]')!
    )
    assert.deepEqual(f.copied, [content])
    assert.equal(
      f.doc.querySelector('[data-testid="privacy-evidence-card"]'),
      null
    )
  } finally {
    await f.close()
  }
})

test("Markdown placeholders retain paragraphs, emphasis, links and code semantics", async () => {
  const f = await fixture()
  try {
    await f.render(f.Markdown, {
      children:
        "**Contact [REDACTED_EMAIL]** and `key [REDACTED]`\n\n[Support](https://example.invalid)",
    })
    assert.equal(
      f.doc.querySelector("strong mark")?.textContent,
      "[REDACTED_EMAIL]"
    )
    assert.equal(f.doc.querySelector("code mark")?.textContent, "[REDACTED]")
    assert.equal(f.doc.querySelectorAll("p").length, 2)
    assert.equal(
      f.doc.querySelector("a")?.getAttribute("href"),
      "https://example.invalid"
    )
  } finally {
    await f.close()
  }
})

test("an explicit producer policy explains field exclusions without claiming removal receipts", async () => {
  const f = await fixture()
  try {
    await f.render(f.Omissions, {
      resource: relayPrivacyResourceAttributes([
        "/path",
        "/customer/private_note",
      ]),
    })
    assert.deepEqual(
      [...f.doc.querySelectorAll("code")].map((node) => node.textContent),
      ["/path", "/customer/private_note"]
    )
    assert.match(
      f.doc.body.textContent ?? "",
      /Fields excluded by reporting configuration/
    )
    assert.doesNotMatch(
      f.doc.body.textContent ?? "",
      /not a count of confirmed removals/
    )
    await f.toggle(f.doc.querySelector("details")!)
    assert.match(
      f.doc.body.textContent ?? "",
      /not a count of confirmed removals/
    )
    for (const resource of [
      undefined,
      {},
      { "aivision.privacy.omitted_paths": '["/path"]' },
    ]) {
      await f.render(f.Omissions, { resource })
      assert.equal(
        f.doc.querySelector('[data-testid="reported-field-omissions"]'),
        null
      )
    }
  } finally {
    await f.close()
  }
})

test("long exclusion declarations keep a short preview and reveal every full path on demand", async () => {
  const f = await fixture()
  try {
    const paths = Array.from(
      { length: 20 },
      (_, index) => `/customer${index}/` + "field_".repeat(70)
    )
    await f.render(f.Omissions, {
      resource: relayPrivacyResourceAttributes(paths),
    })
    assert.equal(f.doc.querySelectorAll("code").length, 2)
    assert.doesNotMatch(f.doc.body.textContent ?? "", /customer19/)
    const details = f.doc.querySelector("details")!
    assert.equal(details.open, false)
    await f.toggle(details)
    assert.deepEqual(
      [...f.doc.querySelectorAll("code")].map((node) => node.textContent),
      paths
    )
    await f.toggle(details, false)
    assert.equal(f.doc.querySelectorAll("code").length, 2)
  } finally {
    await f.close()
  }
})
