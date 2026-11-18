import type { TraceListFilters } from "@/agents/types/langfuse"
import type { TraceSearchValue } from "@/agents/types/trace"

export const SOFA_TRACE_HALF_WINDOW_MS = 30 * 60 * 1000

/** Only list-owned state triggers restoration; detail and time have other owners. */
export function traceListUrlStateKey(params: URLSearchParams): string {
  return JSON.stringify(
    [
      "view",
      "sessionId",
      "query_traceid",
      "query_sessionid",
      "query_search_type",
      "query_search_keyword",
      "env",
      "scene",
      "username",
      "tags",
      "status",
      "min_duration_ms",
      "min_span_count",
      "min_trace_count",
      "min_total_tokens",
    ].map((key) => params.get(key))
  )
}

export function createLatestSearchParams(prev: URLSearchParams) {
  if (typeof window === "undefined" || !window.location.search) {
    return new URLSearchParams(prev)
  }
  return new URLSearchParams(window.location.search)
}

export function isSofaTraceId(value: string): boolean {
  if (value.length < 25 || value.length > 31) return false
  if (!/^[0-9a-fA-F]+$/.test(value)) return false
  const tsRaw = value.slice(8, 21)
  if (tsRaw.length !== 13 || !/^\d+$/.test(tsRaw)) return false
  const ts = Number(tsRaw)
  if (ts < 1262304000000 || ts > 1924997999000) return false
  const seq = Number(value.slice(21, 25))
  if (seq < 1000 || seq > 9000) return false
  return true
}

export function extractSofaTimestampMs(value: string): number | null {
  if (!isSofaTraceId(value)) return null
  return Number(value.slice(8, 21))
}

/**
 * 统一的 trace 时间戳出口。深链 ?traceId= 需要从 id 推导一个时间窗,
 * 否则 SigNoz detail 无 from/to 会 3012(列存扫描全表失败)。
 * 支持两种内嵌时间戳的 id:
 *  - 老版 sofa trace id(25~31 位,内嵌 13 位 ms 时间戳)
 *  - 新版 SigNoz trace id(UUIDv7 风格 32 位 hex,前 48 bit = unix-ms)
 */
export function extractTraceTimestampMs(value: string): number | null {
  const sofaTs = extractSofaTimestampMs(value)
  if (sofaTs !== null) return sofaTs
  // UUIDv7:32 位 hex,前 12 位(48 bit)= unix-ms
  if (value.length === 32 && /^[0-9a-fA-F]{32}$/.test(value)) {
    const ts = parseInt(value.slice(0, 12), 16)
    if (ts > 1262304000000 && ts < 1924997999000) return ts
  }
  return null
}

export function computeTraceTimeWindow(
  traceId: string,
  fallbackFrom: string,
  fallbackTo: string
): { from: string; to: string } {
  const ts = extractTraceTimestampMs(traceId)
  if (ts !== null) {
    return {
      from: new Date(ts - SOFA_TRACE_HALF_WINDOW_MS).toISOString(),
      to: new Date(ts + SOFA_TRACE_HALF_WINDOW_MS).toISOString(),
    }
  }
  return { from: fallbackFrom, to: fallbackTo }
}

function setUrlTimeRange(
  params: URLSearchParams,
  fromTimestamp: string,
  toTimestamp: string
) {
  if (!fromTimestamp || !toTimestamp) return
  params.delete("range")
  params.set("from", fromTimestamp)
  params.set("to", toTimestamp)
}

