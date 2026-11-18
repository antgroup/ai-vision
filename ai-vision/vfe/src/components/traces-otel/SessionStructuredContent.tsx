import { useMemo, type ReactNode } from "react"
import { useTranslation } from "react-i18next"
import { sessionMessages, type SessionMessage } from "./sessionMessages"

interface ContentProps {
  value: unknown
  searchQuery?: string
  renderText: (text: string) => ReactNode
  renderPlainText: (text: string) => ReactNode
}

// These hues identify speakers, never the health or quality of a message.
const ROLE_TONES: Record<string, { label: string; rail: string }> = {
  user: {
    label:
      "bg-blue-500/8 text-blue-700 ring-blue-500/15 dark:bg-blue-400/10 dark:text-blue-300 dark:ring-blue-400/20",
    rail: "border-blue-500/25 dark:border-blue-400/30",
  },
  assistant: {
    label:
      "bg-teal-500/8 text-teal-700 ring-teal-500/15 dark:bg-teal-400/10 dark:text-teal-300 dark:ring-teal-400/20",
    rail: "border-teal-500/25 dark:border-teal-400/30",
  },
  system: {
    label:
      "bg-violet-500/8 text-violet-700 ring-violet-500/15 dark:bg-violet-400/10 dark:text-violet-300 dark:ring-violet-400/20",
    rail: "border-violet-500/25 dark:border-violet-400/30",
  },
  developer: {
    label:
      "bg-violet-500/8 text-violet-700 ring-violet-500/15 dark:bg-violet-400/10 dark:text-violet-300 dark:ring-violet-400/20",
    rail: "border-violet-500/25 dark:border-violet-400/30",
  },
  tool: {
    label:
      "bg-amber-500/8 text-amber-800 ring-amber-500/15 dark:bg-amber-400/10 dark:text-amber-300 dark:ring-amber-400/20",
    rail: "border-amber-500/25 dark:border-amber-400/30",
  },
}
const NEUTRAL_ROLE_TONE = {
  label: "bg-muted/60 text-muted-foreground ring-border/70",
  rail: "border-border/70",
}

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
}

function JsonValue({
  value,
  renderPlainText,
}: Pick<ContentProps, "value" | "renderPlainText">) {
  return (
    <pre
      data-session-json
      className="min-w-0 overflow-x-auto font-mono text-xs leading-5 [overflow-wrap:anywhere] whitespace-pre-wrap"
    >
      <code>{renderPlainText(JSON.stringify(value, null, 2) ?? "")}</code>
    </pre>
  )
}

function Details({
  label,
  value,
  searchQuery,
  renderPlainText,
  children,
}: Pick<ContentProps, "value" | "searchQuery" | "renderPlainText"> & {
  label: ReactNode
  children?: ReactNode
}) {
  const matches = Boolean(
    searchQuery?.trim() &&
    JSON.stringify(value)
      ?.toLocaleLowerCase()
      .includes(searchQuery.trim().toLocaleLowerCase())
  )
  return (
    <details className="min-w-0 text-xs" open={matches}>
      <summary className="w-fit max-w-full cursor-pointer rounded-sm [overflow-wrap:anywhere] text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
        {label}
      </summary>
      <div className="mt-1.5 min-w-0 rounded-md bg-muted/40 p-2.5">
        {children ?? (
          <JsonValue value={value} renderPlainText={renderPlainText} />
        )}
      </div>
    </details>
  )
}

function Fields({
  value,
  ...props
}: Omit<ContentProps, "renderText"> & {
  value: Record<string, unknown>
}) {
  const { t } = useTranslation()
  const count = Object.keys(value).length
  return count ? (
    <Details
      {...props}
      value={value}
      label={t("sessionContent.additionalFields", { count })}
    />
  ) : null
}

