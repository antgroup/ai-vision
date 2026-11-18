import { documentationUrl } from "../../../lib/documentation.ts"

const FEATURE_DOC_PAGES = {
  "user-input": "user-input",
  "span-content": "span-content",
  mcp: "mcp-observability",
  decision: "decision-traces",
  skill: "skill-observability",
  memory: "memory-observability",
  "context-compaction": "traces",
  privacy: "privacy",
} as const

export type ObservabilityViewFeature = keyof typeof FEATURE_DOC_PAGES

export function observabilityViewDocsUrl(
  feature: ObservabilityViewFeature,
  language?: string
): string {
  return documentationUrl(`guides/${FEATURE_DOC_PAGES[feature]}`, language)
}
