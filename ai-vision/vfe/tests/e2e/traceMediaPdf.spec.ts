import { readFileSync } from "node:fs"
import { expect, test } from "@playwright/test"
import { prepare, publicTrace, trace, uiText } from "./traceLabs.fixtures"

test("a Trace PDF renders both pages with the emitted module worker", async ({
  page,
}, testInfo) => {
  const redactedText =
    "Check order for [REDACTED_EMAIL] and keep this question."
  const data = readFileSync(
    new URL("../fixtures/trace-media/sample.pdf", import.meta.url)
  ).toString("base64")
  await prepare(page)
  await publicTrace(page, {
    ...trace,
    spans: trace.spans.map((span, index) =>
      index === 0
        ? {
            ...span,
            attributes: {
              ...span.attributes,
              "gen_ai.operation.name": "chat",
              "gen_ai.input.messages": JSON.stringify([
                {
                  role: "user",
                  content: [
                    { type: "text", text: redactedText },
                    { type: "file", mime_type: "application/pdf", data },
                  ],
                },
              ]),
            },
          }
        : span
    ),
  })

  const summary = page.getByTestId("trace-user-input")
  await expect(summary.getByText(redactedText, { exact: true })).toBeVisible()
  const card = summary.locator("[data-trace-media]")
  const workerResponse = page.waitForResponse((response) =>
    /\/assets\/pdf\.worker[^/]+\.mjs$/.test(new URL(response.url()).pathname)
  )
  await card
    .getByRole("button", { name: uiText("en-US", "spanContent.loadMedia") })
    .click()
  const worker = await workerResponse
  expect(worker.ok()).toBe(true)
  expect(worker.headers()["content-type"]).toMatch(
    /(?:application|text)\/javascript/
  )

  const canvas = card.locator("canvas")
  // The default canvas is blank. Wait for non-white PDF glyphs, not just a canvas element.
  const pixels = () =>
    canvas.evaluate((element: HTMLCanvasElement) => {
      const context = element.getContext("2d")!
      const image = context.getImageData(0, 0, element.width, element.height)
      return image.data.some(
        (channel, index, channels) =>
          index % 4 === 0 && channel < 128 && channels[index + 3] === 255
      )
        ? element.toDataURL()
        : null
    })
  await expect(card.getByText("1 / 2")).toBeVisible()
  await expect.poll(pixels).not.toBeNull()
  const first = await pixels()
  await card
    .getByRole("button", { name: uiText("en-US", "traceMedia.next") })
    .click()
  await expect(card.getByText("2 / 2")).toBeVisible()
  await expect
    .poll(async () => {
      const second = await pixels()
      return second !== null && second !== first
    })
    .toBe(true)
  await expect(card.getByRole("alert")).toHaveCount(0)
  await card.screenshot({ path: testInfo.outputPath("pdf-page-2.png") })
})
