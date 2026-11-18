import { expect, test, type Page } from "@playwright/test"
import { prepare, publicTrace, trace, uiText } from "./traceLabs.fixtures"

test("inline Trace documents render with literal source and isolated HTML", async ({
  page,
}, testInfo) => {
  const html = `<!DOCTYPE html><html lang="en" dir="ltr"><head>
<style>body.report h1 { color: rgb(24, 90, 120) } body { background-image: url(https://trace-preview.invalid/background) }</style>
<meta http-equiv="refresh" content="0; url=https://trace-preview.invalid/redirect">
</head><body class="report" style="padding: 8px">
<!-- A literal </html> here is not a document boundary. -->
<h1>Regional report</h1><table><tr><th>Market</th><td>Japan</td></tr></table>
<img src="https://trace-preview.invalid/image"><a href="https://trace-preview.invalid/link">Reference</a>
<script>parent.postMessage("trace script ran", "*"); const closingTag = "</html>"</script>
</body></html>`
  const markdown =
    "# Market notes\n\n| Market | Priority |\n| --- | --- |\n| Japan | High |"
  const requests: string[] = []
  await page.route("https://trace-preview.invalid/**", (route) => {
    requests.push(route.request().url())
    return route.abort()
  })
  await page.addInitScript(() => {
    window.addEventListener("message", (event) => {
      if (event.data === "trace script ran")
        document.documentElement.dataset.traceScriptRan = "true"
    })
  })
  await prepare(page)
  await publicTrace(page, {
    ...trace,
    spans: trace.spans.map((span, index) =>
      index === 0
        ? {
            ...span,
            attributes: {
              "gen_ai.operation.name": "chat",
              "gen_ai.input.messages": JSON.stringify([
                {
                  role: "user",
                  content: `Review these notes.\n\n\`\`\`md\n${markdown}\n\`\`\``,
                },
              ]),
            },
          }
        : span
    ),
    semantics: {
      ...trace.semantics!,
      output: {
        message: "Report generated.",
        params: JSON.stringify({ content: html }),
      },
    },
  })

  const htmlButton = page
    .getByRole("button", {
      name: uiText("en-US", "traceDocuments.html"),
    })
    .first()
  await expect(htmlButton).toBeVisible()
  await expect(page.locator("iframe[srcdoc]")).toHaveCount(0)
  await htmlButton.click()
  const dialog = page.getByRole("dialog", { name: "HTML", exact: true })
  const iframe = dialog.locator("iframe")
  await expect(iframe).toHaveAttribute("sandbox", "")
  const rendered = dialog.frameLocator("iframe")
  await expect(rendered.locator("html")).toHaveAttribute("lang", "en")
  await expect(rendered.locator("html")).toHaveAttribute("dir", "ltr")
  await expect(rendered.locator("body")).toHaveCSS("padding-top", "8px")
  await expect(
    rendered.getByRole("heading", { name: "Regional report" })
  ).toHaveCSS("color", "rgb(24, 90, 120)")
  await expect(rendered.getByRole("cell", { name: "Japan" })).toBeVisible()
  await expect(
    rendered.locator("a[href], img[src], script, meta[http-equiv=refresh]")
  ).toHaveCount(0)
  await expect(page.locator("[data-trace-script-ran]")).toHaveCount(0)
  expect(requests).toEqual([])
  await dialog.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("html-preview.png"),
  })
  await dialog
    .getByRole("radio", {
      name: uiText("en-US", "traceDocuments.source"),
      exact: true,
    })
    .click()
  expect(await dialog.locator("pre").textContent()).toBe(html)
  await expect(iframe).toHaveCount(0)
  await page.keyboard.press("Escape")
  await expect(htmlButton).toBeFocused()

  await page.keyboard.press("Enter")
  await expect(
    rendered.getByRole("heading", { name: "Regional report" })
  ).toBeVisible()
  await iframe.focus()
  await expect(iframe).toBeFocused()
  const close = dialog.getByRole("button", { name: "Close", exact: true })
  for (let steps = 0; steps < 3; steps++) {
    if (await close.evaluate((element) => element === document.activeElement))
      break
    await page.keyboard.press("Shift+Tab")
  }
  await expect(close).toBeFocused()
  await page.keyboard.press("Enter")
  await expect(dialog).toHaveCount(0)
  await expect(htmlButton).toBeFocused()

  await page
    .getByTestId("trace-user-input")
    .getByRole("button", {
      name: uiText("en-US", "traceDocuments.markdown"),
    })
    .click()
  const markdownDialog = page.getByRole("dialog", {
    name: "Markdown",
    exact: true,
  })
  await expect(
    markdownDialog.getByRole("heading", { name: "Market notes" })
  ).toBeVisible()
  await expect(markdownDialog.getByRole("cell", { name: "High" })).toBeVisible()
  await markdownDialog.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("markdown-preview.png"),
  })
  await page.keyboard.press("Escape")
  await page
    .getByTestId("trace-content-modes")
    .getByRole("button", {
      name: uiText("en-US", "traceView.raw"),
      exact: true,
    })
    .click()
  await expect(
    page.locator("[data-trace-document], iframe[srcdoc]")
  ).toHaveCount(0)
})