function MessageBody({ value, ...props }: ContentProps) {
  if (typeof value === "string") return props.renderText(value)
  if (!Array.isArray(value) || !value.length)
    return <JsonValue value={value} renderPlainText={props.renderPlainText} />
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      {value.map((part, index) => {
        if (typeof part === "string")
          return <div key={index}>{props.renderText(part)}</div>
        if (
          record(part) &&
          ["text", "input_text", "output_text"].includes(String(part.type)) &&
          typeof part.text === "string"
        ) {
          const fields = Object.fromEntries(
            Object.entries(part).filter(
              ([key]) => key !== "text" && key !== "type"
            )
          )
          return (
            <div key={index} className="min-w-0 space-y-1">
              {props.renderText(part.text)}
              <Fields {...props} value={fields} />
            </div>
          )
        }
        return (
          <JsonValue
            key={index}
            value={part}
            renderPlainText={props.renderPlainText}
          />
        )
      })}
    </div>
  )
}

function Message({
  message,
  ...props
}: Omit<ContentProps, "value"> & { message: SessionMessage }) {
  const { t } = useTranslation()
  const { role, content, hasContent, toolCalls, reasoning, name, fields } =
    message
  const roleLabel = [
    "system",
    "developer",
    "user",
    "assistant",
    "tool",
  ].includes(role)
    ? t(`traceLabels.role.${role}`)
    : role
  const tone = Object.hasOwn(ROLE_TONES, role)
    ? ROLE_TONES[role]
    : NEUTRAL_ROLE_TONE
  const auxiliary = toolCalls.length > 0 || reasoning !== null
  return (
    <div
      data-session-message
      className="grid min-w-0 grid-cols-1 gap-2 sm:grid-cols-[6rem_minmax(0,1fr)] sm:gap-3"
    >
      <div className="min-w-0 text-xs">
        <span
          className={`inline-block max-w-full rounded-md px-1.5 py-0.5 font-medium [overflow-wrap:anywhere] ring-1 ring-inset ${tone.label}`}
          title={role}
        >
          {roleLabel}
        </span>
        {name !== null && (
          <span className="mt-1 block font-mono font-normal [overflow-wrap:anywhere] text-muted-foreground">
            {name}
          </span>
        )}
      </div>
      <div
        className={`min-w-0 space-y-1.5 border-l-2 pl-3 text-sm [overflow-wrap:anywhere] text-foreground ${tone.rail}`}
      >
        {hasContent && content !== "" && (
          <MessageBody {...props} value={content} />
        )}
        {hasContent && content === "" && !auxiliary && (
          <span className="text-xs text-muted-foreground italic">
            {t("traceLabels.ioState.empty")}
          </span>
        )}
        {reasoning !== null && (
          <Details
            {...props}
            value={reasoning}
            label={t("traceLabels.thinking")}
          >
            {props.renderText(reasoning)}
          </Details>
        )}
        {toolCalls.map((call, index) => {
          const fn =
            record(call) && record(call.function) ? call.function : null
          const toolName =
            typeof fn?.name === "string"
              ? fn.name
              : record(call) && typeof call.name === "string"
                ? call.name
                : null
          return (
            <Details
              key={index}
              {...props}
              value={call}
              label={
                <>
                  {t("traceLabels.toolCall")}
                  {toolName && (
                    <span className="ml-1.5 font-mono text-foreground">
                      {toolName}
                    </span>
                  )}
                </>
              }
            />
          )
        })}
        <Fields {...props} value={fields} />
      </div>
    </div>
  )
}

/** Presentation only: raw JSON, copying/export and source values stay intact. */
export function SessionStructuredContent({
  value,
  viewMode = "messages",
  ...props
}: ContentProps & { viewMode?: "messages" | "json" }) {
  const conversation = useMemo(() => sessionMessages(value), [value])
  if (!conversation)
    return <JsonValue value={value} renderPlainText={props.renderPlainText} />

  return (
    <div className="min-w-0" data-session-structured-content>
      {viewMode === "json" ? (
        <JsonValue value={value} renderPlainText={props.renderPlainText} />
      ) : (
        <div className="min-w-0 space-y-2">
          <div className="space-y-3.5">
            {conversation.messages.map((message, index) => (
              <Message key={index} {...props} message={message} />
            ))}
          </div>
          <Fields {...props} value={conversation.fields} />
        </div>
      )}
    </div>
  )
}
