/* eslint-disable react-refresh/only-export-components */
import { createContext, useContext, useId, useState, type ReactNode } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import { RefreshCw } from "lucide-react"
import { api, throwOnApiError } from "@/lib/api"
import { useSpace } from "@/contexts/SpaceContext"
import { Button } from "@/components/ui/button"
import { invalidateModelPricingQueries } from "./pricingQueryInvalidation"
import {
  convertUsdAmount,
  defaultDisplayCurrency,
  displayCurrencies,
  formatDecimalAmount,
  type CurrencySetting,
  type DisplayCurrency,
  type DisplayExchangeRates,
  type ExchangeRateStatus,
} from "./currency"

const STORAGE_KEY = "modelPricing.displayCurrency"
const SOURCE_URL = "https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html"
interface Envelope<T> { code: number; message: string; data: T }
const base = (spaceId: string) => `/spaces/${encodeURIComponent(spaceId)}/model-pricing/exchange-rates`
const queryKey = (spaceId: string) => ["model-pricing-exchange-rates", spaceId]

export async function getPricingExchangeRates(spaceId: string): Promise<ExchangeRateStatus> {
  return throwOnApiError(await api.get<Envelope<ExchangeRateStatus>>(base(spaceId))).data
}
export async function syncPricingExchangeRates(spaceId: string): Promise<ExchangeRateStatus> {
  return throwOnApiError(await api.post<Envelope<ExchangeRateStatus>>(`${base(spaceId)}/sync`, {})).data
}

function savedSetting(): CurrencySetting {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === "auto" || displayCurrencies.includes(saved as DisplayCurrency)) return saved as CurrencySetting
  } catch { /* Unavailable storage leaves the interface-language default active. */ }
  return "auto"
}
function format(amount: string | null | undefined, currency: DisplayCurrency, language: string, rates?: ExchangeRateStatus["rates"]) {
  const converted = convertUsdAmount(amount, currency, rates)
  return { ...converted, amount: converted.amount === null ? null : formatDecimalAmount(converted.amount, converted.currency, language) }
}
type CurrencyContextValue = {
  currency: DisplayCurrency
  setting: CurrencySetting
  setCurrency: (currency: CurrencySetting) => void
  format: (amount: string | null | undefined) => ReturnType<typeof format>
  exchangeRates?: DisplayExchangeRates
  status: ExchangeRateStatus["status"] | "loading"
  canManage: boolean
  pending: boolean
  error: boolean
  sync: () => Promise<unknown>
}
const CurrencyContext = createContext<CurrencyContextValue | null>(null)

export function PricingCurrencyProvider({
  spaceId,
  linkExchangeRates,
  children,
}: {
  spaceId?: string
  linkExchangeRates?: DisplayExchangeRates
  children: ReactNode
}) {
  const { i18n } = useTranslation()
  const language = i18n.resolvedLanguage || i18n.language
  const client = useQueryClient()
  const [setting, updateSetting] = useState<CurrencySetting>(savedSetting)
  const currency = setting === "auto" ? defaultDisplayCurrency(language) : setting
  const rates = useQuery({
    queryKey: queryKey(spaceId ?? ""),
    queryFn: () => getPricingExchangeRates(spaceId!),
    enabled: Boolean(spaceId), staleTime: 60_000, retry: false,
  })
  const refresh = useMutation({
    mutationFn: (originSpaceId: string) => syncPricingExchangeRates(originSpaceId),
    onSuccess: async (data, originSpaceId) => {
      const originKey = queryKey(originSpaceId)
      await client.cancelQueries({ queryKey: originKey, exact: true })
      client.setQueryData(originKey, data)
      const otherSpaces = {
        predicate: ({ queryKey: cachedKey }: { queryKey: readonly unknown[] }) =>
          cachedKey[0] === "model-pricing-exchange-rates" &&
          typeof cachedKey[1] === "string" && cachedKey[1].length > 0 &&
          cachedKey[1] !== originSpaceId,
      }
      // Rates are shared; management permission belongs to each Space. Fetch
      // other Spaces again rather than copying the origin's permission flag.
      await client.cancelQueries(otherSpaces)
      await Promise.all([
        client.invalidateQueries({ ...otherSpaces, refetchType: "active" }),
        // Publisher prices quoted in CNY are normalized on the server.
        invalidateModelPricingQueries(client, originSpaceId, "global"),
      ])
    },
    onError: (_error, originSpaceId) => { void client.invalidateQueries({ queryKey: queryKey(originSpaceId) }) },
  })
  // The provider survives Space navigation. An earlier refresh owns its cache
  // and status even when its response arrives after the user switches Spaces.
  const currentRefresh = refresh.variables === spaceId
  const refreshError = currentRefresh && refresh.error
  // A signed link carries display rates without entering the Space's settings.
  const exchangeRates = spaceId ? rates.data : linkExchangeRates
  const setCurrency = (value: CurrencySetting) => {
    if (value !== "auto" && !displayCurrencies.includes(value)) return
    updateSetting(value)
    try { localStorage.setItem(STORAGE_KEY, value) } catch { /* State remains usable without persistence. */ }
  }
  const value: CurrencyContextValue = {
    currency, setting, setCurrency,
    format: (amount) => format(amount, currency, language, exchangeRates?.rates),
    exchangeRates,
    status: rates.isLoading ? "loading" : rates.error || refreshError ? "error" : exchangeRates?.status ?? "empty",
    canManage: rates.data?.canManage ?? false,
    pending: currentRefresh && refresh.isPending, error: Boolean(rates.error || refreshError),
    sync: () => spaceId ? refresh.mutateAsync(spaceId) : Promise.resolve(),
  }
  return <CurrencyContext.Provider value={value}>{children}</CurrencyContext.Provider>
}

