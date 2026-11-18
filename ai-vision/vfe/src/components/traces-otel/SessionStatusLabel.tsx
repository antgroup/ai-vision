import { useTranslation } from "react-i18next"

import { LabsMetricLabel } from "@/components/labs/shared/LabsMetricLabel"
import type { NativeSessionListItem } from "./types"

type SessionStatus = "error" | "ok" | "unset" | "unknown"

function verifiedCounts(session: NativeSessionListItem, status: SessionStatus) {
  const counts = session.trace_status_counts
  const total = session.trace_count
  if (
    !counts ||
    !Number.isSafeInteger(total) ||
    total < 1 ||
    ![counts.unset, counts.ok, counts.error].every(
      (value) => Number.isSafeInteger(value) && value >= 0
    ) ||
    counts.unset + counts.ok + counts.error !== total
  )
    return null

  const aggregate =
    counts.error > 0 ? "error" : counts.ok === total ? "ok" : "unset"
  return aggregate === status ? counts : null
}

/** Session state is evidence from complete-window Trace roots, not loaded children. */
export function SessionStatusLabel({
  session,
}: {
  session: NativeSessionListItem
}) {
  const { t } = useTranslation()
  const value: unknown = session.session_status
  const notReported =
    value === undefined ||
    value === null ||
    (typeof value === "string" && value.trim() === "")
  const status: SessionStatus =
    value === "error" || value === "ok" || value === "unset" ? value : "unknown"
  const counts = verifiedCounts(session, status)
  let labelKey =
    status === "unknown"
      ? notReported
        ? "sessionFilters.statusNotReported"
        : "sessionFilters.statusUnrecognized"
      : `sessionFilters.statusValues.${status}`
  if (status === "unset" && counts) {
    labelKey =
      counts.ok > 0
        ? "sessionFilters.statusSomeUnknown"
        : "sessionFilters.statusAllUnknown"
  } else if (status === "error" && counts) {
    labelKey =
      counts.error === session.trace_count
        ? "sessionFilters.statusAllError"
        : "sessionFilters.statusSomeError"
  }
  const help = [
    t(
      `sessionFilters.statusDescriptions.${status === "unknown" ? (notReported ? "notReported" : "unrecognized") : status}`
    ),
    counts
      ? t("sessionFilters.statusCounts", {
          ...counts,
          total: session.trace_count,
        })
      : null,
  ]
    .filter(Boolean)
    .join("\n\n")

  return (
    <span
      className={
        status === "error" ? "text-destructive" : "text-muted-foreground"
      }
    >
      <LabsMetricLabel label={t(labelKey)} help={help} />
    </span>
  )
}
