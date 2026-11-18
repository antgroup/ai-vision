import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import { JSDOM } from "jsdom"
import { act, createElement } from "react"
import ts from "typescript"
import * as currencyModel from "../src/features/model-pricing/currency.ts"
import * as pricingQueries from "../src/features/model-pricing/pricingQueryInvalidation.ts"
import {
  convertUsdAmount,
  defaultDisplayCurrency,
  formatDecimalAmount,
  multiplyDecimalStrings,
} from "../src/features/model-pricing/currency.ts"

const require = createRequire(import.meta.url)
const queryRuntime =
  require("@tanstack/react-query") as typeof import("@tanstack/react-query")
type CurrencyContext = ReturnType<
  typeof import("../src/features/model-pricing/PricingCurrency").usePricingCurrency
>

async function currencyProviderFixture() {
  const dom = new JSDOM("<div id='root'></div>", {
    url: "https://test.invalid",
  })
  const originals = new Map<string, PropertyDescriptor | undefined>()
  for (const [name, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    localStorage: dom.window.localStorage,
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    originals.set(name, Object.getOwnPropertyDescriptor(globalThis, name))
    Object.defineProperty(globalThis, name, {
      configurable: true,
      writable: true,
      value,
    })
  }
  const snapshot = (canManage: boolean, snapshotId: string) => ({
    baseCurrency: "USD" as const,
    supportedCurrencies: ["USD", "CNY", "JPY"] as const,
    rates: { USD: "1", CNY: "7", JPY: "150" },
    snapshotId,
    rateDate: "2026-10-23",
    fetchedAt: "2026-10-26T00:00:00Z",
    sourceName: "ECB",
    sourceUrl: "https://www.ecb.europa.eu/",
    status: "ready" as const,
    lastError: null,
    scope: "global" as const,
    canManage,
  })
  const savedA = snapshot(true, "saved-a"),
    savedB = snapshot(false, "saved-b")
  const newA = {
    ...snapshot(true, "refreshed-a"),
    rates: { USD: "1", CNY: "8", JPY: "160" },
    rateDate: "2026-10-26",
  }
  const newB = { ...newA, canManage: false }
  let refreshed = false
  let heldRead: Promise<void> | null = null
  let resolveRefresh!: (value: unknown) => void
  let rejectRefresh!: (reason: Error) => void
  const response = new Promise((resolve, reject) => {
    resolveRefresh = resolve
    rejectRefresh = reject
  })
  const reads: string[] = [],
    writes: string[] = []
  const exports =
    {} as typeof import("../src/features/model-pricing/PricingCurrency")
  const source = readFileSync(
    new URL(
      "../src/features/model-pricing/PricingCurrency.tsx",
      import.meta.url
    ),
    "utf8"
  )
  const code = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText
  new Function("require", "exports", code)((name: string) => {
    if (name === "./currency") return currencyModel
    if (name === "./pricingQueryInvalidation") return pricingQueries
    if (name === "react-i18next")
      return {
        useTranslation: () => ({
          t: (key: string) => key,
          i18n: { language: "en-US" },
        }),
      }
    if (name === "@/lib/api")
      return {
        throwOnApiError: (value: unknown) => value,
        api: {
          get: async (path: string) => {
            reads.push(path)
            const data = path.includes("space-b")
              ? refreshed
                ? newB
                : savedB
              : refreshed
                ? newA
                : savedA
            const wait = heldRead
            heldRead = null
            await wait
            return { data }
          },
          post: async (path: string) => {
            writes.push(path)
            const result = await response
            refreshed = true
            return result
          },
        },
      }
    if (name === "@/contexts/SpaceContext")
      return { useSpace: () => ({ currentSpace: null }) }
    if (name === "@/components/ui/button") return { Button: "button" }
    return require(name)
  }, exports)
  const client = new queryRuntime.QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const key = (space: string) => ["model-pricing-exchange-rates", space]
  client.setQueryData(key("space-a"), savedA)
  client.setQueryData(key("space-b"), savedB)
  const { createRoot } =
    require("react-dom/client") as typeof import("react-dom/client")
  const root = createRoot(dom.window.document.getElementById("root")!)
  let current!: CurrencyContext
  let pending: Promise<unknown> | undefined
  function Probe() {
    current = exports.usePricingCurrency()
    return null
  }
  const flush = () =>
    new Promise((resolve) => {
      setTimeout(resolve, 0)
    })
  const render = async (spaceId: string) => {
    await act(async () => {
      root.render(
        createElement(
          queryRuntime.QueryClientProvider,
          { client },
          createElement(
            exports.PricingCurrencyProvider,
            { spaceId },
            createElement(Probe)
          )
        )
      )
      await flush()
    })
    // QueryObserver schedules result notifications after the render commits.
    await act(async () => {
      await flush()
    })
  }
  await render("space-a")
  return {
    client,
    key,
    savedB,
    newA,
    newB,
    reads,
    writes,
    render,
    current: () => current,
    holdNextRead: () => {
      let release!: () => void
      heldRead = new Promise<void>((resolve) => {
        release = resolve
      })
      return release
    },
    start: async () => {
      await act(async () => {
        pending = current.sync()
        void pending.catch(() => {})
        await flush()
      })
    },
    settle: async (failure = false) => {
      await act(async () => {
        if (failure) rejectRefresh(new Error("held A refresh failed"))
        else resolveRefresh({ data: newA })
        await pending?.catch(() => {})
        await flush()
      })
    },
    close: async () => {
      await act(async () => {
        resolveRefresh({ data: newA })
        await pending?.catch(() => {})
        await flush()
        root.unmount()
      })
      client.clear()
      dom.window.close()
      for (const [name, descriptor] of originals) {
        if (descriptor) Object.defineProperty(globalThis, name, descriptor)
        else Reflect.deleteProperty(globalThis, name)
      }
    },
  }
}

test("exchange-rate refresh pending state stays with its originating Space", async () => {
  const fixture = await currencyProviderFixture()
  try {
    await fixture.start()
    assert.equal(fixture.current().pending, true)
    await fixture.render("space-b")
    assert.equal(
      fixture.current().pending,
      false,
      "A refresh must not disable B's controls"
    )
    await fixture.render("space-a")
    assert.equal(fixture.current().pending, true)
  } finally {
    await fixture.close()
  }
})

test("late exchange-rate refresh updates another Space through its own authorized read", async () => {
  const fixture = await currencyProviderFixture()
  try {
    await fixture.start()
    await fixture.render("space-b")
    await fixture.settle()
    assert.deepEqual(fixture.writes, [
      "/spaces/space-a/model-pricing/exchange-rates/sync",
    ])
    assert.deepEqual(
      fixture.client.getQueryData(fixture.key("space-b")),
      fixture.newB
    )
    assert.deepEqual(fixture.reads, [
      "/spaces/space-b/model-pricing/exchange-rates",
    ])
    assert.equal(fixture.current().canManage, false)
    assert.deepEqual(
      fixture.client.getQueryData(fixture.key("space-a")),
      fixture.newA
    )
  } finally {
    await fixture.close()
  }
})

test("successful global FX refresh invalidates inactive Spaces and cached estimates", async () => {
  const fixture = await currencyProviderFixture()
  const estimates = [
    ["nativeTraceDetail", "space-b", "user", "trace"],
    ["nativeSpanDetail", "space-b", "trace", "span"],
    ["traceLinkDetail", "payload", "signature"],
    ["observationLinkDetail", "payload", "signature"],
    ["shareData", "payload", "signature"],
    ["shareTraceDetail", "trace", "payload", "signature"],
  ]
  const unrelated = ["space-members", "space-b"]
  try {
    fixture.client.setQueryData(fixture.key("space-c"), fixture.savedB)
    for (const key of estimates)
      fixture.client.setQueryData(key, { listPriceEquivalent: null })
    fixture.client.setQueryData(unrelated, { canManage: false })
    await fixture.start()
    await fixture.settle()
    for (const space of ["space-b", "space-c"])
      assert.equal(
        fixture.client.getQueryState(fixture.key(space))?.isInvalidated,
        true,
        space
      )
    assert.equal(fixture.client.getQueryState(unrelated)?.isInvalidated, false)
    for (const key of estimates)
      assert.equal(
        fixture.client.getQueryState(key)?.isInvalidated,
        true,
        key[0]
      )
    assert.equal(
      fixture.client.getQueryState(fixture.key("space-a"))?.isInvalidated,
      false
    )
    assert.deepEqual(
      fixture.client.getQueryData(fixture.key("space-a")),
      fixture.newA
    )
    assert.deepEqual(
      fixture.reads,
      [],
      "inactive Spaces refetch when next viewed"
    )
    await fixture.render("space-b")
    assert.deepEqual(fixture.current().exchangeRates, fixture.newB)
    assert.equal(fixture.current().canManage, false)
  } finally {
    await fixture.close()
  }
})

test("an older exchange-rate read cannot overwrite a successful refresh in its originating Space", async () => {
  const fixture = await currencyProviderFixture()
  const origin = fixture.key("space-a")
  const releaseRead = fixture.holdNextRead()
  let pendingRead: Promise<void> | undefined
  try {
    await act(async () => {
      pendingRead = fixture.client.refetchQueries({ queryKey: origin })
    })
    assert.equal(fixture.client.getQueryState(origin)?.fetchStatus, "fetching")
    await fixture.start()
    await fixture.settle()
    await act(async () => {
      releaseRead()
      await pendingRead
    })
    assert.deepEqual(fixture.client.getQueryData(origin), fixture.newA)
  } finally {
    releaseRead()
    await pendingRead
    await fixture.close()
  }
})

test("successful global FX refresh cancels another Space's older in-flight snapshot", async () => {
  const fixture = await currencyProviderFixture()
  let releaseRead = () => {}
  let pendingRead: Promise<void> | undefined
  try {
    await fixture.start()
    await fixture.render("space-b")
    releaseRead = fixture.holdNextRead()
    await act(async () => {
      pendingRead = fixture.client.refetchQueries({
        queryKey: fixture.key("space-b"),
      })
      await new Promise((resolve) => {
        setTimeout(resolve, 0)
      })
    })
    await fixture.settle()
    assert.deepEqual(fixture.current().exchangeRates, fixture.newB)
    await act(async () => {
      releaseRead()
      await pendingRead
    })
    assert.deepEqual(
      fixture.client.getQueryData(fixture.key("space-b")),
      fixture.newB
    )
    assert.equal(fixture.current().canManage, false)
    assert.deepEqual(fixture.reads, [
      "/spaces/space-b/model-pricing/exchange-rates",
      "/spaces/space-b/model-pricing/exchange-rates",
    ])
  } finally {
    releaseRead()
    await pendingRead
    await fixture.close()
  }
})

test("failed exchange-rate refresh neither invalidates nor exposes errors in another Space", async () => {
  const fixture = await currencyProviderFixture()
  try {
    await fixture.start()
    await fixture.render("space-b")
    await fixture.settle(true)
    assert.deepEqual(
      fixture.reads,
      [],
      "A failure must not invalidate B's fresh exchange rates"
    )
    assert.equal(fixture.current().error, false)
    assert.equal(fixture.current().status, "ready")
    assert.equal(
      fixture.client.getQueryState(fixture.key("space-a"))?.isInvalidated,
      true
    )
    await fixture.render("space-a")
    assert.equal(fixture.current().error, true)
  } finally {
    await fixture.close()
  }
})

test("language defaults select currency while unknown languages preserve USD", () => {
  assert.equal(defaultDisplayCurrency("zh-CN"), "CNY")
  assert.equal(defaultDisplayCurrency("ja-JP"), "JPY")
  assert.equal(defaultDisplayCurrency("en-US"), "USD")
  assert.equal(defaultDisplayCurrency("de-DE"), "USD")
})

test("decimal conversion preserves tiny costs and large integer precision", () => {
  assert.equal(
    multiplyDecimalStrings("0.00000001", "7.12345678"),
    "0.0000000712345678"
  )
  assert.equal(
    multiplyDecimalStrings("9007199254740993.01", "7"),
    "63050394783186951.07"
  )
  assert.equal(multiplyDecimalStrings("0", "7.1234"), "0")
  assert.equal(multiplyDecimalStrings("2.50", "0.004"), "0.01")
})

test("unavailable FX falls back to USD and never relabels an unconverted amount", () => {
  assert.deepEqual(convertUsdAmount("200", "CNY", { USD: "1" }), {
    amount: "200",
    currency: "USD",
    converted: false,
  })
  assert.deepEqual(convertUsdAmount("200", "CNY", { USD: "1", CNY: "7" }), {
    amount: "1400",
    currency: "CNY",
    converted: true,
  })
  assert.deepEqual(convertUsdAmount(null, "CNY", { CNY: "7" }), {
    amount: null,
    currency: "CNY",
    converted: false,
  })
  for (const bad of ["NaN", "Infinity", "-2", "1e3", "0", ""])
    assert.equal(convertUsdAmount("200", "CNY", { CNY: bad }).currency, "USD")
})

test("localized display rounds monetary totals without turning positive costs into zero", () => {
  assert.equal(
    formatDecimalAmount("1234567.891", "USD", "en-US"),
    "1,234,567.89"
  )
  assert.equal(formatDecimalAmount("1234567.891", "JPY", "ja-JP"), "1,234,568")
  assert.equal(
    formatDecimalAmount("0.0000000712345678", "CNY", "zh-CN"),
    "0.0000000712346"
  )
  assert.equal(
    formatDecimalAmount("0.0000000000000000000000000000001", "JPY", "ja-JP"),
    "0.0000000000000000000000000000001"
  )
  assert.equal(formatDecimalAmount("0", "CNY", "zh-CN"), "0.00")
  assert.equal(
    formatDecimalAmount("9007199254740993.01", "USD", "en-US"),
    "9,007,199,254,740,993.01"
  )
})
