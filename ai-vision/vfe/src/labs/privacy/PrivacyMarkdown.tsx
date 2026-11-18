import { createElement, type ComponentProps, type ReactNode } from "react"
import ReactMarkdown from "react-markdown"
import { PrivacyMarkedChildren } from "./PrivacyMarkedText"

// Stable renderer types preserve child state, including explicit media consent,
// across format changes and parent re-renders.
const textElements = Object.fromEntries(
  [
    "p",
    "li",
    "td",
    "th",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
    "code",
  ].map((tag) => [
    tag,
    ({
      children,
      node: _node,
      ...attributes
    }: {
      children?: ReactNode
      node?: unknown
    }) => {
      void _node
      return createElement(
        tag,
        attributes,
        <PrivacyMarkedChildren>{children}</PrivacyMarkedChildren>
      )
    },
  ])
)

/** Preserve Markdown semantics while highlighting received placeholders in text. */
export function PrivacyMarkdown({
  components,
  ...props
}: ComponentProps<typeof ReactMarkdown>) {
  return (
    <ReactMarkdown {...props} components={{ ...textElements, ...components }} />
  )
}
