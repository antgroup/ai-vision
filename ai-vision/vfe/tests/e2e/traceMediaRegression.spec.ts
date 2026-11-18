import { readFileSync } from "node:fs"
import { expect, test } from "@playwright/test"
import { prepare, publicTrace, trace } from "./traceLabs.fixtures"

const pdf = readFileSync(
  new URL("../fixtures/trace-media/sample.pdf", import.meta.url)
)
const image = readFileSync(
  new URL("../fixtures/trace-media/image.fixture", import.meta.url)
)

test("anonymous media share remains readable without authenticated background requests", async ({
  page,
}) => {
  await prepare(page)
  const authRequests: string[] = []
  const output = {
    imageUrl: "https://media.example/sample.png",
    mimeType: "image/png",
  }
  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === "/api/v1/share/data")
      return route.fulfill({
        json: {
          code: 0,
          data: {
            resource_type: "trace",
            protocol: "signoz",
            space_id: "space-a",
            agent_product: "Codex",
            trace: {
              ...trace,
              spans: [
                {
                  ...trace.spans[0],
                  attributes: {
                    "gen_ai.input.messages": JSON.stringify([
                      {
                        role: "user",
                        content: [
                          {
                            type: "image_url",
                            image_url: {
                              url: "https://media.example/sample.png",
                            },
                          },
                        ],
                      },
                    ]),
                  },
                },
              ],
              semantics: { ...trace.semantics!, output },
            },
            share_info: {
              sharer_name: "Reviewer",
              created_at: "2026-11-17T00:00:00Z",
              expires_at: "2099-01-01T00:00:00Z",
            },
          },
        },
      })
    authRequests.push(path)
    return route.fulfill({ status: 401, json: { code: "SESSION_INVALID" } })
  })
  await page.goto("/share/trace?payload=fixture&sig=fixture")
  await page.waitForTimeout(500)
  await expect(page).toHaveURL(/\/share\/trace\?/)
  expect(authRequests).toEqual([])
  await expect(page.locator("[data-trace-media]").first()).toBeVisible()
})

test("stored media denial stays local to the preview on an anonymous share", async ({
  page,
}) => {
  await prepare(page)
  await publicTrace(page, {
    ...trace,
    semantics: {
      ...trace.semantics!,
      output: {
        type: "aivision_attachment",
        id: "a".repeat(64),
        spaceId: "space-a",
        agentId: "agent-a",
        mimeType: "image/png",
      },
    },
  })
  await page.route("**/api/v1/trace-attachments/**", (route) =>
    route.fulfill({ status: 401 })
  )
  await page.route("**/api/v1/auth/refresh", (route) =>
    route.fulfill({ status: 401 })
  )
  const card = page.locator("[data-trace-media]").first()
  await card.getByRole("button", { name: "Load media", exact: true }).click()
  await expect(card.getByRole("alert")).toBeVisible()
  await expect(page).toHaveURL(/\/trace-link\?/)
})

for (const direction of ["input", "output"] as const) {
  test(`${direction} PDF keeps loaded pages after its source expires`, async ({
    page,
  }) => {
    await prepare(page)
    let requests = 0
    await page.route("https://media.example/short-lived.pdf", (route) => {
      requests++
      return route.fulfill({
        status: requests === 1 ? 200 : 410,
        contentType: "application/pdf",
        body: requests === 1 ? pdf : Buffer.alloc(0),
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Cache-Control": "no-store",
        },
      })
    })
    const block = {
      type: "document",
      source: {
        media_type: "application/pdf",
        url: "https://media.example/short-lived.pdf",
      },
    }
    const detail =
      direction === "input"
        ? {
            ...trace,
            spans: [
              {
                ...trace.spans[0],
                attributes: {
                  "gen_ai.input.messages": JSON.stringify([
                    { role: "user", content: [block] },
                  ]),
                },
              },
            ],
          }
        : { ...trace, semantics: { ...trace.semantics!, output: block } }
    await publicTrace(page, detail)
    const card = page.locator("[data-trace-media]").first()
    expect(requests).toBe(0)
    await card.getByRole("button", { name: "Load media", exact: true }).click()
    await expect(card.getByText("1 / 2")).toBeVisible()
    const pixels = () =>
      card.locator("canvas").evaluate((canvas: HTMLCanvasElement) => {
        const bytes = canvas
          .getContext("2d")!
          .getImageData(0, 0, canvas.width, canvas.height).data
        return bytes.some(
          (channel, index) =>
            index % 4 === 0 && channel < 128 && bytes[index + 3] === 255
        )
          ? canvas.toDataURL()
          : null
      })
    await expect.poll(pixels).not.toBeNull()
    const first = await pixels()
    await card.getByRole("button", { name: "Next", exact: true }).click()
    await expect(card.getByText("2 / 2")).toBeVisible()
    await expect
      .poll(async () => {
        const second = await pixels()
        return second !== null && second !== first
      })
      .toBe(true)
    await expect(card.getByRole("alert")).toHaveCount(0)
    expect(requests).toBe(1)
    await expect(
      card.getByRole("button", { name: "Next", exact: true })
    ).toBeDisabled()
  })
  test(`${direction} oversized inline media keeps surrounding text and never downloads`, async ({
    page,
  }) => {
    await prepare(page)
    let requests = 0
    await page.route("https://media.example/**", (route) => {
      requests++
      return route.abort()
    })
    const blocks = [
      { type: "text", text: "Keep before." },
      {
        type: "file",
        mime_type: "application/pdf",
        data: "A".repeat(4 * 1024 * 1024 + 4),
      },
      { type: "aivision_media_error", reason: "budget" },
      { type: "aivision_media_error", reason: "filtered" },
      { type: "text", text: "Keep after." },
    ]
    const detail =
      direction === "input"
        ? {
            ...trace,
            spans: [
              {
                ...trace.spans[0],
                attributes: {
                  "gen_ai.input.messages": [{ role: "user", content: blocks }],
                },
              },
            ],
          }
        : { ...trace, semantics: { ...trace.semantics!, output: blocks } }
    await publicTrace(page, detail)
    const content = page.locator("[data-trace-media-content]").first()
    await expect(content).toContainText(/Keep before.*Keep after/s)
    await expect(content.getByRole("alert")).toHaveText([
      "Media exceeds the preview size limit.",
      "Media was filtered or redacted.",
    ])
    await expect(content.locator("[data-trace-media]")).toHaveCount(0)
    expect(requests).toBe(0)
  })
}

test("small image zooms around the preview center and returns keyboard focus", async ({
  page,
}) => {
  await prepare(page)
  await page.route("https://media.example/small.png", (r) =>
    r.fulfill({ contentType: "image/png", body: image })
  )
  await publicTrace(page, {
    ...trace,
    semantics: {
      ...trace.semantics!,
      output: {
        imageUrl: "https://media.example/small.png",
        mimeType: "image/png",
      },
    },
  })
  const card = page.locator("[data-trace-media]").first()
  await card.getByRole("button", { name: "Load media", exact: true }).click()
  const button = card.getByRole("button", { name: "Preview", exact: true })
  await button.click()
  const dialog = page.locator("[data-trace-media-dialog]")
  await dialog.getByRole("button", { name: "Zoom in", exact: true }).click()
  const centered = await dialog.locator("img").evaluate((el) => {
    const a = el.getBoundingClientRect(),
      b = el.parentElement!.getBoundingClientRect()
    return Math.abs((a.top + a.bottom - b.top - b.bottom) / 2)
  })
  expect(centered).toBeLessThan(2)
  await page.keyboard.press("Escape")
  await expect(button).toBeFocused()
})
