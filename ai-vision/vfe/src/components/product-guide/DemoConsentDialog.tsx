import { useEffect, useId, useRef, useState } from "react"
import {
  CircleDollarSign,
  Database,
  GitBranch,
  Play,
  Plug,
  Puzzle,
} from "lucide-react"
import { useTranslation } from "react-i18next"
import {
  fetchNativeSessionList,
  fetchNativeTraceDetail,
} from "@/components/traces-otel/api"
import { Button } from "@/components/ui/button"
import { DialogContent } from "@/components/dialogs/DialogContent"
import {
  Dialog,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { api, ApiBusinessError, throwOnApiError } from "@/lib/api"
import { buildTraceVerificationReport } from "@/pages/onboarding/traceVerificationReport"
import { getCurrentLocale } from "@/i18n"

export interface DemoReceipt {
  agent_uuid: string
  agent_product: string
  trace_id: string
  span_count: number
  diagnoser_uuid: string
  comparison_trace_id: string
  comparison_span_count: number
  scenarios: {
    session: DemoTraceAnchor & { session_id: string; turns: DemoTraceAnchor[] }
    mcp: DemoTraceAnchor & { span_id: string }
    skill: DemoTraceAnchor & { span_id: string }
    decision: DemoTraceAnchor & { span_id: string }
    memory: DemoTraceAnchor & { span_id: string }
  }
}

interface DemoTraceAnchor {
  trace_id: string
  span_count: number
}

const demoScenarios = [
  { id: "guided", icon: Play },
  { id: "session", icon: CircleDollarSign },
  { id: "mcp", icon: Plug },
  { id: "skill", icon: Puzzle },
  { id: "decision", icon: GitBranch },
  { id: "memory", icon: Database },
] as const

export type DemoScenario = (typeof demoScenarios)[number]["id"]

function isTraceAnchor(
  anchor: DemoTraceAnchor | null | undefined
): anchor is DemoTraceAnchor {
  return (
    !!anchor &&
    /^[0-9a-f]{32}$/.test(anchor.trace_id) &&
    !/^0+$/.test(anchor.trace_id) &&
    Number.isSafeInteger(anchor.span_count) &&
    anchor.span_count > 0
  )
}

async function waitForDemoReady<T>(
  signal: AbortSignal,
  check: () => Promise<T>,
  stage: "read" | "import" = "read"
) {
  // Ingest acceptance precedes visibility in the trace index.
  // Dashboard reads can also occupy query capacity during preparation.
  for (let attempt = 0; attempt < 45; attempt++) {
    if (signal.aborted) throw new Error("Demo preparation was aborted")
    try {
      const result = await check()
      if (result) return result
    } catch (error) {
      if (!(error instanceof ApiBusinessError)) throw error
      const retryable =
        stage === "import"
          ? error.statusCode === 429 && error.code === 3037
          : error.statusCode === 404 ||
            error.statusCode === 429 ||
            error.code === 3019 ||
            error.code === 3035
      if (!retryable) throw error
    }
    await new Promise<void>((resolve) => {
      window.setTimeout(resolve, 1000)
    })
  }
  throw new Error("The demo data did not become readable")
}

function readDemoTrace(
  spaceId: string,
  demo: DemoReceipt,
  anchor: DemoTraceAnchor & { span_id?: string },
  signal: AbortSignal
) {
  return waitForDemoReady(signal, async () => {
    const { data } = throwOnApiError(
      await fetchNativeTraceDetail(spaceId, anchor.trace_id, {
        agentProduct: demo.agent_product,
        agentUuid: demo.agent_uuid,
        detail: "1",
        signal,
      })
    )
    if (
      data?.traceId !== anchor.trace_id ||
      data.spans?.length !== anchor.span_count ||
      (anchor.span_id &&
        !data.spans.some((span) => span.span_id === anchor.span_id)) ||
      !data.spans.every(
        (span) =>
          span.trace_id === anchor.trace_id &&
          /^[0-9a-f]{16}$/.test(span.span_id) &&
          !/^0+$/.test(span.span_id)
      )
    )
      return false

    const report = buildTraceVerificationReport(data, anchor.span_count)
    return (
      report.readState === "complete" &&
      !report.missingParentIds.length &&
      !report.cycleSpanIds.length
    )
  })
}

function readDemoSession(
  spaceId: string,
  demo: DemoReceipt,
  signal: AbortSignal
) {
  const { session_id: sessionId } = demo.scenarios.session
  return waitForDemoReady(signal, async () => {
    const { data } = throwOnApiError(
      await fetchNativeSessionList(
        spaceId,
        {
          agent_product: demo.agent_product,
          agent_uuid: demo.agent_uuid,
          session_id: sessionId,
          session_semantics: "canonical-trace-v1",
          limit: 1,
        },
        signal
      )
    )
    return (
      data?.data.some(
        (item) =>
          item.session_id === sessionId &&
          item.trace_count >= demo.scenarios.session.turns.length
      ) ?? false
    )
  })
}

export function DemoConsentDialog({
  spaceId,
  hasDemoAgent,
  onClose,
  onReady,
}: {
  spaceId: string
  hasDemoAgent: boolean
  onClose: () => void
  onReady: (
    demo: DemoReceipt,
    scenario: DemoScenario,
    signal: AbortSignal
  ) => Promise<void>
}) {
  const { t } = useTranslation()
  const radioGroup = useId()
  const request = useRef<AbortController | null>(null)
  const [scenario, setScenario] = useState<DemoScenario>("guided")
  const [status, setStatus] = useState<"confirm" | "working" | "error">(
    "confirm"
  )
  useEffect(
    () => () => {
      const controller = request.current
      request.current = null
      controller?.abort()
    },
    []
  )

  const confirm = async () => {
    if (request.current) return
    const controller = new AbortController()
    request.current = controller
    const timeout = window.setTimeout(() => controller.abort(), 60_000)
    setStatus("working")
    try {
      const { data } = await waitForDemoReady(
        controller.signal,
        async () =>
          throwOnApiError(
            await api.post<{ code: number; data?: DemoReceipt }>(
              `/spaces/${encodeURIComponent(spaceId)}/onboarding/demo`,
              { consent: true, language: getCurrentLocale() },
              { signal: controller.signal }
            )
          ),
        "import"
      )
      if (
        !data?.agent_uuid ||
        !data.agent_product ||
        !data.diagnoser_uuid ||
        !isTraceAnchor(data) ||
        !isTraceAnchor({
          trace_id: data.comparison_trace_id,
          span_count: data.comparison_span_count,
        }) ||
        data.comparison_trace_id === data.trace_id ||
        !isTraceAnchor(data.scenarios?.session) ||
        typeof data.scenarios.session.session_id !== "string" ||
        !data.scenarios.session.session_id.trim() ||
        !Array.isArray(data.scenarios.session.turns) ||
        data.scenarios.session.turns.length < 3 ||
        !data.scenarios.session.turns.every(isTraceAnchor) ||
        new Set(data.scenarios.session.turns.map((turn) => turn.trace_id))
          .size !== data.scenarios.session.turns.length ||
        !(["mcp", "skill", "decision", "memory"] as const).every((id) => {
          const anchor = data.scenarios?.[id]
          return (
            isTraceAnchor(anchor) &&
            /^[0-9a-f]{16}$/.test(anchor.span_id) &&
            !/^0+$/.test(anchor.span_id)
          )
        })
      )
        throw new Error("Invalid demo receipt")
      const anchors =
        scenario === "guided"
          ? [
              data,
              {
                trace_id: data.comparison_trace_id,
                span_count: data.comparison_span_count,
              },
            ]
          : scenario === "session"
            ? data.scenarios.session.turns
            : [data.scenarios[scenario]]
      // An open dashboard can already occupy the Space's trace query slots.
      for (const anchor of anchors)
        await readDemoTrace(spaceId, data, anchor, controller.signal)
      if (scenario === "session")
        await readDemoSession(spaceId, data, controller.signal)
      if (controller.signal.aborted)
        throw new Error("Demo preparation was aborted")
      await onReady(data, scenario, controller.signal)
      if (controller.signal.aborted)
        throw new Error("Demo preparation was aborted")
    } catch {
      if (request.current === controller) {
        controller.abort()
        setStatus("error")
      }
    } finally {
      window.clearTimeout(timeout)
      if (request.current === controller) request.current = null
    }
  }
  const close = () => {
    const controller = request.current
    request.current = null
    controller?.abort()
    onClose()
  }

  return (
    <Dialog open onOpenChange={(open) => !open && close()}>
      <DialogContent
        size="detail"
        showCloseButton={false}
        className="grid-rows-[auto_minmax(0,1fr)_auto] overflow-hidden [@media(max-height:420px)]:grid-rows-[auto_auto_auto] [@media(max-height:420px)]:overflow-y-auto"
      >
        <DialogHeader>
          <DialogTitle>{t("productGuide.demo.entry")}</DialogTitle>
          <DialogDescription>
            {t(
              hasDemoAgent
                ? "productGuide.demo.reopenDescription"
                : "productGuide.demo.description"
            )}
          </DialogDescription>
        </DialogHeader>
        <div className="min-h-0 overflow-y-auto overscroll-contain p-1">
          <fieldset disabled={status === "working"} className="min-w-0">
            <legend className="mb-2 text-sm font-medium">
              {t("productGuide.demo.selectScenario")}
            </legend>
            <div className="grid gap-2 sm:grid-cols-2">
              {demoScenarios.map(({ id, icon: Icon }) => (
                <label key={id} className="block min-w-0">
                  <input
                    className="peer sr-only"
                    type="radio"
                    name={radioGroup}
                    value={id}
                    checked={scenario === id}
                    aria-labelledby={`${radioGroup}-${id}-title`}
                    aria-describedby={`${radioGroup}-${id}-description`}
                    onChange={() => setScenario(id)}
                  />
                  <span className="flex h-full cursor-pointer items-start gap-3 rounded-lg border p-2.5 text-start peer-checked:border-primary peer-checked:bg-muted peer-focus-visible:ring-[3px] peer-focus-visible:ring-ring/50 peer-disabled:cursor-wait sm:p-3">
                    <Icon
                      className="mt-0.5 size-4 shrink-0"
                      aria-hidden="true"
                    />
                    <span className="min-w-0">
                      <span
                        id={`${radioGroup}-${id}-title`}
                        className="block text-sm font-medium"
                      >
                        {t(`productGuide.demo.scenarios.${id}.title`)}
                      </span>
                      <span
                        id={`${radioGroup}-${id}-description`}
                        className="mt-0.5 block text-xs leading-relaxed text-muted-foreground"
                      >
                        {t(`productGuide.demo.scenarios.${id}.description`)}
                      </span>
                    </span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
        </div>
        <div className="space-y-2">
          {status === "working" && (
            <p role="status" className="text-sm">
              {t("productGuide.demo.working")}
            </p>
          )}
          {status === "error" && (
            <p role="alert" className="text-sm text-destructive">
              {t("productGuide.demo.failed")}
            </p>
          )}
          <DialogFooter className="flex-row flex-wrap justify-end">
            <Button variant="outline" onClick={close}>
              {t("common.cancel")}
            </Button>
            <Button
              disabled={status === "working"}
              onClick={() => void confirm()}
            >
              {t(
                `productGuide.demo.${status === "error" ? "retry" : hasDemoAgent ? "openScene" : "confirm"}`
              )}
            </Button>
          </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  )
}
