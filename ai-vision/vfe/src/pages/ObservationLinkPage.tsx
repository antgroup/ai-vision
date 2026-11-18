import { useTranslation } from "react-i18next"
import { useMemo } from "react"
import { useSearchParams } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"

import { api } from "@/lib/api"
import { Skeleton } from "@/components/ui/skeleton"
import { useLinkTheme } from "@/hooks/use-link-theme"
import { TraceLinkShell } from "@/components/traces/shared/TraceLinkShell"
import { ObservationDetailPanel } from "@/components/traces-lf"
import type { DisplayExchangeRates } from "@/features/model-pricing/currency"
import type {
  LangfuseObservationDetail,
  LangfuseTraceDetail,
} from "@/agents/types/langfuse"

type ApiResponse<T> =
  | { code: 0; message?: string; data: T }
  | { code: number; message?: string; data?: unknown }

type ObservationLinkDetailResponse = {
  trace: LangfuseTraceDetail
  observation: LangfuseObservationDetail
  selected_observation_id: string
  agent_product?: string
  space_id: string
  exchange_rates?: DisplayExchangeRates | null
}

function isSuccess<T>(
  response: ApiResponse<T> | undefined
): response is { code: 0; message?: string; data: T } {
  return !!response && response.code === 0
}

function ObservationLinkLoading({ embedded }: { embedded: boolean }) {
  return (
    <TraceLinkShell showBrand={!embedded}>
      <div className="flex h-full flex-col gap-3 p-4">
        <Skeleton className="h-6 w-1/2" />
        <Skeleton className="h-4 w-1/3" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    </TraceLinkShell>
  )
}

function ObservationLinkMessage({
  title,
  message,
}: {
  title: string
  message: string
}) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-6">
      <div className="w-full max-w-xl rounded-lg border bg-card p-6 text-center shadow-sm">
        <h1 className="text-lg font-semibold text-foreground">{title}</h1>
        <p className="mt-2 text-sm text-muted-foreground">{message}</p>
      </div>
    </div>
  )
}

export default function ObservationLinkPage() {
  const { t } = useTranslation()
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
    queryKey: ["observationLinkDetail", payload, sig],
    queryFn: () =>
      api.get<ApiResponse<ObservationLinkDetailResponse>>(
        `/observation-link/detail?${queryString}`,
        { skipAuthRedirect: true }
      ),
    enabled: !!payload && !!sig,
    retry: false,
  })

  const detail = isSuccess<ObservationLinkDetailResponse>(data)
    ? data.data
    : null

  if (!payload || !sig) {
    return (
      <ObservationLinkMessage
        title={t("linkPage.invalidTitle")}
        message={t("linkPage.missingSignature")}
      />
    )
  }

  if (isLoading) {
    return <ObservationLinkLoading embedded={embedded} />
  }

  if (!detail) {
    return (
      <ObservationLinkMessage
        title={t("linkPage.failedTitle")}
        message={data?.message || t("linkPage.invalidDescription")}
      />
    )
  }

  return (
    <TraceLinkShell
      showBrand={!embedded}
      exchangeRates={detail.exchange_rates ?? null}
    >
      <ObservationDetailPanel
        observationId={detail.selected_observation_id}
        traceId={detail.trace.id}
        spaceId={detail.space_id}
        agentProduct={detail.agent_product || undefined}
        desensitized={false}
        preloadedDetail={detail.observation}
      />
    </TraceLinkShell>
  )
}
