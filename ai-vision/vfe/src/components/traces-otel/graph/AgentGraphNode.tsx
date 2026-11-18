import { memo, useRef, useEffect, useState } from "react"
import { Handle, Position, type NodeProps } from "@xyflow/react"
import { useTranslation } from "react-i18next"

import {
  decisionOutcome,
  decisionTypeLabel,
} from "@/components/labs/decision/decisionText"

import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import type { GraphNode } from "./graph-utils"
import { formatDurationMs } from "../format"
import { ErrorBadge, ModelBadge, TraceBadge } from "../shared/TraceBadge"
import {
  PowerContextSpanIcon,
  POWER_CONTEXT_SPAN_COLOR,
} from "@/labs/memory/powerContextSpanIcons"

const TYPE_COLORS: Record<string, string> = {
  Agent: "#a855f7",
  Tool: "#ef4444",
  MCP: "#ef4444",
  Chain: "#6366f1",
  Retriever: "#06b6d4",
  Event: "#f59e0b",
  Workflow: "#06b6d4",
  Task: "#f59e0b",
  LLM: "#6366f1",
  Embedding: "#06b6d4",
  Span: "#94a3b8",
  Skill: "#f59e0b",
}

function getNodeColor(type: string): string {
  return TYPE_COLORS[type] ?? "#94a3b8"
}

function AgentGraphNodeComponent({ data, selected }: NodeProps) {
  const { t } = useTranslation()
  const node = data as unknown as GraphNode
  const decision = node.decisions?.[0]
  const title = decision
    ? decisionTypeLabel(decision.type, t)
    : node.powerContext
      ? t(node.powerContext.labelKey)
      : node.labelKey
        ? t(node.labelKey)
        : node.name || node.id.slice(0, 8)
  const originalName =
    !decision && (node.powerContext || node.labelKey) && node.name
      ? t("modelSpan.originalName", { name: node.name })
      : undefined
  const outcome = node.decisions
    ?.map((info) => decisionOutcome(info, t))
    .filter((value) => value !== "-")
    .join(" · ")
  const modelOutcome = node.decisions?.every(
    (info) => info.type === "model_routing" && info.action !== "error"
  )
  const outcomeTone = node.decisions?.some((info) => info.action === "fallback")
    ? "warning"
    : "default"
  const color = decision
    ? "var(--color-labs, #9335e9)"
    : node.powerContext
      ? POWER_CONTEXT_SPAN_COLOR
      : getNodeColor(
          node.labelKey
            ? node.operation === "embedding"
              ? "Embedding"
              : "LLM"
            : node.type
        )
  const latencyStr =
    node.latency !== null && node.latency >= 0
      ? formatDurationMs(node.latency * 1000)
      : null
  const hasModelBadge = !!(!decision && node.operation && node.model)
  const usageText = `${t("nativeSpanUsage.total")} ${
    node.tokens === null
      ? t("metrics.framework.unknown")
      : node.tokens.toLocaleString()
  }`

  const nodeRef = useRef<HTMLDivElement>(null)
  const [borderColor, setBorderColor] = useState(color)

  useEffect(() => {
    if (!nodeRef.current) return
    const computedStyle = getComputedStyle(nodeRef.current)
    if (node.level === "ERROR") {
      setBorderColor(
        computedStyle.getPropertyValue("--destructive").trim() || "#ef4444"
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
      data-filter-context={node.filterContext || undefined}
      data-search-active={node.searchActive || undefined}
      className={`rounded-lg bg-card text-card-foreground shadow-sm ${node.searchActive ? "ring-2 ring-ring ring-offset-1" : ""}`}
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
            {node.powerContext && (
              <PowerContextSpanIcon
                info={node.powerContext}
                size={15}
                strokeWidth={1.8}
                className="shrink-0"
                style={{ color }}
              />
            )}
            {(decision || (!node.labelKey && !node.powerContext)) && (
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
                      : decision
                        ? "var(--color-bg-labs-tag)"
                        : `${color}14`,
                }}
              >
                {decision ? decisionTypeLabel("other", t) : node.type}
              </span>
            )}
            <HoverCard openDelay={200} closeDelay={100}>
              <HoverCardTrigger asChild>
                <span
                  className="block min-w-0 flex-1 cursor-default truncate text-xs font-normal text-foreground"
                  title={decision ? node.name : originalName}
                >
                  {title}
                </span>
              </HoverCardTrigger>
              <HoverCardContent side="left" className="w-auto">
                <p className="text-sm">{title}</p>
                {decision ? (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {node.name}
                  </p>
                ) : originalName ? (
                  <p className="mt-1 text-xs break-words text-muted-foreground">
                    {originalName}
                  </p>
                ) : null}
              </HoverCardContent>
            </HoverCard>
            {node.powerContext && (
              <TraceBadge className="shrink-0">
                {t(node.powerContext.layerLabelKey)}
              </TraceBadge>
            )}
            {node.level === "ERROR" && <ErrorBadge />}
            {node.filterContext && (
              <span className="shrink-0 text-[10px] text-muted-foreground">
                {t("nativeSpanFilters.context")}
              </span>
            )}
          </div>

          {hasModelBadge && node.model && (
            <div className="mb-1 flex min-w-0">
              <ModelBadge model={node.model} />
            </div>
          )}
          {outcome ? (
            <div className="mb-1 flex min-w-0">
              {modelOutcome ? (
                <ModelBadge
                  model={outcome}
                  title={outcome}
                  tone={outcomeTone}
                />
              ) : (
                <TraceBadge title={outcome} tone={outcomeTone}>
                  {outcome}
                </TraceBadge>
              )}
            </div>
          ) : null}
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            {latencyStr && <span>{latencyStr}</span>}
            {node.hasUsage && (
              <span className="min-w-0 truncate" title={usageText}>
                {usageText}
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
