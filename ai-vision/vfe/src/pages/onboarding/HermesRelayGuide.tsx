import { useState } from "react"
import { useTranslation } from "react-i18next"
import { useNavigate } from "react-router-dom"
import { Button } from "@/components/ui/button"
import { useSpace } from "@/contexts/SpaceContext"
import {
  useOnboardingCopy,
  useOnboardingCredentials,
  useOnboardingDetection,
} from "./useOnboarding"
import {
  hermesRelayConfiguration,
  type HermesRelayOnboardingConfig,
} from "./hermesRelayConfiguration"
import {
  HERMES_RELAY_DEFAULT_PRIVACY,
  parseHermesOmittedPaths,
} from "./hermesRelayPrivacy"
import { HermesRelayPrivacySettings } from "./HermesRelayPrivacySettings"

function Snippet({ label, value }: { label: string; value: string }) {
  const { t } = useTranslation()
  const { copy, copiedLabel, copyErrorLabel } = useOnboardingCopy()
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-3">
        <h4 className="text-sm font-medium">{label}</h4>
        <Button
          variant="ghost"
          size="sm"
          aria-label={`${t("common.copy")} ${label}`}
          onClick={() => void copy(label, value)}
        >
          {t(copiedLabel === label ? "common.copied" : "common.copy")}
        </Button>
      </div>
      <pre
        aria-label={label}
        tabIndex={0}
        className="max-h-48 overflow-auto rounded-lg border bg-muted p-3 text-xs break-all whitespace-pre-wrap focus-visible:outline-2 focus-visible:outline-ring"
      >
        <code>{value}</code>
      </pre>
      {copyErrorLabel === label && (
        <p role="alert" className="text-xs text-destructive">
          {t("hermesOnboarding.copyFailed")}
        </p>
      )}
    </div>
  )
}

