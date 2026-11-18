import { useTranslation } from "react-i18next"
import { useMemo, useCallback } from "react"
import {
  ReactFlow,
  Controls,
  MiniMap,
  Background,
  BackgroundVariant,
  type NodeMouseHandler,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"

import "./graph-dark.css"

import type { LangfuseObservation } from "@/agents/types/langfuse"
import { observationsToGraph } from "./graph-utils"
import { useGraphLayout } from "./useGraphLayout"
import { AgentGraphNode } from "./AgentGraphNode"

const nodeTypes = { agentNode: AgentGraphNode }

export function AgentGraphView({
  observations,
  selectedObsId,
  onSelect,
}: {
  observations: LangfuseObservation[]
  selectedObsId: string | null
  onSelect: (id: string) => void
}) {
  const { t } = useTranslation()
  const { nodes: graphNodes, edges: graphEdges } = useMemo(
    () => observationsToGraph(observations),
    [observations]
  )

  const { nodes, edges } = useGraphLayout(graphNodes, graphEdges)

  const nodesWithSelection = useMemo(
    () =>
      nodes.map((node) => ({
        ...node,
        selected: node.id === selectedObsId,
      })),
    [nodes, selectedObsId]
  )

  const handleNodeClick: NodeMouseHandler = useCallback(
    (_, node) => onSelect(node.id),
    [onSelect]
  )

  if (nodes.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        {t("traceLabels.noObservations")}
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
