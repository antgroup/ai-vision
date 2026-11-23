import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react"
import { createPortal } from "react-dom"
import { useQueryClient } from "@tanstack/react-query"
import { useLocation, useNavigate } from "react-router-dom"
import { useTranslation } from "react-i18next"
import { ArrowRight, X } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { useUser } from "@/contexts/UserContext"
import { useSpace } from "@/contexts/SpaceContext"
import { useAgent } from "@/contexts/AgentContext"
import { useTimeFilter } from "@/contexts/TimeFilterContext"
import { getPresetTimeWindow } from "@/lib/timeFilter"
import { clearResultCache } from "@/agents/lib/dashboardQueryAdapter"
import { isOnboardingDemoAgent } from "@/agents/lib/agent-display"
import {
  DemoConsentDialog,
  type DemoReceipt,
  type DemoScenario,
} from "./DemoConsentDialog"
import {
  connectionSteps,
  dashboardSteps,
  dismissActiveGuideProgress,
  guideRoute,
  guideStorageKey,
  readGuideProgress,
  saveGuideProgress,
  type GuideProgress,
  type GuideStep,
} from "./state"
import "./product-guide.css"

import { ProductGuideContext } from "./useProductGuide"

export function ProductGuideProvider({ children }: { children: ReactNode }) {
  const { user } = useUser()
  const { currentSpace } = useSpace()
  const { currentAgent } = useAgent()
  const identity =
    user && currentSpace
      ? guideStorageKey(
          user.user_uuid,
          currentSpace.id,
          currentAgent?.uuid || currentAgent?.agent_product || "none"
        )
      : ""
  return (
    <GuideSession
      storageKey={identity}
      identityScope={
        user && currentSpace
          ? guideStorageKey(user.user_uuid, currentSpace.id, "")
          : ""
      }
      hasAgent={!!currentAgent}
    >
      {children}
    </GuideSession>
  )
}

function visible(selector: string) {
  return (
    Array.from(document.querySelectorAll<HTMLElement>(selector)).find(
      (element) => {
        const rect = element.getBoundingClientRect()
        return (
          rect.width > 2 &&
          rect.height > 2 &&
          getComputedStyle(element).visibility !== "hidden"
        )
      }
    ) ?? null
  )
}

