import { useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import { TraceCopyButton } from "@/components/traces/shared/TraceTextContent"
import { SpanTypedContent } from "./SpanTypedContent"
import { buildFileChangeDiff } from "./fileChangeDiff"
import type { RecordedChange } from "./spanContent"

export function FileChangeView({ change }: { change: RecordedChange }) {
  const { t } = useTranslation()
  const [mode, setMode] = useState<"diff" | "before" | "after" | "recorded">(
    "diff"
  )
  const [all, setAll] = useState(false)
  const [wrap, setWrap] = useState(true)
  const projection = useMemo(
    () => buildFileChangeDiff(change, all),
    [change, all]
  )
  const tabs = [
    { mode: "diff" as const, label: "spanContent.diffContext" },
    ...(change.before !== undefined
      ? [{ mode: "before" as const, label: "spanContent.before" }]
      : []),
    ...(change.after !== undefined
      ? [{ mode: "after" as const, label: "spanContent.after" }]
      : []),
    ...(change.diff !== undefined
      ? [{ mode: "recorded" as const, label: "spanContent.recordedDiff" }]
      : []),
  ]
  const rows = all ? projection.rows : projection.rows.slice(0, 120)
  return (
    <section
      className="min-w-0 space-y-2 border-t border-border pt-3"
      data-content-file-change
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <h4 className="min-w-0 font-mono text-xs font-medium [overflow-wrap:anywhere] break-words">
          {change.path}
        </h4>
        <span className="text-xs text-[var(--color-success)]">
          + {t("spanContent.diffAdded", { count: projection.added })}
        </span>
        <span className="text-xs text-destructive">
          − {t("spanContent.diffRemoved", { count: projection.removed })}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-1">
        {tabs.map((tab) => (
          <Button
            key={tab.mode}
            type="button"
            variant="ghost"
            size="sm"
            className="h-6 px-2 text-xs aria-pressed:bg-muted"
            aria-pressed={mode === tab.mode}
            onClick={() => setMode(tab.mode)}
          >
            {t(tab.label)}
          </Button>
        ))}
        <div className="ml-auto flex items-center">
          {change.before !== undefined && (
            <TraceCopyButton
              value={change.before}
              label={t("spanContent.copyBefore")}
              className="size-6"
            />
          )}
          {change.after !== undefined && (
            <TraceCopyButton
              value={change.after}
              label={t("spanContent.copyAfter")}
              className="size-6"
            />
          )}
          <TraceCopyButton
            value={
              mode === "recorded"
                ? (change.diff ?? projection.raw)
                : projection.raw
            }
            label={t("spanContent.copyDiff")}
            className="size-6"
          />
        </div>
      </div>
      {mode === "diff" ? (
        <>
          <div className="flex flex-wrap items-center gap-1">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-6 px-2 text-xs"
              onClick={() => setAll(!all)}
            >
              {t(all ? "spanContent.diffContext" : "spanContent.diffAll")}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-6 px-2 text-xs"
              onClick={() => setWrap(!wrap)}
            >
              {t(wrap ? "spanContent.noWrapText" : "spanContent.wrapText")}
            </Button>
          </div>
          {projection.coarse && (
            <p className="text-xs text-muted-foreground">
              {t("spanContent.largeDiff")}
            </p>
          )}
          {change.before === change.after && change.before !== undefined ? (
            <p className="text-xs text-muted-foreground">
              {t("spanContent.noChanges")}
            </p>
          ) : (
            <div
              className="max-h-96 overflow-auto rounded border border-border font-mono text-xs leading-6"
              data-content-diff
            >
              {rows.map((row, index) =>
                row.kind === "omitted" ? (
                  <div
                    key={index}
                    className="bg-muted/50 px-2 text-muted-foreground"
                  >
                    {t("spanContent.unchangedLines", { count: row.count })}
                  </div>
                ) : (
                  <div
                    key={index}
                    className={`flex min-w-0 ${row.kind === "add" ? "bg-[var(--color-bg-success)]" : row.kind === "remove" ? "bg-destructive/10" : ""}`}
                    data-diff-line={row.kind}
                  >
                    <span className="w-9 shrink-0 px-1 text-right text-muted-foreground select-none">
                      {row.oldLine}
                    </span>
                    <span className="w-9 shrink-0 px-1 text-right text-muted-foreground select-none">
                      {row.newLine}
                    </span>
                    <span
                      className={`w-4 shrink-0 select-none ${row.kind === "add" ? "text-[var(--color-success)]" : row.kind === "remove" ? "text-destructive" : "text-muted-foreground"}`}
                    >
                      {row.kind === "add"
                        ? "+"
                        : row.kind === "remove"
                          ? "−"
                          : " "}
                    </span>
                    <div
                      className={
                        wrap
                          ? "min-w-0 flex-1 [overflow-wrap:anywhere] break-words whitespace-pre-wrap"
                          : "flex-1 whitespace-pre"
                      }
                    >
                      {!!row.omittedBefore && (
                        <span className="mr-1 font-sans text-muted-foreground">
                          […{" "}
                          {t("spanContent.unchangedCharacters", {
                            count: row.omittedBefore,
                          })}
                          ]{" "}
                        </span>
                      )}
                      {row.text}
                      {!!row.omittedAfter && (
                        <span className="ml-1 font-sans text-muted-foreground">
                          {" "}
                          […{" "}
                          {t("spanContent.unchangedCharacters", {
                            count: row.omittedAfter,
                          })}
                          ]
                        </span>
                      )}
                      {row.noNewline && (
                        <span className="ml-2 font-sans text-muted-foreground">
                          {t("spanContent.noFinalNewline")}
                        </span>
                      )}
                    </div>
                  </div>
                )
              )}
              {!all && projection.rows.length > rows.length && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-7 text-xs"
                  onClick={() => setAll(true)}
                >
                  {t("spanContent.diffAll")}
                </Button>
              )}
            </div>
          )}
        </>
      ) : (
        <SpanTypedContent
          key={mode}
          value={
            mode === "before"
              ? change.before
              : mode === "after"
                ? change.after
                : change.diff
          }
          format="code"
          copyLabel={t(
            mode === "before"
              ? "spanContent.copyBefore"
              : mode === "after"
                ? "spanContent.copyAfter"
                : "spanContent.copyDiff"
          )}
        />
      )}
    </section>
  )
}
