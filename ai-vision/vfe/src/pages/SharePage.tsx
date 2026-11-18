import { useMemo, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import { ChevronLeft } from "lucide-react"

import { api } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { TraceDetailPanel } from "@/components/traces-lf"
import { SessionDetailPanel } from "@/components/traces-lf"
import { NativeTraceDetail } from "@/components/traces-otel/NativeTraceDetail"
import { NativeSessionDetailPanel } from "@/components/traces-otel/NativeSessionDetailPanel"
import type { NativeSessionDetailPanelProps } from "@/components/traces-otel/NativeSessionDetailPanel"

import { TraceLinkShell } from "@/components/traces/shared/TraceLinkShell"
import { ShareBanner } from "@/components/traces/ShareBanner"
import type {
  LangfuseTraceDetail,
  LangfuseSessionDetail,
} from "@/agents/types/langfuse"
import type { NativeTraceDetailResponse } from "@/components/traces-otel/types"

type ApiResponse<T> =
  | { code: 0; message?: string; data: T }
  | { code: number; message?: string; data?: unknown }

interface ShareInfo {
  sharer_name: string
  created_at: string
  expires_at: string
}

type ShareDataResponse = {
  resource_type: "trace" | "session"
  trace?: LangfuseTraceDetail
  session?: LangfuseSessionDetail
  space_id: string
  agent_product: string
  share_info: ShareInfo
  protocol?: string
  simulated_usage?: boolean
}

function isSuccess<T>(
  response: ApiResponse<T> | undefined
): response is { code: 0; message?: string; data: T } {
  return !!response && response.code === 0
}

function ShareLoading() {
  return (
    <TraceLinkShell>
      <div className="flex h-full flex-col">
        <div className="flex shrink-0 items-center gap-2 border-b bg-muted/50 px-4 py-2">
          <Skeleton className="h-4 w-64" />
        </div>
        <div className="flex shrink-0 items-center justify-between border-b p-4">
          <div className="min-w-0 flex-1 space-y-2">
            <Skeleton className="h-7 w-56 max-w-[70%]" />
            <Skeleton className="h-4 w-48 max-w-[60%]" />
          </div>
        </div>
        <div className="flex flex-1 overflow-hidden">
          <div className="w-80 shrink-0 border-r p-3">
            {Array.from({ length: 6 }).map((_, index) => (
              <Skeleton key={index} className="mb-3 h-12 w-full rounded-lg" />
            ))}
          </div>
          <div className="flex-1 p-4">
            <Skeleton className="h-64 w-full" />
          </div>
        </div>
      </div>
    </TraceLinkShell>
  )
}

function ShareMessage({
  title,
  message,
  onClose,
}: {
  title: string
  message: string
  onClose?: () => void
}) {
  const { t } = useTranslation()
  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-6">
      <div className="w-full max-w-xl rounded-lg border bg-card p-6 text-center shadow-sm">
        <h1 className="text-lg font-semibold text-foreground">{title}</h1>
        <p className="mt-2 text-sm text-muted-foreground">{message}</p>
        {onClose ? (
          <div className="mt-4">
            <Button variant="outline" onClick={onClose}>
              {t("common.close")}
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  )
}

export default function SharePage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const { t } = useTranslation()
  const [drilldownTraceId, setDrilldownTraceId] = useState<string | null>(null)
  const [isFullscreen, setIsFullscreen] = useState(false)

  const payload = searchParams.get("payload") || ""
  const sig = searchParams.get("sig") || ""

  const queryString = useMemo(() => {
    const params = new URLSearchParams()
    if (payload) params.set("payload", payload)
    if (sig) params.set("sig", sig)
    return params.toString()
  }, [payload, sig])

  const { data, isLoading } = useQuery({
    queryKey: ["shareData", payload, sig],
    queryFn: () =>
      api.get<ApiResponse<ShareDataResponse>>(`/share/data?${queryString}`, {
        skipAuthRedirect: true,
      }),
    enabled: !!payload && !!sig,
    retry: false,
  })

  const detail = isSuccess<ShareDataResponse>(data) ? data.data : null

  const { data: drilldownTraceData, isLoading: isDrilldownLoading } = useQuery({
    queryKey: ["shareTraceDetail", drilldownTraceId, payload, sig],
    queryFn: () =>
      api.get<ApiResponse<LangfuseTraceDetail | NativeTraceDetailResponse>>(
        `/share/trace/${drilldownTraceId}?${queryString}`,
        { skipAuthRedirect: true }
      ),
    enabled: !!drilldownTraceId && !!payload && !!sig,
    retry: false,
  })

  const drilldownTrace = isSuccess<
    LangfuseTraceDetail | NativeTraceDetailResponse
  >(drilldownTraceData)
    ? drilldownTraceData.data
    : null

  const handleClose = () => {
    if (window.history.length > 1) {
      navigate(-1)
      return
    }
    window.close()
  }

  const handleBackToSession = () => {
    setDrilldownTraceId(null)
  }

  if (!payload || !sig) {
    return (
      <ShareMessage
        title={t("share.invalidLink")}
        message={t("share.missingParams")}
      />
    )
  }

  if (isLoading) {
    return <ShareLoading />
  }

  if (!detail) {
    return (
      <ShareMessage
        title={t("share.openFailed")}
        message={data?.message || t("share.expired")}
        onClose={handleClose}
      />
    )
  }

  const {
    resource_type,
    share_info,
    agent_product,
    space_id,
    protocol = "lf",
  } = detail
  const isNativeProtocol = protocol === "signoz"
  const demoCost = detail.simulated_usage === true
  const nativeSourceSystem = "signoz" as const

  const traceId =
    detail.trace?.id ??
    (detail.trace as unknown as Record<string, unknown> | undefined)?.traceId
  const sessionId =
    detail.session?.id ??
    (detail.session as unknown as Record<string, unknown> | undefined)
      ?.sessionId

  const renderDrilldownTrace = () => {
    if (isDrilldownLoading) {
      return (
        <div className="flex h-full flex-col items-center justify-center gap-3 p-6">
          <div className="size-6 animate-spin rounded-full border-3 border-muted border-t-foreground" />
          <p className="text-sm text-muted-foreground">
            {t("share.loadingTrace")}
          </p>
        </div>
      )
    }

    if (!drilldownTrace) {
      return (
        <div className="flex h-full flex-col items-center justify-center gap-1">
          <p className="text-sm text-muted-foreground">
            {t("share.traceLoadFailed")}
          </p>
          <Button
            variant="outline"
            size="sm"
            className="mt-2"
            onClick={handleBackToSession}
          >
            {t("share.backToSession")}
          </Button>
        </div>
      )
    }

    if (isNativeProtocol) {
      return (
        <>
          <button
            type="button"
            onClick={handleBackToSession}
            className="flex shrink-0 items-center gap-1 border-b border-border px-4 py-2 text-sm text-muted-foreground hover:text-foreground"
          >
            <ChevronLeft className="size-4" />
            {t("share.backToSession")}
          </button>
          <NativeTraceDetail
            spaceId={space_id}
            traceId={drilldownTraceId}
            agentProduct={agent_product}
            sourceSystem={nativeSourceSystem}
            onClose={handleBackToSession}
            traceData={drilldownTrace as NativeTraceDetailResponse}
            demoCost={demoCost}
            showHeaderActions={false}
            labsApi={false}
          />
        </>
      )
    }

    return (
      <>
        <button
          type="button"
          onClick={handleBackToSession}
          className="flex shrink-0 items-center gap-1 border-b border-border px-4 py-2 text-sm text-muted-foreground hover:text-foreground"
        >
          <ChevronLeft className="size-4" />
          {t("share.backToSession")}
        </button>
        <TraceDetailPanel
          traceId={drilldownTraceId!}
          traceData={drilldownTrace as LangfuseTraceDetail}
          spaceId={space_id}
          agentProduct={agent_product}
          desensitized={false}
          isFullscreen={isFullscreen}
          onToggleFullscreen={() => setIsFullscreen((prev) => !prev)}
          onClose={handleBackToSession}
          onNavigateToSession={undefined}
          observationPathBuilder={(observationId) =>
            `/share/observations/${observationId}?${queryString}`
          }
        />
      </>
    )
  }

  return (
    <TraceLinkShell showBrand={!isFullscreen}>
      <div className="flex h-full flex-col overflow-hidden">
        <ShareBanner
          sharerName={share_info.sharer_name}
          createdAt={share_info.created_at}
          expiresAt={share_info.expires_at}
        />
        {drilldownTraceId && resource_type === "session" ? (
          renderDrilldownTrace()
        ) : resource_type === "trace" && traceId ? (
          isNativeProtocol ? (
            <NativeTraceDetail
              spaceId={space_id}
              traceId={String(traceId)}
              agentProduct={agent_product}
              sourceSystem={nativeSourceSystem}
              onClose={handleClose}
              traceData={detail.trace as unknown as NativeTraceDetailResponse}
              demoCost={demoCost}
              showHeaderActions={false}
              labsApi={false}
            />
          ) : (
            <TraceDetailPanel
              traceId={String(traceId)}
              traceData={detail.trace}
              spaceId={space_id}
              agentProduct={agent_product}
              desensitized={false}
              isFullscreen={isFullscreen}
              onToggleFullscreen={() => setIsFullscreen((prev) => !prev)}
              onClose={handleClose}
              onNavigateToSession={undefined}
              observationPathBuilder={(observationId) =>
                `/share/observations/${observationId}?${queryString}`
              }
            />
          )
        ) : resource_type === "session" && sessionId ? (
          isNativeProtocol ? (
            <NativeSessionDetailPanel
              sessionId={String(sessionId)}
              spaceId={space_id}
              agentProduct={agent_product}
              sourceSystem={nativeSourceSystem}
              isFullscreen={isFullscreen}
              onToggleFullscreen={() => setIsFullscreen((prev) => !prev)}
              onClose={handleClose}
              onNavigateToTrace={(tid) => setDrilldownTraceId(tid)}
              showHeaderActions={false}
              demoCost={demoCost}
              sessionData={
                detail.session as NativeSessionDetailPanelProps["sessionData"]
              }
            />
          ) : (
            <SessionDetailPanel
              sessionId={String(sessionId)}
              spaceId={space_id}
              agentProduct={agent_product}
              desensitized={false}
              isFullscreen={isFullscreen}
              onToggleFullscreen={() => setIsFullscreen((prev) => !prev)}
              onClose={handleClose}
              onNavigateToTrace={(tid) => setDrilldownTraceId(tid)}
              sessionData={detail.session}
              showHeaderActions={false}
            />
          )
        ) : (
          <ShareMessage
            title={t("share.dataExpired")}
            message={t("share.dataExpiredMessage")}
            onClose={handleClose}
          />
        )}
      </div>
    </TraceLinkShell>
  )
}
