/**
 * 诊断器维度色板 — 复用 Agent 市场卡片的渐变淡底风格。
 * 颜色来源：agentProducts.ts 的 MARKET_CARD_THEMES gradient 体系。
 *
 * 4 维度固定映射：
 *   outcome  结果质量 → 蓝调
 *   process  过程质量 → 青绿调
 *   safety   安全合规 → 红调
 *   system   系统性能 → 橙调
 */

export type DimensionKey = "outcome" | "process" | "safety" | "system"

interface DimensionTheme {
  /** 卡片顶部淡底（inline style gradient，同 AgentCard） */
  gradientCss: string
  /** 引擎标签 badge 背景 */
  badgeBg: string
  /** 引擎标签 badge 文字 */
  badgeText: string
}

const DIMENSION_THEMES: Record<DimensionKey, DimensionTheme> = {
  outcome: {
    gradientCss: "linear-gradient(180deg, rgba(237,242,255,0.92) 0%, transparent 100%)",
    badgeBg: "bg-[rgba(237,242,255,0.8)]",
    badgeText: "text-blue-600 dark:text-blue-400",
  },
  process: {
    gradientCss: "linear-gradient(180deg, rgba(233,247,242,0.96) 0%, transparent 100%)",
    badgeBg: "bg-[rgba(233,247,242,0.8)]",
    badgeText: "text-emerald-600 dark:text-emerald-400",
  },
  safety: {
    gradientCss: "linear-gradient(180deg, rgba(255,236,240,0.96) 0%, transparent 100%)",
    badgeBg: "bg-[rgba(255,236,240,0.8)]",
    badgeText: "text-red-600 dark:text-red-400",
  },
  system: {
    gradientCss: "linear-gradient(180deg, rgba(255,247,230,0.96) 0%, transparent 100%)",
    badgeBg: "bg-[rgba(255,247,230,0.8)]",
    badgeText: "text-amber-600 dark:text-amber-400",
  },
}

const FALLBACK: DimensionTheme = DIMENSION_THEMES.outcome

export function getDimensionTheme(category: string | undefined): DimensionTheme {
  return DIMENSION_THEMES[category as DimensionKey] ?? FALLBACK
}
