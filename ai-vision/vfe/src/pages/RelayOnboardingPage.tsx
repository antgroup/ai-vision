import { useState } from "react"
import { useTranslation } from "react-i18next"
import { useNavigate, useSearchParams } from "react-router-dom"
import {
  OnboardingPageTemplate,
  type OnboardingCodeBlock,
} from "@/pages/onboarding/OnboardingPageTemplate"
import {
  useOnboardingCopy,
  useProjectOnboardingCredential,
  useOnboardingDetection,
  useOnboardingProjects,
  useOnboardingTraceVerification,
} from "@/pages/onboarding/useOnboarding"
import { relayEnvironment } from "@/pages/onboarding/relayEnvironment"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Button } from "@/components/ui/button"
import { TraceVerification } from "@/pages/onboarding/TraceVerification"
import { downloadOnboardingFile } from "@/pages/onboarding/download"
import { FrameworkLanguageSelector } from "@/pages/onboarding/FrameworkLanguageSelector"
import {
  availableLanguages,
  guideLanguage,
  guideForLanguage,
  resolveProjectFramework,
  type FrameworkGuide,
  type FrameworkLanguage,
} from "@/pages/onboarding/frameworkGuides"

const RELAY_AGENT_PRODUCT = "nvidia-nemo-relay"

