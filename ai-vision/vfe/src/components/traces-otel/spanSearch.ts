import type { OTelSpan, SpanTreeNode } from "./types"
import { getModelSpanPresentation } from "@/components/traces/shared/modelSpanPresentation"
import { resolveSkillSpanInfo } from "@/labs/skill/skillDisplay"
import { resolvePowerContextSpan } from "@/labs/memory/powerContextSpans"

export type NativeSpanSearchField = "name" | "spanId" | "scene"

export interface NativeSpanSearchMatch {
  spanId: string
  fields: NativeSpanSearchField[]
}

/** Search the same localized operation and layer presented in every view. */
export function getNativeSpanDisplaySearchText(
  span: OTelSpan,
  translate: (key: string) => string
): string {
  if (span.attributes && resolveSkillSpanInfo(span.attributes)) return ""
  const powerContext = resolvePowerContextSpan(span)
  if (powerContext)
    return `${translate(powerContext.labelKey)} ${translate(powerContext.layerLabelKey)}`
  const { labelKey } = getModelSpanPresentation(span)
  return labelKey ? translate(labelKey) : ""
}

function normalizeSearchValue(value: unknown): string {
  if (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return String(value).trim()
  }
  return ""
}

export function getNativeSpanScene(span: OTelSpan | undefined): string {
  if (!span) return ""
  return (
    normalizeSearchValue(span.resource?.["ant.agent.scene"]) ||
    normalizeSearchValue(span.attributes?.["ant.agent.scene"])
  )
}

export function getNativeSpanSearchFields(
  span: OTelSpan | undefined,
  rawTerm: string,
  displayName = ""
): NativeSpanSearchField[] {
  if (!span) return []
  const term = rawTerm.trim().toLowerCase()
  if (!term) return []

  const fields: NativeSpanSearchField[] = []
  const model = getModelSpanPresentation(span).model
  const skillName = span.attributes
    ? resolveSkillSpanInfo(span.attributes)?.name
    : null
  if (
    [
      span.name,
      displayName,
      model,
      skillName,
      normalizeSearchValue(span.attributes?.["gen_ai.tool.name"]),
    ].some((value) => value?.toLowerCase().includes(term))
  )
    fields.push("name")
  if (span.span_id.toLowerCase().includes(term)) fields.push("spanId")
  if (getNativeSpanScene(span).toLowerCase().includes(term))
    fields.push("scene")
  return fields
}

export function flattenNativeSpanTree(tree: SpanTreeNode[]): string[] {
  const result: string[] = []
  const walk = (nodes: SpanTreeNode[]) => {
    for (const node of nodes) {
      result.push(node.spanId)
      walk(node.children)
    }
  }
  walk(tree)
  return result
}

export function findNativeSpanSearchMatches(
  tree: SpanTreeNode[],
  spans: OTelSpan[],
  rawTerm: string,
  displayNameForSpan?: (span: OTelSpan) => string
): NativeSpanSearchMatch[] {
  const term = rawTerm.trim()
  if (!term) return []

  const spanMap = new Map(spans.map((span) => [span.span_id, span]))
  return flattenNativeSpanTree(tree).flatMap((spanId) => {
    const span = spanMap.get(spanId)
    const fields = getNativeSpanSearchFields(
      span,
      term,
      span && displayNameForSpan?.(span)
    )
    return fields.length > 0 ? [{ spanId, fields }] : []
  })
}

export function findNativeSpanAncestors(
  tree: SpanTreeNode[],
  targetSpanId: string | null
): string[] {
  if (!targetSpanId) return []

  const walk = (
    nodes: SpanTreeNode[],
    ancestors: string[]
  ): string[] | null => {
    for (const node of nodes) {
      if (node.spanId === targetSpanId) return ancestors
      const result = walk(node.children, [...ancestors, node.spanId])
      if (result) return result
    }
    return null
  }

  return walk(tree, []) ?? []
}
