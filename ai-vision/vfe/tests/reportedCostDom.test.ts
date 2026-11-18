import assert from "node:assert/strict"
import test from "node:test"
import {
  createElement,
  type ComponentType,
  type PropsWithChildren,
} from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { nativeUiHarness } from "./nativeUiHarness.ts"
import {
  convertUsdAmount,
  formatDecimalAmount,
} from "../src/features/model-pricing/currency.ts"

function costText(value: unknown, available = true, status = "ready") {
  const { ReportedCost } = nativeUiHarness({
    "lucide-react": { ChevronDown: () => null },
    "@/components/ui/popover": {
      Popover: ({ children }: PropsWithChildren) => children,
      PopoverTrigger: ({ children }: PropsWithChildren) => children,
      PopoverContent: ({ children }: PropsWithChildren) =>
        createElement("div", { role: "dialog" }, children),
    },
    "react-i18next": {
      useTranslation: () => ({
        t: (key: string, values: Record<string, unknown> = {}) =>
          `${key} ${Object.values(values).join(" ")}`.trim(),
      }),
    },
    "./PricingCurrency": {
      usePricingCurrency: () => ({
        currency: "CNY",
        status,
        exchangeRates: available
          ? { rateDate: "2026-10-23", rates: { CNY: "7" } }
          : undefined,
        format: (amount: string | null | undefined) => {
          const display = convertUsdAmount(
            amount,
            "CNY",
            available ? { CNY: "7" } : {}
          )
          return {
            ...display,
            amount:
              display.amount === null
                ? null
                : formatDecimalAmount(
                    display.amount,
                    display.currency,
                    "en-US"
                  ),
          }
        },
      }),
    },
  }).load<{ ReportedCost: ComponentType<{ value: unknown }> }>(
    "features/model-pricing/ReportedCost.tsx"
  )
  return renderToStaticMarkup(createElement(ReportedCost, { value }))
}

test("source reported amounts keep zero, small scientific-notation values and local-currency conversion", () => {
  assert.match(costText(0), /CNY 0\.00/)
  assert.match(costText(1e-8), /CNY 0\.00000007/)
  assert.match(costText(1.234), /CNY 8\.64/)
  assert.match(costText(1e-8, false), /USD 0\.00000001/)
})

test("absent, negative and nonfinite amounts stay unknown without claiming zero or coverage", () => {
  for (const value of [null, undefined, -1, NaN, Infinity]) {
    const html = costText(value)
    assert.match(html, /traceLabels\.unknown/)
    assert.doesNotMatch(html, /USD|CNY|0\.00|data-cost-summary/)
  }
})

test("converted source costs expose dated FX evidence and preserve stale or failed-rate warnings", () => {
  const ready = costText(0.01)
  assert.match(ready, /<button[^>]*type="button"[^>]*data-reported-cost="true"/)
  assert.match(ready, /modelPricing\.observability\.recordedCost/)
  assert.match(ready, /ECB/)
  assert.match(ready, /2026-10-23/)
  assert.doesNotMatch(ready, /data-reported-cost-fx/)
  for (const [status, key] of [
    ["stale", "staleCompact"],
    ["error", "retainedCompact"],
    ["loading", "loadingCompact"],
  ]) {
    const html = costText(0.01, true, status)
    assert.match(html, /CNY 0\.07/)
    assert.ok(html.includes(`modelPricing.currency.${key}`))
    assert.match(html, /data-reported-cost-fx/)
    assert.match(html, /2026-10-23/)
  }
  const unavailable = costText(0.01, false, "empty")
  assert.match(unavailable, /USD 0\.01/)
  assert.match(unavailable, /modelPricing.currency.usingUsdCompact/)
})
