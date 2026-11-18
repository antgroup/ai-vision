import { useTranslation } from "react-i18next"
import { memo, useRef, useEffect, useState } from "react"
import { Handle, Position, type NodeProps } from "@xyflow/react"

import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import type { GraphNode } from "./graph-utils"
import { formatLatency } from "../shared/utils"
import { ModelBadge } from "@/components/traces-otel/shared/TraceBadge"

const TYPE_COLORS: Record<string, string> = {
  GENERATION: "#6366f1",
  SPAN: "#06b6d4",
  EVENT: "#f59e0b",
  TOOL: "#ef4444",
  AGENT: "#a855f7",
  CHAIN: "#6366f1",
  RETRIEVER: "#06b6d4",
  EMBEDDING: "#f59e0b",
}

function getNodeColor(type: string): string {
  return TYPE_COLORS[type] ?? "#94a3b8"
}

function AgentGraphNodeComponent({ data, selected }: NodeProps) {
  const { t } = useTranslation()
  const node = data as unknown as GraphNode
  const presentation = node.presentation
  const title = presentation.labelKey ? t(presentation.labelKey) : node.name
  const originalName =
    presentation.labelKey && node.originalName
      ? t("modelSpan.originalName", { name: node.originalName })
      : undefined
  const hasModelBadge = !!(presentation.operation && presentation.model)
  const color = getNodeColor(node.type)
  const latencyStr =
    node.latency !== null && node.latency >= 0
      ? formatLatency(node.latency)
      : null
  const tokens = node.usage
    ? (node.usage.input ?? 0) + (node.usage.output ?? 0)
    : 0

  const nodeRef = useRef<HTMLDivElement>(null)
  const [borderColor, setBorderColor] = useState(color)

  useEffect(() => {
    if (!nodeRef.current) return
    const computedStyle = getComputedStyle(nodeRef.current)
    if (node.level === "ERROR") {
      setBorderColor(
        computedStyle.getPropertyValue("--destructive").trim() || "#ef4444"
      )
    } else if (node.level === "WARNING") {
      setBorderColor(
        computedStyle.getPropertyValue("--chart-3").trim() || "#f59e0b"
      )
    } else if (selected) {
      setBorderColor(
        computedStyle.getPropertyValue("--primary").trim() || color
      )
    } else {
      setBorderColor(
        computedStyle.getPropertyValue("--border").trim() || "#e2e8f0"
      )
    }
  }, [node.level, selected, color])

  return (
    <div
      ref={nodeRef}
      className="rounded-lg bg-card text-card-foreground shadow-sm"
      style={{
        border: `${selected ? 2 : 1}px solid ${borderColor}`,
        width: 220,
        overflow: "hidden",
      }}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="!size-1 !border-none !bg-transparent"
      />

      <div className="flex">
        <div className="w-1 shrink-0" style={{ background: color }} />

        <div
          className={`min-w-0 flex-1 px-3 ${hasModelBadge ? "py-1" : "py-2"}`}
        >
          <div className="mb-1 flex items-center gap-1.5">
            {!presentation.labelKey && (
              <span
                className="shrink-0 rounded px-1.5 text-xs font-normal"
                style={{
                  color:
                    node.level === "ERROR"
                      ? "var(--color-error, #EE4444)"
                      : color,
                  backgroundColor:
                    node.level === "ERROR"
                      ? "var(--color-bg-error, rgba(238,68,68,0.1))"
                      : `${color}14`,
                }}
              >
                {node.type}
              </span>
            )}
            <HoverCard openDelay={200} closeDelay={100}>
              <HoverCardTrigger asChild>
                <span
                  title={originalName}
                  className="block min-w-0 flex-1 cursor-default truncate text-xs font-normal text-foreground"
                >
                  {title}
                </span>
              </HoverCardTrigger>
              <HoverCardContent side="left" className="w-auto">
                <p className="text-sm">{title}</p>
                {originalName && (
                  <p className="mt-1 text-xs break-words text-muted-foreground">
                    {originalName}
                  </p>
                )}
              </HoverCardContent>
            </HoverCard>
            {node.level === "ERROR" && (
              <span
                className="shrink-0 rounded text-[10px] font-bold"
                style={{
                  color: "var(--color-error, #EE4444)",
                  backgroundColor: "var(--color-bg-error, rgba(238,68,68,0.1))",
                  padding: "1px 6px",
                }}
              >
                {t("traceLabels.error")}
              </span>
            )}
          </div>

          {hasModelBadge && presentation.model && (
            <div className="mb-1 flex min-w-0">
              <ModelBadge model={presentation.model} />
            </div>
          )}
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            {latencyStr && <span>{latencyStr}</span>}
            {tokens > 0 && (
              <span>
                {tokens} {t("traceLabels.tokenUnit")}
              </span>
            )}
          </div>
        </div>
      </div>

      <Handle
        type="source"
        position={Position.Bottom}
        className="!size-1 !border-none !bg-transparent"
      />
    </div>
  )
}

export const AgentGraphNode = memo(AgentGraphNodeComponent)
