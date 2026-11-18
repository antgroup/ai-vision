export interface CostCoverage {
  billableCalls: number
  knownCostCalls: number
  reportedCalls: number
  manualCalls: number
  aliasCalls: number
  catalogCalls: number
  referenceCalls: number
  subscriptionCalls?: number
  listPriceEquivalentCalls?: number
  unknownCalls: number
  knownSubtotal: string | null
  referenceSubtotal: string | null
  listPriceEquivalentSubtotal?: string | null
  listPriceEquivalentUnknownReasons?: Record<string, number>
  listPriceEquivalentSources?: Record<string, number>
  currency: string
  unknownReasons: Record<string, number>
  additive: boolean
}

export interface PricingRates {
  input: string | null
  output: string | null
  cacheReadInput: string | null
  cacheWriteInput: string | null
  reasoning: string | null
}
export type PricingSyncSource = "auto" | "api" | "repository"
export type PricingCatalogSource =
  | "api"
  | "repository"
  | "local_repository"
  | "file"
  | "bundled"
export interface CatalogStatus {
  snapshotId: string | null
  fetchedAt: string | null
  modelCount: number
  status: "ready" | "empty" | "error"
  lastError: string | null
  scope: "global"
  warnings: string[]
  sourceType?: PricingCatalogSource
  sourceUrl?: string | null
  sourceRevision?: string | null
}
export interface PricingCandidate {
  provider: string
  model: string
  catalogSnapshot?: string | null
  canonicalModelId: string
  name: string
  rates: PricingRates
  supported: boolean
  unsupportedReasons: string[]
  reasons: string[]
  score: number
}
export type PricingRuleKind = "manual" | "alias" | "reference" | "subscription"
export interface PricingRuleInput {
  provider: string
  model: string
  kind: PricingRuleKind
  effectiveFrom: string
  rates?: PricingRates
  estimateListPrice?: boolean
  catalogProvider?: string | null
  catalogModel?: string | null
  catalogSnapshot?: string | null
}
export interface PricingRule extends Omit<PricingRuleInput, "rates"> {
  uuid: string
  rates: Partial<PricingRates>
  version: string
  active: boolean
  createdAt: string
}
export interface PricingSummary {
  catalog: CatalogStatus
  rules: PricingRule[]
  canManage: boolean
}
export interface PricingInventory {
  from: number
  to: number
  complete: true
  provider: "signoz"
  models: {
    provider: string | null
    model: string | null
    calls: number
    costCoverage: CostCoverage
  }[]
  costCoverage: CostCoverage
  scope: { agentProduct: string | null; agentUuid: string | null }
  warnings: string[]
}
