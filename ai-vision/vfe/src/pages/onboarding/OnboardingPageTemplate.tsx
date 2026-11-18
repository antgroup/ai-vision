import type { ReactNode } from "react"
import { useTranslation } from "react-i18next"
import { useId, useMemo, useState } from "react"
import {
  AppWindow,
  ArrowLeft,
  Check,
  ChevronsUpDown,
  Copy,
  KeyRound,
  Loader2,
  RefreshCw,
  Route,
} from "lucide-react"

import { SpaceGuard } from "@/components/error/SpaceAgentGuard"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { cn } from "@/lib/utils"
import { HighlightedCode, type CodeLanguage } from "./HighlightedCode"
import type { useProjectOnboardingCredential } from "./useOnboarding"

const PROJECT_SEARCH_PAGE_SIZE = 100

type OnboardingProject = {
  uuid: string
  name: string
  customName: string | null
}

type OnboardingSupportOption = {
  id: string
  label: ReactNode
  active: boolean
  onClick: () => void
  logo?: string
  logoLayout?: "wide"
  logoInvertInDark?: boolean
}

type ProjectCredential = ReturnType<typeof useProjectOnboardingCredential>

export type OnboardingCodeBlock = {
  label: string
  value: string
  language: CodeLanguage
  copiedLabel: string | null
  copyErrorLabel?: string | null
  onCopy: (label: string, value: string) => void
  headerLeft?: ReactNode
  headerRight?: ReactNode
  description?: ReactNode
  action?: ReactNode
  collapsible?: boolean
  credential?: ProjectCredential
}

type OnboardingStatus = {
  label: ReactNode
  isConnected?: boolean
  isError?: boolean
  refreshLabel: ReactNode
  isRefreshing?: boolean
  onRefresh: () => void
  viewGraphLabel?: ReactNode
  onViewGraph?: () => void
}

type OnboardingProjectSection = {
  product?: string
  target?: {
    spaceId: string
    sdk: string
    loading: boolean
    credentials: ProjectCredentialDisclosureProps
  }
  projects: OnboardingProject[]
  selectedId: string | null
  onSelect: (projectId: string) => void
  selectLabel: ReactNode
  searchLabel: string
  emptyLabel: ReactNode
  namePlaceholder: string
  name: string
  onNameChange: (value: string) => void
  createLabel: ReactNode
  isCreating?: boolean
  createDisabled?: boolean
  onCreate: () => void
  createError?: ReactNode
}

type OnboardingTemplateProps = {
  title: ReactNode
  description: ReactNode
  backLabel: ReactNode
  onBack: () => void
  status: OnboardingStatus
  supportTitle: ReactNode
  supportIntro?: ReactNode
  supportControls?: ReactNode
  supportHelp?: ReactNode
  supportOptions: OnboardingSupportOption[]
  featuredSupportOptionCount?: number
  project: OnboardingProjectSection
  codeBlocks: OnboardingCodeBlock[]
  codeBlocksEmpty?: ReactNode
  setupHelp?: ReactNode
  verification?: ReactNode
  advanced?: ReactNode
}

function CodeBlock({
  label,
  value,
  language,
  copiedLabel,
  copyErrorLabel,
  onCopy,
  headerLeft,
  headerRight,
  credential,
}: OnboardingCodeBlock) {
  const { t } = useTranslation()
  const copied = copiedLabel === label
  const languageLabel = {
    python: "Python",
    typescript: "TypeScript",
    go: "Go",
    shell: "Shell",
    dotenv: ".env",
  }[language]

  return (
    <div className="overflow-hidden rounded-xl border border-border bg-background">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b px-3 py-2">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground">{languageLabel}</span>
          {headerLeft}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {credential ? (
            <Button
              type="button"
              size="sm"
              disabled={credential.isPending || !credential.canReveal}
              onClick={credential.key ? credential.hide : credential.show}
            >
              {credential.isPending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <KeyRound className="size-3.5" />
              )}
              {t(
                credential.key
                  ? "agentAccess.hideProjectCredential"
                  : "agentAccess.viewProjectCredential"
              )}
            </Button>
          ) : null}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 px-2 text-xs font-normal text-muted-foreground"
            aria-label={`${t("common.copy")} ${label}`}
            onClick={() => onCopy(label, value)}
          >
            {copied ? (
              <Check className="size-3.5 text-emerald-600" />
            ) : (
              <Copy className="size-3.5" />
            )}
            {copied ? (
              <span className="pl-1 text-xs font-normal">
                {t("common.copied")}
              </span>
            ) : null}
          </Button>
          {headerRight ? (
            <div className="ml-2 flex items-center">{headerRight}</div>
          ) : null}
        </div>
      </div>
      <HighlightedCode value={value} language={language} label={label} />
      {credential?.isOneTimeKey && (
        <p className="px-4 pb-3 text-xs text-muted-foreground">
          {t("agentAccess.oneTimeCredentialHint")}
        </p>
      )}
      {credential?.isError && (
        <p role="alert" className="px-4 pb-3 text-xs text-destructive">
          {t("agentAccess.projectCredentialFailed")}
        </p>
      )}
      {copyErrorLabel === label && (
        <p role="alert" className="px-4 pb-3 text-xs text-destructive">
          {t("common.copyFailed")}
        </p>
      )}
    </div>
  )
}

