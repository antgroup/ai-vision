/**
 * Trace entry — the V2 Agent trace page entry point.
 *
 * Reads from SpaceContext and AgentContext; resolves the agent key via
 * the registry; and renders the matching trace view or the default
 * TracePage implementation.
 *
 * Mirrors the DashboardEntry pattern: thin resolution layer that
 * dispatches to agent-specific or default rendering.
 */

import {
  Suspense,
  lazy,
  useState,
  useEffect,
  useMemo,
  useCallback,
  useRef,
} from "react"
import { Bot, GitBranch, Loader2, Store } from "lucide-react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { useTranslation } from "react-i18next"
import { useQuery } from "@tanstack/react-query"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Hint } from "@/components/error/Hint"
import { SpaceAgentGuard } from "@/components/error/SpaceAgentGuard"
import { useAgent } from "@/contexts/AgentContext"
import { useSpace } from "@/contexts/SpaceContext"
import { useTimeFilter } from "@/contexts/TimeFilterContext"
import { getPresetTimeWindow } from "@/lib/timeFilter"
import { resolveRuntimeType, resolveTraceConfig } from "@/agents/registry"
import { resolveCapabilities } from "@/agents/registry/capabilities"
import {
  isOnboardingDemoAgent,
  resolveAgentDisplayName,
} from "@/agents/lib/agent-display"
import { getDatasourceConfig } from "@/agents/lib/observabilityV2Api"
import {
  createLatestSearchParams,
  computeTraceDetailTimeWindow,
  computeTraceTimeWindow,
} from "@/agents/traces/sofaTraceTime"

import { resolveTraceSource, type TraceSource } from "./sourceSelection"

const LfTracePage = lazy(() => import("@/pages/traces-lf/TracePage"))
const NativeTracePage = lazy(
  () => import("@/pages/traces-otel/NativeTracePage")
)

function TraceLoadingFallback() {
  return (
    <div className="flex flex-col items-center justify-center py-16">
      <Loader2 className="size-6 animate-spin text-muted-foreground" />
    </div>
  )
}

export function TraceEntry() {
  const { loading: agentLoading, currentAgent } = useAgent()
  const [searchParams] = useSearchParams()
  // A new showcase entry starts with its own Trace or session selection.
  // Ordinary list and detail navigation keep the same mounted state.
  const demoScene = isOnboardingDemoAgent(currentAgent)
    ? searchParams.get("demo_scene")
    : null

  if (agentLoading) {
    return <TraceLoadingFallback />
  }

  return (
    <SpaceAgentGuard>
      <TraceContent key={demoScene} />
    </SpaceAgentGuard>
  )
}

