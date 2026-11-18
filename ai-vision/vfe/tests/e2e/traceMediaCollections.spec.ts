import { mkdirSync, readFileSync } from "node:fs"
import path from "node:path"
import { expect, test, type Page, type Locator } from "@playwright/test"
import { prepare, publicTrace, trace, uiText } from "./traceLabs.fixtures"

const image = readFileSync(
  new URL("../fixtures/trace-media/image.fixture", import.meta.url)
)
const fixtureMedia = {
  audio: {
    mime: "audio/wav",
    extension: "wav",
    body: readFileSync(
      new URL("../fixtures/trace-media/sample.wav", import.meta.url)
    ),
  },
  video: {
    mime: "video/webm",
    extension: "webm",
    body: readFileSync(
      new URL("../fixtures/trace-media/sample.webm", import.meta.url)
    ),
  },
  pdf: {
    mime: "application/pdf",
    extension: "pdf",
    body: readFileSync(
      new URL("../fixtures/trace-media/sample.pdf", import.meta.url)
    ),
  },
}
const evidenceDir = process.env.AIVISION_MEDIA_EVIDENCE_DIR
const captureStage = process.env.AIVISION_MEDIA_CAPTURE_STAGE || "after"

type Direction = "input" | "output"
const imageBlock = (index: number) => ({
  type: "image_url",
  image_url: { url: `https://media.example/view-${index}.png` },
})

async function showMedia(
  page: Page,
  direction: Direction,
  blocks: unknown[],
  language = "en-US"
) {
  await prepare(page, language)
  const detail = {
    ...trace,
    spans: [
      {
        ...trace.spans[0],
        attributes:
          direction === "input"
            ? { "gen_ai.input.messages": [{ role: "user", content: blocks }] }
            : {},
      },
    ],
    semantics: {
      ...trace.semantics!,
      ...(direction === "output" ? { output: blocks } : {}),
    },
  }
  await publicTrace(page, detail)
  const content =
    direction === "input"
      ? page
          .getByTestId("trace-user-input")
          .locator("[data-trace-media-content]")
      : page.locator("[data-trace-overview-output]")
  await expect(content.first()).toBeVisible()
  await page.waitForTimeout(350)
  return content.first()
}

async function expectPreservedOrder(
  content: ReturnType<Page["locator"]>,
  firstSetCount = 2
) {
  const ordered = await content.evaluate((node, firstSetCount) => {
    const captions = Array.from(node.querySelectorAll("p"))
    const before = captions.find(
      (p) => p.textContent === "Compare these views."
    )!
    const between = captions.find(
      (p) => p.textContent === "Keep this caption between the sets."
    )!
    const after = captions.find(
      (p) => p.textContent === "Choose the clearest view."
    )!
    const cards = node.querySelectorAll("[data-trace-media]")
    const precedes = (a: Element, b: Element) =>
      Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)
    return (
      precedes(before, cards[0]) &&
      precedes(cards[firstSetCount - 1], between) &&
      precedes(between, cards[firstSetCount]) &&
      precedes(cards[cards.length - 1], after)
    )
  }, firstSetCount)
  expect(ordered).toBe(true)
}

async function pdfPixels(card: Locator) {
  return card.locator("canvas").evaluate((canvas: HTMLCanvasElement) => {
    const pixels = canvas
      .getContext("2d")!
      .getImageData(0, 0, canvas.width, canvas.height).data
    return pixels.some(
      (channel, index) =>
        index % 4 === 0 && channel < 128 && pixels[index + 3] === 255
    )
      ? canvas.toDataURL()
      : null
  })
}

async function screenshot(page: Page, name: string) {
  if (!evidenceDir) return
  mkdirSync(evidenceDir, { recursive: true })
  await page.screenshot({
    path: path.join(evidenceDir, `${captureStage}-${name}.png`),
    fullPage: true,
  })
}

