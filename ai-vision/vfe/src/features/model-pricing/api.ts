import { api, throwOnApiError } from "@/lib/api"
import { scheduleNativeQuery } from "@/components/traces-otel/nativeQueryScheduler"
import type {
  CatalogStatus,
  PricingCandidate,
  PricingInventory,
  PricingRule,
  PricingRuleInput,
  PricingSummary,
  PricingSyncSource,
} from "./types"

interface Envelope<T> {
  code: number
  message: string
  data: T
}
const base = (spaceId: string) =>
  `/spaces/${encodeURIComponent(spaceId)}/model-pricing`
export async function getModelPricing(
  spaceId: string
): Promise<PricingSummary> {
  return throwOnApiError(await api.get<Envelope<PricingSummary>>(base(spaceId)))
    .data
}
export async function searchPricingCatalog(
  spaceId: string,
  filters: {
    provider?: string
    model?: string
    q?: string
    reference?: boolean
  }
) {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters))
    if (value) params.set(key, String(value))
  return throwOnApiError(
    await api.get<
      Envelope<{
        catalog: CatalogStatus
        candidates: PricingCandidate[]
        warnings: string[]
      }>
    >(`${base(spaceId)}/catalog?${params}`)
  ).data
}
export async function syncPricingCatalog(
  spaceId: string,
  source: PricingSyncSource = "auto"
) {
  return throwOnApiError(
    await api.post<Envelope<unknown>>(`${base(spaceId)}/sync`, { source })
  ).data
}
export async function createPricingRule(
  spaceId: string,
  input: PricingRuleInput
) {
  return throwOnApiError(
    await api.post<Envelope<PricingRule>>(`${base(spaceId)}/rules`, input)
  ).data
}
export async function deletePricingRule(spaceId: string, uuid: string) {
  throwOnApiError(
    await api.delete<Envelope<unknown>>(
      `${base(spaceId)}/rules/${encodeURIComponent(uuid)}`
    )
  )
}
export async function getPricingInventory(
  spaceId: string,
  window: {
    from: string
    to: string
    agent_product?: string
    agent_uuid?: string
  },
  signal?: AbortSignal
): Promise<PricingInventory> {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(window))
    if (value) params.set(key, String(value))
  const data = throwOnApiError(
    await scheduleNativeQuery(
      () =>
        api.get<Envelope<PricingInventory>>(
          `${base(spaceId)}/inventory?${params}`
        ),
      signal
    )
  ).data
  if (data.complete !== true || data.provider !== "signoz")
    throw new Error("Incomplete native pricing inventory")
  return data
}