test("multiple HTML reports are distinguishable without numbering different formats", async ({
  page,
}) => {
  await prepare(page)
  await publicTrace(page, {
    ...trace,
    semantics: {
      ...trace.semantics!,
      output: {
        first: "<html><body><h1>First report</h1></body></html>",
        second: "<html><body><h1>Second report</h1></body></html>",
        notes: "# Notes\n\n- Verify both reports.",
      },
    },
  })

  const htmlLabel = uiText("en-US", "traceDocuments.html")
  const markdownLabel = uiText("en-US", "traceDocuments.markdown")
  await expect(
    page.getByRole("button", { name: markdownLabel, exact: true })
  ).toBeVisible()
  for (const [field, heading] of [
    ["first", "First report"],
    ["second", "Second report"],
  ]) {
    const trigger = page.getByRole("button", {
      name: `${htmlLabel} · ${field}`,
      exact: true,
    })
    await trigger.click()
    const dialog = page.getByRole("dialog", { name: "HTML", exact: true })
    await expect(
      dialog.frameLocator("iframe").getByRole("heading", { name: heading })
    ).toBeVisible()
    await page.keyboard.press("Escape")
    await expect(trigger).toBeFocused()
  }
})

test("message fragments assemble within one call and later calls keep their own documents", async ({
  page,
}) => {
  const linkedReport = "https://trace-links.invalid/multipart-report.html"
  const requests: string[] = []
  await page.route(linkedReport, (route) => {
    requests.push(route.request().url())
    return route.abort()
  })
  await prepare(page)
  const input = ["<html><body><h1>Input brief", "</h1></body></html>"]
  const output = [
    "<!doctype html><html><body><h1>",
    "First call result",
    "</h1><p>Validated.</p></body></html>",
  ]
  const messages = (role: "user" | "assistant", parts: string[]) =>
    JSON.stringify([
      {
        role,
        content: parts.map((text) => ({
          type: role === "user" ? "input_text" : "output_text",
          text,
        })),
      },
    ])
  const call = (id: string, attrs: Record<string, string>) => ({
    ...trace.spans[2],
    span_id: id,
    name: id,
    attributes: {
      "gen_ai.operation.name": "chat",
      "gen_ai.span.kind": "LLM",
      ...attrs,
    },
  })
  await publicTrace(page, {
    ...trace,
    spans: [
      trace.spans[0],
      call("first-call", {
        "gen_ai.input.messages": messages("user", input),
        "gen_ai.output.messages": messages("assistant", output),
      }),
      call("second-call", {
        "gen_ai.output.messages": messages("assistant", [
          "# Second call notes\n\n- Independent result.\n\n",
          `See [the linked report](${linkedReport}).`,
        ]),
      }),
    ],
  })
  const selectCall = (id: string) =>
    page
      .locator(`[data-testid="native-chain-span"][data-span-id="${id}"]`)
      .click()
  const panel = page.getByTestId("native-span-detail")
  const section = (direction: string) =>
    panel.locator(`[data-conversation-io="${direction}"]`)

  await selectCall("first-call")
  for (const [direction, heading, source] of [
    ["input", "Input brief", input.join("")],
    ["output", "First call result", output.join("")],
  ]) {
    await section(direction).locator("[data-message-disclosure]").click()
    const trigger = section(direction).getByRole("button", {
      name: uiText("en-US", "traceDocuments.html"),
      exact: true,
    })
    await expect(trigger).toHaveCount(1)
    await trigger.click()
    const dialog = page.getByRole("dialog", { name: "HTML", exact: true })
    await expect(
      dialog.frameLocator("iframe").getByRole("heading", { name: heading })
    ).toBeVisible()
    await dialog
      .getByRole("radio", { name: uiText("en-US", "traceDocuments.source") })
      .click()
    await expect(dialog.locator("pre")).toHaveText(source)
    await page.keyboard.press("Escape")
    await expect(trigger).toBeFocused()
  }

  await selectCall("second-call")
  await expect(panel).toHaveAttribute("data-span-id", "second-call")
  const secondOutput = panel.getByTestId("trace-io-output")
  await expect(
    panel.locator(
      '[data-trace-document="html"]:not([data-trace-document-link])'
    )
  ).toHaveCount(0)
  await expect(secondOutput.locator("[data-trace-document-link]")).toHaveCount(
    1
  )
  await expect(
    secondOutput.getByText("the linked report", { exact: true })
  ).toBeVisible()
  await expect(
    secondOutput.locator("[data-trace-document-link-fallback]")
  ).toHaveCount(0)
  await expect(secondOutput.locator(`a[href="${linkedReport}"]`)).toHaveCount(0)
  expect(requests).toEqual([])
  await panel
    .getByRole("button", {
      name: uiText("en-US", "traceDocuments.markdown"),
      exact: true,
    })
    .click()
  await expect(
    page.getByRole("dialog", { name: "Markdown" }).getByRole("heading", {
      name: "Second call notes",
    })
  ).toBeVisible()
})