for (const direction of ["input", "output"] as const) {
  test(`${direction} multiple images preserve independent consent, preview and text order`, async ({
    page,
  }) => {
    const requests: string[] = []
    await page.route("https://media.example/**", async (route) => {
      requests.push(route.request().url())
      await route.fulfill({ contentType: "image/png", body: image })
    })
    const content = await showMedia(page, direction, [
      { type: "text", text: "Compare these views." },
      imageBlock(1),
      imageBlock(2),
      imageBlock(3),
      { type: "text", text: "Keep this caption between the sets." },
      imageBlock(4),
      imageBlock(5),
      { type: "text", text: "Choose the clearest view." },
    ])
    const cards = content.locator("[data-trace-media]")
    await expect(cards).toHaveCount(5)
    expect(
      await cards.evaluateAll((items) =>
        items.map((item) => item.getAttribute("aria-label"))
      )
    ).toEqual(["Image 1", "Image 2", "Image 3", "Image 4", "Image 5"])
    expect(requests).toEqual([])
    const imageGroups = content.locator('[data-trace-media-group="image"]')
    await expect(imageGroups).toHaveCount(2)
    await expect(imageGroups.nth(0).locator("[data-trace-media]")).toHaveCount(
      3
    )
    await expect(imageGroups.nth(1).locator("[data-trace-media]")).toHaveCount(
      2
    )
    await expect(content.getByText("image/png", { exact: true })).toHaveCount(0)
    const geometry = await imageGroups
      .nth(0)
      .locator("[data-trace-media]")
      .evaluateAll((items) =>
        items.map((item) => {
          const rect = item.getBoundingClientRect()
          return { x: rect.x, y: rect.y }
        })
      )
    expect(geometry[0].y).toBe(geometry[1].y)
    expect(geometry[1].x).toBeGreaterThan(geometry[0].x)
    await expect(content).toContainText(
      /Compare these views.*Keep this caption between the sets.*Choose the clearest view/s
    )
    await expectPreservedOrder(content, 3)
    await screenshot(page, `${direction}-images-unloaded`)
    await cards
      .nth(1)
      .getByRole("button", { name: "Load media", exact: true })
      .click()
    await expect(cards.nth(1).locator("img")).toHaveJSProperty(
      "naturalWidth",
      8
    )
    await expect(cards.nth(0).locator("img")).toHaveCount(0)
    expect(requests).toEqual(["https://media.example/view-2.png"])
    await cards
      .nth(1)
      .getByRole("button", { name: "Enlarge image", exact: true })
      .click()
    const dialog = page.locator("[data-trace-media-dialog]")
    await expect(dialog).toBeVisible()
    await expect(dialog.locator("img")).toHaveAttribute(
      "src",
      "https://media.example/view-2.png"
    )
    await page.keyboard.press("Escape")
    await expect(
      cards.nth(1).getByRole("button", { name: "Enlarge image", exact: true })
    ).toBeFocused()
    await cards
      .nth(1)
      .getByRole("button", { name: "Hide media", exact: true })
      .click()
    await expect(cards.nth(1).locator("img")).toHaveCount(0)
    await cards
      .nth(2)
      .getByRole("button", { name: "Load media", exact: true })
      .click()
    await expect(cards.nth(2).locator("img")).toHaveAttribute(
      "src",
      "https://media.example/view-3.png"
    )
  })
}

