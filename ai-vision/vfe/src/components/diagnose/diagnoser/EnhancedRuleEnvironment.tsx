import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import {
  ENHANCED_EXAMPLES,
  type RuleRuntimeManifest,
} from "./enhancedRuleModel"

export function EnhancedRuleEnvironment({
  runtime,
  available,
  loading,
  local,
  error,
  timeout,
  connectionTimeout,
  hasConnection,
  onUpgrade,
  onExample,
}: {
  runtime?: RuleRuntimeManifest
  available?: RuleRuntimeManifest
  loading: boolean
  local: boolean
  error: boolean
  timeout?: number
  connectionTimeout?: number
  hasConnection?: boolean
  onUpgrade: () => void
  onExample: (example: (typeof ENHANCED_EXAMPLES)[number]) => void
}) {
  const { t } = useTranslation()
  const compatible =
    !!available &&
    (!runtime ||
      (runtime.id === available.id &&
        runtime.manifest_sha256 === available.manifest_sha256))
  return (
    <div className="@container space-y-2 border-t pt-3 text-xs">
      <div
        className="flex flex-wrap items-center justify-between gap-2"
        aria-live="polite"
      >
        <p className="text-muted-foreground">
          {t(
            loading
              ? "enhancedRule.checking"
              : error
                ? "enhancedRule.checkFailed"
                : runtime
                  ? compatible
                    ? "enhancedRule.summary"
                    : local
                      ? "enhancedRule.localUnsupported"
                      : available
                        ? "enhancedRule.changedRuntime"
                        : "enhancedRule.unsupported"
                  : "enhancedRule.legacy"
          )}
          {runtime && compatible && timeout
            ? ` · ${t("enhancedRule.effectiveBudget", { seconds: timeout })}`
            : ""}
          {runtime && compatible && connectionTimeout
            ? ` · ${t("enhancedRule.budget", { seconds: connectionTimeout })}`
            : ""}
        </p>
        {!runtime && available && (
          <Button type="button" variant="outline" size="sm" onClick={onUpgrade}>
            {t("enhancedRule.upgrade")}
          </Button>
        )}
      </div>
      {!runtime && hasConnection && !available && !loading && !error && (
        <p className="text-muted-foreground">{t("enhancedRule.unsupported")}</p>
      )}
      {runtime && !loading && !compatible && (
        <p role="alert" className="text-destructive">
          {t("enhancedRule.chooseCompatible")}
        </p>
      )}
      {available && (
        <div className="grid grid-cols-1 gap-x-5 gap-y-2 @min-[540px]:grid-cols-[auto_1fr]">
          <details className="min-w-0">
            <summary className="cursor-pointer rounded-sm underline underline-offset-4 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
              {t("enhancedRule.libraries")}
            </summary>
            <dl className="mt-2 grid grid-cols-[auto_auto] gap-x-4 gap-y-1 text-muted-foreground">
              <dt>Python</dt>
              <dd className="font-mono">{available.python_version}</dd>
              {Object.entries(available.packages ?? {})
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([name, version]) => (
                  <div key={name} className="contents">
                    <dt>{name}</dt>
                    <dd className="font-mono">{version}</dd>
                  </div>
                ))}
            </dl>
          </details>
          {runtime && compatible && (
            <details className="min-w-0 flex-1">
              <summary className="cursor-pointer rounded-sm underline underline-offset-4 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
                {t("enhancedRule.examples")}
              </summary>
              <p className="mt-2 max-w-prose leading-relaxed text-muted-foreground">
                {t("enhancedRule.synthetic", {
                  browseTrace: t("diagnose.diagnoser.form.browseTrace"),
                })}
              </p>
              <div className="mt-3 space-y-3">
                {(["starter", "advanced"] as const).map((group) => (
                  <fieldset key={group} className="min-w-0 space-y-1">
                    <legend className="mb-1 font-medium">
                      {t(`enhancedRule.exampleGroup.${group}`)}
                    </legend>
                    {ENHANCED_EXAMPLES.filter(
                      (example) => example.group === group
                    ).map((example) => (
                      <Button
                        key={example.id}
                        type="button"
                        size="sm"
                        variant="ghost"
                        className="h-auto w-full min-w-0 justify-start px-2 py-1.5 text-left whitespace-normal"
                        aria-label={t("enhancedRule.loadExample", {
                          name: t(`enhancedRule.example.${example.id}`),
                        })}
                        onClick={() => onExample(example)}
                      >
                        <span className="min-w-0 space-y-0.5">
                          <span className="block font-medium">
                            {t(`enhancedRule.example.${example.id}`)}
                          </span>
                          <span className="block text-xs leading-relaxed font-normal text-muted-foreground">
                            {t(`enhancedRule.exampleDescription.${example.id}`)}
                          </span>
                          <span className="block text-[11px] font-normal text-muted-foreground">
                            {example.libraries.join(", ")}
                          </span>
                        </span>
                      </Button>
                    ))}
                  </fieldset>
                ))}
              </div>
            </details>
          )}
        </div>
      )}
    </div>
  )
}
