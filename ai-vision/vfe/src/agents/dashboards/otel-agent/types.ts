/**
 * Shared configuration interface for OTel-based agent dashboards.
 *
 * A new agent dashboard only needs to supply a set of query specs
 * (SQL templates with {{placeholder}} variables) and a lightweight
 * field-mapping config.  As long as the underlying data source uses
 * OpenTelemetry Semantic Conventions for GenAI plus the common
 * agentic.* extension fields, the generic sections will render
 * correctly without any code changes.
 */

export interface OtelQuerySpec {
  queryKey: string
}

/**
 * Query specs consumed by each generic section.
 * Every field is a query spec whose template is rendered server-side.
 */
export interface OtelAgentQuerySpecs {
  /** Single-row aggregation used for hero metrics and empty-data detection. */
  summary: OtelQuerySpec

  /** Raw event stream, one row per event, ordered by time DESC. */
  eventStream: OtelQuerySpec

  /** Token timeseries grouped by time bucket. */
  tokenTimeseries: OtelQuerySpec

  /** Token timeseries pivoted by model name. */
  tokenByModel: OtelQuerySpec

  /** Session list with start/end time and log count. */
  sessionList: OtelQuerySpec

  /** Event counts timeseries grouped by event name. */
  eventTimeseries: OtelQuerySpec

  /** API request counts timeseries grouped by model. */
  modelTimeseries: OtelQuerySpec

  /** API request latency timeseries grouped by model. */
  modelLatencyTimeseries: OtelQuerySpec

  /** Input-token timeseries grouped by model. */
  modelInputTokenTimeseries: OtelQuerySpec

  /** Output-token timeseries grouped by model. */
  modelOutputTokenTimeseries: OtelQuerySpec

  /** Per-request detail table. */
  modelApiDetail: OtelQuerySpec

  /** Tool invocation summary grouped by tool name. */
  toolSummary: OtelQuerySpec

  /** Tool usage timeseries grouped by tool name. */
  toolTimeseries: OtelQuerySpec

  /** Tool decision timeseries grouped by decision value. */
  toolDecisionTimeseries: OtelQuerySpec

  /** Tool invocation detail table. */
  toolDetail: OtelQuerySpec
}