test("partial HTML and Markdown preview received content and copy exact source", async ({
  page,
}, testInfo) => {
  const html = [
    "<!doctype html>",
    '<html lang="en"><head><title>Draft report</title></head>',
    "<body><h1>Draft report</h1>",
    "<p>First result has arrived.",
    "<em",
  ].join("\n")
  const markdown = "# Draft notes\n\n- Completed first item\n- Next item"
  await prepare(page)
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: async (value: string) => {
          document.documentElement.dataset.fixtureClipboard = value
        },
      },
    })
  })
  await publicTrace(page, {
    ...trace,
    spans: trace.spans.map((span, index) =>
      index === 0
        ? {
            ...span,
            attributes: {
              "gen_ai.operation.name": "chat",
              "gen_ai.input.messages": JSON.stringify([
                {
                  role: "user",
                  content: "Review this draft.\n\n```md\n" + markdown,
                },
              ]),
            },
          }
        : span
    ),
    semantics: { ...trace.semantics!, output: { draft: html } },
  })

  const htmlTrigger = page
    .locator("[data-trace-overview-output]")
    .locator('[data-trace-document="html"]')
  const markdownTrigger = page
    .getByTestId("trace-user-input")
    .locator('[data-trace-document="markdown"]')

  await expect(htmlTrigger).toBeVisible()
  await expect(markdownTrigger).toBeVisible()
  await expect(page.locator("iframe[srcdoc]")).toHaveCount(0)
  for (const [format, trigger, source] of [
    ["HTML", htmlTrigger, html],
    ["Markdown", markdownTrigger, markdown],
  ] as const) {
    await trigger.click()
    const dialog = page.getByRole("dialog", { name: format, exact: true })
    if (format === "HTML") {
      const iframe = dialog.locator("iframe")
      await expect(iframe).toHaveAttribute("sandbox", "")
      await expect(iframe).toHaveAttribute("referrerpolicy", "no-referrer")
      const rendered = dialog.frameLocator("iframe")
      await expect(
        rendered.getByRole("heading", { name: "Draft report" })
      ).toBeVisible()
      await expect(
        rendered.getByText("First result has arrived.")
      ).toBeVisible()
      await expect(
        rendered.locator('meta[http-equiv="Content-Security-Policy"]')
      ).toHaveAttribute("content", /default-src 'none'/)
    } else {
      await expect(
        dialog.getByRole("heading", { name: "Draft notes" })
      ).toBeVisible()
      await expect(dialog.getByRole("listitem")).toHaveText([
        "Completed first item",
        "Next item",
      ])
    }
    await dialog.screenshot({
      animations: "disabled",
      path: testInfo.outputPath(`partial-${format.toLowerCase()}.png`),
    })
    await dialog
      .getByRole("radio", { name: uiText("en-US", "traceDocuments.source") })
      .click()
    expect(await dialog.locator("pre").textContent()).toBe(source)
    await expect(dialog.locator("iframe")).toHaveCount(0)
    await dialog
      .getByRole("button", {
        name: uiText("en-US", "spanContent.copyContent"),
        exact: true,
      })
      .click()
    await expect
      .poll(() => page.locator("html").getAttribute("data-fixture-clipboard"))
      .toBe(source)
    await page.keyboard.press("Escape")
    await expect(trigger).toBeFocused()
  }
})

async function traceWithDocumentInput(page: Page, content: string | unknown[]) {
  await prepare(page)
  await publicTrace(page, {
    ...trace,
    spans: trace.spans.map((span, index) =>
      index === 0
        ? {
            ...span,
            attributes: {
              "gen_ai.operation.name": "chat",
              "gen_ai.input.messages": JSON.stringify([
                { role: "user", content },
              ]),
            },
          }
        : span
    ),
  })
  return page.getByTestId("trace-user-input")
}

test("HTML and Markdown input files load independently in a static preview", async ({
  page,
}, testInfo) => {
  const html =
    '<!doctype html><html><body><h1>Uploaded HTML</h1><p>Static file body.</p><img src="https://trace-preview.invalid/uploaded-image" alt="Sample"><script>document.documentElement.dataset.fixtureMarker = "ran"</script></body></html>'
  const markdown = "# Uploaded Markdown\n\n- First saved note"
  const storedMarkdown =
    "# Stored Markdown\n\n- Read through the attachment endpoint"
  const file = (mime: string, filename: string, source: string) => ({
    type: "file",
    mime_type: mime,
    filename,
    data: Buffer.from(source, "utf8").toString("base64"),
  })
  const id = "b".repeat(64)
  const apiPath = "/api/v1/trace-attachments/" + id + "/content"
  const attachmentRequests: Array<{ method: string; query: string }> = []
  const resourceRequests: Array<string> = []
  page.on("request", (request) => {
    const url = new URL(request.url())
    if (url.pathname === apiPath)
      attachmentRequests.push({ method: request.method(), query: url.search })
  })
  await page.route("https://trace-preview.invalid/**", (route) => {
    resourceRequests.push(route.request().url())
    return route.abort()
  })
  const summary = await traceWithDocumentInput(page, [
    file("text/html", "report.html", html),
    file("text/markdown", "notes.md", markdown),
    {
      type: "aivision_attachment",
      id,
      spaceId: "space-a",
      agentId: "agent-a",
      mimeType: "text/markdown",
    },
  ])
  await page.route("**" + apiPath + "*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/markdown; charset=utf-8",
      body: storedMarkdown,
    })
  )

  const files = summary.locator("[data-trace-media]")
  const load = (index: number) =>
    files.nth(index).getByRole("button", {
      name: uiText("en-US", "spanContent.loadMedia"),
      exact: true,
    })
  await expect(files).toHaveCount(3)
  await expect(page.locator("iframe[srcdoc]")).toHaveCount(0)
  expect(attachmentRequests).toEqual([])
  expect(resourceRequests).toEqual([])

  await load(0).click()
  const htmlFile = files.nth(0)
  const iframe = htmlFile.locator("iframe[srcdoc]")
  await expect(iframe).toHaveAttribute("sandbox", "")
  await expect(iframe).toHaveAttribute("referrerpolicy", "no-referrer")
  const rendered = htmlFile.frameLocator("iframe[srcdoc]")
  await expect(
    rendered.getByRole("heading", { name: "Uploaded HTML" })
  ).toBeVisible()
  await expect(rendered.getByText("Static file body.")).toBeVisible()
  await expect(
    rendered.locator("script, img[src], [data-fixture-marker]")
  ).toHaveCount(0)
  await expect(load(1)).toBeVisible()
  await expect(load(2)).toBeVisible()
  expect(attachmentRequests).toEqual([])
  expect(resourceRequests).toEqual([])

  const expand = htmlFile.getByRole("button", {
    name: uiText("en-US", "traceMedia.preview"),
    exact: true,
  })
  await expand.click()
  const expanded = page.locator("[data-trace-media-dialog]")
  await expect(expanded.locator("iframe[srcdoc]")).toHaveAttribute(
    "sandbox",
    ""
  )
  await expect(
    expanded.frameLocator("iframe[srcdoc]").getByRole("heading", {
      name: "Uploaded HTML",
    })
  ).toBeVisible()
  await expect(
    expanded.getByRole("heading", { name: "HTML", exact: true })
  ).toBeFocused()
  await page.keyboard.press("Escape")
  await expect(expanded).toHaveCount(0)
  await expect(expand).toBeFocused()

  await load(1).click()
  await expect(
    files.nth(1).getByRole("heading", { name: "Uploaded Markdown" })
  ).toBeVisible()
  await expect(files.nth(1).getByRole("listitem")).toHaveText(
    "First saved note"
  )
  await expect(load(2)).toBeVisible()
  expect(attachmentRequests).toEqual([])

  await load(2).click()
  await expect(
    files.nth(2).getByRole("heading", { name: "Stored Markdown" })
  ).toBeVisible()
  await expect(files.nth(2).getByRole("listitem")).toHaveText(
    "Read through the attachment endpoint"
  )
  expect(attachmentRequests).toEqual([
    { method: "GET", query: "?space_id=space-a&agent_uuid=agent-a" },
  ])
  expect(resourceRequests).toEqual([])
  await summary.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("input-document-files.png"),
  })
})

