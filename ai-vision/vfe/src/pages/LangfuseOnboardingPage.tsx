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

const LANGFUSE_AGENT_PRODUCT = "langfuse"
const EXISTING_APPLICATION_OPTION = "__existing__"
const FEATURED_FRAMEWORK_OPTION_COUNT = 6

// Connection settings for services that already emit Langfuse traces,
// which has no per-framework guide to source envShell/envDotenv from. Mirrors
// the top-level envTemplate fallback the onboarding guide used to expose.
const CUSTOM_ENV_SHELL = [
  'export LANGFUSE_BASE_URL="${host}"',
  'export LANGFUSE_PUBLIC_KEY="${publicKey}"',
  'export LANGFUSE_SECRET_KEY="${secretKey}"',
]
const CUSTOM_ENV_DOTENV = CUSTOM_ENV_SHELL.map((line) =>
  line.replace(/^export\s+/, "")
)

type EnvironmentFormat = "shell" | "dotenv"

function compareFrameworkGuides(
  left: FrameworkGuide,
  right: FrameworkGuide
): number {
  const leftSort = left.frontendSort ?? Number.MAX_SAFE_INTEGER
  const rightSort = right.frontendSort ?? Number.MAX_SAFE_INTEGER
  return leftSort - rightSort
}

function getTestScriptName(frameworkName: string, language: FrameworkLanguage) {
  const frameworkSlug =
    frameworkName
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "") || "application"

  return `test_${frameworkSlug}.${{ python: "py", typescript: "ts", go: "go" }[language]}`
}