export function syncTraceViewSearchParams(
  params: URLSearchParams,
  {
    view,
    selectedSessionId,
    traceIdKeyword,
    sessionIdKeyword,
    contentSearch,
    fromTimestamp,
    toTimestamp,
    filters,
  }: {
    view: string
    selectedSessionId?: string | null
    traceIdKeyword?: string | null
    sessionIdKeyword?: string | null
    contentSearch?: TraceSearchValue | null
    fromTimestamp: string
    toTimestamp: string
    filters?: TraceListFilters
  }
) {
  const isSessionView = view === "session"
  const hasExplicitTimeRange = params.has("from") && params.has("to")
  const hasPresetTimeRange = params.has("range")

  if (isSessionView) {
    params.set("view", "session")
    if (selectedSessionId) {
      params.set("sessionId", selectedSessionId)
      setUrlTimeRange(params, fromTimestamp, toTimestamp)
    } else {
      params.delete("sessionId")
    }
  } else {
    params.delete("view")
    params.delete("sessionId")
  }

  if (traceIdKeyword) {
    params.set("query_traceid", traceIdKeyword)
    if (
      !hasExplicitTimeRange &&
      !hasPresetTimeRange &&
      (!isSessionView || !selectedSessionId)
    ) {
      const { from, to } = computeTraceTimeWindow(
        traceIdKeyword,
        fromTimestamp,
        toTimestamp
      )
      params.delete("range")
      params.set("from", from)
      params.set("to", to)
    }
  } else {
    params.delete("query_traceid")
  }

  if (sessionIdKeyword) {
    params.set("query_sessionid", sessionIdKeyword)
  } else {
    params.delete("query_sessionid")
  }

  if (
    (contentSearch?.type === "input" || contentSearch?.type === "output") &&
    contentSearch.keyword
  ) {
    params.set("query_search_type", contentSearch.type)
    params.set("query_search_keyword", contentSearch.keyword)
  } else {
    params.delete("query_search_type")
    params.delete("query_search_keyword")
  }

  // 多维筛选同步：env / scene / username 单值，tags 逗号分隔多值
  // （tag 值内含 , 时逐值 encodeURIComponent 后再 join，避免分隔符歧义）。
  syncScalarFilter(params, "env", filters?.env)
  syncScalarFilter(params, "scene", filters?.scene)
  syncScalarFilter(params, "username", filters?.username)
  syncScalarFilter(params, "status", filters?.status)
  syncScalarFilter(
    params,
    "min_duration_ms",
    filters?.thresholdInputs?.minDurationMs ??
      filters?.minDurationMs?.toString()
  )
  syncScalarFilter(
    params,
    "min_span_count",
    filters?.thresholdInputs?.minSpanCount ?? filters?.minSpanCount?.toString()
  )
  syncScalarFilter(
    params,
    "min_trace_count",
    filters?.thresholdInputs?.minTraceCount ??
      filters?.minTraceCount?.toString()
  )
  syncScalarFilter(
    params,
    "min_total_tokens",
    filters?.thresholdInputs?.minTotalTokens ??
      filters?.minTotalTokens?.toString()
  )
  // An explicitly empty invalid URL predicate must remain distinguishable from no filter.
  for (const [field, key] of Object.entries({
    minDurationMs: "min_duration_ms",
    minSpanCount: "min_span_count",
    minTraceCount: "min_trace_count",
    minTotalTokens: "min_total_tokens",
  })) {
    const raw =
      filters?.thresholdInputs?.[
        field as keyof NonNullable<TraceListFilters["thresholdInputs"]>
      ]
    if (raw !== undefined) params.set(key, raw)
  }
  const tags = filters?.tags
  if (tags && tags.length > 0) {
    params.set("tags", tags.map(encodeURIComponent).join(","))
  } else {
    params.delete("tags")
  }
}

function syncScalarFilter(
  params: URLSearchParams,
  key: string,
  value: string | null | undefined
) {
  if (value) {
    params.set(key, value)
  } else {
    params.delete(key)
  }
}

/** 从 URL 解析多维筛选状态，用于首次挂载恢复筛选条件。 */
export function parseTraceListFilters(
  params: URLSearchParams
): TraceListFilters {
  const filters: TraceListFilters = {}
  const env = params.get("env")
  if (env) filters.env = env
  const scene = params.get("scene")
  if (scene) filters.scene = scene
  const username = params.get("username")
  if (username) filters.username = username
  const tagsRaw = params.get("tags")
  if (tagsRaw) {
    const tags = tagsRaw
      .split(",")
      .map((tag) => {
        try {
          return decodeURIComponent(tag)
        } catch {
          return tag
        }
      })
      .filter(Boolean)
    if (tags.length > 0) filters.tags = tags
  }
  const status = params.get("status")
  if (status === "error" || status === "ok" || status === "unset") {
    filters.status = status
  }
  const duration = parseNonnegativeNumber(params.get("min_duration_ms"))
  if (duration !== undefined) filters.minDurationMs = duration
  const count = parseNonnegativeNumber(params.get("min_span_count"))
  if (count !== undefined && Number.isSafeInteger(count))
    filters.minSpanCount = count
  const turns = parseNonnegativeNumber(params.get("min_trace_count"))
  if (turns !== undefined && Number.isSafeInteger(turns) && turns > 0)
    filters.minTraceCount = turns
  const tokens = parseNonnegativeNumber(params.get("min_total_tokens"))
  if (tokens !== undefined && Number.isSafeInteger(tokens))
    filters.minTotalTokens = tokens
  return filters
}

function parseNonnegativeNumber(value: string | null): number | undefined {
  if (!value?.trim()) return undefined
  const number = Number(value)
  return Number.isFinite(number) && number >= 0 ? number : undefined
}

export function parseTraceSearch(
  params: URLSearchParams,
  fallbackTraceId = ""
): TraceSearchValue {
  const type = params.get("query_search_type")
  const keyword = params.get("query_search_keyword")?.trim()
  if ((type === "input" || type === "output") && keyword) {
    return { type, keyword }
  }
  return {
    type: "traceId",
    keyword: params.get("query_traceid") ?? fallbackTraceId,
  }
}

export function parseNanoToMs(
  value: number | string | null | undefined
): number | null {
  if (value === null || value === undefined) return null
  const n = Number(value)
  if (Number.isFinite(n)) {
    return n > 1e15 ? n / 1e6 : n
  }
  if (typeof value === "string") {
    const d = new Date(value)
    if (!isNaN(d.getTime())) return d.getTime()
  }
  return null
}

export const TRACE_DETAIL_PADDING_MS = 10 * 60 * 1000

export function computeTraceDetailTimeWindow(
  startMs: number,
  endMs: number | null | undefined
): { from: string; to: string } {
  const effectiveEnd = endMs && Number.isFinite(endMs) ? endMs : startMs
  return {
    from: new Date(startMs - TRACE_DETAIL_PADDING_MS).toISOString(),
    to: new Date(effectiveEnd + TRACE_DETAIL_PADDING_MS).toISOString(),
  }
}
