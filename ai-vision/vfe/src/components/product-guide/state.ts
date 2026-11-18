export const connectionSteps = [
  "connect",
  "chooseAgent",
  "framework",
  "application",
  "configure",
  "verify",
  "connected",
  "pluginConfigure",
] as const

export const dashboardSteps = [
  "scope",
  "overview",
  "usage",
  "trend",
  "operations",
] as const

export const guideSteps = [
  ...connectionSteps,
  ...dashboardSteps,
  "traces",
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
  "compare",
  "compareCalls",
  "compareEvidence",
] as const
export type GuideStep = (typeof guideSteps)[number]
export interface GuideProgress {
  step: GuideStep
  status: "active" | "paused" | "dismissed" | "completed"
  demo?: {
    traceId: string
    ruleUuid: string
    comparisonTraceId?: string
    mcpConfigSeen?: boolean
    evidenceSpanId?: string
  }
}

function guideUserStoragePrefix(user: string) {
  return `ai-vision:product-guide:v3:${encodeURIComponent(user)}:`
}

export function guideStorageKey(user: string, space: string, agent: string) {
  return `${guideUserStoragePrefix(user)}${encodeURIComponent(space)}:${encodeURIComponent(agent)}`
}

export function readGuideProgress(key: string): GuideProgress | null {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "null")
    if (
      !value ||
      !guideSteps.includes(value.step) ||
      !["active", "paused", "dismissed", "completed"].includes(value.status)
    )
      return null
    if (
      value.demo &&
      (!/^[0-9a-f]{32}$/.test(value.demo.traceId) ||
        typeof value.demo.ruleUuid !== "string" ||
        !value.demo.ruleUuid ||
        (value.demo.comparisonTraceId !== undefined &&
          (!/^[0-9a-f]{32}$/.test(value.demo.comparisonTraceId) ||
            value.demo.comparisonTraceId === value.demo.traceId)) ||
        (value.demo.evidenceSpanId !== undefined &&
          !/^[0-9a-f]{16}$/.test(value.demo.evidenceSpanId)))
    )
      return null
    return value
  } catch {
    return null
  }
}

export function saveGuideProgress(key: string, value: GuideProgress) {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // The walkthrough remains usable without persistent browser storage.
  }
}

export function dismissActiveGuideProgress(user: string) {
  const prefix = guideUserStoragePrefix(user)
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i)
      if (!key?.startsWith(prefix)) continue
      const progress = readGuideProgress(key)
      if (progress?.status === "active") {
        saveGuideProgress(key, { step: progress.step, status: "dismissed" })
      }
    }
  } catch {
    // The current guide can still be dismissed in memory.
  }
}

/** Preserve the selected identity and time window, never another page's detail IDs. */
export function guideRoute(path: string, search: string) {
  const current = new URLSearchParams(search)
  const next = new URLSearchParams()
  for (const key of [
    "spaceId",
    "agent",
    "agent_uuid",
    "range",
    "from",
    "to",
    "source_system",
  ]) {
    const value = current.get(key)
    if (value) next.set(key, value)
  }
  return `${path}?${next}`
}
