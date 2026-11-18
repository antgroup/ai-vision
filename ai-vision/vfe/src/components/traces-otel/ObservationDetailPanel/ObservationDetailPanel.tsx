import { useTranslation } from "react-i18next"
import { getModelSpanPresentation } from "@/components/traces/shared/modelSpanPresentation"
import React, { useState, useEffect, useMemo } from "react"
import { useQuery } from "@tanstack/react-query"

import { nativeApi as api } from "../api"
import { Skeleton } from "@/components/ui/skeleton"
import { Badge } from "@/components/ui/badge"
import { ErrorBadge, ModelBadge } from "../shared/TraceBadge"
import type {
  LangfuseObservation,
  LangfuseObservationDetail,
  TraceQueryTimeRange,
} from "@/agents/types/langfuse"
import {
  formatLatency,
  formatObservationName,
  formatTime,
  deepParseJson,
} from "../shared/utils"
import {
  IOSection,
  DetailSection,
  MetadataTree,
  renderJson,
} from "../shared/MetadataTree"
import { formatJsonHighlighted } from "../shared/JsonDetailWidgets"
import { UsageBadge } from "../shared/CostUsageBadge"

export function ObservationDetailPanel({
  observationId,
  traceId,
  spaceId,
  agentProduct,
  desensitized,
  traceTimeRange,
  path,
  observationData,
  formatMode = "formatted",
}: {
  observationId: string
  traceId?: string
  spaceId: string
  agentProduct?: string
  desensitized: boolean
  traceTimeRange?: TraceQueryTimeRange | null
  path?: string
  observationData?: LangfuseObservation | LangfuseObservationDetail | null
  formatMode?: "formatted" | "json"
}) {
  const { t, i18n } = useTranslation()
  const effectiveTimeRange = useMemo<TraceQueryTimeRange | null>(() => {
    if (traceTimeRange?.from) return traceTimeRange
    const startIso = observationData?.startTime
    if (!startIso) return null
    const ms = new Date(startIso).getTime()
    if (!Number.isFinite(ms)) return null
    const HALF_WINDOW = 30 * 60 * 1000
    return {
      from: new Date(ms - HALF_WINDOW).toISOString(),
      to: new Date(ms + HALF_WINDOW).toISOString(),
    }
  }, [traceTimeRange, observationData?.startTime])

  const { data: obsResp, isLoading } = useQuery({
    queryKey: [
      "signozObservationDetail",
      spaceId,
      observationId,
      traceId,
      agentProduct,
      effectiveTimeRange?.from,
      effectiveTimeRange?.to,
      path,
    ],
    queryFn: () => {
      if (path) {
        return api.get<{ code: number; data: LangfuseObservationDetail }>(path)
      }
      const params = new URLSearchParams()
      if (agentProduct) params.set("agent_product", agentProduct)
      if (effectiveTimeRange?.from) {
        params.set("from", effectiveTimeRange.from)
      }
      if (effectiveTimeRange?.to) {
        params.set("to", effectiveTimeRange.to)
      }
      const suffix = params.toString() ? `?${params.toString()}` : ""
      if (traceId) {
        return api.get<{ code: number; data: LangfuseObservationDetail }>(
          `/spaces/${spaceId}/signoz/trace/${traceId}/span/${observationId}/detail${suffix}`
        )
      }
      return api.get<{ code: number; data: LangfuseObservationDetail }>(
        `/spaces/${spaceId}/observations/${observationId}${suffix}`
      )
    },
    enabled: !!observationId,
    refetchOnWindowFocus: false,
  })

  const apiData =
    obsResp &&
    obsResp.code === 0 &&
    obsResp.data &&
    typeof obsResp.data === "object" &&
    "id" in obsResp.data
      ? obsResp.data
      : null

  const obs: LangfuseObservationDetail | null = (apiData ??
    observationData ??
    null) as LangfuseObservationDetail | null

  const [sectionCollapsed, setSectionCollapsed] = useState<
    Record<string, boolean>
  >({})
  /* eslint-disable react-hooks/set-state-in-effect -- reset sections when observation changes */
  useEffect(() => {
    setSectionCollapsed({})
  }, [observationId])
  /* eslint-enable react-hooks/set-state-in-effect */

  const toggleSection = (key: string) =>
    setSectionCollapsed((prev) => ({ ...prev, [key]: !prev[key] }))

  if (isLoading && !observationData) {
    return (
      <div className="flex flex-col gap-3 p-4">
        <Skeleton className="h-6 w-1/2" />
        <Skeleton className="h-4 w-1/3" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    )
  }

  if (!obs) {
    return (
      <div className="p-6 text-center text-sm text-muted-foreground">
        {t("traceLabels.observationLoadFailed")}
      </div>
    )
  }

  const presentation = getModelSpanPresentation(obs)
  const displayName = presentation.labelKey
    ? t(presentation.labelKey)
    : formatObservationName(obs.name, obs.type, obs.model)
  const originalName =
    presentation.labelKey && obs.name
      ? t("modelSpan.originalName", { name: obs.name })
      : undefined

  return (
    <div className="flex h-full flex-col gap-4 overflow-auto p-4">
      <div>
        <h4 className="text-base font-medium" title={originalName}>
          {displayName}
        </h4>
        {originalName && (
          <p className="mt-1 text-xs break-words text-muted-foreground">
            {originalName}
          </p>
        )}
        <p className="mt-1 font-mono text-xs text-muted-foreground">{obs.id}</p>
      </div>

      <div className="flex flex-wrap gap-2">
        {obs.latency !== null && (
          <Badge variant="secondary" className="font-normal">
            <span>{t("traceLabels.latency")}:</span>
            <span className="font-mono">{formatLatency(obs.latency)}</span>
          </Badge>
        )}
        {obs.startTime && (
          <Badge variant="secondary" className="font-normal">
            <span>{t("nativeSpanUsage.start")}:</span>
            <span className="font-mono">
              {formatTime(obs.startTime, i18n?.resolvedLanguage)}
            </span>
          </Badge>
        )}
        {obs.endTime && (
          <Badge variant="secondary" className="font-normal">
            <span>{t("nativeSpanUsage.end")}:</span>
            <span className="font-mono">
              {formatTime(obs.endTime, i18n?.resolvedLanguage)}
            </span>
          </Badge>
        )}
        {obs.environment && (
          <Badge variant="secondary" className="font-normal">
            <span>{t("traceLabels.environment")}:</span>
            <span>{obs.environment}</span>
          </Badge>
        )}
        {obs.level === "ERROR" && <ErrorBadge />}
        {(obs.promptTokens > 0 || obs.completionTokens > 0) && (
          <UsageBadge
            promptTokens={obs.promptTokens}
            completionTokens={obs.completionTokens}
            totalTokens={obs.totalTokens}
            usageDetails={obs.usageDetails}
            variant="badge"
          />
        )}
        {presentation.model && <ModelBadge model={presentation.model} />}
      </div>

      {obs.statusMessage && (
        <DetailSection title={t("traceLabels.statusMessage")}>
          <span className="text-sm text-foreground">{obs.statusMessage}</span>
        </DetailSection>
      )}

      {obs.input !== null && (
        <IOSection
          title={t("nativeSpanUsage.input")}
          data={desensitized ? "***" : obs.input}
          variant="input"
          viewMode={formatMode}
          collapsed={!!sectionCollapsed["input"]}
          onToggleCollapse={() => toggleSection("input")}
        />
      )}

      {obs.output !== null && (
        <IOSection
          title={t("nativeSpanUsage.output")}
          data={desensitized ? "***" : obs.output}
          variant="output"
          viewMode={formatMode}
          collapsed={!!sectionCollapsed["output"]}
          onToggleCollapse={() => toggleSection("output")}
        />
      )}

      {obs.modelParameters !== null &&
        typeof obs.modelParameters === "object" &&
        Object.keys(obs.modelParameters as Record<string, unknown>).length >
          0 && (
          <DetailSection
            title={t("traceLabels.modelParameters")}
            collapsible
            collapsed={!!sectionCollapsed["modelParams"]}
            onToggleCollapse={() => toggleSection("modelParams")}
          >
            {renderJson(obs.modelParameters) as React.ReactNode}
          </DetailSection>
        )}

      {obs.metadata !== null && (
        <DetailSection
          title={t("traceLabels.metadata")}
          collapsible
          collapsed={!!sectionCollapsed["metadata"]}
          onToggleCollapse={() => toggleSection("metadata")}
        >
          {formatMode === "json" ? (
            <pre className="overflow-x-auto rounded-lg bg-muted/50 p-3 font-mono text-xs leading-relaxed dark:bg-muted/30">
              <code>
                {formatJsonHighlighted(
                  JSON.stringify(deepParseJson(obs.metadata), null, 2)
                )}
              </code>
            </pre>
          ) : (
            <MetadataTree data={obs.metadata} floatToolbar />
          )}
        </DetailSection>
      )}
    </div>
  )
}
