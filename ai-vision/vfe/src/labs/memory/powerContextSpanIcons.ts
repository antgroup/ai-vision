import { createElement } from "react"
import {
  Archive,
  Brain,
  Globe,
  ListPlus,
  Lock,
  Search,
  Waypoints,
  type LucideIcon,
  type LucideProps,
} from "lucide-react"
import type { PowerContextSpanInfo } from "./powerContextSpans"

// A span-family color, independent of the recorded operation outcome.
export const POWER_CONTEXT_SPAN_COLOR = "var(--chart-2)"

const OPERATION_ICONS: Record<PowerContextSpanInfo["iconKind"], LucideIcon> = {
  recall: Brain,
  binding: Waypoints,
  capture: Archive,
  search: Search,
  context: ListPlus,
  lock: Lock,
}

export function getPowerContextSpanIcon(
  info: Pick<PowerContextSpanInfo, "iconKind" | "layer">
): LucideIcon {
  return info.layer === "http" ? Globe : OPERATION_ICONS[info.iconKind]
}

export function PowerContextSpanIcon({
  info,
  ...props
}: LucideProps & { info: Pick<PowerContextSpanInfo, "iconKind" | "layer"> }) {
  return createElement(getPowerContextSpanIcon(info), props)
}
