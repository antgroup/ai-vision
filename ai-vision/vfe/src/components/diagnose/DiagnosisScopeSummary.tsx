import { useTranslation } from "react-i18next"
import { skipReasonLabelKey, type DiagnosisScope } from "./diagnosisScope"

/** Reused by Task details and the same batch's quality overview. */
export function DiagnosisScopeSummary({
  scope,
  compact = false,
  scoreUnavailable = false,
}: {
  scope: DiagnosisScope
  compact?: boolean
  scoreUnavailable?: boolean
}) {
  const { t } = useTranslation()
  const label = (key: string) => t(`diagnose.scope.${key}`)
  const number = (value: number | null) =>
    value === null ? label("unknown") : value
  const definitionLabel = {
    task_snapshot: "frozen",
    legacy_current_definition: "legacy",
    unsupported_snapshot: "unsupported",
  }[scope.definitionSource]

  return (
    <section className="space-y-2 rounded-lg border border-[var(--color-border)] p-3 text-xs leading-relaxed">
      <h3 className="text-sm font-medium">{label("title")}</h3>
      <div className="flex flex-wrap gap-x-4 gap-y-1 tabular-nums">
        <span>
          {scope.selected === null
            ? label("selectedUnknown")
            : t("diagnose.scope.selected", { value: scope.selected })}
        </span>
        <span>
          {scope.scanned === null
            ? label("scannedUnknown")
            : t("diagnose.scope.scanned", { value: scope.scanned })}
        </span>
        <span>
          {t("diagnose.scope.effective", {
            value: number(scope.effective),
            total: number(scope.checks),
          })}
        </span>
        <span>
          {t("diagnose.scope.skipped", { value: number(scope.skipped) })}
        </span>
        <span
          className={scope.failed ? "text-[var(--color-error)]" : undefined}
        >
          {t("diagnose.scope.failed", { value: number(scope.failed) })}
        </span>
      </div>
      <p className="text-muted-foreground">{label("denominator")}</p>
      {scope.state !== "evaluated" ? (
        <p className="text-[var(--color-warning)]">{label(scope.state)}</p>
      ) : scoreUnavailable ? (
        <p className="text-[var(--color-warning)]">
          {label("scoreUnavailable")}
        </p>
      ) : null}
      {scope.sourceIncomplete ? (
        <p className="text-[var(--color-warning)]">
          {label("sourceIncomplete")}
        </p>
      ) : null}
      {scope.skipReasons.length > 0 ? (
        <div className="text-muted-foreground">
          {label("reasons")}{" "}
          {scope.skipReasons.map(({ reason, count }) => (
            <span className="mr-3 inline-block" key={reason}>
              {t(skipReasonLabelKey(reason), {
                defaultValue: label("otherReason"),
              })}
              : {count}
            </span>
          ))}
          <p>{label("reasonUnit")}</p>
        </div>
      ) : null}
      {!compact ? (
        <details className="border-t border-[var(--color-border)] pt-2">
          <summary className="cursor-pointer rounded-sm text-[var(--color-auxiliary)] focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
            {label("definitions")} · {label(definitionLabel)}
          </summary>
          {scope.definitionSource !== "task_snapshot" ? (
            <p className="mt-2 text-[var(--color-warning)]">
              {label(
                scope.definitionSource === "unsupported_snapshot"
                  ? "unsupportedHelp"
                  : "legacyHelp"
              )}
            </p>
          ) : null}
          {scope.definitions.length > 0 ? (
            <div className="mt-2 overflow-x-auto">
              <table className="w-full text-left">
                <thead className="text-muted-foreground">
                  <tr>
                    {["name", "version", "role", "weight"].map((key) => (
                      <th className="px-2 py-1 font-normal" key={key}>
                        {label(key)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {scope.definitions.map((definition) => (
                    <tr
                      key={definition.uuid}
                      className="border-t border-[var(--color-border)]"
                    >
                      <td className="px-2 py-2">{definition.name}</td>
                      <td className="px-2 py-2 tabular-nums">
                        {definition.version ?? "—"}
                      </td>
                      <td className="px-2 py-2">
                        {label(definition.score_role ?? "unknownRole")}
                      </td>
                      <td className="px-2 py-2 tabular-nums">
                        {definition.score_role === "signal"
                          ? label("unscored")
                          : definition.weight}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="mt-2 text-muted-foreground">
              {label("noDefinitions")}
            </p>
          )}
          <details className="mt-2">
            <summary className="cursor-pointer rounded-sm text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
              {label("technical")}
            </summary>
            <p className="mt-2">
              {label("policy")}: {scope.scorePolicy ?? "—"}
            </p>
            {scope.definitions.map((definition) => (
              <dl className="mt-2 space-y-1 break-all" key={definition.uuid}>
                <dt className="font-medium">{definition.name}</dt>
                <dd className="font-mono">
                  {label("revision")}: {definition.revision ?? "—"}
                </dd>
                <dd className="font-mono">
                  {label("digest")}: {definition.digest ?? "—"}
                </dd>
              </dl>
            ))}
          </details>
        </details>
      ) : (
        <p className="text-muted-foreground">{label(definitionLabel)}</p>
      )}
    </section>
  )
}
