import type { LangfuseObservation } from "@/agents/types/langfuse"

export interface ObsTreeNode {
  obs: LangfuseObservation
  children: ObsTreeNode[]
  depth: number
}

/**
 * 将扁平的 observations 列表构建树形结构
 * 根据 parentObservationId 建立父子关系
 */
export function buildObsTree(
  observations: LangfuseObservation[]
): ObsTreeNode[] {
  // 创建 ID 到节点的映射
  const nodeMap = new Map<string, ObsTreeNode>()
  const roots: ObsTreeNode[] = []

  // 第一遍：创建所有节点
  observations.forEach((obs) => {
    nodeMap.set(obs.id, {
      obs,
      children: [],
      depth: 0,
    })
  })

  // 第二遍：建立父子关系
  observations.forEach((obs) => {
    const node = nodeMap.get(obs.id)!

    if (obs.parentObservationId && nodeMap.has(obs.parentObservationId)) {
      // 有父节点，添加到父节点的 children
      const parentNode = nodeMap.get(obs.parentObservationId)!
      parentNode.children.push(node)
    } else {
      // 没有父节点或父节点不存在，作为根节点
      roots.push(node)
    }
  })

  const byStartTime = (a: ObsTreeNode, b: ObsTreeNode) =>
    new Date(a.obs.startTime).getTime() - new Date(b.obs.startTime).getTime()

  function setDepth(nodes: ObsTreeNode[], d: number) {
    nodes.sort(byStartTime)
    for (const n of nodes) {
      n.depth = d
      setDepth(n.children, d + 1)
    }
  }
  setDepth(roots, 0)

  return roots
}

/**
 * 将树形结构扁平化为列表（前序遍历）
 * 保持树的层级顺序
 */
export function flattenTree(tree: ObsTreeNode[]): ObsTreeNode[] {
  const result: ObsTreeNode[] = []

  function traverse(nodes: ObsTreeNode[]) {
    nodes.forEach((node) => {
      result.push(node)
      if (node.children.length > 0) {
        traverse(node.children)
      }
    })
  }

  traverse(tree)
  return result
}
