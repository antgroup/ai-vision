import type { LangfuseObservation } from "@/agents/types/langfuse"
import { formatObservationName } from "../shared/utils"
import { getModelSpanPresentation } from "@/components/traces/shared/modelSpanPresentation"

const AGENTIC_TYPES = new Set([
  "AGENT",
  "TOOL",
  "CHAIN",
  "RETRIEVER",
  "EMBEDDING",
])

export function hasAgenticObservations(
  observations?: LangfuseObservation[]
): boolean {
  if (!observations) return false
  return observations.some((obs) => AGENTIC_TYPES.has(obs.type))
}

export interface GraphNode {
  id: string
  type: string
  name: string
  originalName: string | null
  presentation: ReturnType<typeof getModelSpanPresentation>
  latency: number | null
  totalCost: number | null
  level: string
  usage: LangfuseObservation["usage"]
  startTime: number
  endTime: number | null
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  edgeType: "parent-child" | "sequential"
}

export function observationsToGraph(observations: LangfuseObservation[]): {
  nodes: GraphNode[]
  edges: GraphEdge[]
} {
  const nodes: GraphNode[] = observations.map((obs) => ({
    id: obs.id,
    type: obs.type,
    name: formatObservationName(obs.name, obs.type, obs.model),
    originalName: obs.name,
    presentation: getModelSpanPresentation(obs),
    latency: obs.latency,
    totalCost: obs.totalCost,
    level: obs.level,
    usage: obs.usage,
    startTime: new Date(obs.startTime).getTime(),
    endTime: obs.endTime ? new Date(obs.endTime).getTime() : null,
  }))

  const edges: GraphEdge[] = []
  const idSet = new Set(observations.map((o) => o.id))

  const childrenMap = new Map<string, LangfuseObservation[]>()
  const roots: LangfuseObservation[] = []

  for (const obs of observations) {
    const parentId = obs.parentObservationId
    if (parentId && idSet.has(parentId)) {
      if (!childrenMap.has(parentId)) childrenMap.set(parentId, [])
      childrenMap.get(parentId)!.push(obs)
      edges.push({
        id: `pc-${parentId}-${obs.id}`,
        source: parentId,
        target: obs.id,
        edgeType: "parent-child",
      })
    } else {
      roots.push(obs)
    }
  }

  const addSequentialEdges = (siblings: LangfuseObservation[]) => {
    const sorted = [...siblings].sort(
      (a, b) =>
        new Date(a.startTime).getTime() - new Date(b.startTime).getTime()
    )
    for (let i = 0; i < sorted.length - 1; i++) {
      edges.push({
        id: `seq-${sorted[i].id}-${sorted[i + 1].id}`,
        source: sorted[i].id,
        target: sorted[i + 1].id,
        edgeType: "sequential",
      })
    }
  }

  if (roots.length > 1) addSequentialEdges(roots)

  for (const children of childrenMap.values()) {
    if (children.length > 1) addSequentialEdges(children)
  }

  return { nodes, edges }
}