export function HermesRelayGuide({
  config,
  agentUuid,
  disabled,
}: {
  config: HermesRelayOnboardingConfig
  agentUuid?: string
  disabled: boolean
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { currentSpace } = useSpace()
  const [personalInformation, setPersonalInformation] = useState(
    HERMES_RELAY_DEFAULT_PRIVACY.personalInformation
  )
  const [omittedPathsText, setOmittedPathsText] = useState(
    HERMES_RELAY_DEFAULT_PRIVACY.omittedPaths.join("\n")
  )
  const omittedPaths = parseHermesOmittedPaths(omittedPathsText)
  const credentials = useOnboardingCredentials({
    name: "Hermes Agent",
    requiredPermission: "agent.ingest_token.mint",
  })
  const detection = useOnboardingDetection({
    product: "Hermes",
    queryKeyPrefix: "hermes-onboarding",
    projectUuid: disabled ? null : (agentUuid ?? null),
  })
  const endpoint =
    config.otlp_endpoint ||
    `${window.location.origin}/api/public/otel/v1/traces`
  const configuration = omittedPaths.error
    ? null
    : hermesRelayConfiguration({
        endpoint,
        agentUuid: disabled ? null : agentUuid,
        spaceKey: credentials.spaceApiKey,
        privacy: { personalInformation, omittedPaths: omittedPaths.paths },
      })
  return (
    <div className="space-y-5" data-testid="hermes-relay-guide">
      <div className="space-y-2">
        <p className="text-sm text-muted-foreground">
          {t("hermesOnboarding.description")}
        </p>
        <p className="text-sm text-muted-foreground">
          {t("hermesOnboarding.requirements", {
            hermes: config.hermes_version,
            relay: config.relay_requirement,
          })}
        </p>
        <details className="text-sm">
          <summary className="w-fit cursor-pointer rounded-sm text-muted-foreground focus-visible:outline-2 focus-visible:outline-ring">
            {t("hermesOnboarding.checkEnvironment")}
          </summary>
          <div className="mt-2">
            <Snippet
              label={t("hermesOnboarding.checkEnvironment")}
              value={`hermes --version
hermes doctor`}
            />
          </div>
        </details>
      </div>

      <section className="space-y-3">
        <h4 className="text-sm font-medium">
          {t("hermesOnboarding.authorization")}
        </h4>
        {!agentUuid || disabled ? (
          <p className="text-sm text-muted-foreground">
            {t("hermesOnboarding.createFirst")}
          </p>
        ) : (
          <div className="space-y-3">
            <p className="text-xs break-all text-muted-foreground">
              {t("hermesOnboarding.identity", {
                space: currentSpace?.name ?? credentials.spaceId,
                agent: agentUuid,
              })}
            </p>
            <p className="text-sm text-muted-foreground">
              {t("hermesOnboarding.credentialsHelp")}
            </p>
            <Button
              size="sm"
              disabled={
                !credentials.canCreateApiKey ||
                credentials.createApiKey.isPending ||
                Boolean(credentials.spaceApiKey)
              }
              onClick={() => credentials.createApiKey.mutate()}
            >
              {t(
                credentials.spaceApiKey
                  ? "hermesOnboarding.credentialsCreated"
                  : "hermesOnboarding.createCredentials"
              )}
            </Button>
            {credentials.createApiKey.isError && (
              <p role="alert" className="text-sm text-destructive">
                {t("hermesOnboarding.credentialsFailed")}
              </p>
            )}
            {!credentials.canCreateApiKey && (
              <p className="text-sm text-muted-foreground">
                {t("agentAccess.contactAdmin")}
              </p>
            )}
            <details className="text-xs text-muted-foreground">
              <summary className="w-fit cursor-pointer rounded-sm focus-visible:outline-2 focus-visible:outline-ring">
                {t("hermesOnboarding.authorizationDetailsTitle")}
              </summary>
              <p className="mt-2 leading-5">
                {t("hermesOnboarding.authorizationDetails")}
              </p>
            </details>
          </div>
        )}
      </section>

      <section className="space-y-3 border-t pt-4">
        <h4 className="text-sm font-medium">
          {t("hermesOnboarding.mergeTitle")}
        </h4>
        <p className="text-sm text-muted-foreground">
          {t("hermesOnboarding.mergeHelp")}
        </p>
        <p className="text-sm text-muted-foreground">
          {t("hermesOnboarding.payloadHelp")}
        </p>
        <HermesRelayPrivacySettings
          personalInformation={personalInformation}
          onPersonalInformationChange={setPersonalInformation}
          omittedPathsText={omittedPathsText}
          onOmittedPathsChange={setOmittedPathsText}
          error={omittedPaths.error}
        />
        <details className="text-xs text-muted-foreground">
          <summary className="w-fit cursor-pointer rounded-sm font-medium focus-visible:outline-2 focus-visible:outline-ring">
            {t("hermesOnboarding.configurationDetailsTitle")}
          </summary>
          <div className="mt-2 space-y-2 leading-5">
            <p>{t("hermesOnboarding.configurationDetails")}</p>
            <p>{t("hermesOnboarding.privacyMergeDetails")}</p>
            <p>{t("hermesOnboarding.duplicateHelp")}</p>
            <p>{t("hermesOnboarding.payloadDetails")}</p>
          </div>
        </details>
        {configuration ? (
          <>
            <Snippet
              label={t("hermesOnboarding.configSnippet")}
              value={configuration.toml}
            />
            <Snippet
              label={t("hermesOnboarding.environmentSnippet")}
              value={configuration.environment}
            />
          </>
        ) : !omittedPaths.error ? (
          <p className="text-sm text-muted-foreground">
            {t("hermesOnboarding.configurationPending")}
          </p>
        ) : null}
        {credentials.spaceApiKey &&
          !omittedPaths.error &&
          !configuration &&
          !disabled && (
            <p role="alert" className="text-sm text-destructive">
              {t("hermesOnboarding.endpointError")}
            </p>
          )}
      </section>

      <section className="space-y-3 border-t pt-4">
        <h4 className="text-sm font-medium">
          {t("hermesOnboarding.verifyTitle")}
        </h4>
        <p className="text-sm text-muted-foreground">
          {t("hermesOnboarding.verifyHelp")}
        </p>
        <p role="status" className="text-sm text-muted-foreground">
          {t(
            detection.isError
              ? "hermesOnboarding.queryError"
              : detection.hasTraces
                ? "hermesOnboarding.recentTraces"
                : "hermesOnboarding.waitingTraces",
            { count: detection.traceCount }
          )}
        </p>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={!agentUuid || disabled || detection.isRefreshing}
            onClick={() => void detection.refresh()}
          >
            {t("common.refresh")}
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!agentUuid || disabled}
            onClick={() =>
              navigate(
                `/trace?agent=Hermes&agent_uuid=${encodeURIComponent(agentUuid ?? "")}&spaceId=${encodeURIComponent(credentials.spaceId)}`
              )
            }
          >
            {t("hermesOnboarding.openTraces")}
          </Button>
        </div>
        <details className="text-xs text-muted-foreground">
          <summary className="w-fit cursor-pointer rounded-sm focus-visible:outline-2 focus-visible:outline-ring">
            {t("hermesOnboarding.verificationDetailsTitle")}
          </summary>
          <div className="mt-2 space-y-2 leading-5">
            <p>{t("hermesOnboarding.evidenceBoundary")}</p>
            <p>{t("hermesOnboarding.compactionDetails")}</p>
            <p>{t("hermesOnboarding.versionBoundary")}</p>
          </div>
        </details>
      </section>

      <details className="border-t pt-3 text-xs">
        <summary className="w-fit cursor-pointer rounded-sm text-muted-foreground focus-visible:outline-2 focus-visible:outline-ring">
          {t("hermesOnboarding.sessionSegmentsTitle")}
        </summary>
        <div className="mt-3 space-y-3">
          <p className="text-muted-foreground">
            {t("hermesOnboarding.sessionSegmentsHelp")}
          </p>
          <Snippet
            label="config.yaml"
            value={`gateway:
  telemetry:
    session_segments:
      on_compaction: true
      max_turns: 1`}
          />
        </div>
      </details>

      <a
        href={config.docs_url}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-block rounded text-sm underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-ring"
      >
        {t("frameworkOnboarding.officialDocs")}
      </a>
    </div>
  )
}