function TraceContent() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { currentAgent, agents, agentTypes } = useAgent()
  const { currentSpace } = useSpace()
  const {
    timeRange,
    isCustom,
    fromTimestamp: customFrom,
    toTimestamp: customTo,
    manualRefreshTick,
  } = useTimeFilter()
  const spaceId = currentSpace?.id ?? ""
  const { fromTimestamp, toTimestamp } = useMemo(() => {
    const { from, to } = getPresetTimeWindow(timeRange)
    return {
      fromTimestamp: isCustom && customFrom ? customFrom : from,
      toTimestamp: isCustom && customTo ? customTo : to,
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timeRange, isCustom, customFrom, customTo, manualRefreshTick])

  const [searchParams, setSearchParams] = useSearchParams()
  const initialSearchTraceId = searchParams.get("query_traceid") || ""
  const initialSelectedTraceId =
    searchParams.get("traceId") || searchParams.get("traceid") || ""
  const urlFromTimestamp = searchParams.get("from") || ""
  const urlToTimestamp = searchParams.get("to") || ""
  const urlTimeRange = searchParams.get("range") || ""
  const urlTraceFromTimestamp = searchParams.get("trace_from") || ""
  const urlTraceToTimestamp = searchParams.get("trace_to") || ""
  // Span to focus in the initial detail (MCP drawer deep links).
  const urlFocusSpanId = searchParams.get("focus_span") || ""

  // Seed independent list/detail time windows for trace deep links.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const queryTraceId = params.get("query_traceid")
    const traceId = params.get("traceId") || params.get("traceid")
    const existingFrom = params.get("from")
    const existingTo = params.get("to")
    const existingRange = params.get("range")
    const existingTraceFrom = params.get("trace_from")
    const existingTraceTo = params.get("trace_to")

    const needsListWindow =
      queryTraceId && !existingRange && (!existingFrom || !existingTo)
    const needsDetailWindow =
      traceId && (!existingTraceFrom || !existingTraceTo)

    if (needsListWindow || needsDetailWindow) {
      setSearchParams(
        (prev) => {
          const next = createLatestSearchParams(prev)
          if (needsListWindow && queryTraceId) {
            const { from, to } = computeTraceTimeWindow(
              queryTraceId,
              fromTimestamp,
              toTimestamp
            )
            next.delete("range")
            next.set("from", from)
            next.set("to", to)
          }
          if (needsDetailWindow && traceId) {
            // Legacy detail links stored their detail window in from/to.
            const legacyFrom = !existingRange ? existingFrom : null
            const legacyTo = !existingRange ? existingTo : null
            const { from, to } = computeTraceTimeWindow(
              traceId,
              fromTimestamp,
              toTimestamp
            )
            next.set("trace_from", legacyFrom || from)
            next.set("trace_to", legacyTo || to)
          }
          return next
        },
        { replace: true }
      )
    }
  }, [fromTimestamp, searchParams, setSearchParams, toTimestamp])

  // Selection state — TraceEntry owns traceId URL param
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(
    initialSelectedTraceId || null
  )

  const onTraceSelect = useCallback(
    (traceId: string, startMs?: number, endMs?: number | null) => {
      setSelectedTraceId(traceId)
      setSearchParams(
        (prev) => {
          const next = createLatestSearchParams(prev)
          next.set("traceId", traceId)
          if (startMs !== undefined && !isNaN(startMs)) {
            const { from, to } = computeTraceDetailTimeWindow(startMs, endMs)
            next.set("trace_from", from)
            next.set("trace_to", to)
          } else {
            const { from, to } = computeTraceTimeWindow(
              traceId,
              fromTimestamp,
              toTimestamp
            )
            next.set("trace_from", from)
            next.set("trace_to", to)
          }
          return next
        },
        { replace: true }
      )
    },
    [fromTimestamp, setSearchParams, toTimestamp]
  )

  const onTraceDeselect = useCallback(() => {
    setSelectedTraceId(null)
    setSearchParams(
      (prev) => {
        const next = createLatestSearchParams(prev)
        next.delete("traceId")
        next.delete("traceid")
        next.delete("trace_from")
        next.delete("trace_to")
        return next
      },
      { replace: true }
    )
  }, [setSearchParams])

  const agentProduct = currentAgent?.agent_product ?? ""

  const {
    data: dsConfig,
    isLoading: dsConfigLoading,
    isError: dsConfigError,
    isFetching: dsConfigFetching,
    refetch: refetchDatasourceConfig,
  } = useQuery({
    queryKey: ["datasourceConfig", spaceId, agentProduct],
    queryFn: () => getDatasourceConfig(spaceId, agentProduct),
    enabled: !!spaceId && !!agentProduct,
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  })

  const traceSources = dsConfig?.traceSources ?? []
  const hasSignoz = traceSources.includes("signoz")
  const hasLangfuse = traceSources.includes("langfuse")
  const sourceCount = [hasSignoz, hasLangfuse].filter(Boolean).length
  const showToggle = sourceCount > 1

  // Keep a toggle choice within the space and Agent where it was made.
  const contextKey = `${spaceId}:${agentProduct}`
  const [sourceOverride, setSourceOverride] = useState<{
    contextKey: string
    source: TraceSource
  } | null>(null)
  const pendingSourceFocus = useRef<TraceSource | null>(null)
  const requestedSource = searchParams.get("source_system")
  const source = resolveTraceSource(
    dsConfig,
    contextKey,
    sourceOverride,
    requestedSource
  )
  const setSource = (value: TraceSource) => {
    if (value === source) return
    const next = createLatestSearchParams(searchParams)
    // Task-user and collector identity use the same legacy URL key. Never
    // transfer that predicate to a source with a different identity contract.
    const unsupported = ["username"]
    if (value === "langfuse") {
      unsupported.push(
        "status",
        "min_duration_ms",
        "min_span_count",
        "min_trace_count",
        "min_total_tokens"
      )
      if (
        next.get("view") === "session" ||
        next.get("query_search_type") === "output"
      ) {
        unsupported.push("query_search_type", "query_search_keyword")
      }
    }
    if (unsupported.some((key) => next.has(key))) {
      unsupported.forEach((key) => next.delete(key))
      toast.info(t("traceSearch.traceOnlyFiltersCleared"))
    }
    next.set("source_system", value)
    setSearchParams(next, { replace: true })
    setSourceOverride({ contextKey, source: value })
  }

  // URL range/from/to belong to the list/session query only.
  const effectiveTimeRange = useMemo(() => {
    if (urlFromTimestamp && urlToTimestamp) {
      return {
        fromTimestamp: urlFromTimestamp,
        toTimestamp: urlToTimestamp,
      }
    }
    if (urlTimeRange) {
      return { fromTimestamp, toTimestamp }
    }
    if (initialSearchTraceId) {
      const { from, to } = computeTraceTimeWindow(
        initialSearchTraceId,
        fromTimestamp,
        toTimestamp
      )
      return { fromTimestamp: from, toTimestamp: to }
    }
    return { fromTimestamp, toTimestamp }
  }, [
    initialSearchTraceId,
    urlFromTimestamp,
    urlToTimestamp,
    urlTimeRange,
    fromTimestamp,
    toTimestamp,
  ])

  const selectedTraceTimeRange = useMemo(() => {
    if (!selectedTraceId) return null
    if (urlTraceFromTimestamp && urlTraceToTimestamp) {
      return { from: urlTraceFromTimestamp, to: urlTraceToTimestamp }
    }
    // Backward compatibility for detail links created before trace_from/trace_to.
    if (!urlTimeRange && urlFromTimestamp && urlToTimestamp) {
      return { from: urlFromTimestamp, to: urlToTimestamp }
    }
    const { from, to } = computeTraceTimeWindow(
      selectedTraceId,
      effectiveTimeRange.fromTimestamp,
      effectiveTimeRange.toTimestamp
    )
    return { from, to }
  }, [
    effectiveTimeRange,
    selectedTraceId,
    urlFromTimestamp,
    urlTimeRange,
    urlToTimestamp,
    urlTraceFromTimestamp,
    urlTraceToTimestamp,
  ])

  // --- State: no agent ---
  // Checked before the datasource-config guards below: when no agent is
  // selected the config query is disabled (enabled: !!agentProduct), so
  // dsConfig stays undefined and must not be mistaken for a config error.
  if (!agentProduct) {
    const hasNoAgents = agents.length === 0
    return (
      <Hint
        icon={<Bot />}
        title={t("hint.noAgent")}
        description={
          hasNoAgents ? t("hint.noAgentDescEmpty") : t("hint.noAgentDescSelect")
        }
        action={
          hasNoAgents ? (
            <Button
              variant="outline"
              className="rounded-full px-6"
              onClick={() => navigate("/access?tab=market")}
            >
              <Store className="mr-2 size-4" />
              {t("hint.goToMarket")}
            </Button>
          ) : undefined
        }
      />
    )
  }

  // Wait for datasource config before rendering any trace page.
  if (dsConfigLoading) {
    return <TraceLoadingFallback />
  }

  // Do not issue trace requests until the configured sources are known.
  if (dsConfigError || !dsConfig) {
    return (
      <Hint
        icon={<GitBranch />}
        title={t("hint.traceConfigError")}
        description={t("hint.traceConfigErrorDesc")}
        action={
          <Button
            variant="outline"
            disabled={dsConfigFetching}
            onClick={() => void refetchDatasourceConfig()}
          >
            {t("common.retry")}
          </Button>
        }
      />
    )
  }

  if (requestedSource !== null && source === null) {
    return (
      <Hint
        icon={<GitBranch />}
        title={t("hint.requestedSourceUnavailable")}
        description={t("hint.requestedSourceUnavailableDesc", {
          source: requestedSource,
        })}
        action={
          <Button
            variant="outline"
            onClick={() =>
              setSearchParams(
                (current) => {
                  const next = new URLSearchParams(current)
                  next.delete("source_system")
                  return next
                },
                { replace: true }
              )
            }
          >
            {t("hint.useConfiguredSource")}
          </Button>
        }
      />
    )
  }

  const runtimeType = resolveRuntimeType(currentAgent, agentTypes)
  const capabilities = resolveCapabilities(agentProduct, agentTypes)
  const traceConfig = resolveTraceConfig(runtimeType, agentProduct)
  const agentDisplayLabel = resolveAgentDisplayName(agentProduct, agentTypes)

  // --- State: agent doesn't support trace ---
  if (!capabilities.supportsTrace || sourceCount === 0) {
    return (
      <Hint
        icon={<GitBranch />}
        title={t("hint.traceNotConfigured", { agent: agentDisplayLabel })}
        description={capabilities.traceDisabledTip || t("hint.contactAdmin")}
      />
    )
  }

  // --- State: trace not available ---
  if (traceConfig === null) {
    return (
      <Hint
        icon={<GitBranch />}
        title={t("hint.traceNotConfigured", { agent: agentDisplayLabel })}
        description={t("hint.contactAdmin")}
      />
    )
  }

  if (traceConfig.traceView) {
    const CustomTraceView = traceConfig.traceView
    return (
      <Suspense fallback={<TraceLoadingFallback />}>
        <CustomTraceView
          agentKey={runtimeType}
          agentProduct={agentProduct}
          spaceId={spaceId}
          timeRange={effectiveTimeRange}
          refreshTick={manualRefreshTick}
          initialSearchTraceId={initialSearchTraceId}
          selectedTraceId={selectedTraceId}
          selectedTraceTimeRange={selectedTraceTimeRange}
          onTraceSelect={onTraceSelect}
          onTraceDeselect={onTraceDeselect}
        />
      </Suspense>
    )
  }

  const tracePageProps = {
    agentKey: runtimeType,
    agentProduct,
    agentUuid: currentAgent?.uuid ?? null,
    spaceId,
    timeRange: effectiveTimeRange,
    refreshTick: manualRefreshTick,
    initialSearchTraceId,
    selectedTraceId,
    selectedTraceTimeRange,
    focusSpanId: urlFocusSpanId || null,
    onTraceSelect,
    onTraceDeselect,
  }

  const toggleBtn = (label: string, value: TraceSource) => (
    <button
      type="button"
      aria-pressed={source === value}
      ref={(button) => {
        // The page remounts on source changes. Restore its focused control
        // unless the user moved focus while the new page was loading.
        if (!button || pendingSourceFocus.current !== value || source !== value)
          return
        pendingSourceFocus.current = null
        if (document.activeElement === document.body) button.focus()
      }}
      className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${
        source === value
          ? "bg-primary text-primary-foreground shadow-sm"
          : "text-muted-foreground hover:text-foreground"
      }`}
      onClick={(event) => {
        if (
          value !== source &&
          document.activeElement === event.currentTarget
        ) {
          pendingSourceFocus.current = value
        }
        setSource(value)
      }}
    >
      {label}
    </button>
  )

  const sourceToggle = showToggle ? (
    <div className="flex rounded-lg border border-border bg-muted p-0.5">
      {hasLangfuse && toggleBtn("Langfuse", "langfuse")}
      {hasSignoz && toggleBtn("SigNoz", "signoz")}
    </div>
  ) : null

  return (
    <div className="flex min-w-0 flex-col overflow-x-clip">
      <Suspense fallback={<TraceLoadingFallback />}>
        {source === "signoz" ? (
          <NativeTracePage
            key={`${spaceId}:${agentProduct}:${currentAgent?.uuid ?? ""}:${source}`}
            {...tracePageProps}
            sourceSystem="signoz"
            demoCost={isOnboardingDemoAgent(currentAgent)}
            sourceToggle={sourceToggle}
          />
        ) : (
          <LfTracePage
            key={`${spaceId}:${agentProduct}:${currentAgent?.uuid ?? ""}:langfuse`}
            {...tracePageProps}
            sourceToggle={sourceToggle}
          />
        )}
      </Suspense>
    </div>
  )
}