function CodeSection({
  block,
  guide,
}: {
  block: OnboardingCodeBlock
  guide?: boolean
}) {
  const { t } = useTranslation()
  return (
    <div
      data-product-guide={guide ? "onboarding-code" : undefined}
      className="min-w-0 space-y-2"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-foreground">{block.label}</h3>
        {block.action}
      </div>
      {block.description && (
        <div className="text-sm leading-6 text-muted-foreground">
          {block.description}
        </div>
      )}
      {block.collapsible ? (
        <details className="space-y-2">
          <summary className="cursor-pointer text-sm text-muted-foreground">
            {t("frameworkOnboarding.showSource")}
          </summary>
          <CodeBlock {...block} />
        </details>
      ) : (
        <CodeBlock {...block} />
      )}
    </div>
  )
}

type ProjectCredentialDisclosureProps = {
  credential: ProjectCredential
  buildCodeBlocks: (key: string | null) => OnboardingCodeBlock[]
}

function ProjectCredentialDisclosure({
  credential,
  buildCodeBlocks,
}: ProjectCredentialDisclosureProps) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)
  const contentId = useId()
  const revealedKey = expanded ? credential.key : null

  return (
    <div className="mt-4">
      <Button
        type="button"
        size="sm"
        disabled={!credential.canReveal}
        aria-expanded={expanded}
        aria-controls={contentId}
        onClick={() => {
          setExpanded(!expanded)
          if (expanded) credential.hide()
          else credential.show()
        }}
      >
        <KeyRound className="size-3.5" aria-hidden="true" />
        {t(
          expanded
            ? "agentAccess.hideProjectCredential"
            : "agentAccess.viewProjectCredential"
        )}
      </Button>
      <div id={contentId} hidden={!expanded} className="mt-3 min-w-0 space-y-3">
        {expanded && (
          <>
            <p className="text-sm text-muted-foreground">
              {t("agentAccess.projectCredentialHint")}
            </p>
            {revealedKey ? null : credential.isError ? (
              <div className="space-y-3">
                <p role="alert" className="text-sm text-destructive">
                  {t("agentAccess.projectCredentialFailed")}
                </p>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={credential.show}
                >
                  {t("common.retry")}
                </Button>
              </div>
            ) : (
              <p
                role="status"
                className="flex items-center gap-2 text-muted-foreground"
              >
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                {t("common.loading")}
              </p>
            )}
          </>
        )}
      </div>
      <div className="mt-3 min-w-0 space-y-4">
        {buildCodeBlocks(revealedKey).map((block) => (
          <CodeSection key={block.label} block={block} />
        ))}
      </div>
    </div>
  )
}