function GuideSession({
  children,
  storageKey,
  identityScope,
  hasAgent,
}: {
  children: ReactNode
  storageKey: string
  identityScope: string
  hasAgent: boolean
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const location = useLocation()
  const navigate = useNavigate()
  const { user, dismissProductGuideOffer } = useUser()
  const { currentSpace } = useSpace()
  const { agents, currentAgent, refreshAgents } = useAgent()
  const { setTimeRange } = useTimeFilter()
  const [stored, setStored] = useState<{
    key: string
    scope: string
    progress: GuideProgress | null
  }>(() => ({
    key: storageKey,
    scope: identityScope,
    progress: storageKey ? readGuideProgress(storageKey) : null,
  }))
  // Creating an Agent updates AgentContext while the setup form is still open.
  // Keep that form mounted and carry only connection progress within the same
  // viewer/Space; data exploration and diagnosis never transfer across Agents.
  const progress =
    stored.key === storageKey && stored.scope === identityScope
      ? stored.progress
      : stored.scope === identityScope &&
          stored.progress &&
          !stored.progress.demo &&
          connectionSteps.includes(
            stored.progress.step as (typeof connectionSteps)[number]
          ) &&
          ["active", "paused"].includes(stored.progress.status)
        ? stored.progress
        : storageKey
          ? readGuideProgress(storageKey)
          : null
  if (stored.key !== storageKey || stored.scope !== identityScope)
    setStored({ key: storageKey, scope: identityScope, progress })
  useEffect(() => {
    if (storageKey && progress) saveGuideProgress(storageKey, progress)
  }, [storageKey, progress])
  const active = !!storageKey && progress?.status === "active"
  const offer =
    user?.product_guide_pending === true &&
    !!storageKey &&
    !progress &&
    location.pathname === "/dashboard"
  const watching =
    active || offer || (!!storageKey && location.pathname === "/dashboard")
  const step = progress?.step ?? "connect"
  const demo = progress?.demo
  const [demoOfferScope, setDemoOfferScope] = useState<string | null>(null)
  const demoOpen = !!identityScope && demoOfferScope === identityScope
  if (demoOfferScope && !demoOpen) setDemoOfferScope(null)
  const canStartDemo = [
    "agent.manage",
    "agent.ingest_token.mint",
    "diagnosis.execute",
  ].every((permission) => currentSpace?.permissions.includes(permission))
  const demoEntry = useRef<HTMLButtonElement>(null)
  const demoOpener = useRef<HTMLElement | null>(null)
  const [savingChoice, setSavingChoice] = useState(false)
  const [revision, setRevision] = useState(0)
  const previousFocus = useRef<HTMLElement | null>(null)
  const update = (next: GuideProgress) => {
    setStored({ key: storageKey, scope: identityScope, progress: next })
    if (storageKey) saveGuideProgress(storageKey, next)
  }
  const move = (next: GuideStep) =>
    update({ ...progress, step: next, status: "active" })
  const openRuleEvidence = (
    ruleUuid: string,
    traceId: string,
    spanId: string
  ) => {
    if (
      !active ||
      (step !== "run" && step !== "result") ||
      demo?.ruleUuid !== ruleUuid ||
      demo.traceId !== traceId ||
      !/^[0-9a-f]{16}$/.test(spanId)
    )
      return
    // The source link closes the completed result before the guide observer
    // may see it. Keep the proof; the target Span must still open successfully.
    update({
      ...progress,
      step: "result",
      status: "active",
      demo: { ...demo, evidenceSpanId: spanId },
    })
  }
  const restoreFocus = () => {
    requestAnimationFrame(
      () => previousFocus.current?.isConnected && previousFocus.current.focus()
    )
  }
  const pause = () => {
    update({ ...progress, step, status: "paused" })
    restoreFocus()
  }
  const saveWelcomeChoice = async () => {
    if (user?.product_guide_pending !== true) return true
    if (savingChoice) return false
    setSavingChoice(true)
    try {
      await dismissProductGuideOffer()
      return true
    } catch {
      toast.error(t("productGuide.saveChoiceError"))
      return false
    } finally {
      setSavingChoice(false)
    }
  }
  const skip = async () => {
    if (!(await saveWelcomeChoice())) return
    // Agent changes can leave an active copy behind on the previous scope.
    if (user) dismissActiveGuideProgress(user.user_uuid)
    if (active) {
      update({ step, status: "dismissed" })
      restoreFocus()
    }
  }
  const start = async (restart = false) => {
    previousFocus.current = document.activeElement as HTMLElement
    if (!(await saveWelcomeChoice())) return
    const resume = !restart && progress?.status === "paused"
    let next: GuideStep = resume ? step : "connect"
    if (resume && demo?.comparisonTraceId && next.startsWith("compare")) {
      move(next)
      const params = new URLSearchParams(
        guideRoute("/trace/compare", location.search).split("?")[1]
      )
      params.set("a", demo.comparisonTraceId)
      params.set("b", demo.traceId)
      params.set("showMcp", "1")
      navigate(`/trace/compare?${params}`)
      return
    }
    if (
      [
        "enhanced",
        "enhancedCall",
        "mcpConfig",
        "mcpImport",
        "mcpConfigReady",
        "call",
        "inspect",
        "check",
        "chooseDiagnoser",
        "run",
        "result",
        "evidence",
      ].includes(next) &&
      !visible('[data-testid="native-trace-detail"]') &&
      !visible('[data-product-guide="diagnosis"]')
    )
      next = "traces"
    if (next === "result" && !visible('[data-product-guide="diagnosis"]'))
      next = "check"
    if (resume) move(next)
    else update({ step: next, status: "active" })
    // Resume a cross-page walkthrough at its saved route, without opening or executing anything.
    if (dashboardSteps.includes(next as (typeof dashboardSteps)[number]))
      navigate(guideRoute("/dashboard", location.search))
    else if (
      connectionSteps.includes(next as (typeof connectionSteps)[number])
    ) {
      if (next !== "connect" && !location.pathname.startsWith("/access"))
        navigate(guideRoute("/access", location.search))
    } else if (location.pathname !== "/trace") {
      move("traces")
      navigate(guideRoute("/trace", location.search))
    }
  }

  const browse = async () => {
    previousFocus.current = document.activeElement as HTMLElement
    if (!(await saveWelcomeChoice())) return
    move("scope")
    navigate(guideRoute("/dashboard", location.search))
  }

  const openDemo = () => {
    if (!identityScope || !canStartDemo) return
    demoOpener.current = document.activeElement as HTMLElement | null
    setDemoOfferScope(identityScope)
  }
  const closeDemo = () => {
    setDemoOfferScope(null)
    requestAnimationFrame(() => {
      if (demoOpener.current?.isConnected) demoOpener.current.focus()
      else demoEntry.current?.focus()
      demoOpener.current = null
    })
  }
  const enterDemo = async (
    receipt: DemoReceipt,
    scenario: DemoScenario,
    signal: AbortSignal
  ) => {
    if (!currentSpace || !user) return
    const spaceId = currentSpace.id
    const { agent_product: product, agent_uuid: agentUuid } = receipt
    const inventory = await refreshAgents({ silent: true, signal })
    if (signal.aborted) return
    if (
      !inventory?.some(
        (agent) => agent.uuid === agentUuid && agent.agent_product === product
      )
    )
      throw new Error("The demo Agent did not appear in this Space")
    void queryClient.invalidateQueries({
      queryKey: ["frameworkDashboardMetrics", spaceId, product, agentUuid],
      refetchType: "none",
    })
    clearResultCache()
    const next: GuideProgress = {
      step: "scope",
      status: "active",
      demo: {
        traceId: receipt.trace_id,
        ruleUuid: receipt.diagnoser_uuid,
        comparisonTraceId: receipt.comparison_trace_id,
      },
    }
    const key = guideStorageKey(user.user_uuid, spaceId, agentUuid)
    const range = "24h"
    setTimeRange(range)
    const params = new URLSearchParams({
      spaceId,
      agent: product,
      agent_uuid: agentUuid,
      range,
    })
    if (scenario === "guided") {
      saveGuideProgress(key, next)
      if (storageKey === key) update(next)
    } else {
      if (storageKey !== key && active && progress)
        update({ ...progress, status: "paused" })
      const demoProgress =
        storageKey === key ? progress : readGuideProgress(key)
      if (
        demoProgress?.status === "active" ||
        demoProgress?.status === "paused"
      ) {
        const paused: GuideProgress = {
          ...(demoProgress.demo?.traceId === receipt.trace_id
            ? demoProgress
            : next),
          status: "paused",
        }
        saveGuideProgress(key, paused)
        if (storageKey === key) update(paused)
      }
      params.set("source_system", "signoz")
      params.set("demo_scene", `${scenario}-${Date.now()}`)
      if (scenario === "session") {
        params.set("view", "session")
        params.set("sessionId", receipt.scenarios.session.session_id)
      } else {
        const { from, to } = getPresetTimeWindow(range)
        params.set("traceId", receipt.scenarios[scenario].trace_id)
        params.set("focus_span", receipt.scenarios[scenario].span_id)
        params.set("trace_from", from)
        params.set("trace_to", to)
      }
    }
    previousFocus.current = null
    demoOpener.current = null
    setDemoOfferScope(null)
    void saveWelcomeChoice()
    navigate(`${scenario === "guided" ? "/dashboard" : "/trace"}?${params}`)
  }

  useEffect(() => {
    if (!watching) return
    let frame = 0
    const notify = () => {
      if (frame) return
      frame = requestAnimationFrame(() => {
        frame = 0
        setRevision((value) => value + 1)
      })
    }
    const observer = new MutationObserver(notify)
    observer.observe(document.body, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: [
        "data-guide-state",
        "data-guide-selected",
        "data-guide-mode",
        "data-guide-rule",
        "data-guide-evidence",
        "data-guide-trace",
        "data-guide-content-mode",
        "data-guide-diagnosis-source",
        "data-guide-mcp-spans",
        "data-process-focused",
        "data-state",
      ],
    })
    window.addEventListener("resize", notify)
    window.addEventListener("scroll", notify, true)
    return () => {
      observer.disconnect()
      cancelAnimationFrame(frame)
      window.removeEventListener("resize", notify)
      window.removeEventListener("scroll", notify, true)
    }
  }, [watching])

  // Signals below are rendered by the real query/detail/result surfaces. A request
  // being accepted, a missing target, or an API error never constitutes completion.
  const metrics = storageKey ? visible('[data-product-guide="metrics"]') : null
  const setup = active ? visible('[data-product-guide="connection"]') : null
  const verification = active
    ? visible('[data-product-guide="onboarding-verification"]')
    : null
  const list = active ? visible('[data-product-guide="trace-list"]') : null
  const detail = active ? visible('[data-testid="native-trace-detail"]') : null
  const diagnosis = active ? visible('[data-product-guide="diagnosis"]') : null
  const mcpSummary =
    active && demo ? visible('[data-product-guide="mcp-summary"]') : null
  const mcpSpan =
    active && demo ? visible('[data-product-guide="mcp-span"]') : null
  const mcpConfig =
    active && demo ? visible('[data-product-guide="mcp-config"]') : null
  const mcpImport =
    active && demo ? visible('[data-product-guide="mcp-import"]') : null
  const comparison =
    active && demo ? visible('[data-product-guide="trace-comparison"]') : null
  const comparisonReady =
    comparison?.dataset.guideState === "ready" &&
    comparison.dataset.guideTraceA === demo?.comparisonTraceId &&
    comparison.dataset.guideTraceB === demo?.traceId
  const comparisonEvidence = comparisonReady
    ? visible(
        `[data-product-guide="compare-call-evidence"][data-guide-side="B"][data-guide-span="${demo?.evidenceSpanId}"]`
      )
    : null
  const ruleSelected =
    diagnosis?.dataset.guideMode === "rule" &&
    (!demo ||
      (diagnosis.dataset.guideRule === demo.ruleUuid &&
        diagnosis.dataset.guideTrace === demo.traceId))
  const ruleSucceeded =
    ruleSelected && diagnosis?.dataset.guideState === "succeeded"
  const targetDetail = !demo || detail?.dataset.guideTrace === demo.traceId
  const processCall =
    active && demo && targetDetail
      ? visible(
          '[data-trace-panel="process"] [data-process-focused="true"] button[data-process-source]'
        )
      : null
  const mcpProcessCall = processCall
    ?.closest<HTMLElement>("[data-guide-mcp-spans]")
    ?.dataset.guideMcpSpans?.split(" ")
    .includes(processCall.dataset.processSource ?? "")
    ? processCall
    : null
  const demoInRawMode = !!demo && detail?.dataset.guideContentMode === "raw"
  const demoEvidence =
    demo?.evidenceSpanId &&
    targetDetail &&
    detail?.dataset.guideSelected === demo.evidenceSpanId &&
    list?.dataset.guideDiagnosisSource === demo.evidenceSpanId
  const canBrowse =
    hasAgent &&
    (metrics?.dataset.guideState === "ready" ||
      list?.dataset.guideState === "ready" ||
      detail?.dataset.guideState === "ready")
  useEffect(() => {
    if (!active) return
    let next: GuideStep | undefined
    if (
      location.pathname === "/access" &&
      !demo &&
      !["chooseAgent", "pluginConfigure"].includes(step)
    )
      next = "chooseAgent"
    if (["connect", "chooseAgent"].includes(step) && setup) next = "framework"
    if (
      step === "chooseAgent" &&
      visible('[data-product-guide="plugin-setup"]')
    )
      next = "pluginConfigure"
    if (step === "verify" && verification?.dataset.guideState === "verified")
      next = "connected"
    if (
      step === "connect" &&
      location.pathname === "/trace" &&
      detail?.dataset.guideState === "ready"
    )
      next = "check"
    if (
      step === "traces" &&
      targetDetail &&
      detail?.dataset.guideState === "ready"
    ) {
      // The demo Trace always has MCP evidence. The drawer can report ready
      // before the enhanced summary is measurable, especially on replay.
      next = demo ? "enhanced" : "check"
    }
    if (step === "enhanced" && targetDetail && mcpSpan) next = "enhancedCall"
    if (step === "enhancedCall" && targetDetail && mcpSummary) next = "enhanced"
    if (["mcpConfig", "mcpImport"].includes(step)) {
      if (mcpImport && step !== "mcpImport") next = "mcpImport"
      else if (!mcpImport && mcpConfig?.dataset.guideState === "ready")
        next = "mcpConfigReady"
      else if (!mcpImport && step === "mcpImport") next = "mcpConfig"
    }
    if (step === "mcpConfigReady" && !mcpConfig && targetDetail && mcpSpan)
      next = "check"
    if (step === "compareCalls" && comparisonEvidence) next = "compareEvidence"
    if (step === "compareEvidence" && comparisonReady && !comparisonEvidence)
      next = "compareCalls"
    if (
      step === "inspect" &&
      detail?.dataset.guideState === "ready" &&
      !detail.dataset.guideSelected
    )
      next = "call"
    if (step === "call" && detail?.dataset.guideSelected) next = "inspect"
    if (
      ["check", "inspect", "enhanced", "enhancedCall"].includes(step) &&
      diagnosis
    )
      next = "chooseDiagnoser"
    if (step === "chooseDiagnoser" && ruleSelected) next = "run"
    if (step === "run" && diagnosis && !ruleSelected) next = "chooseDiagnoser"
    const evidence = diagnosis?.dataset.guideEvidence
    if (
      step === "run" &&
      ruleSucceeded &&
      (!demo || (evidence && /^[0-9a-f]{16}$/.test(evidence)))
    )
      next = "result"
    if (step === "result" && demoEvidence && !diagnosis) next = "evidence"
    if (step === "result" && diagnosis && !ruleSucceeded)
      next = ruleSelected ? "run" : "chooseDiagnoser"
    if (next) {
      const value: GuideProgress = {
        ...progress,
        step: next,
        status: "active",
        ...(demo && next === "result" && evidence
          ? { demo: { ...demo, evidenceSpanId: evidence } }
          : {}),
        ...(demo && next === "mcpConfigReady"
          ? { demo: { ...demo, mcpConfigSeen: true } }
          : {}),
      }
      const frame = requestAnimationFrame(() => {
        setStored({ key: storageKey, scope: identityScope, progress: value })
        saveGuideProgress(storageKey, value)
      })
      return () => cancelAnimationFrame(frame)
    }
  }, [
    active,
    step,
    revision,
    location.pathname,
    detail,
    diagnosis,
    mcpSummary,
    mcpSpan,
    mcpConfig,
    mcpImport,
    comparisonEvidence,
    comparisonReady,
    ruleSelected,
    ruleSucceeded,
    targetDetail,
    demoInRawMode,
    demoEvidence,
    demo,
    progress,
    setup,
    verification,
    storageKey,
    identityScope,
  ])

  const selectors: Record<GuideStep, string> = {
    scope: '[data-product-guide="scope"]',
    overview: '[data-product-guide="metrics"]',
    usage: '[data-product-guide="dashboard-usage"]',
    trend: '[data-product-guide="dashboard-trend"]',
    operations: '[data-product-guide="dashboard-operations"]',
    traces: demo
      ? `[data-product-guide="trace-row"][data-guide-trace="${demo.traceId}"]`
      : '[data-product-guide="trace-row"]',
    enhanced: '[data-product-guide="mcp-summary"]',
    enhancedCall: '[data-product-guide="mcp-span"]',
    mcpConfig: '[data-product-guide="mcp-config-open"]',
    mcpImport: '[data-product-guide="mcp-import"]',
    mcpConfigReady: '[data-product-guide="mcp-config"]',
    compare: '[data-product-guide="compare-overview"]',
    compareCalls: '[data-product-guide="compare-mcp-toggle"]',
    compareEvidence: '[data-product-guide="compare-call-evidence"]',
    call: '[data-testid="trace-tree-results"]',
    inspect: '[data-product-guide="span-details"]',
    check: '[data-product-guide="open-check"]',
    chooseDiagnoser: '[data-product-guide="diagnosis-select"]',
    run: '[data-product-guide="diagnosis-run"]',
    result: '[data-product-guide="diagnosis-results"]',
    evidence: '[data-product-guide="span-details"]',
    connect: '[data-product-guide="access"]',
    chooseAgent:
      '[data-product-guide="agent-market"], [data-product-guide="agent-market-tab"]',
    framework: '[data-product-guide="onboarding-framework"]',
    application: '[data-product-guide="onboarding-project"]',
    configure: '[data-product-guide="onboarding-code"]',
    verify: '[data-product-guide="onboarding-verification"]',
    connected: '[data-product-guide="onboarding-verified"]',
    pluginConfigure: '[data-product-guide="plugin-setup"]',
  }
  let target = active ? visible(selectors[step]) : null
  let message: string = step
  let action: string | null = null
  const listState = list?.dataset.guideState
  const isDashboardStep = dashboardSteps.includes(
    step as (typeof dashboardSteps)[number]
  )
  const nextDashboardStep = dashboardSteps
    .slice(dashboardSteps.indexOf(step as (typeof dashboardSteps)[number]) + 1)
    .find(
      (candidate) =>
        visible(selectors[candidate])?.dataset.guideState === "ready"
    )
  if (active && !target && step !== "traces" && !detail && !diagnosis) {
    message = "returnToFlow"
    action = "return"
  }
  if (step === "connect") action = "openAccess"
  if (
    step === "chooseAgent" &&
    !!visible('[data-product-guide="agent-market-tab"]') &&
    !visible('[data-product-guide="agent-market"]')
  ) {
    message = "openMarket"
    action = "openMarket"
  }
  if (step === "framework" && target?.dataset.guideState === "ready")
    action = "application"
  if (step === "application" && target?.dataset.guideState === "ready")
    action = "configure"
  if (step === "configure" && target) action = "verify"
  if (step === "verify" && verification?.dataset.guideState === "error")
    message = "verificationError"
  if (step === "verify" && verification?.dataset.guideState === "incomplete")
    message = "verificationIncomplete"
  if (step === "connected" && verification?.dataset.guideState === "verified")
    action = "viewDashboard"
  if (isDashboardStep && metrics?.dataset.guideState !== "ready") {
    action = null
    if (metrics?.dataset.guideState === "empty") {
      message = demo ? "demoWaitingForDashboard" : "noTraceYet"
      target = demo ? metrics : visible(selectors.connect)
      action = demo ? "retryDashboard" : "openAccess"
    } else if (metrics?.dataset.guideState === "error") {
      message = "queryError"
      target = metrics
      if (demo) action = "retryDashboard"
    } else {
      message = "waitingForData"
      target = visible(selectors.scope)
    }
  }
  if (isDashboardStep && metrics?.dataset.guideState === "ready") {
    if (
      !target ||
      (step !== "scope" && target.dataset.guideState !== "ready")
    ) {
      target = metrics
      message = "sectionUnavailable"
    }
    action = nextDashboardStep ? "next" : "viewTraces"
  }
  if (step === "traces" && listState === "empty") {
    message = demo ? "demoWaitingForTrace" : "empty"
    target = visible('[data-product-guide="time"]')
    if (!demo) action = "connect"
  }
  if (step === "traces" && listState === "error") {
    message = "queryError"
    target = list
  }
  if (step === "traces" && detail && detail.dataset.guideState !== "ready") {
    message = detail.dataset.guideState === "error" ? "detailError" : "loading"
    target = detail
  }
  if (step === "traces" && detail && !targetDetail) {
    message = "demoOtherTrace"
    target = detail
  }
  if ((step === "enhanced" || step === "enhancedCall") && demoInRawMode) {
    message = "enhancedMode"
    target = visible('[data-product-guide="trace-mode-enhanced"]')
  }
  if (step === "enhanced" && !demoInRawMode && mcpProcessCall) {
    message = "mcpProcess"
    target = mcpProcessCall
    action = "viewMcpCall"
  }
  if (
    ["enhanced", "enhancedCall", "call", "inspect", "check"].includes(step) &&
    !detail
  ) {
    message = "traces"
    target = visible(selectors.traces)
    if (target) action = null
  }
  if (
    (step === "inspect" || (step === "enhancedCall" && !demoInRawMode)) &&
    target
  ) {
    if (visible(selectors.check)) action = "check"
    else message = "checkUnavailable"
  }
  if (
    step === "enhancedCall" &&
    !demoInRawMode &&
    target &&
    demo &&
    !demo.mcpConfigSeen &&
    currentSpace?.role === 1
  )
    action = "importConfig"
  if (step === "mcpConfig" && mcpConfig) {
    message =
      mcpConfig.dataset.guideState === "empty"
        ? "mcpConfigEmpty"
        : mcpConfig.dataset.guideState === "error"
          ? "mcpConfigError"
          : "mcpConfigLoading"
    target = mcpConfig
    action = null
  }
  if (step === "mcpConfigReady" && mcpConfig) action = "backToCall"
  if (step === "mcpImport" && mcpImport) {
    if (mcpImport.dataset.guideMode === "search") {
      const configure = visible('[data-product-guide="mcp-registry-configure"]')
      const result = visible('[data-product-guide="mcp-registry-select"]')
      if (configure) {
        message = "mcpRegistryConfigure"
        target =
          visible('[data-product-guide="mcp-registry-preview"]') ?? configure
      } else if (result) {
        message = "mcpRegistrySelect"
        target = result
      } else {
        message = "mcpSearch"
        target =
          visible('[data-product-guide="mcp-registry-search"]') ?? mcpImport
      }
    } else
      target = visible('[data-product-guide="mcp-import-save"]') ?? mcpImport
  }
  if (step.startsWith("compare")) {
    action = null
    if (!comparisonReady) {
      message =
        comparison?.dataset.guideState === "error" ? "queryError" : "loading"
      target = comparison
    } else if (step === "compare") action = "compareCalls"
    else if (
      step === "compareCalls" &&
      visible(selectors.compareCalls)?.dataset.guideState === "ready"
    ) {
      message = "compareSelectCall"
      target = visible(
        `[data-product-guide="compare-failed-call"][data-guide-side="B"][data-guide-span="${demo?.evidenceSpanId}"]`
      )
    } else if (step === "compareEvidence" && comparisonEvidence) {
      target = comparisonEvidence
      action = "finish"
    }
  }
  if (step === "check" && detail && !target) {
    message = "checkUnavailable"
    target = detail
  }
  if (["chooseDiagnoser", "run"].includes(step) && !diagnosis) {
    message = "check"
    target = visible(selectors.check)
  }
  if (step === "run" && target?.dataset.guideState === "running")
    message = "running"
  if (
    ["chooseDiagnoser", "run"].includes(step) &&
    diagnosis?.dataset.guideState === "failed"
  )
    message = "checkFailed"
  if (
    ["chooseDiagnoser", "run"].includes(step) &&
    diagnosis?.dataset.guideState === "denied"
  ) {
    message = "checkUnavailable"
    target = diagnosis
  }

  if (step === "result" && target && ruleSucceeded) {
    if (demo && demo.evidenceSpanId) {
      message = "demoResult"
      target =
        visible(
          `[data-product-guide="diagnosis-results"] [data-source-span="${demo.evidenceSpanId}"]`
        ) ?? target
    } else if (!demo) action = "finish"
  }
  if (
    step === "run" &&
    demo &&
    ruleSucceeded &&
    !diagnosis?.dataset.guideEvidence
  )
    message = "demoEvidenceMissing"
  if (step === "evidence" && demoEvidence && target)
    action = demo?.comparisonTraceId ? "viewComparison" : "finish"

  const onAction = () => {
    if (action === "openAccess") {
      move("chooseAgent")
      navigate(guideRoute("/access", location.search))
    }
    if (action === "openMarket")
      visible('[data-product-guide="agent-market-tab"]')?.click()
    if (["application", "configure", "verify"].includes(action ?? ""))
      move(action as GuideStep)
    if (action === "viewDashboard" && setup && user && currentSpace) {
      const params = new URLSearchParams(location.search)
      const agentUuid = setup.dataset.guideAgent
      const product = setup.dataset.guideProduct
      if (!agentUuid || !product) return
      // A first Trace may have arrived while the initial zero-count dashboard
      // is still fresh in the cache. Recheck this application on return.
      void queryClient.invalidateQueries({
        queryKey: [
          "frameworkDashboardMetrics",
          currentSpace.id,
          product,
          agentUuid,
        ],
        refetchType: "none",
      })
      // The existing section adapter has a separate browser response cache.
      // Drop its pre-ingestion snapshots before mounting the dashboard again.
      clearResultCache()
      params.set("spaceId", currentSpace.id)
      params.set("agent", product)
      params.set("agent_uuid", agentUuid)
      const next: GuideProgress = { step: "overview", status: "active" }
      saveGuideProgress(
        guideStorageKey(user.user_uuid, currentSpace.id, agentUuid),
        next
      )
      update(next)
      navigate(guideRoute("/dashboard", params.toString()))
    }
    if (action === "return") void start()
    if (action === "retryDashboard" && currentSpace && currentAgent) {
      clearResultCache()
      void queryClient.invalidateQueries({
        queryKey: [
          "frameworkDashboardMetrics",
          currentSpace.id,
          currentAgent.agent_product,
          currentAgent.uuid,
        ],
      })
    }
    if (action === "next" && nextDashboardStep) move(nextDashboardStep)
    if (action === "viewTraces") {
      move("traces")
      navigate(guideRoute("/trace", location.search))
    }
    if (action === "connect") {
      move("connect")
      navigate(guideRoute("/access", location.search))
    }
    if (action === "check") move("check")
    if (action === "viewMcpCall") mcpProcessCall?.click()
    if (action === "importConfig") move("mcpConfig")
    if (action === "backToCall") {
      visible('[data-product-guide="mcp-config-close"]')?.click()
      move("check")
    }
    if (action === "viewComparison" && demo?.comparisonTraceId) {
      const params = new URLSearchParams(
        guideRoute("/trace/compare", location.search).split("?")[1]
      )
      params.set("a", demo.comparisonTraceId)
      params.set("b", demo.traceId)
      params.set("showMcp", "1")
      move("compare")
      navigate(`/trace/compare?${params}`)
    }
    if (action === "compareCalls") move("compareCalls")
    if (action === "finish") update({ ...progress, step, status: "completed" })
  }
  const parent =
    target?.closest<HTMLElement>('[role="dialog"]') ?? document.body
  const mobileSidebar = active
    ? visible('[data-sidebar="sidebar"][data-mobile="true"]')
    : null
  const sidebarCoversTarget =
    mobileSidebar && (!target || !mobileSidebar.contains(target))

  return (
    <ProductGuideContext.Provider
      value={{
        active,
        canBrowse,
        canStartDemo,
        isDemoAgent: isOnboardingDemoAgent(currentAgent),
        start: () => void start(),
        restart: () => void start(true),
        browse: () => void browse(),
        openDemo,
        openRuleEvidence,
        resumable: progress?.status === "paused",
      }}
    >
      {children}
      {offer && !demoOpen && (
        <aside
          className="product-guide-offer"
          aria-label={t("productGuide.title")}
        >
          <button
            type="button"
            className="product-guide-close"
            aria-label={t("common.close")}
            disabled={savingChoice}
            onClick={() => void skip()}
          >
            <X size={16} />
          </button>
          <p>
            {t(
              canStartDemo
                ? "productGuide.demo.welcome"
                : "productGuide.welcome"
            )}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            {canStartDemo && (
              <Button
                size="sm"
                ref={demoEntry}
                disabled={savingChoice}
                onClick={openDemo}
              >
                {t("productGuide.demo.entry")}
              </Button>
            )}
            <Button
              size="sm"
              variant={canStartDemo ? "outline" : "default"}
              disabled={savingChoice}
              onClick={() => void start()}
            >
              {t("productGuide.connectAction")}
              <ArrowRight size={14} />
            </Button>
          </div>
          <div className="mt-2 flex flex-wrap items-center justify-end gap-x-2">
            {canBrowse && (
              <Button
                size="sm"
                variant="ghost"
                disabled={savingChoice}
                onClick={() => void browse()}
              >
                {t("productGuide.browse")}
              </Button>
            )}
            <Button
              size="sm"
              variant="ghost"
              disabled={savingChoice}
              onClick={() => void skip()}
            >
              {t("productGuide.skip")}
            </Button>
          </div>
        </aside>
      )}
      {demoOpen && currentSpace && (
        <DemoConsentDialog
          key={identityScope}
          spaceId={currentSpace.id}
          hasDemoAgent={agents.some(isOnboardingDemoAgent)}
          onClose={closeDemo}
          onReady={enterDemo}
        />
      )}
      {active && !demoOpen && !sidebarCoversTarget && (
        <Spotlight
          key={`${step}:${parent.dataset.slot ?? "page"}`}
          target={target}
          parent={parent}
          revision={revision}
          message={t(`productGuide.steps.${message}`, {
            action: t(
              message === "check" ? "diagnose.quick.open" : "diagnose.quick.run"
            ),
            verifyAction: t("frameworkOnboarding.verify"),
            sourceAction: t("diagnose.quick.sourceStep"),
            enhancedAction: t("traceView.enhanced"),
          })}
          title={t("productGuide.title")}
          phase={
            demo
              ? t(
                  `productGuide.phases.${step.startsWith("compare") ? "compare" : ["enhanced", "enhancedCall", "mcpConfig", "mcpImport", "mcpConfigReady"].includes(step) ? "mcp" : ["check", "chooseDiagnoser", "run", "result", "evidence"].includes(step) ? "diagnosis" : "dashboard"}`
                )
              : undefined
          }
          pauseLabel={t("productGuide.pause")}
          skipLabel={t("productGuide.skip")}
          skipDisabled={savingChoice}
          closeLabel={t("common.close")}
          onPause={pause}
          onSkip={() => void skip()}
          actionLabel={action ? t(`productGuide.actions.${action}`) : null}
          onAction={onAction}
          secondaryActionLabel={
            (action === "importConfig" ||
              (step === "mcpConfig" && !mcpImport)) &&
            visible(selectors.check)
              ? t("productGuide.actions.continueToDiagnosis")
              : undefined
          }
          onSecondaryAction={() => {
            visible('[data-product-guide="mcp-config-close"]')?.click()
            move("check")
          }}
        />
      )}
    </ProductGuideContext.Provider>
  )
}