test("inline HTML input renders static content without running its script", async ({
  page,
}, testInfo) => {
  const source =
    '<!doctype html><html><body><h1>Inline user report</h1><p>Read this report.</p><img src="https://trace-preview.invalid/inline-image" alt="Sample"><script>document.documentElement.dataset.fixtureMarker = "ran"</script></body></html>'
  const resourceRequests: string[] = []
  await page.route("https://trace-preview.invalid/**", (route) => {
    resourceRequests.push(route.request().url())
    return route.abort()
  })
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: async (value: string) => {
          document.documentElement.dataset.fixtureClipboard = value
        },
      },
    })
  })

  const summary = await traceWithDocumentInput(page, source)
  const trigger = summary.getByRole("button", {
    name: uiText("en-US", "traceDocuments.html"),
    exact: true,
  })
  await expect(trigger).toBeVisible()
  await expect(page.locator("iframe[srcdoc]")).toHaveCount(0)
  await trigger.click()
  const modal = page.getByRole("dialog", { name: "HTML", exact: true })
  const frame = modal.locator("iframe[srcdoc]")
  await expect(frame).toHaveAttribute("sandbox", "")
  await expect(frame).toHaveAttribute("referrerpolicy", "no-referrer")
  const rendered = modal.frameLocator("iframe[srcdoc]")
  await expect(
    rendered.getByRole("heading", { name: "Inline user report" })
  ).toBeVisible()
  await expect(rendered.getByText("Read this report.")).toBeVisible()
  await expect(
    rendered.locator("script, img[src], [data-fixture-marker]")
  ).toHaveCount(0)
  expect(resourceRequests).toEqual([])
  await modal.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("inline-html-user-input.png"),
  })

  await modal
    .getByRole("radio", { name: uiText("en-US", "traceDocuments.source") })
    .click()
  await expect(frame).toHaveCount(0)
  expect(await modal.locator("pre").textContent()).toBe(source)
  await modal
    .getByRole("button", {
      name: uiText("en-US", "spanContent.copyContent"),
      exact: true,
    })
    .click()
  await expect
    .poll(() => page.locator("html").getAttribute("data-fixture-clipboard"))
    .toBe(source)
  await page.keyboard.press("Escape")
  await expect(trigger).toBeFocused()
  expect(resourceRequests).toEqual([])
})

test("stored plaintext HTML opens through the sandboxed document preview after loading", async ({
  page,
}) => {
  const storedHTML =
    "<!doctype html><html><body><h1>Stored HTML report</h1><p>Résumé from the attached file.</p></body></html>"
  const id = "c".repeat(64)
  const apiPath = "/api/v1/trace-attachments/" + id + "/content"
  const requested: string[] = []
  page.on("request", (request) => {
    const url = new URL(request.url())
    if (url.pathname === apiPath) requested.push(url.search)
  })
  const summary = await traceWithDocumentInput(page, [
    {
      type: "aivision_attachment",
      id,
      spaceId: "space-a",
      agentId: "agent-a",
      mimeType: "text/plain",
    },
  ])
  await page.route("**" + apiPath + "*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/plain; charset=utf-8",
      body: storedHTML,
    })
  )
  const card = summary.locator("[data-trace-media]")
  const preview = card.getByRole("button", {
    name: uiText("en-US", "traceDocuments.html"),
    exact: true,
  })
  const load = card.getByRole("button", {
    name: uiText("en-US", "spanContent.loadMedia"),
    exact: true,
  })
  await expect(load).toBeVisible()
  await expect(preview).toHaveCount(0)
  expect(requested).toEqual([])
  await load.click()
  await expect(preview).toBeVisible()
  expect(requested).toEqual(["?space_id=space-a&agent_uuid=agent-a"])
  await expect(card.locator("a[href]")).toHaveCount(0)
  await expect(page.locator("iframe[srcdoc]")).toHaveCount(0)
  await preview.click()

  const dialog = page.getByRole("dialog", { name: "HTML", exact: true })
  await expect(dialog.locator("iframe[srcdoc]")).toHaveAttribute("sandbox", "")
  await expect(
    dialog.frameLocator("iframe[srcdoc]").getByRole("heading", {
      name: "Stored HTML report",
    })
  ).toBeVisible()
  await expect(
    dialog
      .frameLocator("iframe[srcdoc]")
      .getByText("Résumé from the attached file.")
  ).toBeVisible()
  await dialog
    .getByRole("radio", { name: uiText("en-US", "traceDocuments.source") })
    .click()
  expect(await dialog.locator("pre").textContent()).toBe(storedHTML)
  await page.keyboard.press("Escape")
  await expect(preview).toBeFocused()
  expect(requested).toEqual(["?space_id=space-a&agent_uuid=agent-a"])
})