for (const direction of ["input", "output"] as const) {
  for (const kind of ["audio", "video", "pdf"] as const) {
    test(`${direction} multiple ${kind} items retain independent player and document state`, async ({
      page,
    }) => {
      const fixture = fixtureMedia[kind]
      const requests: string[] = []
      await page.route("https://media.example/**", async (route) => {
        requests.push(route.request().url())
        await route.fulfill({
          contentType: fixture.mime,
          body: fixture.body,
          headers: { "Access-Control-Allow-Origin": "*" },
        })
      })
      const block = (index: number) => ({
        type: kind === "pdf" ? "document" : kind,
        source: {
          media_type: fixture.mime,
          url: `https://media.example/${kind}-${index}.${fixture.extension}`,
        },
      })
      const content = await showMedia(page, direction, [
        { type: "text", text: "Compare these views." },
        block(1),
        block(2),
        { type: "text", text: "Keep this caption between the sets." },
        block(3),
        { type: "text", text: "Choose the clearest view." },
      ])
      const cards = content.locator("[data-trace-media]")
      await expect(cards).toHaveCount(3)
      for (const index of [0, 1]) {
        await expect(
          cards
            .nth(index)
            .getByText(`${kind}-${index + 1}.${fixture.extension}`, {
              exact: true,
            })
        ).toBeVisible()
      }
      await expectPreservedOrder(content)
      expect(requests).toEqual([])
      await cards
        .nth(1)
        .getByRole("button", { name: "Load media", exact: true })
        .click()
      if (kind === "pdf") {
        await expect(
          cards.nth(1).getByText("1 / 2", { exact: true })
        ).toBeVisible()
        await expect.poll(() => pdfPixels(cards.nth(1))).not.toBeNull()
        const pageOne = await pdfPixels(cards.nth(1))
        await cards
          .nth(1)
          .getByRole("button", { name: "Next", exact: true })
          .click()
        await expect
          .poll(async () => {
            const pageTwo = await pdfPixels(cards.nth(1))
            return pageTwo !== null && pageTwo !== pageOne
          })
          .toBe(true)
        await expect(
          cards.nth(1).getByText("2 / 2", { exact: true })
        ).toBeVisible()
      } else {
        const player = cards.nth(1).locator(kind)
        await expect
          .poll(() =>
            player.evaluate((element: HTMLMediaElement) => element.readyState)
          )
          .toBeGreaterThanOrEqual(2)
        await player.evaluate(async (element: HTMLMediaElement) =>
          element.play()
        )
        await expect
          .poll(() =>
            player.evaluate((element: HTMLMediaElement) => element.currentTime)
          )
          .toBeGreaterThan(0.1)
        await player.evaluate((element: HTMLMediaElement) => element.pause())
      }
      expect(requests).toEqual([
        `https://media.example/${kind}-2.${fixture.extension}`,
      ])
      await cards
        .nth(0)
        .getByRole("button", { name: "Load media", exact: true })
        .click()
      if (kind === "pdf") {
        await expect(
          cards.nth(0).getByText("1 / 2", { exact: true })
        ).toBeVisible()
        await expect(
          cards.nth(1).getByText("2 / 2", { exact: true })
        ).toBeVisible()
        await expect.poll(() => pdfPixels(cards.nth(0))).not.toBeNull()
      } else {
        await expect
          .poll(() =>
            cards
              .nth(0)
              .locator(kind)
              .evaluate((element: HTMLMediaElement) => element.readyState)
          )
          .toBeGreaterThanOrEqual(2)
        await expect(cards.nth(0).locator(kind)).toHaveJSProperty(
          "paused",
          true
        )
        await expect(cards.nth(1).locator(kind)).toHaveJSProperty(
          "paused",
          true
        )
      }
      if (kind === "video") {
        for (const index of [0, 1]) {
          const player = cards.nth(index).locator("video")
          await expect
            .poll(() =>
              player.evaluate((element: HTMLVideoElement) => element.readyState)
            )
            .toBe(4)
          await player.evaluate(async (element: HTMLVideoElement) => {
            element.currentTime = 0.5
            await element.play()
          })
          await expect
            .poll(() =>
              player.evaluate(
                (element: HTMLVideoElement) => element.currentTime
              )
            )
            .toBeGreaterThan(0.65)
          await player.evaluate((element: HTMLVideoElement) => element.pause())
          await expect(player).toHaveJSProperty("paused", true)
        }
        await page.waitForTimeout(500)
      }
      await content
        .locator(`[data-trace-media-group="${kind}"]`)
        .first()
        .scrollIntoViewIfNeeded()
      await screenshot(page, `${direction}-multiple-${kind}`)
      await cards
        .nth(1)
        .getByRole("button", { name: "Hide media", exact: true })
        .click()
      await expect(
        cards.nth(1).locator(kind === "pdf" ? "canvas" : kind)
      ).toHaveCount(0)
      await expect(
        cards.nth(0).locator(kind === "pdf" ? "canvas" : kind)
      ).toBeVisible()
      await expect(
        cards.nth(2).locator(kind === "pdf" ? "canvas" : kind)
      ).toHaveCount(0)
    })
  }
}

test("many images keep the narrow translated layout within the viewport", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.addInitScript(() => localStorage.setItem("theme", "dark"))
  const requests: string[] = []
  await page.route("https://media.example/**", (route) => {
    requests.push(route.request().url())
    return route.abort()
  })
  const content = await showMedia(
    page,
    "output",
    Array.from({ length: 18 }, (_, index) => imageBlock(index + 1)),
    "ja-JP"
  )
  await expect(content.locator("[data-trace-media]")).toHaveCount(18)
  expect(requests).toEqual([])
  const clipped = await content.locator("button").evaluateAll(
    (buttons) =>
      buttons.filter((button) => {
        const rect = button.getBoundingClientRect()
        return rect.width > 0 && (rect.left < 0 || rect.right > innerWidth)
      }).length
  )
  expect(clipped).toBe(0)
  await content.locator("[data-trace-media]").first().scrollIntoViewIfNeeded()
  await screenshot(page, "many-images-ja-dark-narrow")
  await expect(
    content.getByRole("button", {
      name: uiText("ja-JP", "spanContent.loadMedia"),
      exact: true,
    })
  ).toHaveCount(18)
})

