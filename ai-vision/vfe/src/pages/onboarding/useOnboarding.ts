import { writeClipboardText } from "@/lib/clipboard"
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import {
  useIsMutating,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"

import { useAgent } from "@/contexts/AgentContext"
import { useSpace } from "@/contexts/SpaceContext"
import { api, ApiBusinessError, throwOnApiError } from "@/lib/api"
import { fetchNativeTraceDetail, nativeApi } from "@/components/traces-otel/api"

import {
  buildTraceVerificationReport,
  expectedSpanCount,
  type TraceVerificationReport,
} from "./traceVerificationReport"

const SPACE_KEY_QUERY_KEY = "space-keys"

interface OnboardingProject {
  uuid: string
  name: string
  customName: string | null
  metadata: Record<string, unknown> | null
  status: number | null
}

interface SettingsResponse {
  code: number
  data?: {
    permissions: string[]
  }
  message?: string
}

interface SpaceKeysResponse {
  code: number
  message?: string
  data: {
    items: {
      id: number
      key_prefix: string
      name: string
      enabled: boolean
    }[]
  }
}

interface SpaceKeyCreateResponse {
  code: number
  data?: { id: number; key: string; key_prefix: string; name: string }
  message?: string
}

interface CreateProjectResponse {
  code: number
  message?: string
  data?: { agent_uuid?: string | number }
}

type UseOnboardingCredentialsOptions = {
  name: string
  requiredPermission?: string
  onError?: () => void
}

type UseOnboardingProjectsOptions = {
  product: string
  queryKeyPrefix: string
  initialProjectId?: string | null
}

type UseOnboardingDetectionOptions = {
  product: string
  queryKeyPrefix: string
  projectUuid: string | null
}

export function useOnboardingCopy() {
  const [copiedLabel, setCopiedLabel] = useState<string | null>(null)
  const [copyErrorLabel, setCopyErrorLabel] = useState<string | null>(null)
  const copiedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(
    () => () => {
      if (copiedTimerRef.current) clearTimeout(copiedTimerRef.current)
    },
    []
  )

  const copy = useCallback(async (label: string, value: string) => {
    setCopiedLabel(null)
    setCopyErrorLabel(null)
    if (copiedTimerRef.current) clearTimeout(copiedTimerRef.current)
    try {
      await writeClipboardText(value)
      setCopiedLabel(label)
      copiedTimerRef.current = window.setTimeout(() => {
        setCopiedLabel(null)
      }, 1600)
    } catch {
      setCopyErrorLabel(label)
    }
  }, [])

  return {
    copiedLabel,
    copyErrorLabel,
    copy,
  }
}

export function useOnboardingCredentials({
  name,
  requiredPermission,
  onError,
}: UseOnboardingCredentialsOptions) {
  const { currentSpace } = useSpace()
  const spaceId = currentSpace?.id ?? ""
  const queryClient = useQueryClient()
  const [spaceApiKey, setSpaceApiKey] = useState<string | null>(null)
  const [credentialError, setCredentialError] = useState<string | null>(null)
  const previousSpaceIdRef = useRef(spaceId)

  useEffect(() => {
    if (previousSpaceIdRef.current === spaceId) return
    previousSpaceIdRef.current = spaceId
    setSpaceApiKey(null)
    setCredentialError(null)
  }, [spaceId])

  const settingsQuery = useQuery({
    queryKey: ["onboarding-settings", spaceId],
    queryFn: () =>
      api
        .get<SettingsResponse>(`/spaces/${spaceId}/settings`)
        .then(throwOnApiError),
    enabled: !!spaceId && !!requiredPermission,
    staleTime: 30_000,
  })

  const spaceKeysQuery = useQuery({
    queryKey: [SPACE_KEY_QUERY_KEY, spaceId],
    queryFn: () =>
      api
        .get<SpaceKeysResponse>(`/spaces/${spaceId}/space-keys`)
        .then(throwOnApiError),
    enabled: !!spaceId,
    staleTime: 30_000,
  })

  const existingSpaceKey =
    spaceKeysQuery.data?.data.items.find((item) => item.enabled) ??
    spaceKeysQuery.data?.data.items[0] ??
    null
  const canCreateApiKey = requiredPermission
    ? settingsQuery.data?.data?.permissions.includes(requiredPermission)
    : true
  const selectedSecretKey =
    spaceApiKey ??
    (existingSpaceKey ? `${existingSpaceKey.key_prefix}••••••••` : "sk-aiv-...")

  const createApiKey = useMutation({
    mutationFn: async () => {
      const response = await api.post<SpaceKeyCreateResponse>(
        `/spaces/${spaceId}/space-keys`,
        { name }
      )
      return throwOnApiError(response)
    },
    onSuccess: (response) => {
      setCredentialError(null)
      setSpaceApiKey(response.data?.key ?? null)
      void queryClient.invalidateQueries({
        queryKey: [SPACE_KEY_QUERY_KEY, spaceId],
      })
    },
    onError: () => {
      setCredentialError(null)
      onError?.()
    },
  })

  return {
    spaceId,
    createApiKey,
    canCreateApiKey: canCreateApiKey ?? false,
    credentialError,
    existingSpaceKey,
    selectedSecretKey,
    spaceApiKey,
  }
}

export function useProjectOnboardingCredential(
  projectUuid: string | null,
  oneTimeKeyName?: string,
  isPendingProject = false
) {
  const { currentSpace } = useSpace()
  const spaceId = currentSpace?.id ?? ""
  const canReveal = Boolean(
    projectUuid && currentSpace?.permissions.includes("agent.ingest_token.mint")
  )
  const queryClient = useQueryClient()
  const scope = useMemo(
    () => ({ spaceId, projectUuid, canReveal, oneTimeKeyName }),
    [spaceId, projectUuid, canReveal, oneTimeKeyName]
  )
  const activeScope = useRef<{
    scope: typeof scope
    isPendingProject: boolean
  } | null>(null)
  const requestVersion = useRef(0)
  const oneTimeKeyRequests = useRef(new Set<string>())
  const [revealed, setRevealed] = useState<{
    scope: typeof scope
    key: string
    oneTime: boolean
  } | null>(null)
  if (revealed && revealed.scope !== scope) setRevealed(null)
  const key = revealed?.scope === scope ? revealed.key : null

  useLayoutEffect(() => {
    activeScope.current = { scope, isPendingProject }
    return () => {
      activeScope.current = null
    }
  }, [scope, isPendingProject])

  const reveal = useMutation({
    mutationFn: async ({
      target,
      version,
    }: {
      target: typeof scope
      version: number
    }) => {
      if (!target.projectUuid || !target.canReveal) return
      const stillCurrent = () =>
        activeScope.current?.scope === target &&
        requestVersion.current === version
      let rawKey: string | undefined
      let oneTime = false
      try {
        const response = throwOnApiError(
          await api.post<{ code: number; data?: { key: string } }>(
            `/spaces/${target.spaceId}/agents/${target.projectUuid}/onboarding-credential`,
            {}
          )
        )
        rawKey = response.data?.key
      } catch (error) {
        if (
          !target.oneTimeKeyName ||
          !(error instanceof ApiBusinessError) ||
          error.statusCode !== 503 ||
          error.code !== 5032
        ) {
          throw error
        }
        if (!stillCurrent()) return
        if (!activeScope.current?.isPendingProject) throw error
        const project = `${target.spaceId}:${target.projectUuid}`
        if (oneTimeKeyRequests.current.has(project)) throw error
        // Count the request even if its outcome is unknown to this page.
        oneTimeKeyRequests.current.add(project)
        const response = throwOnApiError(
          await api.post<SpaceKeyCreateResponse>(
            `/spaces/${target.spaceId}/space-keys`,
            { name: target.oneTimeKeyName }
          )
        )
        rawKey = response.data?.key
        oneTime = true
      }
      if (!rawKey) throw new Error("Missing setup credential")
      // Keep plaintext out of the mutation cache and ignore an obsolete response.
      if (stillCurrent()) {
        setRevealed({ scope: target, key: rawKey, oneTime })
      }
      void queryClient.invalidateQueries({
        queryKey: [SPACE_KEY_QUERY_KEY, target.spaceId],
      })
    },
  })
  const show = () => {
    if (canReveal)
      reveal.mutate({ target: scope, version: ++requestVersion.current })
  }
  const hide = () => {
    requestVersion.current++
    setRevealed(null)
    reveal.reset()
  }

  return {
    key,
    canReveal,
    isOneTimeKey: revealed?.scope === scope && revealed.oneTime,
    isPending: reveal.variables?.target === scope && reveal.isPending,
    isError: reveal.variables?.target === scope && reveal.isError,
    show,
    hide,
  }
}

export function useOnboardingProjects({
  product,
  queryKeyPrefix,
  initialProjectId = null,
}: UseOnboardingProjectsOptions) {
  const { currentSpace } = useSpace()
  const spaceId = currentSpace?.id ?? ""
  const { agents, agentTypes, loading, refreshAgents } = useAgent()
  const queryClient = useQueryClient()
  const [applicationName, setApplicationName] = useState("")
  const [chosenProjectId, setSelectedProjectId] = useState<string | null>(null)
  const selectedProjectId = initialProjectId ?? chosenProjectId
  const previousSpaceIdRef = useRef(spaceId)

  useEffect(() => {
    if (previousSpaceIdRef.current === spaceId) return
    previousSpaceIdRef.current = spaceId
    setSelectedProjectId(null)
  }, [spaceId])

  const projects: OnboardingProject[] = agents
    .filter(
      (agent) => agent.agent_product === product || agent.agent_name === product
    )
    .map((agent) => ({
      uuid: String(agent.uuid),
      name: String(agent.name ?? agent.agent_name),
      customName: agent.custom_name ? String(agent.custom_name) : null,
      metadata: agent.metadata ?? null,
      status: typeof agent.status === "number" ? agent.status : null,
    }))

  const selectedProject =
    projects.find((project) => project.uuid === selectedProjectId) ?? null

  const createApplication = useMutation({
    mutationFn: async (metadata?: { framework: string }) => {
      const response = await api.post<CreateProjectResponse>(
        `/spaces/${spaceId}/agents/create`,
        {
          agent_product: product,
          name: applicationName.trim(),
          ...(metadata ? { metadata } : {}),
        }
      )
      return throwOnApiError(response)
    },
    onSuccess: async (response) => {
      const createdProjectId = response.data?.agent_uuid
      setSelectedProjectId(createdProjectId ? String(createdProjectId) : null)
      setApplicationName("")
      await Promise.allSettled([
        refreshAgents({ silent: true }),
        queryClient.invalidateQueries({
          queryKey: [queryKeyPrefix, "trace-count", spaceId],
        }),
      ])
    },
  })

  return {
    agentTypes,
    spaceId,
    loading,
    applicationName,
    createApplication,
    projects,
    selectedProject,
    selectedProjectUuid: selectedProject?.uuid ?? null,
    setApplicationName,
    setSelectedProjectId,
  }
}

export function useOnboardingDetection({
  product,
  queryKeyPrefix,
  projectUuid,
}: UseOnboardingDetectionOptions) {
  const { currentSpace } = useSpace()
  const spaceId = currentSpace?.id ?? ""
  const [detectionStartedAt] = useState(() => Date.now())
  const [now, setNow] = useState(() => Date.now())
  const [manualRefreshing, setManualRefreshing] = useState(false)

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 10_000)
    return () => window.clearInterval(timer)
  }, [])

  const traceQuery = useQuery({
    queryKey: [queryKeyPrefix, "trace-count", spaceId, projectUuid],
    queryFn: () =>
      nativeApi
        .post<{
          code: number
          message?: string
          data: {
            meta: {
              totalItems: number
            }
          }
        }>(`/spaces/${spaceId}/signoz/traces/list`, {
          page: 1,
          limit: 1,
          agent_product: product,
          agent_uuid: projectUuid,
          from: new Date(detectionStartedAt - 30 * 60 * 1000).toISOString(),
        })
        .then(throwOnApiError),
    enabled: Boolean(spaceId && projectUuid),
    refetchInterval: (query) =>
      query.state.data?.data.meta.totalItems ? false : 5_000,
  })

  const refresh = useCallback(async () => {
    if (!spaceId || !projectUuid) return
    setManualRefreshing(true)
    try {
      await traceQuery.refetch()
    } finally {
      setManualRefreshing(false)
    }
  }, [projectUuid, spaceId, traceQuery])

  const traceCount = traceQuery.data?.data.meta.totalItems ?? 0

  return {
    hasTraces: traceCount > 0,
    traceCount,
    isListeningTimeout:
      traceCount === 0 && now - detectionStartedAt > 5 * 60 * 1000,
    isRefreshing: manualRefreshing,
    isError: traceQuery.isError,
    refresh,
  }
}

