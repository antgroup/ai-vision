import { ReportedCost } from "@/features/model-pricing/ReportedCost"
import { useTranslation } from "react-i18next"
import { getModelSpanPresentation } from "@/components/traces/shared/modelSpanPresentation"
import React, { useState, useEffect } from "react"
import { useQuery } from "@tanstack/react-query"

import { api } from "@/lib/api"
import { Skeleton } from "@/components/ui/skeleton"
import { Badge } from "@/components/ui/badge"
import { ModelBadge } from "@/components/traces-otel/shared/TraceBadge"
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
  agentUuid,
  desensitized,
  traceTimeRange,
  path,
  observationData,
  preloadedDetail,
  formatMode = "formatted",
}: {
  observationId: string
  traceId?: string
  spaceId: string
  agentProduct?: string
  agentUuid?: string | null
  desensitized: boolean
  traceTimeRange?: TraceQueryTimeRange | null
  path?: string
  observationData?: LangfuseObservation | LangfuseObservationDetail | null
  preloadedDetail?: LangfuseObservationDetail
  formatMode?: "formatted" | "json"
}) {
  const { t, i18n } = useTranslation()
  const { data: obsResp, isLoading } = useQuery({
    queryKey: [
      "observationDetail",
      spaceId,
      observationId,
      traceId,
      agentProduct,
      agentUuid,
      traceTimeRange?.from,
      traceTimeRange?.to,
      path,
    ],
    queryFn: () => {
      if (path) {
        return api.get<{ code: number; data: LangfuseObservationDetail }>(path)
      }
      const params = new URLSearchParams()
      if (agentProduct) params.set("agent_product", agentProduct)
      if (agentUuid) params.set("agent_uuid", agentUuid)
      if (traceTimeRange?.from) {
        params.set("from", traceTimeRange.from)
      }
      if (traceTimeRange?.to) {
        params.set("to", traceTimeRange.to)
      }
      const suffix = params.toString() ? `?${params.toString()}` : ""
      if (traceId) {
        return api.get<{ code: number; data: LangfuseObservationDetail }>(
          `/spaces/${spaceId}/lf/trace/${traceId}/span/${observationId}/detail${suffix}`
        )
      }
      return api.get<{ code: number; data: LangfuseObservationDetail }>(
        `/spaces/${spaceId}/observations/${observationId}${suffix}`
      )
    },
    enabled: !!observationId && !preloadedDetail,
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

  const obs: LangfuseObservationDetail | null = (preloadedDetail ??
    apiData ??
    observationData ??
    null) as LangfuseObservationDetail | null

  const [sectionCollapsed, setSectionCollapsed] = useState<
    Record<string, boolean>
  >({})
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSectionCollapsed({})
  }, [observationId])

  const toggleSection = (key: string) =>
    setSectionCollapsed((prev) => ({ ...prev, [key]: !prev[key] }))

  if (isLoading && !observationData && !preloadedDetail) {
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
        {obs.level === "ERROR" && (
          <Badge variant="destructive" className="font-medium">
            {t("traceLabels.error")}
          </Badge>
        )}
        {(obs.promptTokens > 0 || obs.completionTokens > 0) && (
          <UsageBadge
            promptTokens={obs.promptTokens}
            completionTokens={obs.completionTokens}
            totalTokens={obs.totalTokens}
            usageDetails={obs.usageDetails}
            variant="badge"
          />
        )}
        {(["GENERATION", "EMBEDDING"].includes(obs.type.toUpperCase()) ||
          (obs.calculatedTotalCost !== null &&
            obs.calculatedTotalCost !== undefined) ||
          ("totalCost" in obs &&
            obs.totalCost !== null &&
            obs.totalCost !== undefined)) && (
          <ReportedCost
            value={
              obs.calculatedTotalCost ??
              ("totalCost" in obs ? obs.totalCost : undefined)
            }
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