test("Chinese desktop collections show images and full-width audio without repeated type copy", async ({
  page,
}) => {
  const previewImages = await page.evaluate(() =>
    ["circle", "square"].map((shape) => {
      const canvas = document.createElement("canvas")
      canvas.width = 320
      canvas.height = 200
      const context = canvas.getContext("2d")!
      context.fillStyle = shape === "circle" ? "#dbeafe" : "#dcfce7"
      context.fillRect(0, 0, 320, 200)
      context.fillStyle = shape === "circle" ? "#2563eb" : "#16a34a"
      if (shape === "circle") {
        context.beginPath()
        context.arc(160, 100, 60, 0, Math.PI * 2)
        context.fill()
      } else context.fillRect(100, 40, 120, 120)
      return canvas.toDataURL().split(",")[1]
    })
  )
  await page.route("https://media.example/**", async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith(".png"))
      return route.fulfill({
        contentType: "image/png",
        body: Buffer.from(
          previewImages[url.pathname.includes("circle") ? 0 : 1],
          "base64"
        ),
      })
    const kind = url.pathname.endsWith(".wav")
      ? "audio"
      : url.pathname.endsWith(".webm")
        ? "video"
        : "pdf"
    return route.fulfill({
      contentType: fixtureMedia[kind].mime,
      body: fixtureMedia[kind].body,
      headers: { "Access-Control-Allow-Origin": "*" },
    })
  })
  const content = await showMedia(
    page,
    "output",
    [
      { type: "text", text: "比较两张候选图，保留更清晰的一张。" },
      {
        type: "image_url",
        image_url: { url: "https://media.example/circle.png" },
      },
      {
        type: "image_url",
        image_url: { url: "https://media.example/square.png" },
      },
      ...(["audio", "video", "pdf"] as const).flatMap((kind) =>
        [1, 2].map((index) => ({
          type: kind === "pdf" ? "document" : kind,
          source: {
            media_type: fixtureMedia[kind].mime,
            url: `https://media.example/${kind}-${index}.${fixtureMedia[kind].extension}`,
          },
        }))
      ),
    ],
    "zh-CN"
  )
  const groups = content.locator("[data-trace-media-group]")
  await expect(groups).toHaveCount(4)
  for (let index = 0; index < 4; index++)
    await expect(groups.nth(index).locator("[data-trace-media]")).toHaveCount(2)
  await expect(
    content.getByText(/^(image\/png|audio\/wav|video\/webm|application\/pdf)$/)
  ).toHaveCount(0)
  const images = groups.nth(0).locator("[data-trace-media]")
  const audio = groups.nth(1).locator("[data-trace-media]")
  for (let index = 0; index < 2; index++) {
    await images
      .nth(index)
      .getByRole("button", {
        name: uiText("zh-CN", "spanContent.loadMedia"),
        exact: true,
      })
      .click()
    await expect(images.nth(index).locator("img")).toHaveJSProperty(
      "naturalWidth",
      320
    )
    await audio
      .nth(index)
      .getByRole("button", {
        name: uiText("zh-CN", "spanContent.loadMedia"),
        exact: true,
      })
      .click()
    await expect
      .poll(() =>
        audio
          .nth(index)
          .locator("audio")
          .evaluate((element: HTMLMediaElement) => element.readyState)
      )
      .toBeGreaterThanOrEqual(2)
  }
  const rows = await audio.evaluateAll((items) =>
    items.map((item) => {
      const rect = item.getBoundingClientRect()
      return { x: rect.x, y: rect.y, width: rect.width }
    })
  )
  expect(rows[0].x).toBe(rows[1].x)
  expect(rows[0].width).toBe(rows[1].width)
  expect(rows[1].y).toBeGreaterThan(rows[0].y)
  await content.scrollIntoViewIfNeeded()
  await screenshot(page, "mixed-zh-light-desktop")
  if (evidenceDir)
    await content.screenshot({
      path: path.join(evidenceDir, `${captureStage}-mixed-zh-light-detail.png`),
    })
})
