import { useMemo } from "react"
import { useTranslation } from "react-i18next"
import type { LangfuseTraceDetail } from "@/agents/types/langfuse"
import { buildTraceProcess } from "@/components/traces-otel/traceProcess"
import { TraceProcessView } from "@/components/traces-otel/TraceProcessView"
import { observationProcessSpans } from "./observationProcessAdapter"

/** The same single-Trace reading projection for both Observation detail routes. */
export function ObservationTraceProcess({
  trace,
  masked = false,
  onSelectObservation,
}: {
  trace: LangfuseTraceDetail
  masked?: boolean
  onSelectObservation: (id: string) => void
}) {
  const { t } = useTranslation()
  const model = useMemo(
    () => (masked ? null : buildTraceProcess(observationProcessSpans(trace))),
    [trace, masked]
  )
  if (!model) {
    return (
      <p className="text-sm text-muted-foreground" data-process-masked>
        {t("traceProcess.masked")}
      </p>
    )
  }
  return <TraceProcessView model={model} onSelectSpan={onSelectObservation} />
}
