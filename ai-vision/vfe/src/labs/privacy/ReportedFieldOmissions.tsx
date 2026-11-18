import { useState } from "react"
import { useTranslation } from "react-i18next"
import { ShieldMinus } from "lucide-react"
import { reportedOmittedPaths } from "./relayPrivacyPolicy"

function FieldOmissionsContent({ paths }: { paths: readonly string[] }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  return (
    <details
      data-testid="reported-field-omissions"
      className="min-w-0 rounded-md border border-amber-200 bg-amber-50/70 px-3 py-2 text-xs leading-5 dark:border-amber-400/25 dark:bg-amber-400/5"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className="cursor-pointer rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
        <span className="ml-1 inline-flex max-w-[calc(100%_-_1rem)] items-start gap-1.5 align-top">
          <ShieldMinus
            className="mt-0.5 size-3.5 shrink-0 text-amber-700 dark:text-amber-300"
            aria-hidden
          />
          <span
            className="line-clamp-2 min-w-0 flex-1 [overflow-wrap:anywhere]"
            data-privacy-path-preview
          >
            {t("labs.privacy.excludedPaths")}{" "}
            {open
              ? t("labs.privacy.excludedPathsCount", { count: paths.length })
              : paths.slice(0, 2).map((path, index) => (
                  <span key={path}>
                    {index > 0 && ", "}
                    <code>{path}</code>
                  </span>
                ))}
          </span>
          {!open && paths.length > 2 && (
            <span className="shrink-0 text-muted-foreground">
              {t("labs.privacy.morePaths", { count: paths.length - 2 })}
            </span>
          )}
        </span>
      </summary>
      {open && (
        <div className="mt-2 space-y-2">
          <p className="text-muted-foreground">
            {t("labs.privacy.excludedPathsHelp")}
          </p>
          <ul
            className="max-h-48 space-y-1 overflow-auto rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            tabIndex={0}
            aria-label={t("labs.privacy.allExcludedPaths")}
          >
            {paths.map((path) => (
              <li key={path}>
                <code className="[overflow-wrap:anywhere] whitespace-pre-wrap">
                  {path}
                </code>
              </li>
            ))}
          </ul>
        </div>
      )}
    </details>
  )
}

/** A short configuration hint; full paths and their meaning remain available. */
export function FieldOmissions({ paths }: { paths: readonly string[] }) {
  if (!paths.length) return null
  return <FieldOmissionsContent key={JSON.stringify(paths)} paths={paths} />
}

/** Explains only the selected record's explicit producer declaration. */
export function ReportedFieldOmissions({ resource }: { resource: unknown }) {
  return <FieldOmissions paths={reportedOmittedPaths(resource)} />
}
