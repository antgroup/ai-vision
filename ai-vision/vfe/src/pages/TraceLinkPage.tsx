import { useTranslation } from "react-i18next"
import { useMemo } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"

import { api } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { useLinkTheme } from "@/hooks/use-link-theme"
import { TraceDetailPanel } from "@/components/traces-lf"
import { NativeTraceDetail } from "@/components/traces-otel/NativeTraceDetail"
import { TraceLinkShell } from "@/components/traces/shared/TraceLinkShell"
import type { LangfuseTraceDetail } from "@/agents/types/langfuse"
import type { NativeTraceDetailResponse } from "@/components/traces-otel/types"
import type { DisplayExchangeRates } from "@/features/model-pricing/currency"

type ApiResponse<T> =
  | { code: 0; message?: string; data: T }
  | { code: number; message?: string; data?: unknown }

type TraceLinkDetailResponse = {
  trace: LangfuseTraceDetail | NativeTraceDetailResponse
  langfuse_config?: {
    show_langfuse_link?: boolean
    project_url?: string
  } | null
  agent_product?: string
  space_id: string
  exchange_rates?: DisplayExchangeRates | null
}

function isSuccess<T>(
  response: ApiResponse<T> | undefined
): response is { code: 0; message?: string; data: T } {
  return !!response && response.code === 0
}

function isNativeTraceDetail(
  trace: LangfuseTraceDetail | NativeTraceDetailResponse | null
): trace is NativeTraceDetailResponse {
  // Trace-link detail follows the space datasource routing. Langfuse detail
  // returns `id`/`observations`; SigNoz returns `traceId`/`spans`.
  return (
    !!trace &&
    typeof (trace as { traceId?: unknown }).traceId === "string" &&
    Array.isArray((trace as { spans?: unknown }).spans)
  )
}

function TraceLinkLoading({ embedded }: { embedded: boolean }) {
  return (
    <TraceLinkShell showBrand={!embedded}>
      <div className="flex h-full flex-col">
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

function TraceLinkMessage({
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

export default function TraceLinkPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const payload = searchParams.get("payload") || ""
  const sig = searchParams.get("sig") || ""
  const embedded = searchParams.get("embed") === "1"
  useLinkTheme(searchParams.get("theme"))

  const queryString = useMemo(() => {
    const params = new URLSearchParams()
    if (payload) params.set("payload", payload)
    if (sig) params.set("sig", sig)
    return params.toString()
  }, [payload, sig])

  const { data, isLoading } = useQuery({
    queryKey: ["traceLinkDetail", payload, sig],
    queryFn: () =>
      api.get<ApiResponse<TraceLinkDetailResponse>>(
        `/trace-link/detail?${queryString}`,
        { skipAuthRedirect: true }
      ),
    enabled: !!payload && !!sig,
    retry: false,
  })

  const detail = isSuccess<TraceLinkDetailResponse>(data) ? data.data : null
  const trace = detail?.trace ?? null
  const langfuseConfig = detail?.langfuse_config ?? null
  const agentProduct = detail?.agent_product || ""
  const spaceId = detail?.space_id || ""
  const isNativeTrace = isNativeTraceDetail(trace)

  const handleClose = () => {
    if (window.history.length > 1) {
      navigate(-1)
      return
    }
    window.close()
  }

  if (!payload || !sig) {
    return (
      <TraceLinkMessage
        title={t("linkPage.invalidTitle")}
        message={t("linkPage.missingSignature")}
      />
    )
  }

  if (isLoading) {
    return <TraceLinkLoading embedded={embedded} />
  }

  if (!detail || !trace) {
    return (
      <TraceLinkMessage
        title={t("linkPage.failedTitle")}
        message={data?.message || t("linkPage.invalidDescription")}
        onClose={handleClose}
      />
    )
  }

  return (
    <TraceLinkShell
      showBrand={!embedded}
      exchangeRates={detail.exchange_rates ?? null}
    >
      {isNativeTrace ? (
        <NativeTraceDetail
          spaceId={spaceId}
          traceId={trace.traceId}
          agentProduct={agentProduct}
          onClose={handleClose}
          traceData={trace}
          labsApi={false}
          showHeaderActions={false}
        />
      ) : (
        <TraceDetailPanel
          traceId={trace.id}
          traceData={trace}
          spaceId={spaceId}
          agentProduct={agentProduct}
          langfuseProjectUrl={embedded ? "" : langfuseConfig?.project_url || ""}
          showLangfuseLink={
            embedded ? false : (langfuseConfig?.show_langfuse_link ?? false)
          }
          desensitized={false}
          isFullscreen={false}
          onToggleFullscreen={() => {}}
          onClose={handleClose}
          observationPathBuilder={(observationId) =>
            `/trace-link/observations/${observationId}?${queryString}`
          }
          showHeaderActions={false}
        />
      )}
    </TraceLinkShell>
  )
}