function visibleTargetRect(target: HTMLElement) {
  const bounds = target.getBoundingClientRect()
  let { left, right, top, bottom } = bounds
  for (
    let ancestor = target.parentElement;
    ancestor;
    ancestor = ancestor.parentElement
  ) {
    const style = getComputedStyle(ancestor)
    if (style.overflowX !== "visible" || style.overflowY !== "visible") {
      const clip = ancestor.getBoundingClientRect()
      if (style.overflowX !== "visible") {
        left = Math.max(left, clip.left)
        right = Math.min(right, clip.right)
      }
      if (style.overflowY !== "visible") {
        top = Math.max(top, clip.top)
        bottom = Math.min(bottom, clip.bottom)
      }
    }
    if (style.position === "fixed") break
  }
  return new DOMRect(
    left,
    top,
    Math.max(0, right - left),
    Math.max(0, bottom - top)
  )
}

function Spotlight({
  target,
  parent,
  revision,
  message,
  title,
  phase,
  pauseLabel,
  skipLabel,
  skipDisabled,
  closeLabel,
  onPause,
  onSkip,
  actionLabel,
  onAction,
  secondaryActionLabel,
  onSecondaryAction,
}: {
  target: HTMLElement | null
  parent: HTMLElement
  revision: number
  message: string
  title: string
  phase?: string
  pauseLabel: string
  skipLabel: string
  skipDisabled: boolean
  closeLabel: string
  onPause: () => void
  onSkip: () => void
  actionLabel: string | null
  onAction: () => void
  secondaryActionLabel?: string
  onSecondaryAction: () => void
}) {
  const [rect, setRect] = useState<DOMRect | null>(null)
  const card = useRef<HTMLDivElement>(null)
  const layer = useRef<HTMLDivElement>(null)
  const importDialog =
    parent.dataset.productGuide === "mcp-import" ? parent : null
  useEffect(() => {
    card.current?.focus({ preventScroll: true })
  }, [message, parent])
  useLayoutEffect(() => {
    const element = layer.current
    // Native top-layer rendering escapes transformed/clipped Radix dialogs while
    // the portal remains inside the dialog DOM and its keyboard focus scope.
    // Changing drawers can replace the portal DOM without changing the step.
    if (typeof element?.showPopover === "function") element.showPopover()
    return () => {
      if (element?.isConnected && typeof element.hidePopover === "function")
        element.hidePopover()
    }
  }, [parent])
  useLayoutEffect(() => {
    // DOM geometry must be synchronized before paint to avoid a misplaced spotlight.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setRect(target ? visibleTargetRect(target) : null)
    if (!target) return
    // A drawer's transform animates its position without resizing its content.
    // Follow only active ancestor motion so the highlight settles on the target.
    let frame = 0
    const measure = () => {
      const next = visibleTargetRect(target)
      setRect((current) =>
        current &&
        current.x === next.x &&
        current.y === next.y &&
        current.width === next.width &&
        current.height === next.height
          ? current
          : next
      )
      let element: HTMLElement | null = target
      while (element) {
        if (
          element
            .getAnimations?.()
            .some(
              (animation) =>
                animation.playState === "running" || animation.pending
            )
        ) {
          frame = requestAnimationFrame(measure)
          return
        }
        element = element.parentElement
      }
    }
    const refresh = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(measure)
    }
    const motion = (event: Event) => {
      if (event.target instanceof Element && event.target.contains(target))
        refresh()
    }
    const resize = new ResizeObserver(refresh)
    resize.observe(target)
    if (importDialog && importDialog !== target) resize.observe(importDialog)
    // Sidebar width animates on a sibling. The content inset resizes even
    // when a target only moves or its row wraps at a container breakpoint.
    const inset = target.closest('[data-slot="sidebar-inset"]')
    if (inset) resize.observe(inset)
    refresh()
    document.addEventListener("animationstart", motion, true)
    document.addEventListener("transitionrun", motion, true)
    return () => {
      resize.disconnect()
      cancelAnimationFrame(frame)
      document.removeEventListener("animationstart", motion, true)
      document.removeEventListener("transitionrun", motion, true)
    }
  }, [importDialog, target, revision])
  useEffect(() => {
    if (!target) return
    const box = target.getBoundingClientRect()
    if (
      box.bottom > innerHeight ||
      box.top < 0 ||
      visibleTargetRect(target).height < box.height - 1 ||
      target.dataset.productGuide?.startsWith("mcp-")
    )
      target.scrollIntoView({ block: "center", behavior: "instant" })
  }, [target])
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (
        event.key !== "Escape" ||
        (event.target as HTMLElement)?.closest(
          '[role="listbox"], [role="combobox"]'
        )
      )
        return
      event.preventDefault()
      event.stopPropagation()
      onPause()
    }
    document.addEventListener("keydown", escape, true)
    return () => document.removeEventListener("keydown", escape, true)
  }, [onPause])

  const width = Math.min(344, innerWidth - 24)
  const [height, setHeight] = useState(155)
  useLayoutEffect(() => {
    if (!card.current) return
    const resize = new ResizeObserver(() =>
      setHeight(card.current?.getBoundingClientRect().height ?? 155)
    )
    resize.observe(card.current)
    return () => resize.disconnect()
  }, [parent])
  const focus =
    rect && rect.width > 0 && rect.height > 0
      ? {
          left: Math.max(4, rect.left - 5),
          top: Math.max(4, rect.top - 5),
          right: Math.min(innerWidth - 4, rect.right + 5),
          bottom: Math.min(innerHeight - 4, rect.bottom + 5),
        }
      : null
  const bottom = Math.max(12, innerHeight - height - 16)
  let left = Math.max(12, innerWidth - width - 16)
  let top = bottom
  if (focus && focus.right > focus.left && focus.bottom > focus.top) {
    // Highlight the current control, but leave the import dialog's other
    // controls reachable by placing the card outside it whenever there is room.
    const dialogBounds = importDialog && visibleTargetRect(importDialog)
    const placement =
      dialogBounds &&
      (innerHeight - dialogBounds.bottom >= height + 22 ||
        dialogBounds.top >= height + 22 ||
        dialogBounds.left >= width + 24 ||
        innerWidth - dialogBounds.right >= width + 24)
        ? dialogBounds
        : focus
    // A tall import dialog may fill the viewport. In that case prefer the
    // space above the highlight, keeping its footer buttons reachable.
    const preferAbove = !!importDialog && placement === focus
    const fitsAbove = placement.top >= height + 22
    left = Math.max(
      12,
      Math.min(innerWidth - width - 12, placement.right - width)
    )
    if (
      innerHeight - placement.bottom >= height + 22 &&
      (!preferAbove || !fitsAbove)
    ) {
      top = placement.bottom + 12
    } else if (fitsAbove) {
      top = placement.top - height - 12
    } else if (placement.left >= width + 24) {
      left = placement.left - width - 12
      top = Math.max(12, Math.min(bottom, placement.top))
    } else if (innerWidth - placement.right >= width + 24) {
      left = placement.right + 12
      top = Math.max(12, Math.min(bottom, placement.top))
    } else {
      const overlap = (y: number) =>
        Math.max(0, Math.min(y + height, focus.bottom) - Math.max(y, focus.top))
      if (overlap(12) < overlap(bottom)) top = 12
    }
    // Keep the trace mode switch and the expanded call's status row visible.
    const callRow = target?.matches(
      '[data-product-guide="compare-call-evidence"]'
    )
      ? target.closest("tr")?.previousElementSibling
      : null
    const modeSwitch = target
      ?.closest('[data-testid="native-trace-detail"]')
      ?.querySelector<HTMLElement>('[data-testid="trace-content-modes"]')
    const importFooter = importDialog?.querySelector<HTMLElement>(
      '[data-slot="dialog-footer"]'
    )
    const adjacent =
      callRow instanceof HTMLElement &&
      callRow.matches('[data-product-guide="compare-failed-call"]')
        ? callRow
        : (importFooter ?? modeSwitch)
    const controls = adjacent && visibleTargetRect(adjacent)
    if (
      controls &&
      controls.width > 0 &&
      controls.height > 0 &&
      controls.left < left + width &&
      controls.right > left &&
      controls.top < top + height &&
      controls.bottom > top
    ) {
      const above = controls.top - height - 12
      if (above >= 12 && (importFooter || above + height + 12 <= focus.top))
        top = above
    }
  }
  return createPortal(
    <div
      ref={layer}
      popover="manual"
      className="product-guide-layer"
      data-product-guide-overlay
      onPointerDown={(event) => event.stopPropagation()}
    >
      {focus && focus.right > focus.left && focus.bottom > focus.top && (
        <>
          <div
            className="product-guide-ring"
            style={{
              left: focus.left,
              top: focus.top,
              width: focus.right - focus.left,
              height: focus.bottom - focus.top,
            }}
          />
        </>
      )}
      <div
        ref={card}
        tabIndex={-1}
        role="region"
        aria-label={title}
        aria-live="polite"
        className="product-guide-card"
        style={{ left, top: Math.max(12, top), width }}
      >
        <button
          type="button"
          className="product-guide-close"
          aria-label={closeLabel}
          onClick={onPause}
        >
          <X size={16} />
        </button>
        {phase && (
          <div className="mb-2 pr-6 text-xs font-medium text-muted-foreground">
            {phase}
          </div>
        )}
        <p>{message}</p>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Button
            size="sm"
            variant="ghost"
            disabled={skipDisabled}
            onClick={onSkip}
          >
            {skipLabel}
          </Button>
          <Button size="sm" variant="ghost" onClick={onPause}>
            {pauseLabel}
          </Button>
          {secondaryActionLabel && (
            <Button size="sm" variant="outline" onClick={onSecondaryAction}>
              {secondaryActionLabel}
            </Button>
          )}
          {actionLabel && (
            <Button size="sm" onClick={onAction}>
              {actionLabel}
              <ArrowRight size={14} />
            </Button>
          )}
        </div>
      </div>
    </div>,
    parent
  )
}
