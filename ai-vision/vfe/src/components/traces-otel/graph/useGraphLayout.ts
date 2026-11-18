import { useMemo } from "react"
import dagre from "dagre"
import { type Node, type Edge, MarkerType } from "@xyflow/react"
import type { GraphNode, GraphEdge } from "./graph-utils"

const NODE_WIDTH = 220
const NODE_HEIGHT = 80

export function useGraphLayout(
  graphNodes: GraphNode[],
  graphEdges: GraphEdge[]
): { nodes: Node[]; edges: Edge[] } {
  return useMemo(() => {
    if (graphNodes.length === 0) return { nodes: [], edges: [] }

    const dagreGraph = new dagre.graphlib.Graph()
    dagreGraph.setDefaultEdgeLabel(() => ({}))
    dagreGraph.setGraph({ rankdir: "TB", nodesep: 40, ranksep: 60 })

    for (const node of graphNodes) {
      dagreGraph.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT })
    }
    for (const edge of graphEdges) {
      dagreGraph.setEdge(edge.source, edge.target)
    }

    dagre.layout(dagreGraph)

    const nodes: Node[] = graphNodes.map((graphNode) => {
      const pos = dagreGraph.node(graphNode.id)
      return {
        id: graphNode.id,
        type: "agentNode",
        position: {
          x: pos.x - NODE_WIDTH / 2,
          y: pos.y - NODE_HEIGHT / 2,
        },
        data: graphNode as unknown as Record<string, unknown>,
      }
    })

    const edges: Edge[] = graphEdges.map((graphEdge) => ({
      id: graphEdge.id,
      source: graphEdge.source,
      target: graphEdge.target,
      type: "default",
      style: { stroke: "#64748b" },
      markerEnd: {
        type: MarkerType.ArrowClosed,
        width: 16,
        height: 16,
      },
      animated: true,
    }))

    return { nodes, edges }
  }, [graphNodes, graphEdges])
}
