import type { OtelAgentQuerySpecs } from "../types"

/** Namespace used by SigNoz's structured dashboard API. */
export interface OtelAgentFieldConfig {
  queryKeyPrefix: string
}

export function buildOtelQuerySpecs(
  cfg: OtelAgentFieldConfig
): OtelAgentQuerySpecs {
  const p = cfg.queryKeyPrefix
  return {
    summary: {
      queryKey: `${p}.summary`,
    },
    eventStream: {
      queryKey: `${p}.overview.event_stream`,
    },
    tokenTimeseries: {
      queryKey: `${p}.tokens.timeseries`,
    },
    tokenByModel: {
      queryKey: `${p}.overview.token_by_model`,
    },
    sessionList: {
      queryKey: `${p}.sessions.list`,
    },
    eventTimeseries: {
      queryKey: `${p}.events.timeseries`,
    },
    modelTimeseries: {
      queryKey: `${p}.models.timeseries`,
    },
    modelLatencyTimeseries: {
      queryKey: `${p}.models.latency_timeseries`,
    },
    modelInputTokenTimeseries: {
      queryKey: `${p}.models.input_token_timeseries`,
    },
    modelOutputTokenTimeseries: {
      queryKey: `${p}.models.output_token_timeseries`,
    },
    modelApiDetail: {
      queryKey: `${p}.models.api_detail`,
    },
    toolSummary: {
      queryKey: `${p}.tools.summary`,
    },
    toolTimeseries: {
      queryKey: `${p}.tools.timeseries`,
    },
    toolDecisionTimeseries: {
      queryKey: `${p}.tools.decision_timeseries`,
    },
    toolDetail: {
      queryKey: `${p}.tools.detail`,
    },
  }
}
