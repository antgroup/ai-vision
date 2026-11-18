import { useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import {
  TraceCopyButton,
  TraceTextContent,
} from "@/components/traces/shared/TraceTextContent"
import { TraceRecordControls } from "@/components/traces/shared/TraceRecordControls"
import { SpanTypedContent } from "./SpanTypedContent"
import {
  collectSpanToolDefinitions,
  type SpanToolDefinition,
} from "./spanMessageProjection"
import { resolveReportedInputKind } from "../traces/shared/traceUserInput"
import type { OTelSpan } from "./types"

interface SpanAvailableToolsProps {
  span: OTelSpan
  masked?: boolean
}

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
}

function ToolDefinition({ definition }: { definition: SpanToolDefinition }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [schemaOpen, setSchemaOpen] = useState(false)
  const name =
    definition.name ||
    t("modelContext.definitionFallback", { number: definition.index + 1 })
  const copyValue =
    typeof definition.rawValue === "string"
      ? definition.rawValue
      : JSON.stringify(definition.rawValue, null, 2)
  const value = definition.value
  const fn = record(value) && record(value.function) ? value.function : value
  const description =
    record(fn) && typeof fn.description === "string"
      ? fn.description
      : undefined
  const parameters = record(fn)
    ? record(fn.parameters)
      ? fn.parameters
      : record(fn.input_schema)
        ? fn.input_schema
        : undefined
    : undefined
  const required = Array.isArray(parameters?.required)
    ? parameters.required.filter(
        (name): name is string => typeof name === "string"
      )
    : []
  return (
    <li className="min-w-0 py-2 first:pt-0" data-tool-definition>
      <div className="flex min-w-0 items-start justify-between gap-2">
        <details
          className="min-w-0 flex-1"
          onToggle={(event) => setOpen(event.currentTarget.open)}
        >
          <summary className="w-fit max-w-full cursor-pointer rounded-sm font-mono text-xs [overflow-wrap:anywhere] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
            {name}
          </summary>
          {open && (
            <div className="mt-2 min-w-0 space-y-2">
              {description && (
                <div data-tool-description>
                  <TraceTextContent
                    content={description}
                    previewLines={3}
                    textClassName="font-sans text-xs leading-5"
                  />
                </div>
              )}
              {required.length > 0 && (
                <p
                  data-tool-required-parameters
                  className="text-xs leading-5 [overflow-wrap:anywhere]"
                >
                  <span className="text-muted-foreground">
                    {t("modelContext.requiredParameters")}:{" "}
                  </span>
                  {required.map((name, index) => (
                    <span key={`${index}:${name}`}>
                      {index > 0 && ", "}
                      <code>{name}</code>
                    </span>
                  ))}
                </p>
              )}
              <details
                data-tool-full-definition
                open={schemaOpen}
                onToggle={(event) => setSchemaOpen(event.currentTarget.open)}
              >
                <summary className="w-fit cursor-pointer rounded-sm text-xs text-muted-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
                  {t("modelContext.fullDefinition")}
                </summary>
                {schemaOpen && (
                  <div className="mt-2">
                    <SpanTypedContent
                      value={definition.rawValue}
                      format="json"
                      compact
                      showCopy={false}
                    />
                  </div>
                )}
              </details>
            </div>
          )}
        </details>
        <TraceCopyButton
          value={copyValue ?? ""}
          label={t("modelContext.copyDefinition", { name })}
        />
      </div>
    </li>
  )
}

function AvailableToolsContent({ span }: SpanAvailableToolsProps) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [limit, setLimit] = useState(5)
  const definitions = useMemo(() => {
    const kind = resolveReportedInputKind(span.attributes ?? {})
    return kind === "LLM" || kind === "GENERATION"
      ? collectSpanToolDefinitions(span.attributes ?? {})
      : []
  }, [span])
  if (!definitions.length) return null
  return (
    <details
      className="min-w-0 border-t border-border pt-3 text-xs"
      data-testid="span-available-tools"
      data-available-tool-count={definitions.length}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className="w-fit max-w-full cursor-pointer rounded-sm font-medium [overflow-wrap:anywhere] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
        {t("modelContext.availableTools", { count: definitions.length })}
      </summary>
      {open && (
        <div className="mt-2 min-w-0 space-y-2">
          <p className="max-w-prose leading-5 text-muted-foreground">
            {t("modelContext.availabilityHint")}
          </p>
          <ul className="min-w-0 divide-y divide-border">
            {definitions.slice(0, limit).map((definition) => (
              <ToolDefinition
                key={`${definition.attributeKey}:${definition.index}`}
                definition={definition}
              />
            ))}
          </ul>
          <TraceRecordControls
            shown={Math.min(limit, definitions.length)}
            total={definitions.length}
            onMore={() => setLimit((value) => value + 5)}
            onAll={() => setLimit(definitions.length)}
            onCollapse={() => setLimit(5)}
          />
        </div>
      )}
    </details>
  )
}

/** A declared tool snapshot is model context, never a list of executed tools. */
export function SpanAvailableTools(props: SpanAvailableToolsProps) {
  if (props.masked) return null
  return (
    <AvailableToolsContent
      key={`${props.span.trace_id}/${props.span.span_id}`}
      {...props}
    />
  )
}
