import { expect, type Locator } from "@playwright/test"
import { uiText } from "./e2e/traceLabs.fixtures"

type RecordValue = Record<string, unknown>

async function expandPreview(section: Locator) {
  const preview = section.getByRole("button", {
    name: /^(?:Show all \(|展开全部)/,
  })
  if (await preview.count()) await preview.click()
  const longStrings = section.getByRole("button", { name: /^\.\.\.expand \(/ })
  while (await longStrings.count()) await longStrings.first().click()
  const more = section.getByRole("button", {
    name: /^Show \d+ more characters?$/,
  })
  while (await more.count()) await more.first().click()
  const collapsed = section.locator(
    'button[aria-expanded="false"]:not([aria-haspopup])'
  )
  while (await collapsed.count()) {
    const id = await collapsed.first().getAttribute("aria-controls")
    await collapsed.first().click()
    if (id)
      await expect(
        section.locator(`button[aria-controls="${id}"]`)
      ).toHaveAttribute("aria-expanded", "true")
  }
  const longMetadata = section.locator("span.line-clamp-2.cursor-pointer")
  while (await longMetadata.count()) await longMetadata.first().click()
}

async function assertJsonRendered(content: Locator, value: unknown) {
  if (value !== null && typeof value === "object") {
    const entries = Object.entries(value)
    if (!entries.length) {
      await expect(content).toContainText(Array.isArray(value) ? "[" : "{")
      await expect(content).toContainText(Array.isArray(value) ? "]" : "}")
    }
    for (const [key, child] of entries) {
      await expect(content).toContainText(`${JSON.stringify(key)}:`)
      await assertJsonRendered(content, child)
    }
  } else {
    await expect(
      content.getByText(JSON.stringify(value), { exact: true }).first()
    ).toBeVisible()
  }
}

function chatMessages(value: unknown): RecordValue[] | undefined {
  if (!Array.isArray(value) || !value.length) return undefined
  return value.every(
    (item) =>
      item &&
      typeof item === "object" &&
      (typeof item.role === "string" ||
        ["human", "ai", "system", "tool"].includes(item.type))
  )
    ? (value as RecordValue[])
    : undefined
}

async function assertFormattedRendered(content: Locator, value: unknown) {
  const messages = chatMessages(value)
  if (messages) {
    for (const message of messages) {
      // Chat presentation omits transport keys and displays message content.
      const text = message.content
      if (typeof text === "string" && text.length)
        await expect(content).toContainText(text)
      else if (text !== undefined && text !== null && text !== "")
        await assertFormattedRendered(content, text)
    }
    return
  }
  if (value !== null && typeof value === "object") {
    const record = value as RecordValue
    if (!Array.isArray(value) && chatMessages(record.messages)) {
      await assertFormattedRendered(content, record.messages)
      const rest = Object.fromEntries(
        Object.entries(record).filter(([key]) => key !== "messages")
      )
      if (Object.keys(rest).length) await assertFormattedRendered(content, rest)
      return
    }
    const entries = Object.entries(value)
    if (!entries.length) await expect(content).toContainText("empty object")
    for (const [key, child] of entries) {
      const rows = content.getByRole("row").filter({
        has: content.page().getByRole("cell").getByText(key, { exact: true }),
      })
      if (child !== null && typeof child === "object") {
        await expect(rows.first()).toBeVisible()
        await assertFormattedRendered(content, child)
      } else {
        const text = typeof child === "string" ? child : JSON.stringify(child)
        const row = rows.first()
        await expect(row).toBeVisible()
        // Long values have a separate expansion button in the same cell.
        const cell = row.getByRole("cell").nth(1)
        const literal = cell.locator("[id]").first()
        await expect((await literal.count()) ? literal : cell).toHaveText(text)
      }
    }
  } else {
    await expect(content).toHaveText(
      typeof value === "string" ? value : JSON.stringify(value)
    )
  }
}

export async function expandDetailSection(scope: Locator, title: string) {
  const section = scope.locator(
    `[data-testid="trace-detail-section"][data-section-title="${title}"]`
  )
  await expect(section).toBeVisible()
  const toggle = section.locator("button[aria-expanded][aria-controls]").first()
  if ((await toggle.getAttribute("aria-expanded")) === "false")
    await toggle.click()
  await expect(toggle).toHaveAttribute("aria-expanded", "true")
  const id = await toggle.getAttribute("aria-controls")
  await expect(section.locator(`[id="${id}"]`)).toBeVisible()
  return section
}

async function assertEnhancedIO(
  scope: Locator,
  field: "input" | "output",
  value: unknown
) {
  const page = scope.page()
  if ((await scope.getAttribute("data-testid")) !== "native-span-detail") {
    if (field === "input")
      await expandDetailSection(
        scope,
        uiText("en-US", "traceLabels.originalInput")
      )
    const section = scope.getByTestId(`trace-io-${field}`)
    await expect(section).toBeVisible()
    await expandPreview(section)
    if (value === null) await expect(section).toContainText("Unknown")
    else
      await assertFormattedRendered(
        section.getByTestId("trace-io-content"),
        value
      )
    return
  }
  const enhancement = scope.getByTestId("span-content-enhancement")
  await expect(enhancement).toBeVisible()
  const conversation =
    (await enhancement.getAttribute("data-content-kind")) === "conversation"
  const section = enhancement.locator("section").filter({
    has: page.getByRole("heading", {
      name: uiText(
        "en-US",
        conversation
          ? field === "output"
            ? "spanContent.latestOutput"
            : "spanContent.lastUser"
          : field === "input"
            ? "spanContent.arguments"
            : "spanContent.result"
      ),
      exact: true,
    }),
  })
  await expect(section).toBeVisible()
  const excerpt = section.locator("[data-content-excerpt]")
  await expect(excerpt).toHaveCount(1)
  const semantic =
    conversation &&
    value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    "role" in value &&
    "content" in value
      ? (value as RecordValue).content
      : value
  const text =
    typeof semantic === "string" ? semantic : JSON.stringify(semantic, null, 2)
  const json = excerpt.getByRole("button", { name: "JSON", exact: true })
  if (await json.count()) {
    await json.click()
    await expect(json).toHaveAttribute("aria-pressed", "true")
  }
  const body = excerpt.locator("[data-content-body]")
  await expect(body).toHaveText(
    text === "" ? uiText("en-US", "spanContent.emptyValue") : text
  )
  // JSON can be shorter than the previous Tree. Use the rendered dimensions,
  // rather than a transient expand button left over from that presentation.
  if (
    await body.evaluate(
      (element) => element.scrollHeight > element.clientHeight + 1
    )
  ) {
    await excerpt
      .getByRole("button", {
        name: uiText("en-US", "spanContent.expandText"),
        exact: true,
      })
      .click()
  }
  await expect
    .poll(() =>
      body.evaluate(
        (element) => element.scrollHeight <= element.clientHeight + 1
      )
    )
    .toBe(true)
  await excerpt
    .getByRole("button", {
      name: uiText("en-US", "spanContent.copyContent"),
      exact: true,
    })
    .click()
  await expect
    .poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toBe(text)
}

/** Original Tree/JSON presentation and copy must retain the independent source value. */
export async function assertRenderedIO(
  scope: Locator,
  field: "input" | "output",
  value: unknown,
  present = true,
  revealOriginal?: () => Promise<void>,
  originalValue: unknown = value
) {
  const section = scope.getByTestId(`trace-io-${field}`)
  const page = scope.page()
  const modes = page.getByTestId("trace-content-modes")
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"])
  await modes
    .getByRole("button", {
      name: uiText("en-US", "traceView.enhanced"),
      exact: true,
    })
    .click()
  if (present) await assertEnhancedIO(scope, field, value)
  await modes
    .getByRole("button", {
      name: uiText("en-US", "traceView.raw"),
      exact: true,
    })
    .click()
  if (!present) {
    expect(value, "Absent IO must retain a null projection").toBeNull()
    await expect(section).toHaveCount(0)
    return
  }
  await revealOriginal?.()
  await expect(section).toBeVisible()
  const content = section.getByTestId("trace-io-content")
  for (const mode of [uiText("en-US", "traceView.tree"), "JSON"]) {
    await modes.getByRole("button", { name: mode, exact: true }).click()
    await revealOriginal?.()
    await expandPreview(section)
    if (mode === "JSON") await assertJsonRendered(content, originalValue)
    else await assertFormattedRendered(content, originalValue)
    const copy =
      typeof originalValue === "string"
        ? originalValue
        : JSON.stringify(originalValue, null, 2)
    await section.getByTitle("Copy", { exact: true }).first().click()
    await expect
      .poll(() => page.evaluate(() => navigator.clipboard.readText()))
      .toBe(copy)
  }
}
