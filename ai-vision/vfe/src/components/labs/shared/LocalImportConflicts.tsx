import { useId } from "react"
import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { importDialogStyles } from "./importDialogStyles"
import type { LocalImportReview } from "./localImportReview"

const selectClass =
  "h-8 min-w-0 max-w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-page)] px-2 text-xs text-[var(--color-text-primary)] focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--color-labs)] disabled:opacity-50"

export function LocalImportConflicts({
  review,
  disabled,
}: {
  review: LocalImportReview<unknown>
  disabled?: boolean
}) {
  const { t } = useTranslation()
  const id = useId()
  const label = (key: string) => t(`labs.common.importConflicts.${key}`)
  if (review.status === "loading")
    return (
      <p role="status" className="text-xs text-muted-foreground">
        {label("checking")}
      </p>
    )
  if (review.status === "error")
    return (
      <div
        role="alert"
        className="flex flex-wrap items-center gap-2 text-xs text-[var(--color-error)]"
      >
        {label("checkFailed")}
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={disabled}
          onClick={() => void review.refresh()}
        >
          {t("common.retry")}
        </Button>
      </div>
    )
  if (!review.rows.length) return null
  return (
    <ul
      aria-label={label("heading")}
      className={importDialogStyles.previewList}
    >
      {review.rows.map((row, index) => {
        const nameId = `${id}-name-${index}`
        const actionId = `${id}-action-${index}`
        const nameMessageId = `${id}-message-${index}`
        const sourceLabels = row.options.map(({ source, detail }) =>
          detail ? `${source} · ${detail}` : source
        )
        const metadata = [
          row.options.length === 1 ? row.options[0].source : null,
          row.duplicateCount
            ? t("labs.common.importConflicts.duplicates", {
                count: row.duplicateCount,
              })
            : null,
        ]
          .filter(Boolean)
          .join(" · ")
        const nameMessage =
          row.error ?? (row.acceptedName ? undefined : "nameMatchesAgent")
        return (
          <li
            data-import-conflict
            key={row.key}
            className="grid min-w-0 gap-x-4 gap-y-1.5 px-3 py-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)]"
          >
            <div className="flex min-w-0 flex-col justify-center [overflow-wrap:anywhere] sm:min-h-8 sm:self-start">
              <span
                id={nameId}
                className="text-sm font-medium text-[var(--color-text-primary)]"
              >
                {row.name}
              </span>
              {metadata ? (
                <span className="text-xs text-muted-foreground">
                  {metadata}
                </span>
              ) : null}
            </div>
            {row.options.length > 1 || row.existing ? (
              <div className="min-w-0 space-y-1.5">
                <div className="flex min-w-0 flex-wrap items-center gap-1.5 sm:justify-end">
                  {row.options.length > 1 ? (
                    <select
                      aria-label={label("chooseSource")}
                      aria-describedby={nameId}
                      className={`${selectClass} flex-1 basis-36 sm:max-w-64`}
                      title={
                        row.selected === undefined
                          ? undefined
                          : sourceLabels[row.selected]
                      }
                      value={row.selected ?? ""}
                      disabled={disabled}
                      onChange={(event) =>
                        review.chooseSource(
                          row.key,
                          event.target.value
                            ? Number(event.target.value)
                            : undefined
                        )
                      }
                    >
                      <option value="">{label("chooseSource")}</option>
                      {sourceLabels.map((source, sourceIndex) => (
                        <option key={sourceIndex} value={sourceIndex}>
                          {source}
                        </option>
                      ))}
                      {row.existing ? null : (
                        <option value="-1">{label("skip")}</option>
                      )}
                    </select>
                  ) : null}
                  {row.existing ? (
                    <select
                      id={actionId}
                      aria-label={label("action")}
                      aria-describedby={nameId}
                      className={selectClass}
                      value={row.action}
                      disabled={disabled}
                      onChange={(event) =>
                        review.chooseAction(
                          row.key,
                          event.target.value as typeof row.action
                        )
                      }
                    >
                      <option value="keep">{label("keep")}</option>
                      <option value="replace">
                        {label(
                          row.replaceConfirmed ? "willReplace" : "replace"
                        )}
                      </option>
                      <option value="rename">{label("rename")}</option>
                    </select>
                  ) : null}
                  {row.existing &&
                  row.action === "replace" &&
                  !row.replaceConfirmed ? (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      aria-describedby={nameId}
                      className={`${importDialogStyles.compactAction} text-xs`}
                      disabled={disabled || !row.hasSource}
                      onClick={(event) => {
                        const action =
                          event.currentTarget.ownerDocument.getElementById(
                            actionId
                          )
                        review.confirmReplace(row.key)
                        action?.focus()
                      }}
                    >
                      {label("confirmReplace")}
                    </Button>
                  ) : null}
                </div>
                {row.action === "rename" ? (
                  <div className="space-y-1">
                    <div className="flex min-w-0 flex-wrap items-center gap-1.5 sm:justify-end">
                      <Input
                        aria-label={label("newName")}
                        aria-describedby={
                          nameMessage ? `${nameId} ${nameMessageId}` : nameId
                        }
                        aria-invalid={!!row.error}
                        className={`${importDialogStyles.field} h-8 min-w-28 flex-1 text-xs sm:max-w-64 md:text-xs`}
                        value={row.draftName}
                        disabled={disabled || row.checking}
                        onChange={(event) =>
                          review.editName(row.key, event.target.value)
                        }
                      />
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        aria-describedby={nameId}
                        className={`${importDialogStyles.compactAction} text-xs`}
                        disabled={
                          disabled ||
                          row.checking ||
                          !!row.acceptedName ||
                          !row.hasSource
                        }
                        onClick={() => void review.confirmName(row.key)}
                      >
                        {label(
                          row.checking
                            ? "checkingName"
                            : row.acceptedName
                              ? "nameConfirmed"
                              : "confirmName"
                        )}
                      </Button>
                    </div>
                    {nameMessage ? (
                      <p
                        id={nameMessageId}
                        role={row.error ? "alert" : undefined}
                        className={`text-xs sm:text-right ${row.error ? "text-[var(--color-error)]" : "text-muted-foreground"}`}
                      >
                        {label(nameMessage)}
                      </p>
                    ) : null}
                  </div>
                ) : null}
              </div>
            ) : null}
          </li>
        )
      })}
    </ul>
  )
}