function ProjectCombobox({
  projects,
  selectedId,
  onSelect,
  selectLabel,
  searchLabel,
  emptyLabel,
}: {
  projects: OnboardingProject[]
  selectedId: string | null
  onSelect: (projectId: string) => void
  selectLabel: ReactNode
  searchLabel: string
  emptyLabel: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState("")
  const id = useId()
  const labelId = `${id}-label`
  const valueId = `${id}-value`
  const selectedProject = projects.find(
    (project) => project.uuid === selectedId
  )
  const filteredProjects = useMemo(() => {
    const query = search.trim().toLowerCase()
    if (!query) return projects
    return projects.filter((project) =>
      (project.customName || project.name).toLowerCase().includes(query)
    )
  }, [projects, search])
  const visibleProjects = filteredProjects.slice(0, PROJECT_SEARCH_PAGE_SIZE)

  return (
    <Popover open={open} onOpenChange={setOpen}>
      {selectedProject && (
        <span id={labelId} className="sr-only">
          {selectLabel}
        </span>
      )}
      <PopoverTrigger asChild>
        <Button
          className="h-9 w-full justify-between font-normal"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          aria-labelledby={selectedProject ? `${labelId} ${valueId}` : valueId}
        >
          <span id={valueId} className="truncate">
            {selectedProject
              ? selectedProject.customName || selectedProject.name
              : selectLabel}
          </span>
          <ChevronsUpDown className="size-4 shrink-0 text-muted-foreground" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="w-(--radix-popover-trigger-width) p-1"
      >
        <Command shouldFilter={false}>
          <CommandInput placeholder={searchLabel} onValueChange={setSearch} />
          <CommandList className="max-h-64">
            <CommandEmpty>{emptyLabel}</CommandEmpty>
            {visibleProjects.map((project) => (
              <CommandItem
                key={project.uuid}
                value={project.uuid}
                data-checked={project.uuid === selectedId}
                onSelect={() => {
                  onSelect(project.uuid)
                  setOpen(false)
                }}
              >
                <span className="min-w-0 truncate">
                  {project.customName || project.name}
                </span>
              </CommandItem>
            ))}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}

export function OnboardingPageTemplate({
  title,
  description,
  backLabel,
  onBack,
  status,
  supportTitle,
  supportIntro,
  supportControls,
  supportHelp,
  supportOptions,
  featuredSupportOptionCount,
  project,
  codeBlocks,
  codeBlocksEmpty,
  setupHelp,
  verification,
  advanced,
}: OnboardingTemplateProps) {
  const { t } = useTranslation()
  const [showAllSupportOptions, setShowAllSupportOptions] = useState(false)
  const featuredCount = featuredSupportOptionCount ?? supportOptions.length
  const activeSupportOptionIsHidden =
    !showAllSupportOptions &&
    supportOptions.some(
      (option, index) => option.active && index >= featuredCount
    )
  const showAllOptions = showAllSupportOptions || activeSupportOptionIsHidden
  const visibleSupportOptions = showAllOptions
    ? supportOptions
    : supportOptions.slice(0, featuredCount)
  const targetProject = project.target
    ? project.projects.find((item) => item.uuid === project.selectedId)
    : null

  return (
    <SpaceGuard>
      <div
        data-product-guide="connection"
        data-guide-agent={project.selectedId || ""}
        data-guide-product={project.product || ""}
        className="mx-auto w-full max-w-6xl px-6 py-8"
      >
        <Button className="mb-6" variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="size-4" />
          {backLabel}
        </Button>

        <div className="mb-8">
          <h1 className="text-3xl leading-tight font-semibold tracking-tight text-foreground">
            {title}
          </h1>
          <p className="mt-4 max-w-3xl text-sm leading-6 text-muted-foreground">
            {description}
          </p>
        </div>

        <section className="rounded-3xl border bg-card p-6 shadow-lg lg:p-8">
          <h2 className="text-xl font-semibold text-foreground">
            {supportTitle}
          </h2>
          {supportIntro}
          <div
            data-product-guide="onboarding-framework"
            data-guide-state={codeBlocks.length > 0 ? "ready" : "waiting"}
          >
            {supportControls}
            <div className="mt-4 flex flex-wrap items-center gap-1.5">
              {visibleSupportOptions.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  aria-pressed={option.active}
                  className={cn(
                    "flex max-w-full items-center gap-1.5 border px-2 py-1.5 text-[13px] leading-5 whitespace-nowrap transition-colors",
                    option.logo ? "rounded-xl" : "rounded-lg",
                    option.active
                      ? "border-primary bg-primary/10 text-foreground"
                      : option.logo
                        ? "border-border bg-background hover:bg-muted"
                        : "border-border text-foreground hover:bg-muted"
                  )}
                  onClick={option.onClick}
                >
                  {option.logo && (
                    <img
                      className={cn(
                        "object-contain",
                        option.logoLayout === "wide" ? "h-6 w-16" : "size-5",
                        option.logoInvertInDark &&
                          "dark:brightness-0 dark:invert"
                      )}
                      src={option.logo}
                      alt=""
                      aria-hidden="true"
                    />
                  )}
                  <span className="whitespace-nowrap">{option.label}</span>
                </button>
              ))}
              {supportOptions.length > featuredCount ? (
                <button
                  type="button"
                  aria-label={
                    showAllOptions ? t("common.collapse") : t("common.showMore")
                  }
                  className={cn(
                    "flex max-w-full items-center rounded-xl border border-border bg-background text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
                    "px-2 py-1.5 text-[13px] leading-5 whitespace-nowrap"
                  )}
                  onClick={() =>
                    setShowAllSupportOptions((expanded) => !expanded)
                  }
                >
                  {showAllOptions ? (
                    t("common.collapse")
                  ) : (
                    <span className="whitespace-nowrap">
                      {t("common.showMore")}
                    </span>
                  )}
                </button>
              ) : null}
            </div>
          </div>

          {supportHelp}

          <div
            data-product-guide="onboarding-project"
            data-guide-state={project.selectedId ? "ready" : "waiting"}
            className="mt-6 rounded-2xl border border-border bg-background p-4"
          >
            {project.target ? (
              targetProject ? (
                <>
                  <dl className="grid gap-3 text-sm sm:grid-cols-2">
                    <div className="min-w-0">
                      <dt className="text-muted-foreground">
                        {t("agentAccess.projectName")}
                      </dt>
                      <dd className="font-medium break-words">
                        {targetProject.customName || targetProject.name}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">SDK</dt>
                      <dd>{project.target.sdk}</dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">
                        {t("agentAccess.projectId")}
                      </dt>
                      <dd className="font-mono text-xs break-all">
                        {targetProject.uuid}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">
                        {t("spaceSettings.page.spaceId")}
                      </dt>
                      <dd className="font-mono text-xs break-all">
                        {project.target.spaceId}
                      </dd>
                    </div>
                  </dl>
                  <ProjectCredentialDisclosure
                    key={`${project.target.spaceId}:${targetProject.uuid}:${project.target.credentials.credential.canReveal}`}
                    {...project.target.credentials}
                  />
                </>
              ) : (
                <p className="text-sm text-muted-foreground" role="status">
                  {project.target.loading
                    ? t("common.loading")
                    : t("agentAccess.projectUnavailable")}
                </p>
              )
            ) : (
              <>
                <ProjectCombobox
                  projects={project.projects}
                  selectedId={project.selectedId}
                  onSelect={project.onSelect}
                  selectLabel={project.selectLabel}
                  searchLabel={project.searchLabel}
                  emptyLabel={project.emptyLabel}
                />
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <Input
                    className="h-9 min-w-48 flex-1 text-sm"
                    value={project.name}
                    placeholder={project.namePlaceholder}
                    aria-label={project.namePlaceholder}
                    maxLength={128}
                    onChange={(event) =>
                      project.onNameChange(event.target.value)
                    }
                  />
                  <Button
                    size="sm"
                    disabled={
                      project.isCreating ||
                      project.createDisabled ||
                      !project.name.trim()
                    }
                    onClick={project.onCreate}
                  >
                    {project.isCreating ? (
                      <Loader2 className="size-3.5 animate-spin" />
                    ) : (
                      <AppWindow className="size-3.5" />
                    )}
                    {project.createLabel}
                  </Button>
                </div>
                {project.createError ? (
                  <p className="mt-2 text-xs text-destructive">
                    {project.createError}
                  </p>
                ) : null}
              </>
            )}
          </div>

          {setupHelp && (
            <div className="mt-6 text-sm leading-6 text-muted-foreground">
              {setupHelp}
            </div>
          )}

          {codeBlocks.length > 0 ? (
            <div className="mt-6 space-y-6">
              {codeBlocks.map((block, index) => (
                <CodeSection
                  key={block.label}
                  block={block}
                  guide={index === 0}
                />
              ))}
            </div>
          ) : codeBlocksEmpty ? (
            <p className="mt-6 text-sm leading-6 text-muted-foreground">
              {codeBlocksEmpty}
            </p>
          ) : null}

          <div className="mt-8 space-y-4 border-t pt-6">
            {verification}
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p
                role="status"
                className="flex items-center gap-2 text-sm text-muted-foreground"
              >
                {status.isConnected ? (
                  <Check className="size-4 text-emerald-600" />
                ) : null}
                {status.label}
              </p>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={status.isRefreshing}
                  onClick={status.onRefresh}
                >
                  {status.isRefreshing ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <RefreshCw className="size-3.5" />
                  )}
                  {status.refreshLabel}
                </Button>
                {status.isConnected && status.onViewGraph ? (
                  <Button size="sm" onClick={status.onViewGraph}>
                    <Route className="size-3.5" />
                    {status.viewGraphLabel}
                  </Button>
                ) : null}
              </div>
            </div>
          </div>
          {advanced && (
            <details className="mt-6 border-t pt-4">
              <summary className="cursor-pointer text-sm font-medium">
                {t("frameworkOnboarding.advanced")}
              </summary>
              <div className="mt-3 space-y-3 text-sm leading-6 text-muted-foreground">
                {advanced}
              </div>
            </details>
          )}
        </section>
      </div>
    </SpaceGuard>
  )
}
