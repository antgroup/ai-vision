/**
 * Agent Trace V2 — Type definitions for the per-agent trace registry.
 *
 * These types define the extension points that agent-specific trace
 * configurations can use to customize trace list columns, filters,
 * detail panel sections, graph rendering, and session views.
 *
 * The config-first design means most agents only need to declare a
 * plain config object. Full component override (`traceView`) is the
 * escape hatch for agents that need a fundamentally different layout.
 */

import type { ReactNode } from "react"
import type {
  LangfuseTrace,
  LangfuseTraceDetail,
  LangfuseObservation,
  TraceQueryTimeRange,
} from "./langfuse"

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

export interface TracePageContext {
  agentKey: string
  agentProduct: string
  agentUuid?: string | null
  spaceId: string
  timeRange: { fromTimestamp: string; toTimestamp: string }
  refreshTick: number
  sourceToggle?: React.ReactNode
  initialSearchTraceId?: string
  selectedTraceId?: string | null
  selectedTraceTimeRange?: TraceQueryTimeRange | null
  /** Span to focus when the initial trace detail opens (e.g. an MCP drawer
   * deep link pointing at one tool call). */
  focusSpanId?: string | null
  onTraceSelect?: (
    traceId: string,
    startMs?: number,
    endMs?: number | null
  ) => void
  onTraceDeselect?: () => void
}

// ---------------------------------------------------------------------------
// Column definitions
// ---------------------------------------------------------------------------

export interface TraceColumnDef {
  key: string
  label: string
  sortField?: string
  width?: string
  render?: (trace: LangfuseTrace, ctx: TracePageContext) => ReactNode
  hidden?: boolean
}

// ---------------------------------------------------------------------------
// Filter definitions
// ---------------------------------------------------------------------------

export type TraceSearchType = "traceId" | "input" | "output"

export interface TraceSearchValue {
  type: TraceSearchType
  keyword: string
}

export interface TraceFilterDef {
  key: string
  label: string
  type: "text" | "select" | "tag"
  options?: { label: string; value: string }[]
  toParams: (value: string) => Record<string, string>
}

// ---------------------------------------------------------------------------
// Detail panel sections
// ---------------------------------------------------------------------------

export type TraceDetailSectionPosition =
  | "before-io"
  | "after-io"
  | "before-graph"
  | "after-graph"
  | "tab"

export interface TraceDetailSectionDef {
  key: string
  label: string
  position: TraceDetailSectionPosition
  render: (trace: LangfuseTraceDetail, ctx: TracePageContext) => ReactNode
}

// ---------------------------------------------------------------------------
// Agent trace config
// ---------------------------------------------------------------------------

export interface AgentTraceConfig {
  /**
   * Full custom trace page component — bypasses all default rendering.
   * When set, all other config fields are ignored.
   */
  traceView?: React.ComponentType<TracePageContext> | null

  /** Additional columns appended after default columns. */
  extraColumns?: TraceColumnDef[]
  /** Column keys to hide from the default set. */
  hiddenColumns?: string[]
  /** Replace the default column set entirely. */
  columns?: TraceColumnDef[]

  /** Additional filters beyond the defaults. */
  extraFilters?: TraceFilterDef[]

  /** Extra sections injected into the trace detail drawer. */
  detailSections?: TraceDetailSectionDef[]
  /** Custom observation detail renderer. */
  observationDetail?: React.ComponentType<{
    observation: LangfuseObservation
    ctx: TracePageContext
  }>

  /** Custom node renderer for AgentGraphView. */
  graphNodeRenderer?: React.ComponentType<{ observation: LangfuseObservation }>
  /** Custom observation type color/icon mapping. */
  observationStyles?: Record<
    string,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    { color: string; icon: React.ComponentType<any> }
  >

  /** Additional session columns. */
  extraSessionColumns?: TraceColumnDef[]
  /** Custom session card renderer. */
  sessionCardRenderer?: React.ComponentType<{
    trace: LangfuseTrace
    ctx: TracePageContext
  }>

  supportsSession?: boolean
  showLangfuseLink?: boolean
}
