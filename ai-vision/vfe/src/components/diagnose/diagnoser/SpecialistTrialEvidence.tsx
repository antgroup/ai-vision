import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import type { MetricDTO } from "../types"
import { structuredOutputFieldTypes } from "../specialistPolicy"
import type { StructuredOutputField } from "../specialistPolicy"

interface SourceReference {
  traceId: string
  spanId: string | null
  fieldPath: string
  callId: string | null
}
interface SourceLocation {
  source: SourceReference
  start: number
  end: number
}
interface NumericCheck {
  id: string
  expected: string
  actual: string
  expression: string
  unitMatches: boolean
  consistent: boolean
  expressionLocation: SourceLocation
  resultLocation: SourceLocation
}
interface ProtectedMatch {
  id: string
  count: number
  location: SourceLocation
}
interface Citation {
  sourceId: string
  label: "request" | "final" | "history" | "languagePolicy"
  historyIndex?: number
  quote: string
}
type OpenSource = (traceId: string, spanId?: string) => void
type StructuredIssue =
  | { code: "invalid_json" | "not_object" }
  | {
      code: "missing_field" | "invalid_type"
      field: string
      expectedType: StructuredOutputField["type"] | undefined
    }

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}
function text(value: unknown, max: number): value is string {
  // Match the API's Python string length: an astral character is one codepoint.
  return (
    typeof value === "string" &&
    value.length > 0 &&
    Array.from(value).length <= max
  )
}
function offset(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0
}
function identifier(value: unknown): value is string {
  return text(value, 32) && /^[a-z][a-z0-9_]*$/.test(value)
}
function decimal(value: unknown): value is string {
  return text(value, 256) && /^[+-]?\d+(?:\.\d+)?$/.test(value)
}
function source(value: unknown): SourceReference | null {
  if (
    !record(value) ||
    !text(value.trace_id, 128) ||
    !text(value.field_path, 1024) ||
    !(value.span_id === null || text(value.span_id, 256)) ||
    !(
      value.call_id === null ||
      value.call_id === undefined ||
      text(value.call_id, 256)
    )
  )
    return null
  return {
    traceId: value.trace_id,
    spanId: value.span_id,
    fieldPath: value.field_path,
    callId: value.call_id ?? null,
  }
}
function location(value: unknown): SourceLocation | null {
  if (
    !record(value) ||
    !offset(value.start) ||
    !offset(value.end) ||
    value.end <= value.start
  )
    return null
  const reference = source(value.source)
  return reference
    ? { source: reference, start: value.start, end: value.end }
    : null
}
function numeric(value: unknown): NumericCheck | null {
  if (
    !record(value) ||
    !identifier(value.check_id) ||
    !decimal(value.expected) ||
    !decimal(value.actual) ||
    !text(value.expression, 128) ||
    typeof value.unit_matches !== "boolean" ||
    typeof value.consistent !== "boolean"
  )
    return null
  const expressionLocation = location(value.expression_location)
  const resultLocation = location(value.result_location)
  if (!expressionLocation || !resultLocation) return null
  return {
    id: value.check_id,
    expected: value.expected,
    actual: value.actual,
    expression: value.expression,
    unitMatches: value.unit_matches,
    consistent: value.consistent,
    expressionLocation,
    resultLocation,
  }
}
function protectedMatch(value: unknown): ProtectedMatch | null {
  if (
    !record(value) ||
    !identifier(value.marker_id) ||
    !offset(value.count) ||
    value.count === 0 ||
    !record(value.first_offset)
  )
    return null
  const matchLocation = location({
    source: value.source,
    start: value.first_offset.start,
    end: value.first_offset.end,
  })
  return matchLocation
    ? { id: value.marker_id, count: value.count, location: matchLocation }
    : null
}
function citation(value: unknown): Citation | null {
  if (!record(value) || !text(value.quote, 512)) return null
  if (value.source_id === "request" || value.source_id === "final")
    return {
      sourceId: value.source_id,
      label: value.source_id,
      quote: value.quote,
    }
  if (value.source_id === "policy:language")
    return {
      sourceId: value.source_id,
      label: "languagePolicy",
      quote: value.quote,
    }
  if (
    typeof value.source_id === "string" &&
    /^history:(?:[0-9]|[1-3][0-9])$/.test(value.source_id)
  )
    return {
      sourceId: value.source_id,
      label: "history",
      historyIndex: Number(value.source_id.slice(8)) + 1,
      quote: value.quote,
    }
  return null
}
function knownItems<T>(
  values: unknown,
  parse: (value: unknown) => T | null
): T[] {
  return Array.isArray(values)
    ? values.map(parse).filter((value): value is T => value !== null)
    : []
}

