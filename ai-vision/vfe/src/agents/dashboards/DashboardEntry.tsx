/**
 * Dashboard entry — the V2 Agent dashboard entry point.
 *
 * Reads from SpaceContext, AgentContext, TimeFilterContext; resolves
 * the agent key via the registry; and renders the matching dashboard
 * or an appropriate fallback state.
 *
 * Does NOT: know specific query keys, render charts, import agent sections,
 * or contain UCS rows data transformation logic.
 */

import { Bot, ChartArea, Database, Loader2, Store } from "lucide-react"
import { Suspense, useCallback, useDeferredValue, useMemo } from "react"
import { useNavigate } from "react-router-dom"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import { Hint } from "@/components/error/Hint"
import { PageBreadcrumb } from "@/components/layout/PageBreadcrumb"
import {
  SourceSystemToggleHeader,
  SourceSystemToggleProvider,
} from "@/agents/dashboards/SourceSystemToggle"
import { useDashboardTraceSourceSystem } from "@/agents/dashboards/DashboardTraceSourceContext"
import { DashboardCost } from "./DashboardCost"
import { SourceSystemProvider } from "@/contexts/SourceSystemContext"
import { DASHBOARD_FAIL_CLOSED_SOURCE_SYSTEM } from "@/agents/dashboards/sourceSystemSelection"
import {
  NativeDetailDrawer,
  type NativeDetailDrawerProps,
} from "@/components/traces-otel/DetailDrawer"
import { useSpace } from "@/contexts/SpaceContext"
import { useAgent } from "@/contexts/AgentContext"
import { useTimeFilter } from "@/contexts/TimeFilterContext"
import { SessionDrawerProvider } from "@/contexts/SessionDrawerContext"
import {
  PRODUCT_OVERRIDES,
  RUNTIME_REGISTRY,
  resolveDashboardAgent,
  resolveDashboardRuntime,
  resolveRuntimeType,
} from "@/agents/registry"
import { resolveCapabilities } from "@/agents/registry/capabilities"
import type { QueryTimeRange, TraceQueryTimeRange } from "@/agents/types"
import { getPresetTimeWindow } from "@/lib/timeFilter"
import {
  isOnboardingDemoAgent,
  resolveAgentDisplayName,
} from "@/agents/lib/agent-display"
import { DashboardQueryLifecycleProvider } from "@/agents/shared/DashboardQueryLifecycle"
import type { Agent } from "@/contexts/AgentContext"
import { useDetailNavigation } from "@/agents/traces/useDetailNavigation"

export interface DashboardEntryProps {
  /** Optional agent product override (e.g. from URL param). */
  agentProduct?: string
  /** Optional agent UUID override (multi-instance products like langfuse). */
  agentUuid?: string
  /** Optional space ID override from URL (e.g. /spaces/:spaceId/...). */
  spaceIdOverride?: string
}

function getAgentValue(agent: Agent): string {
  return String(agent.agent_product || agent.agent_name || "")
}

function findAgentByProduct(agents: Agent[], product: string): Agent | null {
  return (
    agents.find(
      (agent) =>
        getAgentValue(agent) === product ||
        agent.agent_product === product ||
        agent.agent_name === product
    ) ?? null
  )
}

function findAgentByUuid(agents: Agent[], uuid: string): Agent | null {
  return agents.find((agent) => agent.uuid === uuid) ?? null
}

function toTraceQueryTimeRange(timeRange: QueryTimeRange) {
  if (timeRange.type === "absolute") {
    return {
      from: new Date(timeRange.from).toISOString(),
      to: new Date(timeRange.to).toISOString(),
    }
  }
  const window = getPresetTimeWindow(timeRange.range)
  return {
    from: window.from,
    to: window.to,
  }
}

function DashboardTraceDetailDrawer(
  props: Omit<NativeDetailDrawerProps, "sourceSystem">
) {
  const traceSourceSystem = useDashboardTraceSourceSystem()

  if (traceSourceSystem !== "signoz") {
    return null
  }

  return <NativeDetailDrawer {...props} sourceSystem={traceSourceSystem} />
}

