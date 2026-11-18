/**
 * Agent product utilities: themes, sorting, categorization, icon helpers.
 * Migrated from aivision-fe/src/lib/agentProducts.ts
 */

// ── Capability helpers ──

export function getCapability(
  capabilities: Record<string, unknown> | null | undefined,
  key: string,
  defaultValue: boolean = true,
): boolean {
  if (!capabilities || !(key in capabilities)) return defaultValue
  return Boolean(capabilities[key])
}

export function getCapabilityTip(
  capabilities: Record<string, unknown> | null | undefined,
  key: string,
): string {
  if (!capabilities || !(key in capabilities)) return ""
  return String(capabilities[key] || "")
}

// ── Sorting ──

const PRODUCT_ORDER_MAP: Record<string, number> = {
  ANT_CLAUDE_CODE: 0,
  "Ant Claude Code": 0,
  AntCC: 0,
}

function getAgentProductPriority(agentProduct: string) {
  return PRODUCT_ORDER_MAP[agentProduct] ?? Number.MAX_SAFE_INTEGER
}

export function sortItemsByAgentProduct<
  T extends { agent_product: string },
>(items: T[]): T[] {
  return [...items]
    .map((item, index) => ({
      item,
      index,
      priority: getAgentProductPriority(item.agent_product),
    }))
    .sort((a, b) => a.priority - b.priority || a.index - b.index)
    .map(({ item }) => item)
}

// ── Card themes ──

export interface AgentCardTheme {
  /** Pure CSS gradient string for inline style (light mode only, hidden in dark) */
  gradientCss: string
}

function gradient(topRgba: string): AgentCardTheme {
  return {
    gradientCss: `linear-gradient(180deg, ${topRgba} 0%, rgba(255,255,255,1) 60%)`,
  }
}

const MARKET_CARD_THEMES: Record<string, AgentCardTheme> = {
  "Ant Claude Code": gradient("rgba(237,242,255,0.92)"),
  ANT_CLAUDE_CODE: gradient("rgba(237,242,255,0.92)"),
  AntCC: gradient("rgba(237,242,255,0.92)"),
  CodeFuse: gradient("rgba(233,247,242,0.96)"),
  CODEFUSE: gradient("rgba(233,247,242,0.96)"),
  "CodeFuse CLI": gradient("rgba(233,247,242,0.96)"),
  "Claude Code": gradient("rgba(255,241,235,0.96)"),
  CLAUDE_CODE: gradient("rgba(255,241,235,0.96)"),
  HomiClaw: gradient("rgba(247,248,250,0.96)"),
  HOMICLAW: gradient("rgba(247,248,250,0.96)"),
  AntCodex: gradient("rgba(243,237,255,0.96)"),
  ANTCODEX: gradient("rgba(243,237,255,0.96)"),
  Codex: gradient("rgba(237,246,255,0.96)"),
  CODEX: gradient("rgba(237,246,255,0.96)"),
}

const FALLBACK_THEMES: AgentCardTheme[] = [
  gradient("rgba(230,248,246,0.96)"),
  gradient("rgba(255,236,240,0.96)"),
  gradient("rgba(255,247,230,0.96)"),
  gradient("rgba(232,234,255,0.96)"),
  gradient("rgba(230,247,255,0.96)"),
  gradient("rgba(250,235,255,0.96)"),
]

export function getCardThemes(
  agentProducts: string[],
): AgentCardTheme[] {
  let fallbackIdx = 0
  return agentProducts.map((product) => {
    const specific = MARKET_CARD_THEMES[product]
    if (specific) return specific
    return FALLBACK_THEMES[fallbackIdx++ % FALLBACK_THEMES.length]
  })
}

export function getMarketTheme(
  agentProduct: string,
  fallbackIndex?: number,
): AgentCardTheme {
  const specific = MARKET_CARD_THEMES[agentProduct]
  if (specific) return specific
  if (fallbackIndex !== undefined) {
    return FALLBACK_THEMES[fallbackIndex % FALLBACK_THEMES.length]
  }
  return FALLBACK_THEMES[0]
}

// ── Icon helpers ──

export function isImageIcon(
  icon: string | null | undefined,
): boolean {
  return !!icon && /^(https?:\/\/|image\/|\/)/.test(icon)
}

export function getIconFallback(agentProduct: string): string {
  const compact = agentProduct
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("")
  return compact || "AI"
}
