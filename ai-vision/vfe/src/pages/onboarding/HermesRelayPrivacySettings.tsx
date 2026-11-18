import { useId } from "react"
import { useTranslation } from "react-i18next"
import { Checkbox } from "@/components/ui/checkbox"
import { Textarea } from "@/components/ui/textarea"
import type { parseHermesOmittedPaths } from "./hermesRelayPrivacy"

export function HermesRelayPrivacySettings({
  personalInformation,
  onPersonalInformationChange,
  omittedPathsText,
  onOmittedPathsChange,
  error,
}: {
  personalInformation: boolean
  onPersonalInformationChange: (enabled: boolean) => void
  omittedPathsText: string
  onOmittedPathsChange: (value: string) => void
  error: ReturnType<typeof parseHermesOmittedPaths>["error"]
}) {
  const { t } = useTranslation()
  const id = useId()
  const errorKey =
    error === "too_many"
      ? "hermesOnboarding.privacyPathTooMany"
      : error === "too_long"
        ? "hermesOnboarding.privacyPathTooLong"
        : "hermesOnboarding.privacyPathInvalid"
  return (
    <fieldset className="min-w-0 space-y-3 border-t pt-4">
      <legend className="px-0 pr-2 text-sm font-medium">
        {t("hermesOnboarding.privacyTitle")}
      </legend>
      <div className="space-y-1">
        <p className="text-sm font-medium">
          {t("hermesOnboarding.privacyCredentials")}
        </p>
        <p className="text-sm text-muted-foreground">
          {t("hermesOnboarding.privacyCredentialsHelp")}
        </p>
      </div>
      <div className="flex items-start gap-3">
        <Checkbox
          id={`${id}-personal-information`}
          className="mt-0.5"
          checked={personalInformation}
          onCheckedChange={(checked) =>
            onPersonalInformationChange(checked === true)
          }
          aria-describedby={`${id}-personal-information-help`}
        />
        <div className="min-w-0 space-y-1">
          <label
            htmlFor={`${id}-personal-information`}
            className="cursor-pointer text-sm leading-5 font-medium"
          >
            {t("hermesOnboarding.privacyPersonalInformation")}
          </label>
          <p
            id={`${id}-personal-information-help`}
            className="text-sm text-muted-foreground"
          >
            {t("hermesOnboarding.privacyPersonalInformationHelp")}
          </p>
        </div>
      </div>
      <details>
        <summary className="cursor-pointer rounded text-sm font-medium focus-visible:outline-2 focus-visible:outline-ring">
          {t("hermesOnboarding.privacyAdvanced")}
        </summary>
        <div className="mt-3 space-y-2">
          <label
            htmlFor={`${id}-omitted-paths`}
            className="text-sm font-medium"
          >
            {t("hermesOnboarding.privacyOmittedPaths")}
          </label>
          <p id={`${id}-paths-help`} className="text-sm text-muted-foreground">
            {t("hermesOnboarding.privacyOmittedPathsHelp")}
          </p>
          <Textarea
            id={`${id}-omitted-paths`}
            rows={3}
            value={omittedPathsText}
            onChange={(event) => onOmittedPathsChange(event.target.value)}
            spellCheck={false}
            autoCapitalize="none"
            autoComplete="off"
            aria-invalid={Boolean(error)}
            aria-describedby={
              error ? `${id}-paths-help ${id}-paths-error` : `${id}-paths-help`
            }
            placeholder="/customer/account_number"
            className="[field-sizing:fixed] max-h-36 min-h-24 resize-y overflow-auto rounded-lg border bg-muted font-mono text-xs md:text-xs"
          />
          {error && (
            <p
              id={`${id}-paths-error`}
              role="alert"
              className="text-sm text-destructive"
            >
              {t(errorKey)}
            </p>
          )}
          <a
            href="https://docs.nvidia.com/nemo/relay/v0.10.4/configure-plugins/pii-redaction/configuration"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-block rounded text-sm text-muted-foreground underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-ring"
          >
            {t("hermesOnboarding.privacyConfigurationDocs")}
          </a>
        </div>
      </details>
      <p className="text-sm text-muted-foreground">
        {t("hermesOnboarding.privacyApplyHelp")}
      </p>
    </fieldset>
  )
}
