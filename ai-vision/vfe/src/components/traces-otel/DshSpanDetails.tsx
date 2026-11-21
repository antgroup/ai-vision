import { useTranslation } from "react-i18next"
import { TraceCopyButton } from "@/components/traces/shared/TraceTextContent"
import { formatDurationMs } from "./format"
import type { OTelSpan } from "./types"

function reportedNumber(value: unknown): number | null {
  const number =
    typeof value === "number"
      ? value
      : typeof value === "string" && value.trim()
        ? Number(value)
        : NaN
  return Number.isFinite(number) && number >= 0 ? number : null
}

function reportedCount(value: unknown): number | null {
  const number = reportedNumber(value)
  return number !== null && Number.isSafeInteger(number) ? number : null
}

function reportedId(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null
}

function formatReportedDuration(ms: number): string {
  return ms === 0 ? "0ms" : formatDurationMs(ms)
}

export function DshSpanDetails({ span }: { span: OTelSpan }) {
  const { t } = useTranslation()
  const resource = span.resource ?? {}
  // The authenticated collector owns this identity; SDK tags can be customized.
  const trustedProduct = resource["aivision.agent.product"]
  const product =
    trustedProduct === undefined
      ? resource["ant.agent.product"]
      : trustedProduct
  if (
    product !== "DeepSeekHarness" &&
    (product !== undefined || resource["ant.agent.type"] !== "dsh")
  ) {
    return null
  }

  const attrs = span.attributes ?? {}
  const session =
    reportedId(attrs["gen_ai.session.id"]) ??
    reportedId(attrs["gen_ai.conversation.id"])
  const turn = reportedCount(attrs["agentic.turn.number"])
  const step = reportedCount(attrs["agentic.step.number"])
  const position = [
    turn !== null ? t("dshSpanDetails.turn", { number: turn }) : null,
    step !== null ? t("dshSpanDetails.step", { number: step }) : null,
  ]
    .filter(Boolean)
    .join(" · ")
  const firstToken = reportedNumber(
    attrs["gen_ai.response.time_to_first_token"]
  )
  const generation = reportedNumber(attrs["agentic.llm.generation.duration_ms"])
  const retries = reportedCount(attrs["agentic.llm.retry_count"])

  if (
    !session &&
    !position &&
    firstToken === null &&
    generation === null &&
    retries === null
  ) {
    return null
  }

  return (
    <section
      aria-label={t("dshSpanDetails.title")}
      data-testid="dsh-span-details"
      className="rounded-md border border-border bg-muted/30 px-3 py-2"
    >
      <dl className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)] items-center gap-x-3 gap-y-1 text-xs">
        {position && (
          <>
            <dt className="text-muted-foreground">
              {t("dshSpanDetails.title")}
            </dt>
            <dd>{position}</dd>
          </>
        )}
        {session && (
          <>
            <dt className="text-muted-foreground">
              {t("dshSpanDetails.session")}
            </dt>
            <dd className="flex min-w-0 items-center gap-1">
              <span className="font-mono [overflow-wrap:anywhere]">
                {session}
              </span>
              <TraceCopyButton
                value={session}
                label={t("dshSpanDetails.copySession")}
              />
            </dd>
          </>
        )}
        {firstToken !== null && (
          <>
            <dt className="text-muted-foreground">
              {t("dshSpanDetails.firstToken")}
            </dt>
            <dd className="font-mono">{formatReportedDuration(firstToken)}</dd>
          </>
        )}
        {generation !== null && (
          <>
            <dt className="text-muted-foreground">
              {t("dshSpanDetails.generation")}
            </dt>
            <dd className="font-mono">{formatReportedDuration(generation)}</dd>
          </>
        )}
        {retries !== null && (
          <>
            <dt className="text-muted-foreground">
              {t("dshSpanDetails.retries")}
            </dt>
            <dd className="font-mono">{retries}</dd>
          </>
        )}
      </dl>
    </section>
  )
}