test("declared HTML input URL loads once without raw navigation or embedded requests", async ({
  page,
}) => {
  const url = "https://trace-preview.invalid/report.html"
  const html =
    '<!doctype html><html><body><h1>External HTML report</h1><img src="https://trace-preview.invalid/embedded-image" alt="Sample"></body></html>'
  const loads: Array<{ method: string; headers: Record<string, string> }> = []
  const embeddedRequests: string[] = []
  await page.route("https://trace-preview.invalid/**", async (route) => {
    const request = route.request()
    if (request.url() !== url) {
      embeddedRequests.push(request.url())
      return route.abort()
    }
    loads.push({
      method: request.method(),
      headers: await request.allHeaders(),
    })
    return route.fulfill({
      status: 200,
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Access-Control-Allow-Origin": "*",
      },
      body: html,
    })
  })
  const summary = await traceWithDocumentInput(page, [
    { type: "file", mime_type: "text/html", url },
  ])
  const card = summary.locator("[data-trace-media]")
  const load = card.getByRole("button", {
    name: uiText("en-US", "spanContent.loadMedia"),
    exact: true,
  })
  await expect(load).toBeVisible()
  await expect(card.locator("a[href]")).toHaveCount(0)
  await expect(card.locator("iframe[srcdoc]")).toHaveCount(0)
  expect(loads).toEqual([])
  expect(embeddedRequests).toEqual([])
  await load.click()

  await expect(card.locator("iframe[srcdoc]")).toHaveAttribute("sandbox", "")
  const frame = card.frameLocator("iframe[srcdoc]")
  await expect(
    frame.getByRole("heading", { name: "External HTML report" })
  ).toBeVisible()
  await expect(frame.locator("img[src]")).toHaveCount(0)
  await expect(card.locator("a[href]")).toHaveCount(0)
  expect(loads).toHaveLength(1)
  expect(loads[0].method).toBe("GET")
  expect(loads[0].headers).not.toHaveProperty("referer")
  expect(loads[0].headers).not.toHaveProperty("authorization")
  expect(loads[0].headers).not.toHaveProperty("cookie")
  expect(embeddedRequests).toEqual([])
})

test("recognizable HTML links in Trace input and output load only on explicit preview", async ({
  page,
}, testInfo) => {
  const reports = [
    ["https://trace-links.invalid/input.HTML?version=7", "Input link report"],
    ["https://trace-links.invalid/output.htm", "Output link report"],
  ] as const
  const requests: string[] = []
  const unexpected: string[] = []
  await page.route("https://trace-links.invalid/**", async (route) => {
    const url = route.request().url()
    const report = reports.find(([source]) => source === url)
    if (!report) {
      unexpected.push(url)
      await route.abort()
      return
    }
    const headers = await route.request().allHeaders()
    expect(route.request().method()).toBe("GET")
    expect(headers).not.toHaveProperty("referer")
    expect(headers).not.toHaveProperty("cookie")
    expect(headers).not.toHaveProperty("authorization")
    requests.push(url)
    await route.fulfill({
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Access-Control-Allow-Origin": "*",
      },
      body: `<html><body><h1>${report[1]}</h1><img src="https://trace-links.invalid/embedded" alt="Chart summary"><script>document.documentElement.dataset.previewMarker = "ran"</script></body></html>`,
    })
  })
  await prepare(page)
  await publicTrace(page, {
    ...trace,
    spans: trace.spans.map((span, index) =>
      index === 0
        ? {
            ...span,
            attributes: {
              "gen_ai.operation.name": "chat",
              "gen_ai.input.messages": JSON.stringify([
                {
                  role: "user",
                  content: `[Read the input report](${reports[0][0]}) before reviewing.`,
                },
              ]),
            },
          }
        : span
    ),
    semantics: {
      ...trace.semantics!,
      output: {
        report: reports[1][0],
        documentation: "https://trace-links.invalid/about",
        command: "print('https://trace-links.invalid/example.html')",
      },
    },
  })
  const input = page.getByTestId("trace-user-input")
  const output = page.locator("[data-trace-overview-output]")
  const buttons = [input, output].map((panel) =>
    panel.locator("[data-trace-document-link]")
  )
  for (const button of buttons) await expect(button).toHaveCount(1)
  expect(requests).toEqual([])
  expect(unexpected).toEqual([])
  await expect(page.locator("iframe[srcdoc]")).toHaveCount(0)

  for (const [i, button] of buttons.entries()) {
    await button.click()
    const dialog = page.getByRole("dialog", { name: "HTML", exact: true })
    const frame = dialog.locator("iframe[srcdoc]")
    await expect(frame).toHaveAttribute("sandbox", "")
    await expect(frame).toHaveAttribute("referrerpolicy", "no-referrer")
    const body = dialog.frameLocator("iframe[srcdoc]")
    await expect(
      body.getByRole("heading", { name: reports[i][1] })
    ).toBeVisible()
    await expect(
      body.locator("script, img, [data-preview-marker]")
    ).toHaveCount(0)
    await expect(body.locator("body")).toContainText("Chart summary")
    await dialog.screenshot({
      animations: "disabled",
      path: testInfo.outputPath(`html-link-${i}.png`),
    })
    await page.keyboard.press("Escape")
    await expect(button).toBeFocused()
  }
  expect(requests).toEqual(reports.map(([source]) => source))
  expect(unexpected).toEqual([])
})

