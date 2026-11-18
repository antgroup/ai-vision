import { stableQueryIdentity } from "./queryIdentity.ts"
/**
 * Agent Dashboard V2 -- Dashboard query adapter.
 *
 * Single entry point for dashboard sections to execute V2 queries; sections
 * never build /api/v2 requests directly. Internally delegates to
 * observabilityV2Api for the HTTP call and agentQueryCrypto for encryption.
 */

import type {
  AgentQueryPayload,
  AgentQueryResult,
  DashboardScope,
  QueryTimeRange,
} from "@/agents/types"

import { resolveDefaultStep } from "@/components/bizcharts/helpers/timeStep"
import { scheduleNativeQuery } from "@/components/traces-otel/nativeQueryScheduler"
import {
  encryptAgentQueryPayload,
  isEncryptionEnabled,
  isKeyReady,
  requireAgentQueryRandom,
  setEncryptionKey,
} from "@/agents/lib/agentQueryCrypto"
import {
  getQueryConfig,
  postAgentDashboardQuery,
  type AgentSourceSystem,
} from "@/agents/lib/observabilityV2Api"

async function ensureEncryptionKeyLoaded(
  spaceId: string,
  agentProduct: string,
  signal?: AbortSignal
): Promise<void> {
  if (isKeyReady()) return
  const config = await getQueryConfig(spaceId, agentProduct, signal)
  if (config.kid && config.keyB64) {
    await setEncryptionKey(config.kid, config.keyB64)
  }
}

/** Minimal query specification that a dashboard section supplies. */
export interface SectionQuerySpec {
  queryKey: string
  params?: Record<string, unknown>
}

/** Row data transformed from the raw backend response into section-friendly shape. */
export interface SectionQueryResult {
  queryKey: string
  columns: AgentQueryResult["columns"]
  rows: Record<string, unknown>[]
  encrypted: boolean
  queryTimeMs: number
}

const QUERY_RESULT_CACHE_TTL_MS = 30_000
const resultCache = new Map<
  string,
  { expiresAt: number; result: SectionQueryResult }
>()
interface InFlightRequest<T> {
  promise: Promise<T>
  signal?: AbortSignal
}

const inFlightQueries = new Map<string, InFlightRequest<SectionQueryResult>>()

function buildRequestCacheKey(input: {
  spaceId: string
  agentProduct: string
  spec: SectionQuerySpec
  scope: DashboardScope
  timeRange: QueryTimeRange
  sourceSystem?: AgentSourceSystem
}): string {
  return stableQueryIdentity({
    spaceId: input.spaceId,
    agentProduct: input.agentProduct,
    scope: input.scope,
    queryKey: input.spec.queryKey,
    timeRange: input.timeRange,
    params: input.spec.params ?? null,
    encrypted: isEncryptionEnabled(),
    sourceSystem: input.sourceSystem ?? "signoz",
  })
}

function buildPayload(
  spec: SectionQuerySpec,
  scope: DashboardScope,
  timeRange: QueryTimeRange
): AgentQueryPayload {
  return {
    scope,
    queryKey: spec.queryKey,
    timeRange,
    params: { time_step: resolveDefaultStep(timeRange), ...spec.params },
  }
}

function transformQueryResult(result: AgentQueryResult): SectionQueryResult {
  return {
    queryKey: result.queryKey,
    columns: result.columns,
    rows: result.rows,
    encrypted: result.meta.encrypted,
    queryTimeMs: result.meta.query_time_ms,
  }
}

export async function executeSectionQuery(input: {
  spaceId: string
  agentProduct: string
  spec: SectionQuerySpec
  scope: DashboardScope
  timeRange: QueryTimeRange
  sourceSystem?: AgentSourceSystem
  signal?: AbortSignal
}): Promise<SectionQueryResult> {
  const {
    spaceId,
    agentProduct,
    spec,
    scope,
    timeRange,
    sourceSystem,
    signal,
  } = input
  const cacheKey = buildRequestCacheKey({ ...input, sourceSystem })
  const cached = resultCache.get(cacheKey)
  const now = Date.now()

  if (cached && cached.expiresAt > now) {
    return cached.result
  }

  const inFlight = inFlightQueries.get(cacheKey)
  if (inFlight && inFlight.signal === signal && !inFlight.signal?.aborted) {
    return inFlight.promise
  }

  const request = executeSectionQueryUncached({
    spaceId,
    agentProduct,
    spec,
    scope,
    timeRange,
    sourceSystem,
    signal,
  })
    .then((result) => {
      resultCache.set(cacheKey, {
        expiresAt: Date.now() + QUERY_RESULT_CACHE_TTL_MS,
        result,
      })
      return result
    })
    .finally(() => {
      if (inFlightQueries.get(cacheKey)?.promise === request) {
        inFlightQueries.delete(cacheKey)
      }
    })

  inFlightQueries.set(cacheKey, { promise: request, signal })
  return request
}

async function executeSectionQueryUncached(input: {
  spaceId: string
  agentProduct: string
  spec: SectionQuerySpec
  scope: DashboardScope
  timeRange: QueryTimeRange
  sourceSystem?: AgentSourceSystem
  signal?: AbortSignal
}): Promise<SectionQueryResult> {
  const {
    spaceId,
    agentProduct,
    spec,
    scope,
    timeRange,
    sourceSystem,
    signal,
  } = input

  const payload = buildPayload(spec, scope, timeRange)

  const encrypted = isEncryptionEnabled()

  if (encrypted) {
    requireAgentQueryRandom()
    await ensureEncryptionKeyLoaded(spaceId, agentProduct, signal)
  }
  const body = encrypted ? await encryptAgentQueryPayload(payload) : payload

  const request = {
    spaceId,
    agentProduct,
    payload: body,
    encrypted,
    sourceSystem,
  }
  // Let the V2 client drain the response before releasing the physical slot.
  const result = await scheduleNativeQuery(
    () => postAgentDashboardQuery(request),
    signal
  )

  return transformQueryResult(result)
}

// Called by useSectionQuery on manual refresh to drop stale cache entries.
export function clearResultCache(): void {
  resultCache.clear()
}