export function SpacePricingCurrencyProvider({ children }: { children: ReactNode }) {
  const { currentSpace } = useSpace()
  return <PricingCurrencyProvider spaceId={currentSpace?.id}>{children}</PricingCurrencyProvider>
}

/** Standalone shared summaries remain usable without the application provider. */
export function usePricingCurrency(): CurrencyContextValue {
  const context = useContext(CurrencyContext)
  const { i18n } = useTranslation()
  if (context) return context
  return { currency: "USD", setting: "auto", setCurrency: () => {},
    format: (amount) => format(amount, "USD", i18n.resolvedLanguage || i18n.language),
    status: "empty", canManage: false, pending: false, error: false, sync: async () => {} }
}

/** Everyday display preference; refresh and source administration live in settings. */
export function CompactCurrencySelector() {
  const { t, i18n } = useTranslation()
  const pricing = usePricingCurrency()
  const evidence = pricing.exchangeRates
  const available = Boolean(
    evidence?.rateDate && evidence.rates[pricing.currency]
  )
  const showStatus =
    pricing.currency !== "USD" && (!available || pricing.status !== "ready")
  const statusId = useId()
  return (
    <div
      data-pricing-currency-compact
      className="flex min-w-0 max-w-[min(43vw,14rem)] flex-col items-end gap-1.5 text-right text-xs text-muted-foreground"
    >
      <label className="min-w-0 max-w-full">
        <span className="sr-only">{t("modelPricing.currency.label")}</span>
        <select
          aria-describedby={showStatus ? statusId : undefined}
          value={pricing.setting}
          onChange={(event) =>
            pricing.setCurrency(event.target.value as CurrencySetting)
          }
          className="h-8 max-w-full rounded-md border border-input bg-background px-2 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <option value="auto">
            {t("modelPricing.currency.auto", {
              currency: defaultDisplayCurrency(
                i18n.resolvedLanguage || i18n.language
              ),
            })}
          </option>
          {displayCurrencies.map((currency) => (
            <option key={currency} value={currency}>
              {currency}
            </option>
          ))}
        </select>
      </label>
      <span id={statusId} role="status" className="leading-relaxed empty:sr-only">
        {showStatus &&
          t(
            pricing.status === "loading"
              ? "modelPricing.currency.loading"
              : available
                ? `modelPricing.currency.${pricing.status}`
                : "modelPricing.currency.fallback"
          )}
      </span>
    </div>
  )
}

export function CostCurrencyControls({ spaceId }: { spaceId: string }) {
  const { t, i18n } = useTranslation()
  const pricing = usePricingCurrency()
  const evidence = pricing.exchangeRates
  return (
    <div data-pricing-currency className="space-y-2 text-xs text-muted-foreground">
      <div className="flex flex-wrap items-center gap-2">
        <label className="inline-flex flex-wrap items-center gap-2">
          <span>{t("modelPricing.currency.label")}</span>
          <select aria-label={t("modelPricing.currency.label")} value={pricing.setting}
            onChange={(event) => pricing.setCurrency(event.target.value as CurrencySetting)}
            className="h-8 max-w-full rounded-md border border-input bg-background px-2 text-foreground shadow-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            <option value="auto">{t("modelPricing.currency.auto", { currency: defaultDisplayCurrency(i18n.resolvedLanguage || i18n.language) })}</option>
            {displayCurrencies.map((currency) => <option key={currency} value={currency}>{currency}</option>)}
          </select>
        </label>
        {pricing.canManage && <Button type="button" size="sm" variant="outline" disabled={pricing.pending || !spaceId}
          onClick={() => { void pricing.sync().catch(() => {}) }}>
          <RefreshCw className={`mr-1 size-3 ${pricing.pending ? "animate-spin" : ""}`} />
          {t(pricing.pending ? "modelPricing.currency.syncing" : "modelPricing.currency.sync")}
        </Button>}
      </div>
      {evidence?.rateDate && <p>{t("modelPricing.currency.reference", { date: evidence.rateDate })}{" "}
        <a href={SOURCE_URL} target="_blank" rel="noreferrer" className="underline underline-offset-2">ECB</a></p>}
      {pricing.status === "loading" ? <p>{t("modelPricing.currency.loading")}</p>
        : pricing.status !== "ready" && <p role={pricing.status === "error" ? "status" : undefined}>{t(`modelPricing.currency.${pricing.status}`)}</p>}
      <p>{t("modelPricing.currency.information")}</p>
    </div>
  )
}