function captureLinkClipboard(page: Page) {
  return page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText(text: string) {
          document.documentElement.setAttribute("data-fixture-clipboard", text)
          return Promise.resolve()
        },
      },
    })
  })
}

test("HTML link preview handles unsupported responses, redirects, and retry", async ({
  page,
}) => {
  const url = "https://trace-links.invalid/report.html"
  const redirect = "https://trace-links.invalid/another.html"
  const requests: string[] = []
  let attempts = 0
  await page.route("https://trace-links.invalid/**", (route) => {
    const target = route.request().url()
    requests.push(target)
    if (target !== url) return route.abort()
    attempts++
    const headers = { "Access-Control-Allow-Origin": "*" }
    if (attempts === 1) {
      return route.fulfill({
        headers: { ...headers, "Content-Type": "text/plain" },
        body: "<html><body>Unexpected response</body></html>",
      })
    }
    if (attempts === 2) {
      return route.fulfill({
        status: 302,
        headers: { ...headers, Location: redirect },
      })
    }
    if (attempts === 3) {
      return route.fulfill({
        headers: { ...headers, "Content-Type": "text/html" },
        body: `<html><body>${"x".repeat(512 * 1024)}</body></html>`,
      })
    }
    return route.fulfill({
      headers: { ...headers, "Content-Type": "text/html" },
      body: "<html><body><h1>Report restored</h1></body></html>",
    })
  })
  await captureLinkClipboard(page)
  const summary = await traceWithDocumentInput(page, url)
  await summary.locator("[data-trace-document-link]").click()
  const dialog = page.getByRole("dialog", { name: "HTML", exact: true })
  const error = dialog.getByRole("alert")
  const retry = dialog.getByRole("button", {
    name: uiText("en-US", "common.retry"),
  })
  for (let attempt = 1; attempt <= 3; attempt++) {
    await expect(error).toHaveText(uiText("en-US", "traceMedia.error"))
    await expect(dialog.locator("iframe")).toHaveCount(0)
    expect(requests).toEqual(Array(attempt).fill(url))
    if (attempt === 1) {
      const fallback = dialog.locator("[data-trace-document-link-fallback]")
      await expect(fallback.getByText(url, { exact: true })).toBeVisible()
      await expect(fallback.locator("a[href]")).toHaveCount(0)
      const pageUrl = page.url()
      await fallback
        .getByRole("button", { name: uiText("en-US", "share.copyLink") })
        .click()
      await expect
        .poll(() => page.locator("html").getAttribute("data-fixture-clipboard"))
        .toBe(url)
      expect(requests).toEqual([url])
      expect(page.url()).toBe(pageUrl)
      expect(page.context().pages()).toHaveLength(1)
    }
    await retry.click()
  }
  await expect(
    dialog
      .frameLocator("iframe")
      .getByRole("heading", { name: "Report restored" })
  ).toBeVisible()
  expect(requests).toEqual(Array(4).fill(url))
})

test("expanded Markdown keeps one static HTML action and bounds mounted code controls", async ({
  page,
}) => {
  const url = "https://trace-links.invalid/report.html"
  const requests: string[] = []
  await page.route("https://trace-links.invalid/**", (route) => {
    const requested = route.request().url()
    requests.push(requested)
    return requested === url
      ? route.fulfill({
          headers: {
            "Content-Type": "text/html",
            "Access-Control-Allow-Origin": "*",
          },
          body: "<html><body><h1>Markdown report</h1></body></html>",
        })
      : route.abort()
  })
  const fences = Array.from(
    { length: 48 },
    (_, index) => `\`\`\`js\nconst example = ${index}\n\`\`\``
  )
  await prepare(page)
  await publicTrace(page, {
    ...trace,
    semantics: {
      ...trace.semantics!,
      output: [
        `[Read the report](${url}) and [regular documentation](https://trace-links.invalid/guide).`,
        ...fences,
      ].join("\n\n"),
    },
  })
  const output = page.locator("[data-trace-overview-output]")
  await output
    .getByRole("button", { name: uiText("en-US", "traceProcess.fullOutput") })
    .click()
  await expect(
    output.getByRole("link", { name: "Read the report" })
  ).toHaveCount(0)
  await expect(
    output.getByText("Read the report", { exact: true })
  ).toBeVisible()
  await expect(
    output.getByRole("link", { name: "regular documentation" })
  ).toHaveAttribute("href", "https://trace-links.invalid/guide")
  await expect(output.locator("pre code")).toHaveCount(48)
  await expect(output.locator("[data-trace-code]")).toHaveCount(8)
  const preview = output.locator("[data-trace-document-link]")
  await expect(preview).toHaveCount(1)
  expect(requests).toEqual([])
  await preview.click()
  await expect(
    page
      .getByRole("dialog")
      .frameLocator("iframe")
      .getByRole("heading", { name: "Markdown report" })
  ).toBeVisible()
  expect(requests).toEqual([url])
})

