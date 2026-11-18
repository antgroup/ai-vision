// V2 Observability 类型定义
// 前端所有 Widget 和 Adapter 统一使用这些类型

// === Metric 语义类型 ===

export type MetricValueType = "number" | "timeseries" | "table" | "distribution"

export interface MetricDefinition {
  metric_key: string
  display_name: string
  value_type: MetricValueType
  unit?: string
  description?: string
}

// === 查询结果类型 (V2 标准格式) ===

export interface NumberMetricData {
  value: number
  previous_value?: number
  delta?: number
}

export interface NumberMetricResult {
  value_type: "number"
  data: NumberMetricData
}

export interface TimeseriesSeries {
  name: string
  points: Array<[number, number]>
}

export interface TimeseriesMetricData {
  series: TimeseriesSeries[]
}

export interface TimeseriesMetricResult {
  value_type: "timeseries"
  data: TimeseriesMetricData
}

export type TableColumnDataType =
  | "string"
  | "number"
  | "datetime"
  | "duration"
  | "token"
  | "status"

export interface TableColumn {
  key: string
  label: string
  data_type: TableColumnDataType
}

export interface TableMetricData {
  columns: TableColumn[]
  rows: Record<string, unknown>[]
}

export interface TableMetricResult {
  value_type: "table"
  data: TableMetricData
}

export type MetricResult = NumberMetricResult | TimeseriesMetricResult | TableMetricResult

// === Query Request / Response ===

export interface QueryRequest {
  agent_product: string
  fetch_group: string
  time_range: string
  time_start?: string
  time_end?: string
  limit?: number
}

export interface QueryResponse {
  results: Record<string, MetricResult>
  meta: {
    query_count: number
    execution_time_ms: number
  }
}

// === V1 Adapter 映射 ===

export interface TableToTimeseriesConfig {
  timeKey: string
  valueKey: string
  seriesKey: string
  fallbackLabel?: string
}

export interface V1MetricMapping {
  metric_key: string
  data_item_key: string
  datasource_type: "logs" | "metrics"
  transform?: (raw: unknown) => unknown
  table_to_timeseries?: TableToTimeseriesConfig
}

// === V1 API 响应类型 ===

export interface V1QueryResponse {
  code: number
  message: string
  data?: {
    value_type: "number" | "timeseries" | "table"
    data: unknown
    execution_time_ms?: number
  }
}

export interface V1Panel {
  id: number
  panel_type: string
  title: string
  description?: string | null
  data_item_key: string
  datasource_type: string
  sort_order: number
  grid_width: number
  group_name: string
  panel_config?: Record<string, unknown> | null
}

export interface V1PanelGroup {
  group_name: string
  panels: V1Panel[]
}

export interface V1PanelsResponse {
  code: number
  message: string
  data: {
    groups: V1PanelGroup[]
  }
}

export interface V1SpaceDashboardResponse {
  code: number
  message: string
  data: {
    groups: V1PanelGroup[]
    agent_products: string[]
  }
}