function structuredIssue(value: unknown): StructuredIssue | null {
  if (!record(value)) return null
  if (value.code === "invalid_json" || value.code === "not_object")
    return { code: value.code }
  if (
    (value.code !== "missing_field" && value.code !== "invalid_type") ||
    typeof value.field !== "string" ||
    !/^[A-Za-z_][A-Za-z0-9_-]{0,63}$/.test(value.field)
  )
    return null
  const expectedType = structuredOutputFieldTypes.find(
    (type) => type === value.expected_type
  )
  if (value.code === "invalid_type" && !expectedType) return null
  return { code: value.code, field: value.field, expectedType }
}

function ObservedSource({
  title,
  reference,
  location,
  onOpenSource,
}: {
  title: string
  reference: SourceReference
  location?: SourceLocation
  onOpenSource?: OpenSource
}) {
  const { t } = useTranslation()
  const values = [
    ["traceId", reference.traceId],
    ["spanId", reference.spanId ?? t("diagnose.specialist.evidence.noSpanId")],
    ["fieldPath", reference.fieldPath],
    ...(reference.callId ? [["callId", reference.callId]] : []),
    ...(location ? [["offset", `${location.start}–${location.end}`]] : []),
  ]
  return (
    <div className="grid gap-1.5 text-xs">
      <div className="flex flex-wrap items-center justify-between gap-1">
        <p className="font-medium">{title}</p>
        {onOpenSource && (
          <Button
            variant="link"
            size="sm"
            className="h-auto px-0 py-1 text-xs"
            onClick={() =>
              onOpenSource(reference.traceId, reference.spanId ?? undefined)
            }
          >
            {t("diagnose.specialist.evidence.openSource")}
          </Button>
        )}
      </div>
      <dl className="grid gap-1 text-muted-foreground">
        {values.map(([key, value]) => (
          <div
            key={key}
            className="grid gap-0.5 sm:grid-cols-[6rem_minmax(0,1fr)] sm:gap-2"
          >
            <dt>{t(`diagnose.specialist.evidence.${key}`)}</dt>
            <dd className="min-w-0 font-mono break-words text-foreground">
              {value}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

export function SpecialistTrialEvidence({
  metric,
  onOpenSource,
}: {
  metric: MetricDTO
  onOpenSource?: OpenSource
}) {
  const { t } = useTranslation()
  if (!record(metric.evidence)) return null
  const checks =
    metric.name === "numeric_consistency"
      ? knownItems(metric.evidence.checks, numeric)
      : []
  const matches =
    metric.name === "protected_content_exposure"
      ? knownItems(metric.evidence.matches, protectedMatch)
      : []
  const citations = [
    "answer_relevance",
    "followup_reasonableness",
    "response_language",
  ].includes(metric.name)
    ? knownItems(metric.evidence.citations, citation)
    : []
  const structuredSource =
    metric.name === "structured_output_contract"
      ? source(metric.evidence.source)
      : null
  const structuredIssues = structuredSource
    ? knownItems(metric.evidence.issues, structuredIssue)
    : []
  const toolName =
    metric.name === "required_tool_execution" &&
    metric.evidence.status === "success" &&
    typeof metric.evidence.tool_name === "string" &&
    /^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$/.test(metric.evidence.tool_name)
      ? metric.evidence.tool_name
      : null
  const toolSource = toolName ? source(metric.evidence.source) : null
  if (
    !checks.length &&
    !matches.length &&
    !citations.length &&
    !structuredSource &&
    !toolSource
  )
    return null
  return (
    <section
      aria-label={t("diagnose.specialist.evidence.title")}
      className="grid gap-3 text-xs"
    >
      <p className="font-medium">{t("diagnose.specialist.evidence.title")}</p>
      {checks.map((check, index) => (
        <div
          key={`${check.id}:${index}`}
          className="grid gap-3 rounded-md border bg-background p-3"
        >
          <p className="font-mono text-muted-foreground">{check.id}</p>
          <dl className="grid grid-cols-2 gap-3">
            <div className="min-w-0">
              <dt className="text-muted-foreground">
                {t("diagnose.specialist.evidence.expected")}
              </dt>
              <dd className="mt-1 font-mono text-sm font-medium break-words">
                {check.expected}
              </dd>
            </div>
            <div className="min-w-0">
              <dt className="text-muted-foreground">
                {t("diagnose.specialist.evidence.actual")}
              </dt>
              <dd
                className={`mt-1 font-mono text-sm font-medium break-words ${check.consistent ? "text-foreground" : "text-destructive"}`}
              >
                {check.actual}
              </dd>
            </div>
            <div className="col-span-2 min-w-0">
              <dt className="text-muted-foreground">
                {t("diagnose.specialist.evidence.expression")}
              </dt>
              <dd className="mt-1 font-mono break-words whitespace-pre-wrap">
                {check.expression}
              </dd>
            </div>
          </dl>
          {!check.unitMatches && (
            <p className="text-destructive">
              {t("diagnose.specialist.evidence.unitMismatch")}
            </p>
          )}
          <div className="grid gap-3 border-t pt-3">
            <ObservedSource
              title={t("diagnose.specialist.evidence.expressionSource")}
              reference={check.expressionLocation.source}
              location={check.expressionLocation}
              onOpenSource={onOpenSource}
            />
            <ObservedSource
              title={t("diagnose.specialist.evidence.resultSource")}
              reference={check.resultLocation.source}
              location={check.resultLocation}
              onOpenSource={onOpenSource}
            />
          </div>
        </div>
      ))}
      {matches.map((match, index) => (
        <div
          key={`${match.id}:${index}`}
          className="grid gap-3 rounded-md border bg-background p-3"
        >
          <dl className="grid grid-cols-2 gap-3">
            <div className="min-w-0">
              <dt className="text-muted-foreground">
                {t("diagnose.specialist.evidence.markerId")}
              </dt>
              <dd className="mt-1 font-mono break-words">{match.id}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">
                {t("diagnose.specialist.evidence.count")}
              </dt>
              <dd className="mt-1 font-mono">{match.count}</dd>
            </div>
          </dl>
          <ObservedSource
            title={t("diagnose.specialist.evidence.firstMatchSource")}
            reference={match.location.source}
            location={match.location}
            onOpenSource={onOpenSource}
          />
        </div>
      ))}
      {citations.map((item, index) => (
        <div
          key={`${item.sourceId}:${index}`}
          className="grid gap-2 rounded-md border bg-background p-3"
        >
          <p className="flex flex-wrap items-center gap-2">
            <span className="font-medium">
              {t(`diagnose.specialist.evidence.${item.label}`, {
                count: item.historyIndex,
              })}
            </span>
            <span className="font-mono text-muted-foreground">
              {item.sourceId}
            </span>
          </p>
          <blockquote className="border-l-2 pl-3 text-sm leading-6 break-words whitespace-pre-wrap">
            {item.quote}
          </blockquote>
        </div>
      ))}
      {structuredSource && (
        <div className="grid gap-3 rounded-md border bg-background p-3">
          {structuredIssues.length > 0 && (
            <ul className="grid gap-1.5">
              {structuredIssues.map((issue, index) => (
                <li
                  key={`${issue.code}:${index}`}
                  className="break-words text-destructive"
                >
                  {"field" in issue && <code>{issue.field} · </code>}
                  {t(`diagnose.specialist.evidence.jsonIssues.${issue.code}`, {
                    type:
                      "expectedType" in issue && issue.expectedType
                        ? t(
                            `diagnose.specialist.fieldTypes.${issue.expectedType}`
                          )
                        : "",
                  })}
                </li>
              ))}
            </ul>
          )}
          <ObservedSource
            title={t("diagnose.specialist.evidence.resultSource")}
            reference={structuredSource}
            onOpenSource={onOpenSource}
          />
        </div>
      )}
      {toolSource && (
        <div className="grid gap-3 rounded-md border bg-background p-3">
          <p>
            {t("diagnose.specialist.evidence.toolSucceeded")}{" "}
            <code className="break-all">{toolName}</code>
          </p>
          <ObservedSource
            title={t("diagnose.specialist.evidence.toolResultSource")}
            reference={toolSource}
            onOpenSource={onOpenSource}
          />
        </div>
      )}
    </section>
  )
}