export function RelayOnboardingPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const initialProjectId = searchParams.get("agent_uuid")
  const [frameworkId, setFrameworkId] = useState<string | null>(null)
  const [language, setLanguage] = useState<FrameworkLanguage>("python")
  const [envFormat, setEnvFormat] = useState<"shell" | "dotenv">("shell")
  const [exampleMode, setExampleMode] = useState<"existing" | "complete">(
    "existing"
  )
  const { copiedLabel, copyErrorLabel, copy } = useOnboardingCopy()
  const projects = useOnboardingProjects({
    product: RELAY_AGENT_PRODUCT,
    queryKeyPrefix: "relay-onboarding",
    initialProjectId,
  })
  const detection = useOnboardingDetection({
    product: RELAY_AGENT_PRODUCT,
    queryKeyPrefix: "relay-onboarding",
    projectUuid: projects.selectedProjectUuid,
  })
  const {
    agentTypes,
    applicationName,
    createApplication,
    loading,
    projects: relayProjects,
    selectedProject,
    selectedProjectUuid: relayProjectUuid,
    setApplicationName,
    setSelectedProjectId,
    spaceId,
  } = projects
  const credential = useProjectOnboardingCredential(
    relayProjectUuid,
    initialProjectId ? undefined : "NVIDIA NeMo Relay",
    selectedProject?.status === 2
  )
  const guide = (agentTypes.find(
    (type) =>
      type.agent_product === RELAY_AGENT_PRODUCT ||
      type.agent_name === RELAY_AGENT_PRODUCT
  )?.config_guide ?? {}) as { frameworks?: FrameworkGuide[] }
  const allFrameworks = [...(guide.frameworks ?? [])].sort(
    (a, b) => (a.frontendSort ?? 99) - (b.frontendSort ?? 99)
  )
  const isEditing = !!initialProjectId && relayProjectUuid === initialProjectId
  const savedFramework = selectedProject?.metadata?.framework
  const activeExample = frameworkId
    ? allFrameworks.find((framework) => framework.id === frameworkId)
    : resolveProjectFramework(allFrameworks, savedFramework, isEditing)
  const effectiveLanguage = frameworkId
    ? language
    : activeExample
      ? guideLanguage(activeExample)
      : language
  const frameworks = allFrameworks.filter(
    (guide) => guideLanguage(guide) === effectiveLanguage
  )
  const isPython = effectiveLanguage === "python"
  const showExisting =
    exampleMode === "existing" && Boolean(activeExample?.existingExample)
  const exampleFilename =
    activeExample?.exampleFilename ?? `relay-${activeExample?.id}.py`
  const completeSnippet =
    (isPython ? activeExample?.pipExample : activeExample?.npmExample) ?? ""
  const selectLanguage = (next: FrameworkLanguage) => {
    setLanguage(next)
    setFrameworkId(
      guideForLanguage(allFrameworks, activeExample, next)?.id ?? null
    )
  }

  const frameworkUpdate = isEditing ? (frameworkId ?? undefined) : undefined
  const verification = useOnboardingTraceVerification({
    product: RELAY_AGENT_PRODUCT,
    projectUuid: relayProjectUuid,
    frameworkUpdate,
  })
  const downloadExample = () => {
    if (completeSnippet)
      downloadOnboardingFile(exampleFilename, completeSnippet)
  }
  const environmentSelector = (
    <Tabs
      value={envFormat}
      onValueChange={(value) =>
        setEnvFormat(value === "dotenv" ? "dotenv" : "shell")
      }
    >
      <TabsList className="h-7">
        <TabsTrigger value="shell">Shell</TabsTrigger>
        <TabsTrigger value="dotenv">.env</TabsTrigger>
      </TabsList>
    </Tabs>
  )
  function environmentBlocks(key: string | null): OnboardingCodeBlock[] {
    const environmentBlock: OnboardingCodeBlock = {
      label: t("relayOnboarding.environmentLabel"),
      language: envFormat,
      value: relayEnvironment({
        origin: window.location.origin,
        agentUuid: relayProjectUuid,
        spaceKey: key,
        format: envFormat,
        completeExample:
          Boolean(activeExample) &&
          !showExisting &&
          activeExample?.requiresModel !== false,
      }),
      description: (
        <>
          {activeExample && (
            <p>
              {t(
                isPython
                  ? "frameworkOnboarding.relayCredentials"
                  : "frameworkOnboarding.relayJsCredentials"
              )}
            </p>
          )}
          {!isEditing && relayProjectUuid && !credential.key && (
            <p>{t("agentAccess.projectCredentialHint")}</p>
          )}
        </>
      ),
      copiedLabel,
      copyErrorLabel,
      onCopy: copy,
      headerLeft: environmentSelector,
      credential: isEditing ? undefined : credential,
    }
    const blocks: OnboardingCodeBlock[] = [environmentBlock]
    if (key) {
      blocks.push({
        label: "SpaceKey",
        language: "shell",
        value: key,
        copiedLabel,
        copyErrorLabel,
        onCopy: copy,
      })
    }
    return blocks
  }

  return (
    <OnboardingPageTemplate
      title={t("relayOnboarding.title")}
      description={t("relayOnboarding.description")}
      backLabel={t("relayOnboarding.backToAccess")}
      onBack={() =>
        navigate(initialProjectId ? "/access" : "/access?tab=market")
      }
      status={{
        label: !relayProjectUuid
          ? t("relayOnboarding.selectProject")
          : verification.verified
            ? t("relayOnboarding.traceVerified")
            : detection.isError
              ? t("relayOnboarding.statusError")
              : detection.hasTraces
                ? t("relayOnboarding.statusConnected", {
                    count: detection.traceCount,
                  })
                : detection.isListeningTimeout
                  ? t("relayOnboarding.statusTimeout")
                  : detection.isRefreshing
                    ? t("relayOnboarding.statusChecking")
                    : t("relayOnboarding.statusListening"),
        isConnected: verification.verified,
        isError: !verification.verified && detection.isError,
        refreshLabel: t("relayOnboarding.refresh"),
        isRefreshing: detection.isRefreshing,
        onRefresh: detection.refresh,
        viewGraphLabel: t("relayOnboarding.viewGraph"),
        onViewGraph: () =>
          navigate(
            `/trace?agent=${RELAY_AGENT_PRODUCT}&agent_uuid=${encodeURIComponent(relayProjectUuid ?? "")}&spaceId=${encodeURIComponent(spaceId)}&traceId=${encodeURIComponent(verification.traceId.trim().toLowerCase())}`
          ),
      }}
      supportTitle={t("relayOnboarding.supportMatrixTitle")}
      supportControls={
        <FrameworkLanguageSelector
          value={effectiveLanguage}
          onChange={selectLanguage}
          languages={availableLanguages(allFrameworks)}
        />
      }
      supportOptions={frameworks.map((framework) => ({
        id: framework.id,
        label: framework.label,
        logo: framework.logo,
        logoLayout: framework.logoLayout,
        logoInvertInDark: framework.logoInvertInDark,
        active: framework.id === activeExample?.id,
        onClick: () => {
          setFrameworkId(framework.id)
          setLanguage(guideLanguage(framework))
        },
      }))}
      project={{
        product: RELAY_AGENT_PRODUCT,
        target: initialProjectId
          ? {
              spaceId,
              sdk: "NVIDIA NeMo Relay",
              loading,
              credentials: { credential, buildCodeBlocks: environmentBlocks },
            }
          : undefined,
        projects: relayProjects,
        selectedId: relayProjectUuid,
        onSelect: (id) => {
          setSelectedProjectId(id)
          const saved = relayProjects.find((project) => project.uuid === id)
            ?.metadata?.framework
          const framework = resolveProjectFramework(allFrameworks, saved)
          if (framework) {
            setFrameworkId(framework.id)
            setLanguage(guideLanguage(framework))
          }
        },
        selectLabel: t("relayOnboarding.selectProject"),
        searchLabel: t("relayOnboarding.searchProject"),
        emptyLabel: t("relayOnboarding.projectSearchEmpty"),
        namePlaceholder: t("relayOnboarding.applicationNamePlaceholder"),
        name: applicationName,
        onNameChange: setApplicationName,
        createLabel: t("relayOnboarding.createApplication"),
        isCreating: createApplication.isPending,
        createDisabled: !spaceId || !applicationName.trim(),
        onCreate: () =>
          createApplication.mutate(
            activeExample ? { framework: activeExample.id } : undefined
          ),
        createError: createApplication.isError
          ? t("relayOnboarding.createApplicationFailed")
          : undefined,
      }}
      setupHelp={
        activeExample ? (
          <div className="space-y-2">
            <p>
              {t(
                isPython
                  ? "frameworkOnboarding.relaySetup"
                  : "frameworkOnboarding.relayJsSetup"
              )}
            </p>
            <p>{t("frameworkOnboarding.envHelp")}</p>
          </div>
        ) : undefined
      }
      verification={
        <>
          {frameworkUpdate !== undefined &&
            frameworkUpdate !== savedFramework && (
              <p className="text-sm text-muted-foreground">
                {t("agentAccess.saveSetupHint")}
              </p>
            )}
          <TraceVerification verification={verification} />
        </>
      }
      advanced={
        <>
          <p>
            {t(
              isPython
                ? "relayOnboarding.coverageBoundary"
                : "frameworkOnboarding.relayJsCoverage"
            )}
          </p>
          <p>{t("frameworkOnboarding.lifecycleHelp")}</p>
          <p>{t("frameworkOnboarding.relayScope")}</p>
          {isPython && <p>{t("relayOnboarding.executionBoundary")}</p>}
          <a
            className="underline"
            href={
              activeExample?.officialDocs ??
              "https://docs.nvidia.com/nemo/relay/about-nemo-relay/overview"
            }
            target="_blank"
            rel="noreferrer"
          >
            {t("frameworkOnboarding.officialDocs")}
          </a>
        </>
      }
      codeBlocks={
        activeExample && (!initialProjectId || isEditing)
          ? [
              ...(isEditing ? [] : environmentBlocks(credential.key)),
              {
                label: t("relayOnboarding.installLabel"),
                language: "shell",
                value:
                  (isPython
                    ? activeExample.pipInstall
                    : activeExample.npmInstall) ?? "",
                description: t(
                  isPython
                    ? "frameworkOnboarding.relayVersions"
                    : "frameworkOnboarding.relayJsVersions"
                ),
                copiedLabel,
                copyErrorLabel,
                onCopy: copy,
                headerLeft: (
                  <span>
                    {isPython ? "pip" : "npm"} ·{" "}
                    {activeExample.runtime ?? "Python 3.11+"}
                  </span>
                ),
              },
              ...(showExisting && activeExample.bootstrapExample
                ? [
                    {
                      label: t("relayOnboarding.bootstrapTitle"),
                      language: "python" as const,
                      value: activeExample.bootstrapExample ?? "",
                      description: t("frameworkOnboarding.adapterHelp"),
                      collapsible: true,
                      action: (
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={!activeExample.bootstrapExample}
                          onClick={() =>
                            downloadOnboardingFile(
                              "relay_tracing.py",
                              activeExample.bootstrapExample ?? ""
                            )
                          }
                        >
                          {t("frameworkOnboarding.downloadAdapter")}
                        </Button>
                      ),
                      copiedLabel,
                      copyErrorLabel,
                      onCopy: copy,
                      headerLeft: (
                        <span className="font-mono text-xs">
                          relay_tracing.py
                        </span>
                      ),
                    },
                  ]
                : []),
              {
                label: t("langfuseOnboarding.exampleTitle"),
                language: effectiveLanguage,
                action:
                  !isPython && showExisting ? (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={downloadExample}
                    >
                      {t("frameworkOnboarding.downloadModule", {
                        filename: exampleFilename,
                      })}
                    </Button>
                  ) : undefined,
                description: (
                  <div className="space-y-2">
                    <p>
                      {t(`frameworkOnboarding.relayNotes.${activeExample.id}`, {
                        defaultValue: [activeExample.integrationNotes ?? ""]
                          .flat()
                          .join(" "),
                      })}
                    </p>
                    {showExisting && (
                      <p>
                        {t(
                          isPython
                            ? "frameworkOnboarding.existingVariables"
                            : "frameworkOnboarding.relayJsExistingHelp",
                          { filename: exampleFilename }
                        )}
                      </p>
                    )}
                  </div>
                ),
                value:
                  (showExisting
                    ? activeExample.existingExample
                    : completeSnippet) ?? "",
                copiedLabel,
                copyErrorLabel,
                onCopy: copy,
                headerLeft: (
                  <Tabs
                    value={exampleMode}
                    onValueChange={(value) =>
                      setExampleMode(
                        value === "complete" ? "complete" : "existing"
                      )
                    }
                  >
                    <TabsList className="h-7">
                      <TabsTrigger value="existing">
                        {t("frameworkOnboarding.existingCode")}
                      </TabsTrigger>
                      <TabsTrigger value="complete">
                        {t("frameworkOnboarding.completeExample")}
                      </TabsTrigger>
                    </TabsList>
                  </Tabs>
                ),
                headerRight: !showExisting ? (
                  <Button size="sm" variant="ghost" onClick={downloadExample}>
                    {t("frameworkOnboarding.downloadExample")}
                  </Button>
                ) : undefined,
              },
              ...(!showExisting
                ? [
                    {
                      label: t("frameworkOnboarding.runExample"),
                      language: "shell" as const,
                      value: `${envFormat === "dotenv" ? "set -a\n. ./.env\nset +a\n" : ""}${activeExample.runCommand ?? `python ${exampleFilename}`}`,
                      copiedLabel,
                      copyErrorLabel,
                      onCopy: copy,
                      description: t(
                        activeExample.requiresModel === false
                          ? "frameworkOnboarding.runLocalHelp"
                          : "frameworkOnboarding.runHelp"
                      ),
                    },
                  ]
                : []),
            ]
          : []
      }
      codeBlocksEmpty={
        activeExample || isEditing
          ? undefined
          : t("relayOnboarding.exampleSelectFirst")
      }
    />
  )
}