test("HTML links beyond the preview limit keep an inert, copyable address", async ({
  page,
}, testInfo) => {
  const origin = "https://trace-links.invalid"
  const overflowUrl = `${origin}/report-9.html?signature=AbC%2B9&team=preview#summary`
  const reports = Array.from(
    { length: 9 },
    (_, index) =>
      `[Report ${index + 1}](${index === 8 ? overflowUrl : `${origin}/report-${index + 1}.html`})`
  )
  const previewUrl = `${origin}/report-1.html`
  const requests: string[] = []
  await page.route(`${origin}/**`, (route) => {
    const url = route.request().url()
    requests.push(url)
    return url === previewUrl
      ? route.fulfill({
          headers: {
            "Content-Type": "text/html",
            "Access-Control-Allow-Origin": "*",
          },
          body: "<html><body><h1>First of nine reports</h1></body></html>",
        })
      : route.abort()
  })
  await captureLinkClipboard(page)
  await prepare(page)
  await publicTrace(page, {
    ...trace,
    semantics: { ...trace.semantics!, output: reports.join(" · ") },
  })

  const output = page.locator("[data-trace-overview-output]")
  await output
    .getByRole("button", { name: uiText("en-US", "traceProcess.fullOutput") })
    .click()
  const previews = output.locator("[data-trace-document-link]")
  await expect(previews).toHaveCount(8)
  const fallback = output.locator("[data-trace-document-link-fallback]")
  await expect(fallback).toHaveCount(1)
  await expect(fallback.getByText(overflowUrl, { exact: true })).toBeVisible()
  await expect(output.locator("a[href]")).toHaveCount(0)
  await expect(page.locator("iframe[srcdoc]")).toHaveCount(0)
  expect(requests).toEqual([])

  const pageUrl = page.url()
  await fallback
    .getByRole("button", { name: uiText("en-US", "share.copyLink") })
    .click()
  await expect
    .poll(() => page.locator("html").getAttribute("data-fixture-clipboard"))
    .toBe(overflowUrl)
  expect(page.url()).toBe(pageUrl)
  expect(page.context().pages()).toHaveLength(1)
  expect(requests).toEqual([])
  await output.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("copy-overflow-html-link.png"),
  })

  await previews.first().click()
  const dialog = page.getByRole("dialog", { name: "HTML", exact: true })
  await expect(dialog.locator("iframe[srcdoc]")).toHaveAttribute("sandbox", "")
  await expect(
    dialog
      .frameLocator("iframe[srcdoc]")
      .getByRole("heading", { name: "First of nine reports" })
  ).toBeVisible()
  expect(requests).toEqual([previewUrl])
})

test("Select-from Markdown prose offers an HTML preview and retains ordinary links", async ({
  page,
}) => {
  const origin = "https://trace-links.invalid"
  const htmlUrl = `${origin}/report.html`
  const referenceUrl = `${origin}/guide`
  const requests: string[] = []
  await page.route(`${origin}/**`, (route) => {
    const url = route.request().url()
    requests.push(url)
    return url === htmlUrl
      ? route.fulfill({
          headers: {
            "Content-Type": "text/html",
            "Access-Control-Allow-Origin": "*",
          },
          body: "<html><body><h1>Report linked from prose</h1></body></html>",
        })
      : route.abort()
  })
  await prepare(page)
  await publicTrace(page, {
    ...trace,
    semantics: {
      ...trace.semantics!,
      output: `Select the record from [the report](${htmlUrl}). See [the guide](${referenceUrl}).`,
    },
  })

  const output = page.locator("[data-trace-overview-output]")
  await output
    .getByRole("button", { name: uiText("en-US", "traceProcess.fullOutput") })
    .click()
  const preview = output.locator("[data-trace-document-link]")
  await expect(preview).toHaveCount(1)
  await expect(output.getByText("the report", { exact: true })).toBeVisible()
  await expect(output.getByRole("link", { name: "the report" })).toHaveCount(0)
  await expect(output.getByRole("link", { name: "the guide" })).toHaveAttribute(
    "href",
    referenceUrl
  )
  await expect(page.locator("iframe[srcdoc]")).toHaveCount(0)
  expect(requests).toEqual([])

  await preview.click()
  const dialog = page.getByRole("dialog", { name: "HTML", exact: true })
  await expect(dialog.locator("iframe[srcdoc]")).toHaveAttribute("sandbox", "")
  await expect(
    dialog
      .frameLocator("iframe[srcdoc]")
      .getByRole("heading", { name: "Report linked from prose" })
  ).toBeVisible()
  expect(requests).toEqual([htmlUrl])
})

