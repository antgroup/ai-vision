import { useTranslation } from "react-i18next"
import { useMemo, useCallback, useEffect, useState } from "react"
import {
  ReactFlow,
  Controls,
  MiniMap,
  Background,
  BackgroundVariant,
  type NodeMouseHandler,
  type ReactFlowInstance,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"

import "./graph-dark.css"

import type { OTelSpan } from "../types"
import { spansToGraph } from "./graph-utils"
import { useGraphLayout } from "./useGraphLayout"
import { AgentGraphNode } from "./AgentGraphNode"

const nodeTypes = { agentNode: AgentGraphNode }

export function SpanGraphView({
  spans,
  selectedSpanId,
  onSelectSpan,
  visibleSpanIds,
  matchingSpanIds,
  activeSearchSpanId,
}: {
  spans: OTelSpan[]
  selectedSpanId: string | null
  onSelectSpan: (id: string) => void
  visibleSpanIds?: ReadonlySet<string>
  matchingSpanIds?: ReadonlySet<string>
  activeSearchSpanId?: string | null
}) {
  const { t } = useTranslation()
  const [flow, setFlow] = useState<ReactFlowInstance | null>(null)
  useEffect(() => {
    if (flow && activeSearchSpanId) {
      void flow.fitView({
        nodes: [{ id: activeSearchSpanId }],
        padding: 0.4,
        maxZoom: 1.2,
        duration: 0,
      })
    }
  }, [flow, activeSearchSpanId])
  const { nodes: graphNodes, edges: graphEdges } = useMemo(
    () => spansToGraph(spans),
    [spans]
  )

  // Project semantics from the original trace before hiding unmatched branches.
  // Retained ancestors are context, not additional filter matches.
  const visibleGraph = useMemo(
    () => ({
      nodes: visibleSpanIds
        ? graphNodes.filter((node) => visibleSpanIds.has(node.id))
        : graphNodes,
      edges: visibleSpanIds
        ? graphEdges.filter(
            (edge) =>
              visibleSpanIds.has(edge.source) && visibleSpanIds.has(edge.target)
          )
        : graphEdges,
    }),
    [graphNodes, graphEdges, visibleSpanIds]
  )
  const { nodes, edges } = useGraphLayout(
    visibleGraph.nodes,
    visibleGraph.edges
  )

  const nodesWithSelection = useMemo(
    () =>
      nodes.map((node) => ({
        ...node,
        selected: node.id === selectedSpanId,
        data: {
          ...node.data,
          filterContext:
            matchingSpanIds !== undefined && !matchingSpanIds.has(node.id),
          searchActive: node.id === activeSearchSpanId,
        },
      })),
    [nodes, selectedSpanId, matchingSpanIds, activeSearchSpanId]
  )

  const handleNodeClick: NodeMouseHandler = useCallback(
    (_, node) => onSelectSpan(node.id),
    [onSelectSpan]
  )

  if (nodes.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        {t("traceLabels.noSpans")}
      </div>
    )
  }

  return (
    <div className="h-full w-full">
      <ReactFlow
        nodes={nodesWithSelection}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={handleNodeClick}
        onInit={setFlow}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.2}
        maxZoom={1.5}
        nodesDraggable={false}
        nodesConnectable={false}
        proOptions={{ hideAttribution: true }}
      >
        <Controls showInteractive={false} />
        <MiniMap nodeStrokeWidth={3} style={{ height: 80, width: 120 }} />
        <Background variant={BackgroundVariant.Dots} gap={16} size={1} />
      </ReactFlow>
    </div>
  )
}
