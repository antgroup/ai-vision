import type { ReactNode } from "react"
import { AppLogo } from "@/components/layout/AppLogo"
import { PricingCurrencyProvider } from "@/features/model-pricing/PricingCurrency"
import type { DisplayExchangeRates } from "@/features/model-pricing/currency"

export function TraceLinkShell({
  children,
  showBrand = true,
  exchangeRates,
}: {
  children: ReactNode
  showBrand?: boolean
  exchangeRates?: DisplayExchangeRates | null
}) {
  return (
    <div className="flex h-svh w-full flex-col overflow-hidden bg-background">
      {showBrand ? (
        <header className="flex h-12 shrink-0 items-center border-b px-4">
          <AppLogo
            aria-label="AI Vision"
            className="rounded-md focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          />
        </header>
      ) : null}
      <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        {exchangeRates !== undefined ? (
          <PricingCurrencyProvider linkExchangeRates={exchangeRates ?? undefined}>
            {children}
          </PricingCurrencyProvider>
        ) : (
          children
        )}
      </main>
    </div>
  )
}