// Connection evidence belongs to a space, project and exact request. Merely
// choosing a framework or finding an older trace must never mark a project.
export function useOnboardingTraceVerification({
  product,
  projectUuid,
  frameworkUpdate,
}: {
  product: string
  projectUuid: string | null
  frameworkUpdate?: string | null
}) {
  const { currentSpace } = useSpace()
  const { refreshAgents } = useAgent()
  const queryClient = useQueryClient()
  const [traceId, setTraceId] = useState("")
  const [expectedCount, setExpectedCount] = useState("")
  const [attempt, setAttempt] = useState<{
    key: string
    status: "pending" | "verified" | "error"
    report?: TraceVerificationReport
    markError?: boolean
  } | null>(null)
  const spaceId = currentSpace?.id ?? ""
  // An issued write belongs to the project even when the user changes a
  // framework, edits the Trace ID or reopens the page while it is in flight.
  const mutationKey = ["onboarding-mark-connected", spaceId, projectUuid]
  const saveFilter = { mutationKey, exact: true }
  const saving = useIsMutating(saveFilter) > 0
  const { mutateAsync: markConnected } = useMutation({
    mutationKey,
    mutationFn: async ({
      spaceId,
      projectUuid,
      framework,
    }: {
      spaceId: string
      projectUuid: string
      framework?: string | null
    }) => {
      const path = `/spaces/${spaceId}/agents/${projectUuid}/mark-connected`
      const body = framework === undefined ? {} : { metadata: { framework } }
      const response = await api.post<{ code: number; message?: string }>(
        path,
        body
      )
      throwOnApiError(response)
    },
  })
  const normalizedTraceId = traceId.trim().toLowerCase()
  const key = JSON.stringify({
    spaceId,
    product,
    projectUuid,
    normalizedTraceId,
    expectedCount: expectedCount.trim(),
    frameworkUpdate,
  })
  if (attempt && attempt.key !== key) setAttempt(null)
  const activeKey = useRef(key)
  const sequence = useRef(0)
  useEffect(() => {
    activeKey.current = key
    sequence.current += 1
    return () => {
      activeKey.current = ""
    }
  }, [key])
  const valid = Boolean(
    spaceId &&
    projectUuid &&
    /^[a-f0-9]{32}$/.test(normalizedTraceId) &&
    (!expectedCount.trim() || expectedSpanCount(expectedCount) !== null)
  )
  const verify = async () => {
    if (!valid || !projectUuid || queryClient.isMutating(saveFilter)) return
    const requestSequence = ++sequence.current
    const stillCurrent = () =>
      activeKey.current === key && sequence.current === requestSequence
    setAttempt({ key, status: "pending" })
    try {
      const response = await fetchNativeTraceDetail(
        spaceId,
        normalizedTraceId,
        {
          agentProduct: product,
          agentUuid: projectUuid,
          detail: "1",
        }
      )
      if (
        response.code !== 0 ||
        response.data?.traceId !== normalizedTraceId ||
        !response.data.spans?.length ||
        response.data.spans.some(
          (span) =>
            span.trace_id !== normalizedTraceId ||
            !/^[a-f0-9]{16}$/i.test(span.span_id) ||
            /^0+$/.test(span.span_id)
        )
      ) {
        throw new Error("Trace not readable in the selected project")
      }
      if (!stillCurrent()) return
      if (queryClient.isMutating(saveFilter)) {
        setAttempt(null)
        return
      }
      const report = buildTraceVerificationReport(
        response.data,
        expectedSpanCount(expectedCount)
      )
      try {
        await markConnected({
          spaceId,
          projectUuid,
          framework: frameworkUpdate,
        })
      } catch {
        if (stillCurrent())
          setAttempt({ key, status: "verified", report, markError: true })
        return
      }
      if (stillCurrent()) setAttempt({ key, status: "verified", report })
      void refreshAgents({ silent: true })
    } catch {
      if (stillCurrent()) setAttempt({ key, status: "error" })
    }
  }
  return {
    traceId,
    setTraceId,
    expectedCount,
    setExpectedCount,
    expectedCountInvalid: Boolean(
      expectedCount.trim() && expectedSpanCount(expectedCount) === null
    ),
    report: attempt?.key === key ? (attempt.report ?? null) : null,
    markError: attempt?.key === key && attempt.markError === true,
    valid,
    verify,
    verified: attempt?.key === key && attempt.status === "verified",
    verifying: saving || (attempt?.key === key && attempt.status === "pending"),
    error: attempt?.key === key && attempt.status === "error",
  }
}