test("adjacent model text parts keep a single complete code snippet in each conversation direction", async ({
  page,
}) => {
  const link = "https://trace-code.invalid/report.html"
  const inputCode = `  const report = "${link}";  \r\n`
  const outputCode = `def report():\r\n    return "${link}"\r\n`
  const requests: string[] = []
  await page.route("https://trace-code.invalid/**", (route) => {
    requests.push(route.request().url())
    return route.abort()
  })
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText(text: string) {
          document.documentElement.setAttribute("data-fixture-clipboard", text)
          return Promise.resolve()
        },
      },
    })
  })
  const messages = (role: "user" | "assistant", parts: string[]) =>
    JSON.stringify([
      {
        role,
        content: parts.map((text) => ({
          type: role === "user" ? "input_text" : "output_text",
          text,
        })),
      },
    ])
  await prepare(page)
  await publicTrace(page, {
    ...trace,
    spans: [
      trace.spans[0],
      {
        ...trace.spans[2],
        span_id: "split-code-call",
        attributes: {
          "gen_ai.operation.name": "chat",
          "gen_ai.span.kind": "LLM",
          "gen_ai.input.messages": messages("user", [
            "Review this code.\r\n\r\n``",
            `\`js\r\n${inputCode.slice(0, 32)}`,
            `${inputCode.slice(32)}\`\`\``,
          ]),
          "gen_ai.output.messages": messages("assistant", [
            `\`\`\`py\r\n${outputCode.slice(0, 20)}`,
            `${outputCode.slice(20, -1)}`,
            `${outputCode.slice(-1)}\`\`\``,
          ]),
        },
      },
    ],
  })
  await page
    .locator(
      '[data-testid="native-chain-span"][data-span-id="split-code-call"]'
    )
    .click()
  const panel = page.getByTestId("native-span-detail")
  for (const [direction, language, keyword, source] of [
    ["input", "javascript", "const", inputCode],
    ["output", "python", "def", outputCode],
  ]) {
    const section = panel.locator(`[data-conversation-io="${direction}"]`)
    await section.locator("[data-message-disclosure]").click()
    const snippet = section.locator("[data-trace-code]")
    await expect(snippet).toHaveCount(1)
    await expect(snippet).toHaveAttribute("data-language", language)
    await expect(snippet.locator(".tok-keyword").first()).toHaveText(keyword)
    expect(await snippet.locator("code").textContent()).toBe(source)
    await snippet
      .getByRole("button", { name: uiText("en-US", "spanContent.copyContent") })
      .click()
    await expect
      .poll(() => page.locator("html").getAttribute("data-fixture-clipboard"))
      .toBe(source)
    await expect(section.locator("[data-trace-document-link]")).toHaveCount(0)
  }
  expect(requests).toEqual([])
})

test("Trace input and output highlight fenced code with literal copy and no link requests", async ({
  page,
}, testInfo) => {
  const inputCode = [
    'const report = "https://trace-code.invalid/input.html"  ',
    "const title = 'Regional report'",
    "const sections = ['Summary', 'Results', 'Notes']",
    "const labels = sections.map((section) => section.toUpperCase())",
    "  // Preserve this indentation and the final newline.  ",
    "const summary = { title, labels }",
    "const markup = '<img src=\"https://trace-code.invalid/image\">'",
    "",
  ].join("\r\n")
  const outputCode =
    'def report():\n    return "https://trace-code.invalid/output.html"\n'
  const requests: string[] = []
  await page.route("https://trace-code.invalid/**", (route) => {
    requests.push(route.request().url())
    return route.abort()
  })
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText(text: string) {
          document.documentElement.setAttribute("data-fixture-clipboard", text)
          return Promise.resolve()
        },
      },
    })
  })
  await prepare(page)
  await publicTrace(page, {
    ...trace,
    spans: trace.spans.map((span, index) =>
      index === 0
        ? {
            ...span,
            attributes: {
              "gen_ai.operation.name": "chat",
              "gen_ai.input.messages": JSON.stringify([
                {
                  role: "user",
                  content: `Review this snippet.\r\n\r\n\`\`\`js\r\n${inputCode}\`\`\``,
                },
              ]),
            },
          }
        : span
    ),
    semantics: {
      ...trace.semantics!,
      output: { message: `\`\`\`python\n${outputCode}\`\`\`` },
    },
  })
  const input = page.getByTestId("trace-user-input")
  const output = page.locator("[data-trace-overview-output]")
  const javascript = input.locator(
    '[data-trace-code][data-language="javascript"]'
  )
  const python = output.locator('[data-trace-code][data-language="python"]')
  await expect(javascript.locator(".tok-keyword").first()).toHaveText("const")
  await output
    .getByRole("button", { name: uiText("en-US", "traceProcess.fullOutput") })
    .click()
  await expect(python.locator(".tok-keyword").first()).toHaveText("def")
  const expand = javascript.getByRole("button", {
    name: uiText("en-US", "spanContent.expandText"),
  })
  await expect(expand).toHaveAttribute("aria-expanded", "false")
  const preview = javascript.locator("pre")
  const collapsedHeight = await preview.evaluate(
    (element) => element.clientHeight
  )
  await expand.click()
  const collapse = javascript.getByRole("button", {
    name: uiText("en-US", "spanContent.collapseText"),
  })
  await expect(collapse).toHaveAttribute("aria-expanded", "true")
  await expect
    .poll(() => preview.evaluate((element) => element.clientHeight))
    .toBeGreaterThan(collapsedHeight)
  expect(await javascript.locator("code").textContent()).toBe(inputCode)
  expect(await python.locator("code").textContent()).toBe(outputCode)
  for (const [snippet, source] of [
    [javascript, inputCode],
    [python, outputCode],
  ] as const) {
    await snippet
      .getByRole("button", {
        name: uiText("en-US", "spanContent.copyContent"),
      })
      .click()
    await expect
      .poll(() => page.locator("html").getAttribute("data-fixture-clipboard"))
      .toBe(source)
    await expect(snippet.locator("a, img, script")).toHaveCount(0)
  }
  await expect(input.locator("[data-trace-document-link]")).toHaveCount(0)
  await expect(output.locator("[data-trace-document-link]")).toHaveCount(0)
  expect(requests).toEqual([])
  await input.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("code-input.png"),
  })
  await output.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("code-output.png"),
  })
  await collapse.click()
  await expect(expand).toHaveAttribute("aria-expanded", "false")
  await page.setViewportSize({ width: 390, height: 740 })
  for (const snippet of [javascript, python]) {
    await expect(snippet).toBeVisible()
    await expect
      .poll(() => snippet.evaluate((element) => element.scrollWidth))
      .toBeLessThan(390)
  }
  await input.screenshot({
    animations: "disabled",
    path: testInfo.outputPath("code-input-narrow.png"),
  })
})
