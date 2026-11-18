import {
  Children,
  cloneElement,
  Fragment,
  isValidElement,
  type ReactNode,
} from "react"
import { useTranslation } from "react-i18next"
import { privacyMarkerSegments } from "./privacyMarkers"

/** Presentation only: received placeholders remain unchanged in text and copy. */
export function PrivacyMarkedText({ text }: { text: string }) {
  const { t } = useTranslation()
  return privacyMarkerSegments(text).map((part, index) =>
    part.marked ? (
      <mark
        key={index}
        data-privacy-marker
        title={t("labs.privacy.markerTitle")}
        className="rounded-sm bg-amber-100 px-0.5 text-amber-950 ring-1 ring-amber-300 ring-inset dark:bg-amber-400/15 dark:text-amber-200 dark:ring-amber-400/30"
      >
        {part.text}
      </mark>
    ) : (
      <Fragment key={index}>{part.text}</Fragment>
    )
  )
}

/** Mark text inside existing Markdown elements without changing their structure. */
export function PrivacyMarkedChildren({
  children,
}: {
  children?: ReactNode
}): ReactNode {
  return Children.map(children, (child) => {
    if (typeof child === "string") return <PrivacyMarkedText text={child} />
    if (
      isValidElement(child) &&
      (child.type === PrivacyMarkedChildren || child.type === PrivacyMarkedText)
    )
      return child
    if (
      isValidElement<{ children?: ReactNode }>(child) &&
      child.props.children !== undefined
    )
      return cloneElement(
        child,
        {},
        <PrivacyMarkedChildren>{child.props.children}</PrivacyMarkedChildren>
      )
    return child
  })
}