export function DashboardEntry({
  agentProduct: agentProductOverride,
  agentUuid: agentUuidOverride,
  spaceIdOverride,
}: DashboardEntryProps) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { currentSpace } = useSpace()
  const { agents, currentAgent, agentTypes, loading: agentLoading } = useAgent()
  const { timeRange, isCustom, fromTimestamp, toTimestamp, refreshTick } =
    useTimeFilter()

  const spaceId = spaceIdOverride || currentSpace?.id || ""
  const dashboardScope = spaceIdOverride ? "space" : "personal"

  const currentAgentProduct =
    currentAgent?.agent_product ?? currentAgent?.agent_name ?? ""
  // Prefer exact agent_uuid match: multi-instance products (langfuse) share
  // one agent_product, so product-only resolution picks the wrong project.
  const routeAgent = agentUuidOverride
    ? findAgentByUuid(agents, agentUuidOverride)
    : agentProductOverride
      ? findAgentByProduct(agents, agentProductOverride)
      : null
  const routeAgentType = agentProductOverride
    ? agentTypes.find(
        (agentType) =>
          agentType.agent_product === agentProductOverride ||
          agentType.agent_name === agentProductOverride
      )
    : null
  const rawAgentProduct = agentProductOverride
    ? (routeAgent?.agent_product ??
      routeAgent?.agent_name ??
      routeAgentType?.agent_product ??
      agentProductOverride)
    : currentAgentProduct

  const dashboardAgent = resolveDashboardAgent({
    agents,
    currentAgent,
    agentProduct: rawAgentProduct,
    agentUuid: agentUuidOverride,
    spaceId,
    currentSpaceId: currentSpace?.id ?? "",
  })

  const routeRuntimeType =
    routeAgent?.runtime_type ??
    routeAgentType?.runtime_type ??
    resolveRuntimeType(routeAgent, agentTypes)
  const runtimeType = agentProductOverride
    ? routeRuntimeType || agentProductOverride
    : resolveRuntimeType(currentAgent, agentTypes)
  const agentKey = resolveDashboardRuntime(dashboardAgent, runtimeType)

  // Resolve presets through the legacy time-window helper so chart domains,
  // zero-padding, and backend queries use the same exact boundaries.
  // refreshTick is in deps so preset windows slide forward on manual/auto refresh.
  const queryTimeRange: QueryTimeRange = useMemo(() => {
    if (isCustom) {
      return {
        type: "absolute" as const,
        from: new Date(fromTimestamp).getTime(),
        to: new Date(toTimestamp).getTime(),
      }
    }
    const window = getPresetTimeWindow(timeRange)
    return {
      type: "absolute" as const,
      from: new Date(window.from).getTime(),
      to: new Date(window.to).getTime(),
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fromTimestamp, isCustom, timeRange, toTimestamp, refreshTick])

  // Header controls must reflect clicks immediately, but rendering a full
  // dashboard switch is expensive. Defer one complete query snapshot so a
  // refresh cannot pair a new tick with a stale time range or agent identity.
  const dashboardContext = useMemo(
    () => ({
      refreshTick,
      timeRange: queryTimeRange,
      timeRangePreset: isCustom ? undefined : timeRange,
      agentKey,
      agentProduct: rawAgentProduct,
      agentUuid: dashboardAgent?.uuid,
      spaceId,
      scope: dashboardScope,
    }),
    [
      refreshTick,
      queryTimeRange,
      isCustom,
      timeRange,
      agentKey,
      rawAgentProduct,
      dashboardAgent,
      spaceId,
      dashboardScope,
    ]
  )
  const deferredDashboardContext = useDeferredValue(dashboardContext)
  const {
    timeRange: dashboardTimeRange,
    agentKey: dashboardAgentKey,
    agentProduct: dashboardAgentProduct,
  } = deferredDashboardContext
  const queryLifecycleIdentity = useMemo(
    () =>
      [
        agentKey,
        rawAgentProduct,
        dashboardAgent?.uuid ?? "",
        spaceId,
        dashboardScope,
        `${queryTimeRange.from}:${queryTimeRange.to}`,
        refreshTick,
      ].join("|"),
    [
      agentKey,
      rawAgentProduct,
      dashboardAgent?.uuid,
      spaceId,
      dashboardScope,
      queryTimeRange,
      refreshTick,
    ]
  )
  const dashboardTimeWindow = useMemo(
    () => toTraceQueryTimeRange(dashboardTimeRange),
    [dashboardTimeRange]
  )

  // Preserve the registry resolution contract: product customization first,
  // then fall back to the dashboard shared by the runtime family.
  const productOverride = PRODUCT_OVERRIDES[dashboardAgentProduct]
  const runtimeEntry = RUNTIME_REGISTRY[dashboardAgentKey]
  const DashboardComponent =
    productOverride?.dashboard ?? runtimeEntry?.dashboard ?? null
  const dashboardSessionSemantics = productOverride?.dashboard
    ? productOverride.dashboardSessionSemantics
    : runtimeEntry?.dashboardSessionSemantics

  // --- Session / trace detail drawer state ---
  const detailNavigation = useDetailNavigation()
  const currentDetail = detailNavigation.current
  const selectedSessionId =
    currentDetail?.kind === "session" ? currentDetail.id : null
  const selectedTraceId =
    currentDetail?.kind === "trace" ? currentDetail.id : null
  const focusTraceId =
    currentDetail?.kind === "session"
      ? (currentDetail.focusTraceId ?? null)
      : null
  const sessionTimeRangeOverride =
    currentDetail?.kind === "session" ? (currentDetail.timeRange ?? null) : null

  const handleSessionClick = useCallback(
    (sessionId: string, timeRange?: TraceQueryTimeRange) => {
      detailNavigation.openFromList({
        kind: "session",
        id: sessionId,
        timeRange: timeRange ?? null,
      })
    },
    [detailNavigation]
  )

  const handleDrawerClose = useCallback(() => {
    detailNavigation.closeCurrent(currentDetail?.navigationKey)
  }, [currentDetail?.navigationKey, detailNavigation])

  const handleOpenTrace = useCallback(
    (traceId: string) => {
      detailNavigation.openRelated({ kind: "trace", id: traceId })
    },
    [detailNavigation]
  )

  const handleOpenSession = useCallback(
    (sessionId: string, focusTid?: string) => {
      detailNavigation.openRelated({
        kind: "session",
        id: sessionId,
        // Navigated from a trace — no row timestamps available; fall back to
        // the dashboard's global window.
        timeRange: null,
        focusTraceId: focusTid ?? null,
      })
    },
    [detailNavigation]
  )

  // --- State: loading ---
  if (agentLoading || (!!spaceId && spaceId !== currentSpace?.id)) {
    return (
      <div className="flex flex-col items-center justify-center py-16">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
        <p className="mt-3 text-sm text-muted-foreground">
          {t("appState.loadingAgents")}
        </p>
      </div>
    )
  }

  // --- State: no agent ---
  if (!rawAgentProduct || (agentUuidOverride && !dashboardAgent)) {
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

  // --- State: unsupported agent ---
  const capabilities = resolveCapabilities(rawAgentProduct, agentTypes)
  const agentDisplayLabel = resolveAgentDisplayName(rawAgentProduct, agentTypes)

  if (!DashboardComponent || !capabilities.supportsDashboard) {
    return (
      <Hint
        icon={<ChartArea />}
        title={t("hint.dashboardNotConfigured", { agent: agentDisplayLabel })}
        description={t("hint.contactAdmin")}
      />
    )
  }

  // --- State: no space ---
  if (!spaceId) {
    return (
      <Hint
        icon={<Database />}
        title={t("hint.noSpace")}
        description={t("hint.noSpaceDesc")}
      />
    )
  }

  // All registered agents go through SourceSystemToggleProvider so the
  // correct defaultEventSource is resolved from the backend config.
  // The toggle buttons are only rendered when eventSources.length > 1.
  const supportsSourceToggle = true
  const breadcrumb = (
    <PageBreadcrumb
      pageTitle={t("dashboard.title")}
      agentProduct={rawAgentProduct}
      compact
    />
  )

  const dashboardContent = (
    <>
      <DashboardQueryLifecycleProvider identity={queryLifecycleIdentity}>
        <SessionDrawerProvider onSessionClick={handleSessionClick}>
          <Suspense
            fallback={
              <>
                <div className="px-4">{breadcrumb}</div>
                <div className="flex flex-col items-center justify-center py-16">
                  <Loader2 className="size-6 animate-spin text-muted-foreground" />
                </div>
              </>
            }
          >
            <div
              data-dashboard-heading
              className="flex min-w-0 items-center gap-2 px-4"
            >
              <div className="min-w-0 flex-1">{breadcrumb}</div>
              {supportsSourceToggle && <SourceSystemToggleHeader />}
              {dashboardContext === deferredDashboardContext &&
                deferredDashboardContext.agentUuid &&
                currentSpace?.permissions?.includes("model_pricing.read") && (
                  <DashboardCost
                    spaceId={deferredDashboardContext.spaceId}
                    agentUuid={deferredDashboardContext.agentUuid}
                    from={dashboardTimeWindow.from}
                    to={dashboardTimeWindow.to}
                    refreshTick={deferredDashboardContext.refreshTick}
                    simulated={isOnboardingDemoAgent(dashboardAgent)}
                  />
                )}
            </div>
            <DashboardComponent {...deferredDashboardContext} />
          </Suspense>
        </SessionDrawerProvider>
      </DashboardQueryLifecycleProvider>
      <DashboardTraceDetailDrawer
        spaceId={spaceId}
        agentProduct={rawAgentProduct || undefined}
        agentUuid={dashboardAgent?.uuid ?? null}
        demoCost={
          spaceId === currentSpace?.id && isOnboardingDemoAgent(dashboardAgent)
        }
        sessionId={selectedSessionId}
        sessionSemantics={dashboardSessionSemantics}
        sessionTimeRange={sessionTimeRangeOverride ?? dashboardTimeWindow}
        traceId={selectedTraceId}
        traceTimeRange={dashboardTimeWindow}
        focusTraceId={focusTraceId}
        onClose={handleDrawerClose}
        onOpenTrace={handleOpenTrace}
        onOpenSession={handleOpenSession}
      />
    </>
  )

  return supportsSourceToggle ? (
    <SourceSystemToggleProvider
      key={`${spaceId}:${dashboardAgentProduct}`}
      agentProduct={dashboardAgentProduct}
      requestLifecycleIdentity={rawAgentProduct}
    >
      {dashboardContent}
    </SourceSystemToggleProvider>
  ) : (
    <SourceSystemProvider sourceSystem={DASHBOARD_FAIL_CLOSED_SOURCE_SYSTEM}>
      {dashboardContent}
    </SourceSystemProvider>
  )
}