export function LangfuseOnboardingPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const initialProjectId = searchParams.get("agent_uuid")
  const [selectedFrameworkId, setSelectedFrameworkId] = useState<string | null>(
    null
  )
  const [envFormat, setEnvFormat] = useState<EnvironmentFormat>("shell")
  const [exampleMode, setExampleMode] = useState("existing")
  const [language, setLanguage] = useState<FrameworkLanguage>("python")

  const { copiedLabel, copyErrorLabel, copy } = useOnboardingCopy()
  const projects = useOnboardingProjects({
    product: LANGFUSE_AGENT_PRODUCT,
    queryKeyPrefix: "langfuse-onboarding",
    initialProjectId,
  })
  const detection = useOnboardingDetection({
    product: LANGFUSE_AGENT_PRODUCT,
    queryKeyPrefix: "langfuse-onboarding",
    projectUuid: projects.selectedProjectUuid,
  })

  const {
    agentTypes,
    applicationName,
    createApplication,
    loading,
    projects: langfuseProjects,
    selectedProject,
    selectedProjectUuid,
    setApplicationName,
    setSelectedProjectId,
    spaceId,
  } = projects
  const credential = useProjectOnboardingCredential(
    selectedProjectUuid,
    initialProjectId ? undefined : "Agent Framework Used",
    selectedProject?.status === 2
  )
  const {
    hasTraces,
    isListeningTimeout,
    isRefreshing: manualRefreshing,
    isError: traceError,
    refresh: refreshDetection,
    traceCount,
  } = detection
  const langfuseHost = window.location.origin
  const langfuseGuide = (agentTypes.find(
    (type) =>
      type.agent_product === LANGFUSE_AGENT_PRODUCT ||
      type.agent_name === LANGFUSE_AGENT_PRODUCT
  )?.config_guide ?? {}) as {
    frameworks?: FrameworkGuide[]
  }

  const allFrameworks = [...(langfuseGuide.frameworks ?? [])].sort(
    compareFrameworkGuides
  )
  const isEditing =
    !!initialProjectId && selectedProjectUuid === initialProjectId
  const savedFrameworkValue = selectedProject?.metadata?.framework
  // Null is the saved choice for an application already using Langfuse tracing.
  const initialFramework = resolveProjectFramework(
    allFrameworks,
    savedFrameworkValue,
    isEditing && savedFrameworkValue !== null
  )
  const effectiveLanguage =
    selectedFrameworkId === null && initialFramework
      ? guideLanguage(initialFramework)
      : language
  const effectiveFrameworkId =
    selectedFrameworkId ??
    initialFramework?.id ??
    (isEditing ? EXISTING_APPLICATION_OPTION : null)
  const isExistingApplication =
    effectiveFrameworkId === EXISTING_APPLICATION_OPTION
  const supportFrameworks = allFrameworks.filter(
    (guide) => guideLanguage(guide) === effectiveLanguage
  )
  const activeFramework = allFrameworks.find(
    (framework) => framework.id === effectiveFrameworkId
  )
  const frameworkUpdate =
    isEditing && selectedFrameworkId !== null
      ? isExistingApplication
        ? null
        : activeFramework?.id
      : undefined
  const verification = useOnboardingTraceVerification({
    product: LANGFUSE_AGENT_PRODUCT,
    projectUuid: selectedProjectUuid,
    frameworkUpdate,
  })

  const frameworkEnvTemplate =
    envFormat === "dotenv"
      ? (activeFramework?.envDotenv ?? CUSTOM_ENV_DOTENV)
      : (activeFramework?.envShell ?? CUSTOM_ENV_SHELL)

  const selectFramework = (guide: FrameworkGuide) => {
    setSelectedFrameworkId(guide.id)
    setLanguage(guideLanguage(guide))
  }
  const selectLanguage = (next: FrameworkLanguage) => {
    setLanguage(next)
    setSelectedFrameworkId(
      isExistingApplication && next !== "go"
        ? EXISTING_APPLICATION_OPTION
        : (guideForLanguage(allFrameworks, activeFramework, next)?.id ?? null)
    )
  }

  const selectExistingApplication = () => {
    setLanguage(effectiveLanguage)
    setSelectedFrameworkId(EXISTING_APPLICATION_OPTION)
  }

  const isPipFramework = effectiveLanguage === "python"
  const isGoFramework = effectiveLanguage === "go"
  const testScriptName =
    activeFramework?.exampleFilename ??
    getTestScriptName(
      activeFramework?.label ?? "Application",
      effectiveLanguage
    )
  const installSnippet = isPipFramework
    ? (activeFramework?.pipInstall ?? "")
    : isGoFramework
      ? (activeFramework?.install ?? "")
      : (activeFramework?.npmInstall ?? activeFramework?.install ?? "")
  const completeExampleSnippet = isPipFramework
    ? (activeFramework?.pipExample ?? "")
    : isGoFramework
      ? (activeFramework?.code ?? "")
      : (activeFramework?.npmExample ?? activeFramework?.code ?? "")
  const showExisting =
    exampleMode === "existing" && Boolean(activeFramework?.existingExample)
  const codeSnippet = showExisting
    ? (activeFramework?.existingExample ?? "")
    : completeExampleSnippet

  // Existing applications keep their model credentials and provider endpoints.
  const environmentTemplate = showExisting
    ? frameworkEnvTemplate.filter((line) =>
        /^(?:export\s+)?LANGFUSE_[A-Z0-9_]+=/.test(line.trimStart())
      )
    : frameworkEnvTemplate
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
  function environmentBlock(key: string | null): OnboardingCodeBlock {
    const envLines = environmentTemplate.map((line) =>
      line
        .replace("${host}", langfuseHost)
        .replace("${publicKey}", selectedProjectUuid ?? "agent-uuid")
        .replace("${secretKey}", key ?? "<SPACE_KEY>")
    )
    const environmentSnippet =
      envFormat === "shell"
        ? envLines.join("\n")
        : envLines.map((line) => line.replace(/^export\s+/, "")).join("\n")
    return {
      label: t("langfuseOnboarding.environmentLabel"),
      value: environmentSnippet,
      language: envFormat,
      description: (
        <>
          <p>{t("frameworkOnboarding.langfuseCredentials")}</p>
          {!isEditing && selectedProjectUuid && !credential.key && (
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
  }
  const onboardingCodeBlocks: OnboardingCodeBlock[] = [
    ...(isEditing ? [] : [environmentBlock(credential.key)]),
    ...(isExistingApplication
      ? []
      : [
          {
            label: t("langfuseOnboarding.installLabel"),
            value: installSnippet,
            language: "shell" as const,
            description: (
              <div className="space-y-2">
                <p>
                  {t(
                    isPipFramework
                      ? "frameworkOnboarding.versionHelp"
                      : isGoFramework
                        ? "frameworkOnboarding.langfuseGoVersions"
                        : "frameworkOnboarding.langfuseJsVersions"
                  )}
                </p>
                {activeFramework?.id === "google-adk" && (
                  <p>{t("frameworkOnboarding.adkVersions")}</p>
                )}
              </div>
            ),
            copiedLabel,
            copyErrorLabel,
            onCopy: copy,
            headerLeft: (
              <span className="inline-flex h-7 items-center rounded-md bg-muted px-3 text-sm font-medium text-foreground">
                {isPipFramework ? "pip" : isGoFramework ? "go modules" : "npm"}
                {activeFramework?.runtime
                  ? ` · ${activeFramework.runtime}`
                  : ""}
              </span>
            ),
          },
          {
            label: t("langfuseOnboarding.exampleTitle"),
            value: codeSnippet,
            language: effectiveLanguage,
            copiedLabel,
            copyErrorLabel,
            onCopy: copy,
            description: (
              <div className="space-y-2">
                <p>
                  {t(
                    `frameworkOnboarding.langfuseNotes.${activeFramework?.id}`,
                    {
                      defaultValue: activeFramework?.integrationNotes
                        ? [activeFramework.integrationNotes].flat().join(" ")
                        : t("frameworkOnboarding.instrumentationHelp"),
                    }
                  )}
                </p>
                {showExisting && (
                  <p>
                    {t(
                      isPipFramework
                        ? "frameworkOnboarding.existingVariables"
                        : isGoFramework
                          ? "frameworkOnboarding.existingGoVariables"
                          : "frameworkOnboarding.existingJsVariables"
                    )}
                  </p>
                )}
              </div>
            ),
            headerLeft: activeFramework?.existingExample ? (
              <Tabs value={exampleMode} onValueChange={setExampleMode}>
                <TabsList className="h-7">
                  <TabsTrigger value="existing">
                    {t("frameworkOnboarding.existingCode")}
                  </TabsTrigger>
                  <TabsTrigger value="complete">
                    {t("frameworkOnboarding.completeExample")}
                  </TabsTrigger>
                </TabsList>
              </Tabs>
            ) : (
              <span>{testScriptName}</span>
            ),
            headerRight: !showExisting ? (
              <Button
                size="sm"
                variant="ghost"
                onClick={() =>
                  downloadOnboardingFile(testScriptName, completeExampleSnippet)
                }
              >
                {t("frameworkOnboarding.downloadExample")}
              </Button>
            ) : undefined,
          },
          ...(!showExisting
            ? [
                {
                  label: t("frameworkOnboarding.runExample"),
                  language: "shell" as const,
                  value: `${envFormat === "dotenv" ? "set -a\n. ./.env\nset +a\n" : ""}${activeFramework?.runCommand ?? `${isPipFramework ? "python" : isGoFramework ? "go run" : "npx tsx"} ${testScriptName}`}`,
                  copiedLabel,
                  copyErrorLabel,
                  onCopy: copy,
                  description: t(
                    activeFramework?.requiresModel === false
                      ? "frameworkOnboarding.runLocalHelp"
                      : "frameworkOnboarding.runHelp"
                  ),
                },
              ]
            : []),
        ]),
  ]

  return (
    <OnboardingPageTemplate
      title={t("langfuseOnboarding.title")}
      description={t("langfuseOnboarding.description")}
      backLabel={t("langfuseOnboarding.backToAccess")}
      onBack={() =>
        navigate(initialProjectId ? "/access" : "/access?tab=market")
      }
      status={{
        label: !selectedProjectUuid
          ? t("langfuseOnboarding.selectProject")
          : verification.verified
            ? t("frameworkOnboarding.traceVerified")
            : traceError
              ? t("langfuseOnboarding.statusError")
              : hasTraces
                ? t("langfuseOnboarding.statusConnected", {
                    count: traceCount,
                  })
                : isListeningTimeout
                  ? t("langfuseOnboarding.statusTimeout")
                  : manualRefreshing
                    ? t("langfuseOnboarding.statusChecking")
                    : t("langfuseOnboarding.statusListening"),
        isConnected: verification.verified,
        isError: !verification.verified && traceError,
        refreshLabel: t("langfuseOnboarding.refresh"),
        isRefreshing: manualRefreshing,
        onRefresh: refreshDetection,
        viewGraphLabel: t("langfuseOnboarding.viewGraph"),
        onViewGraph: () =>
          navigate(
            `/trace?agent=${LANGFUSE_AGENT_PRODUCT}&agent_uuid=${encodeURIComponent(selectedProjectUuid ?? "")}&spaceId=${encodeURIComponent(spaceId)}&traceId=${encodeURIComponent(verification.traceId.trim().toLowerCase())}`
          ),
      }}
      supportTitle={t("langfuseOnboarding.supportMatrixTitle")}
      supportIntro={
        <div className="mt-4 space-y-4">
          {isGoFramework ? (
            <p className="text-sm leading-6 text-muted-foreground">
              {t("frameworkOnboarding.goScope")}
            </p>
          ) : (
            <div className="grid gap-3 md:grid-cols-2">
              <div className="rounded-xl border border-border bg-background p-4">
                <h3 className="text-sm font-semibold text-foreground">
                  {t("langfuseOnboarding.existingSdkTitle")}
                </h3>
                <p className="mt-2 text-sm leading-6 text-muted-foreground">
                  {t("langfuseOnboarding.existingSdkHelp")}
                </p>
                <Button
                  type="button"
                  variant={isExistingApplication ? "secondary" : "outline"}
                  size="sm"
                  className="mt-4 h-auto max-w-full whitespace-normal"
                  aria-pressed={isExistingApplication}
                  onClick={selectExistingApplication}
                >
                  {t("langfuseOnboarding.existingApplication")}
                </Button>
              </div>
              <div className="rounded-xl border border-border bg-background p-4">
                <h3 className="text-sm font-semibold text-foreground">
                  {t("langfuseOnboarding.firstSetupTitle")}
                </h3>
                <p className="mt-2 text-sm leading-6 text-muted-foreground">
                  {t("langfuseOnboarding.firstSetupHelp")}
                </p>
              </div>
            </div>
          )}
          <div className="space-y-2 text-sm leading-6 break-words text-muted-foreground">
            {!isGoFramework && (
              <p>{t("frameworkOnboarding.langfuseCompatibility")}</p>
            )}
            <p>{t("langfuseOnboarding.queryHelp")}</p>
          </div>
        </div>
      }
      supportControls={
        <FrameworkLanguageSelector
          value={effectiveLanguage}
          onChange={selectLanguage}
          languages={availableLanguages(allFrameworks)}
        />
      }
      featuredSupportOptionCount={FEATURED_FRAMEWORK_OPTION_COUNT}
      supportOptions={supportFrameworks.map((framework) => ({
        id: framework.id,
        label: t(`frameworkOnboarding.langfuseLabels.${framework.id}`, {
          defaultValue: framework.label,
        }),
        active: effectiveFrameworkId === framework.id,
        onClick: () => selectFramework(framework),
        logo: framework.logo,
        logoLayout: framework.logoLayout,
        logoInvertInDark: framework.logoInvertInDark,
      }))}
      supportHelp={
        supportFrameworks.some((framework) => framework.id === "manual-js") ? (
          <p className="mt-3 text-sm leading-6 text-muted-foreground">
            {t("frameworkOnboarding.manualInstrumentationHelp")}
          </p>
        ) : undefined
      }
      project={{
        product: LANGFUSE_AGENT_PRODUCT,
        target: initialProjectId
          ? {
              spaceId,
              sdk: "Langfuse",
              loading,
              credentials: {
                credential,
                buildCodeBlocks: (key) => [environmentBlock(key)],
              },
            }
          : undefined,
        projects: langfuseProjects,
        selectedId: selectedProjectUuid,
        onSelect: (id) => {
          setSelectedProjectId(id)
          if (isExistingApplication) return
          const saved = langfuseProjects.find((project) => project.uuid === id)
            ?.metadata?.framework
          const framework = resolveProjectFramework(allFrameworks, saved)
          if (framework) selectFramework(framework)
        },
        selectLabel: t("langfuseOnboarding.selectProject"),
        searchLabel: t("langfuseOnboarding.searchProject"),
        emptyLabel: t("langfuseOnboarding.projectSearchEmpty"),
        namePlaceholder: t("langfuseOnboarding.applicationNamePlaceholder"),
        name: applicationName,
        onNameChange: setApplicationName,
        createLabel: t("langfuseOnboarding.createApplication"),
        isCreating: createApplication.isPending,
        createDisabled: !spaceId || !applicationName.trim(),
        onCreate: () =>
          createApplication.mutate(
            activeFramework ? { framework: activeFramework.id } : undefined
          ),
        createError: createApplication.isError
          ? t("langfuseOnboarding.createApplicationFailed")
          : undefined,
      }}
      setupHelp={
        effectiveFrameworkId ? (
          <div className="space-y-2">
            <p>
              {isExistingApplication
                ? t("frameworkOnboarding.existingHelp")
                : t("frameworkOnboarding.langfuseSetup")}
            </p>
            <p>{t("frameworkOnboarding.envHelp")}</p>
          </div>
        ) : undefined
      }
      verification={
        <>
          {frameworkUpdate !== undefined &&
            frameworkUpdate !== savedFrameworkValue && (
              <p className="text-sm text-muted-foreground">
                {t("agentAccess.saveSetupHint")}
              </p>
            )}
          <TraceVerification verification={verification} />
        </>
      }
      advanced={
        <>
          <p>{t("langfuseOnboarding.legacyQueryHelp")}</p>
          <p>{t("frameworkOnboarding.lifecycleHelp")}</p>
          <a
            className="underline"
            href={
              activeFramework?.officialDocs ??
              "https://langfuse.com/docs/observability/sdk/overview"
            }
            target="_blank"
            rel="noreferrer"
          >
            {t("frameworkOnboarding.officialDocs")}
          </a>
        </>
      }
      codeBlocks={
        effectiveFrameworkId && (!initialProjectId || isEditing)
          ? onboardingCodeBlocks
          : []
      }
      codeBlocksEmpty={
        effectiveFrameworkId
          ? undefined
          : t("langfuseOnboarding.exampleSelectFirst")
      }
    />
  )
}
