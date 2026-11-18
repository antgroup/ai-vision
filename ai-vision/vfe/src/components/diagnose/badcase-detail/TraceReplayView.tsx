import { useTranslation } from "react-i18next"
import type { TFunction } from "i18next"
import { useMemo, useState } from "react"
import { ChevronRight } from "lucide-react"

import { cn } from "@/lib/utils"

import type {
  TraceGraphStep,
  TraceHistoryTurn,
  TraceProjectionStep,
  TraceRetrievedDoc,
} from "../diagnoseApi"
import { extractContentText, stepRole } from "./traceReplayUtils"

function formatUnknownValue(value: unknown): string {
  if (value === null || value === undefined) return ""
  if (typeof value === "string") return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

/** 通用可折叠列表：每项默认显 label+preview 一行，点开看 detail 段落（滚动窗口）。 */
function FoldableList({
  items,
}: {
  items: {
    label: string
    preview?: string
    detail?: { label: string; text: string }[]
  }[]
}) {
  const { t } = useTranslation()
  const [openIdx, setOpenIdx] = useState<number | null>(null)
  if (!items.length)
    return (
      <p className="text-xs text-muted-foreground">
        {t("diagnose.replay.none")}
      </p>
    )
  return (
    <div className="mt-1 flex flex-col gap-1">
      {items.map((it, j) => {
        const open = openIdx === j
        return (
          <div key={j} className="rounded-md bg-muted/30 px-3 py-1.5">
            <button
              type="button"
              className="flex w-full items-center gap-2 text-left"
              onClick={() => setOpenIdx(open ? null : j)}
            >
              <span className="shrink-0 font-mono text-xs text-foreground">
                {it.label}
              </span>
              {it.preview && !open && (
                <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
                  {it.preview}
                </span>
              )}
            </button>
            {open && it.detail && it.detail.length > 0 && (
              <div className="mt-1.5 flex flex-col gap-1.5">
                {it.detail.map((d, k) => (
                  <div key={k}>
                    <span className="text-xs text-muted-foreground">
                      {d.label}
                    </span>
                    <p className="max-h-60 overflow-y-auto text-xs leading-relaxed break-words whitespace-pre-wrap text-foreground">
                      {d.text}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

/** 多轮历史列表：每轮默认 query/answer 各显一行，点该轮展开全部。 */
function HistoryTurnList({ history }: { history: TraceHistoryTurn[] }) {
  const { t } = useTranslation()
  const [openTurn, setOpenTurn] = useState<number | null>(null)
  if (!history.length)
    return (
      <p className="text-xs text-muted-foreground">
        {t("diagnose.replay.noHistory")}
      </p>
    )
  return (
    <div className="flex flex-col gap-1">
      {history.map((h, i) => {
        const open = openTurn === i
        return (
          <div key={i} className="rounded-md bg-muted/30 px-3 py-1.5">
            <button
              type="button"
              className="flex w-full items-center gap-2 text-left"
              onClick={() => setOpenTurn(open ? null : i)}
            >
              <span className="shrink-0 font-mono text-xs text-muted-foreground">
                {t("diagnose.replay.turnNumber", { number: i + 1 })}
              </span>
              <span
                className={cn(
                  "min-w-0 flex-1 truncate text-xs text-foreground",
                  !open && "truncate"
                )}
              >
                {h.user_query || t("diagnose.replay.noQuestion")}
              </span>
            </button>
            {open && (
              <div className="mt-1.5 flex flex-col gap-1.5 pl-1">
                <div>
                  <span className="text-xs text-muted-foreground">
                    {t("traceLabels.role.user")}
                  </span>
                  <p className="max-h-60 overflow-y-auto text-xs leading-relaxed break-words whitespace-pre-wrap text-foreground">
                    {h.user_query}
                  </p>
                </div>
                <div>
                  <span className="text-xs text-muted-foreground">
                    {t("traceLabels.role.assistant")}
                  </span>
                  <p className="max-h-60 overflow-y-auto text-xs leading-relaxed break-words whitespace-pre-wrap text-foreground">
                    {h.assistant_answer}
                  </p>
                </div>
              </div>
            )}
            {!open && (
              <p className="mt-0.5 truncate text-xs text-muted-foreground">
                {h.assistant_answer || t("diagnose.replay.noAnswer")}
              </p>
            )}
          </div>
        )
      })}
    </div>
  )
}

/** 召回文档列表：每条默认显一行 title，点该条展开完整 content。 */
function RetrievedDocList({ docs }: { docs: TraceRetrievedDoc[] }) {
  const { t } = useTranslation()
  const [openDoc, setOpenDoc] = useState<number | null>(null)
  if (!docs.length)
    return (
      <p className="text-xs text-muted-foreground">
        {t("diagnose.replay.noRetrievedDocuments")}
      </p>
    )
  return (
    <div className="flex flex-col gap-1">
      {docs.map((d, j) => {
        const open = openDoc === j
        return (
          <div key={j} className="rounded-md bg-muted/30 px-3 py-1.5">
            <button
              type="button"
              className="flex w-full items-center gap-2 text-left"
              onClick={() => setOpenDoc(open ? null : j)}
            >
              <span className="shrink-0 font-mono text-xs text-muted-foreground">
                {t("diagnose.replay.documentNumber", { number: j + 1 })}
              </span>
              <span className="min-w-0 flex-1 truncate text-xs font-medium text-foreground">
                {d.title || t("diagnose.replay.noTitle")}
              </span>
              {d.score && (
                <span className="shrink-0 text-xs text-muted-foreground">
                  {t("diagnose.replay.relevance", {
                    score: d.score.value.toFixed(3),
                  })}
                </span>
              )}
            </button>
            {open && (
              <p className="mt-1.5 max-h-80 overflow-y-auto text-xs leading-relaxed break-words whitespace-pre-wrap text-foreground">
                {d.content}
              </p>
            )}
          </div>
        )
      })}
    </div>
  )
}

type ReplayPhase = "request" | "context" | "agent" | "answer"

const REPLAY_PHASES: Array<{ key: ReplayPhase; label: string }> = [
  { key: "request", label: "diagnose.replay.phases.request" },
  { key: "context", label: "diagnose.replay.phases.context" },
  { key: "agent", label: "diagnose.replay.phases.agent" },
  { key: "answer", label: "diagnose.replay.phases.answer" },
]

function compactPreview(value: unknown, limit = 96): string {
  const text = formatUnknownValue(value).replace(/\s+/g, " ").trim()
  if (!text) return ""
  return text.length > limit ? `${text.slice(0, limit)}…` : text
}

function parseReplayJson(value: unknown): unknown {
  if (typeof value !== "string") return value
  const trimmed = value.trim()
  if (!trimmed || (!trimmed.startsWith("{") && !trimmed.startsWith("[")))
    return null
  try {
    return JSON.parse(trimmed)
  } catch {
    return null
  }
}

function replayObject(value: unknown): Record<string, unknown> | null {
  const parsed = parseReplayJson(value)
  return parsed && typeof parsed === "object" && !Array.isArray(parsed)
    ? (parsed as Record<string, unknown>)
    : null
}

function replayArray(value: unknown): unknown[] {
  const parsed = parseReplayJson(value)
  return Array.isArray(parsed) ? parsed : []
}

function replayString(value: unknown): string {
  return typeof value === "string" ? value.trim() : ""
}

function replayBusinessSummary(
  step: TraceProjectionStep,
  t: TFunction
): string {
  const output = replayString(step.output)
  if (!output) return ""

  if (/^arec路由节点_(?:perception|planning)$/.test(step.node)) {
    return t(
      step.node.endsWith("perception")
        ? "diagnose.replay.business.perceptionIntent"
        : "diagnose.replay.business.planningIntent",
      { value: compactPreview(output, 160) }
    )
  }

  if (step.node === "配置信息" && step.kind !== "group") {
    const modules = replayArray(output).filter(
      (item): item is string => typeof item === "string" && !!item.trim()
    )
    if (modules.length)
      return t("diagnose.replay.business.enabledModules", {
        count: modules.length,
      })
    if (output.startsWith("config="))
      return t("diagnose.replay.business.configuration", {
        value: output.slice("config=".length),
      })
  }

  const data = replayObject(output)
  if (!data) return ""

  if (step.node === "健康档案消费") {
    const enabled = replayString(data.chatEnableState)
    if (enabled === "0") return t("diagnose.replay.business.healthUnavailable")
    if (enabled) return t("diagnose.replay.business.healthEnabled")
  }

  if (step.node === "个性化记忆召回") {
    const triggered = replayString(data.personalizationTrace)
    if (triggered === "0")
      return t("diagnose.replay.business.memoryNotTriggered")
    if (triggered) return t("diagnose.replay.business.memoryTriggered")
  }

  if (step.node === "个性化意图") {
    const parts: string[] = []
    const classification = replayString(data.classificationResult)
    const trace = replayString(data.personalizationTrace)
    const selected = replayObject(data.userPeekMember)
    const role = replayString(selected?.role)
    const availableRoles = replayArray(data.availableRoles)
    if (classification)
      parts.push(
        t("diagnose.replay.business.classification", { value: classification })
      )
    else if (trace === "0")
      parts.push(t("diagnose.replay.business.classificationNotTriggered"))
    else if (trace)
      parts.push(t("diagnose.replay.business.classificationTriggered"))
    if (role) parts.push(t("diagnose.replay.business.subject", { value: role }))
    if (availableRoles.length)
      parts.push(
        t("diagnose.replay.business.availableRoles", {
          count: availableRoles.length,
        })
      )
    return parts.join(" · ")
  }

  if (step.node === "服务推荐") {
    const services = Array.isArray(data.finalServiceNames)
      ? data.finalServiceNames.filter(
          (item): item is string => typeof item === "string" && !!item.trim()
        )
      : []
    const fallbackService = replayString(
      data.topServiceName || data.recServiceNames
    )
    const city = replayString(data.cityName)
    const parts = [services.join("、") || fallbackService, city].filter(Boolean)
    if (parts.length)
      return t("diagnose.replay.business.recommendedServices", {
        value: parts.join(" · "),
      })
  }

  if (step.node === "药盒识别-识别") {
    const results = Array.isArray(data["药盒OCR结果"])
      ? data["药盒OCR结果"]
      : []
    return results.length
      ? t("diagnose.replay.business.medicineResults", { count: results.length })
      : t("diagnose.replay.business.noMedicineResults")
  }

  return ""
}

function replayStepName(step: TraceProjectionStep, t: TFunction): string {
  if (step.kind === "group" && step.target === "history")
    return t("diagnose.replay.history")
  if (step.node === "RAG") return t("diagnose.replay.ragRetrieval")
  if (step.node === "Graph") return t("diagnose.replay.graphTitle")
  if (step.node === "EddyGraph") return t("diagnose.replay.graphEntry")
  return step.node
}

function replayStepSummary(
  step: TraceProjectionStep,
  graphSteps: TraceGraphStep[],
  retrieved: TraceRetrievedDoc[],
  history: TraceHistoryTurn[],
  t: TFunction
): string {
  if (step.kind === "group" && step.target === "history") {
    const recent = history.at(-1)?.user_query
    return history.length
      ? [
          t("diagnose.replay.historyCount", { count: history.length }),
          recent
            ? t("diagnose.replay.latestTurn", {
                value: compactPreview(recent, 54),
              })
            : "",
        ]
          .filter(Boolean)
          .join(" · ")
      : t("diagnose.replay.noHistory")
  }
  if (step.node === "RAG") {
    return retrieved.length
      ? t("diagnose.replay.retrievedCount", { count: retrieved.length })
      : t("diagnose.replay.retrievalEmpty")
  }
  if (step.node === "Graph") {
    const toolCalls = graphSteps.reduce(
      (count, item) =>
        count +
        item.semantic_events.filter((event) => event.event_type === "tool_call")
          .length,
      0
    )
    return [
      t("diagnose.replay.nodeCount", { count: graphSteps.length }),
      toolCalls ? t("diagnose.replay.toolCallCount", { count: toolCalls }) : "",
    ]
      .filter(Boolean)
      .join(" · ")
  }
  if (step.node === "EddyGraph") return t("diagnose.replay.graphRuntimeEntry")
  const businessSummary = replayBusinessSummary(step, t)
  if (businessSummary) return businessSummary
  const output = compactPreview(extractContentText(step.output) ?? step.output)
  if (output) return output
  const input = compactPreview(step.input)
  if (input) return t("diagnose.replay.inputSummary", { input })
  if (step.status === "ERROR") return t("diagnose.replay.nodeFailed")
  return t("diagnose.replay.noInputOutput")
}

function replayPhase(step: TraceProjectionStep, index: number): ReplayPhase {
  if (
    /(?:流式回答|medaicore-query结束|服务推荐|推荐问题|输出包装)/i.test(
      step.node
    )
  )
    return "answer"
  if (
    (step.kind === "group" && step.target === "history") ||
    step.node === "RAG" ||
    step.type === "RETRIEVER" ||
    (step.node !== "配置信息" &&
      /(?:记忆|健康档案|上下文|配置)/.test(step.node))
  )
    return "context"
  if (
    stepRole(step) === "answer" ||
    /(?:LLM生成|回答生成|Answer模型LLM结果)/i.test(step.node)
  )
    return "answer"
  if (
    step.node === "Graph" ||
    step.node === "EddyGraph" ||
    ["AGENT", "TOOL", "GENERATION"].includes(step.type)
  )
    return "agent"
  if (
    index === 0 ||
    /(?:请求入口|perception|意图|slot|问题解析|请求解析)/i.test(step.node)
  )
    return "request"
  return "agent"
}

function formatStepLatency(latencyMs: number | null): string {
  if (latencyMs === null) return ""
  if (latencyMs < 1000) return `${latencyMs}ms`
  return `${(latencyMs / 1000).toFixed(1)}s`
}

export function StepList({
  steps,
  graphSteps,
  retrieved,
  history,
  onOpenGraphReplay,
}: {
  steps: TraceProjectionStep[]
  graphSteps?: TraceGraphStep[]
  retrieved?: TraceRetrievedDoc[]
  history?: TraceHistoryTurn[]
  onOpenGraphReplay?: () => void
}) {
  const { t } = useTranslation()
  const groupedSteps = useMemo(() => {
    const phaseRank = new Map(
      REPLAY_PHASES.map((phase, index) => [phase.key, index])
    )
    const groups: Array<{
      key: ReplayPhase
      label: string
      steps: TraceProjectionStep[]
    }> = []
    let currentRank = 0
    for (const [index, step] of steps.entries()) {
      const inferred = replayPhase(step, index)
      currentRank = Math.max(currentRank, phaseRank.get(inferred) ?? 0)
      const phase = REPLAY_PHASES[currentRank]
      const current = groups.at(-1)
      if (current?.key === phase.key) current.steps.push(step)
      else groups.push({ ...phase, steps: [step] })
    }
    return groups
  }, [steps])

  if (!steps.length) return <p className="text-sm text-muted-foreground">—</p>
  return (
    <div>
      <ol className="relative flex flex-col">
        <span
          className="absolute top-8 bottom-2 left-[5px] w-px bg-border"
          aria-hidden
        />
        {groupedSteps.map((phase) => (
          <div key={phase.key} className="contents">
            <div className="relative z-10 mt-2 mb-2 flex items-center gap-2 bg-card py-1 first:mt-0">
              <span className="text-xs font-medium text-foreground">
                {t(phase.label)}
              </span>
              <span className="text-[11px] text-muted-foreground">
                {t("diagnose.replay.stepCount", { count: phase.steps.length })}
              </span>
            </div>
            {phase.steps.map((step) => (
              <StepItem
                key={`${step.node}-${step.index}`}
                step={step}
                graphSteps={graphSteps}
                retrieved={retrieved}
                history={history}
                onOpenGraphReplay={onOpenGraphReplay}
              />
            ))}
          </div>
        ))}
      </ol>
    </div>
  )
}

/** 单个步骤：业务节点点开展开 IO；RAG/EddyGraph/Graph 折叠块点开展示专门变量。 */
function StepItem({
  step,
  graphSteps,
  retrieved,
  history,
  onOpenGraphReplay,
}: {
  step: TraceProjectionStep
  graphSteps?: TraceGraphStep[]
  retrieved?: TraceRetrievedDoc[]
  history?: TraceHistoryTurn[]
  onOpenGraphReplay?: () => void
}) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)
  const isDeviation = step.is_deviation === true
  const isCollapsible = step.kind === "group"
  // 折叠块内容
  const collapsibleContent = (() => {
    if (!isCollapsible) return null
    if (step.target === "history") {
      const hs = history ?? []
      if (!hs.length)
        return (
          <p className="text-xs text-muted-foreground">
            {t("diagnose.replay.noHistory")}
          </p>
        )
      return <HistoryTurnList history={hs} />
    }
    if (step.target === "retrieval") {
      const docs = retrieved ?? []
      if (!docs.length)
        return (
          <p className="text-xs text-muted-foreground">
            {t("diagnose.replay.noRetrievedDocuments")}
          </p>
        )
      return <RetrievedDocList docs={docs} />
    }
    if (step.target === "graph_trajectory") {
      const gss = graphSteps ?? []
      if (!gss.length)
        return (
          <p className="text-xs text-muted-foreground">
            {t("diagnose.replay.noGraphNodes")}
          </p>
        )
      return (
        <FoldableList
          items={gss.map((g) => {
            const output = formatUnknownValue(g.output)
            const input = formatUnknownValue(g.input)
            const route = g.route_to ? `${g.node} → ${g.route_to}` : ""
            return {
              label: g.node,
              preview: (route || output).replace(/\n/g, " ").slice(0, 60),
              detail: [
                route && { label: t("diagnose.replay.route"), text: route },
                input && { label: t("nativeSpanUsage.input"), text: input },
                output && { label: t("nativeSpanUsage.output"), text: output },
              ].filter(Boolean) as { label: string; text: string }[],
            }
          })}
        />
      )
    }
    return null
  })()

  const hasDetail = isCollapsible
    ? !!collapsibleContent
    : Boolean(step.input || step.output)
  const opensGraphReplay =
    step.target === "graph_trajectory" && !!onOpenGraphReplay
  const canExpand = hasDetail || opensGraphReplay
  const summary = replayStepSummary(
    step,
    graphSteps ?? [],
    retrieved ?? [],
    history ?? [],
    t
  )
  const latency = formatStepLatency(step.latency_ms ?? null)
  const deviationLabels = Array.from(
    new Set(
      step.deviation_labels?.length
        ? step.deviation_labels
        : step.deviation_description
          ? [step.deviation_description]
          : []
    )
  )
  const displayIndex =
    step.index === null || step.index === undefined ? "—" : step.index + 1
  return (
    <li className="relative flex gap-3 pb-4 last:pb-0">
      <span className="relative z-10 mt-1 shrink-0">
        {isDeviation ? (
          <span className="block size-[11px] rounded-full bg-destructive ring-4 ring-destructive/10" />
        ) : (
          <span className="block size-[11px] rounded-full border-2 border-border bg-background" />
        )}
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="flex min-w-0 items-center gap-2">
          <span className="w-4 shrink-0 font-mono text-xs text-muted-foreground tabular-nums">
            {displayIndex}
          </span>
          <span className="min-w-0 truncate font-mono text-sm text-foreground">
            {replayStepName(step, t)}
          </span>
          {isDeviation && deviationLabels.length ? (
            <span className="flex shrink-0 gap-1">
              {deviationLabels.map((label) => (
                <span
                  key={label}
                  className="shrink-0 rounded bg-destructive/10 px-1 text-[10px] leading-[18px] font-medium text-destructive"
                >
                  {label}
                </span>
              ))}
            </span>
          ) : null}
          {isCollapsible && step.child_count ? (
            <span className="shrink-0 text-xs text-muted-foreground">
              ({step.child_count})
            </span>
          ) : null}
          <span className="min-w-0 flex-1" />
          {latency && (
            <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
              {latency}
            </span>
          )}
          {canExpand && (
            <button
              type="button"
              className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
              onClick={() => {
                if (opensGraphReplay) {
                  onOpenGraphReplay?.()
                  return
                }
                setExpanded((v) => !v)
              }}
            >
              {opensGraphReplay
                ? t("diagnose.replay.openGraph")
                : expanded
                  ? t("common.collapse")
                  : t("common.showMore")}
              <ChevronRight
                className={cn("size-3.5", expanded && "rotate-90")}
              />
            </button>
          )}
        </div>
        {summary ? (
          <span className="pl-6 text-xs leading-relaxed text-muted-foreground">
            {summary}
          </span>
        ) : null}
        {expanded && hasDetail ? (
          <div className="border-l border-border pl-3">
            {collapsibleContent ?? (
              <div className="flex flex-col gap-2">
                {formatUnknownValue(step.input) && (
                  <div>
                    <span className="text-xs text-muted-foreground">
                      {t("nativeSpanUsage.input")}
                    </span>
                    <p className="max-h-80 overflow-y-auto text-xs leading-relaxed break-words whitespace-pre-wrap text-foreground">
                      {formatUnknownValue(step.input)}
                    </p>
                  </div>
                )}
                {formatUnknownValue(step.output) && (
                  <div>
                    <span className="text-xs text-muted-foreground">
                      {t("nativeSpanUsage.output")}
                    </span>
                    <p className="max-h-80 overflow-y-auto text-xs leading-relaxed break-words whitespace-pre-wrap text-foreground">
                      {formatUnknownValue(step.output)}
                    </p>
                  </div>
                )}
              </div>
            )}
          </div>
        ) : null}
      </div>
    </li>
  )
}
