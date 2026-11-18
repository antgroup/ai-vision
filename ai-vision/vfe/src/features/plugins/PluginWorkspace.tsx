import { writeClipboardText } from "@/lib/clipboard"
import { Fragment, useState, type ReactNode } from "react"
import { useTranslation } from "react-i18next"
import { Link } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import {
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  ExternalLink,
  Layers3,
  TerminalSquare,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { SpaceGuard } from "@/components/error/SpaceAgentGuard"
import { useSpace } from "@/contexts/SpaceContext"
import { useOptionalUser } from "@/contexts/UserContext"
import {
  SandboxBindingSection,
  SandboxConnectionsSection,
} from "@/features/sandbox-connections/SandboxConnectionsSection"
import {
  listSandboxConnections,
  listSandboxProviders,
} from "@/features/sandbox-connections/api"
import { useSandboxConnectionPermissions } from "@/features/sandbox-connections/useSandboxConnectionPermissions"
import {
  openShellPluginPath,
  pluginsPath,
  sandboxPluginPath,
} from "./navigation"
import { OpenShellInstallation } from "./OpenShellInstallation"
import { OpenShellBootstrap } from "./OpenShellBootstrap"

type Level = "catalog" | "sandbox" | "openshell"
const HELP_COMMAND = "aiv plugin install openshell --help"

function Breadcrumbs({
  level,
  spaceId,
}: {
  level: Exclude<Level, "catalog">
  spaceId: string
}) {
  const { t } = useTranslation()
  const entries = [
    { label: t("plugins.title"), path: pluginsPath(spaceId) },
    { label: t("plugins.sandboxTitle"), path: sandboxPluginPath(spaceId) },
    ...(level === "openshell"
      ? [{ label: "OpenShell", path: openShellPluginPath(spaceId) }]
      : []),
  ]
  return (
    <nav
      aria-label={t("plugins.breadcrumb")}
      className="mb-7 text-xs text-muted-foreground"
    >
      <ol className="flex flex-wrap items-center gap-x-2 gap-y-1">
        {entries.slice(0, -1).map((entry, index) => (
          <Fragment key={entry.path}>
            {index > 0 && (
              <li aria-hidden="true">
                <ChevronRight className="size-3.5" />
              </li>
            )}
            <li>
              <Link
                to={entry.path}
                className="rounded-sm hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
              >
                {entry.label}
              </Link>
            </li>
          </Fragment>
        ))}
      </ol>
    </nav>
  )
}

function PageHeader({ level }: { level: Level }) {
  const { t } = useTranslation()
  const title =
    level === "catalog"
      ? t("plugins.title")
      : level === "sandbox"
        ? t("plugins.sandboxTitle")
        : "OpenShell"
  return (
    <header className="mb-7">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
    </header>
  )
}

function Catalog({ spaceId }: { spaceId: string }) {
  const { t } = useTranslation()
  return (
    <div data-testid="plugins-catalog">
      <Link
        to={sandboxPluginPath(spaceId)}
        aria-labelledby="sandbox-catalog-title"
        aria-describedby="sandbox-catalog-description"
        className="group flex items-center gap-4 rounded-xl border p-4 transition-colors hover:bg-muted/30 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none sm:gap-5 sm:p-6"
      >
        <div className="flex size-11 shrink-0 items-center justify-center rounded-lg bg-muted/60 sm:size-12">
          <Layers3 aria-hidden="true" className="size-6 stroke-[1.25]" />
        </div>
        <div className="min-w-0 flex-1">
          <h2 id="sandbox-catalog-title" className="font-semibold">
            {t("plugins.sandboxTitle")}
          </h2>
          <p
            id="sandbox-catalog-description"
            className="mt-1 text-sm text-muted-foreground"
          >
            {t("plugins.sandboxTagline")}
          </p>
        </div>
        <ChevronRight
          aria-hidden="true"
          className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5 motion-reduce:transform-none"
        />
      </Link>
    </div>
  )
}

function SandboxDirectory({
  spaceId,
  canManage,
}: {
  spaceId: string
  canManage: boolean
}) {
  const { t } = useTranslation()
  const providers = useQuery({
    queryKey: ["sandbox-providers", spaceId],
    queryFn: () => listSandboxProviders(spaceId),
  })
  const connections = useQuery({
    queryKey: ["sandbox-connections", spaceId],
    queryFn: () => listSandboxConnections(spaceId),
  })
  const openShell = providers.data?.items.find(
    (provider) => provider.id === "openshell"
  )
  return (
    <div
      data-testid="sandbox-plugin"
      className="grid items-start gap-9 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,22rem)] lg:gap-10"
    >
      <section className="min-w-0" aria-labelledby="sandbox-providers-title">
        <h2
          id="sandbox-providers-title"
          className="border-b pb-4 text-base font-semibold"
        >
          {t("plugins.availableProviders")}
        </h2>
        {providers.isLoading && (
          <p role="status" className="py-6 text-sm text-muted-foreground">
            {t("sandbox.loadingProviders")}
          </p>
        )}
        {providers.isError && (
          <div role="alert" className="space-y-3 py-6 text-sm">
            <p>{t("sandbox.catalogError")}</p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void providers.refetch()}
            >
              {t("common.retry")}
            </Button>
          </div>
        )}
        {openShell && (
          <Link
            to={openShellPluginPath(spaceId)}
            aria-label={t("plugins.openProvider")}
            className="group block border-b py-6 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none sm:px-1"
          >
            <div className="flex items-start gap-4">
              <div className="flex size-11 shrink-0 items-center justify-center rounded-lg border bg-background">
                <TerminalSquare
                  aria-hidden="true"
                  className="size-5 stroke-[1.5]"
                />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-3">
                  <h3 className="text-base font-semibold">OpenShell</h3>
                  <Badge variant="outline">{t("plugins.providerType")}</Badge>
                </div>
                <p className="mt-2 max-w-md text-sm leading-relaxed text-muted-foreground">
                  {t("plugins.providerDescription")}
                </p>
              </div>
              <ChevronRight
                aria-hidden="true"
                className="mt-1.5 size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5 motion-reduce:transform-none"
              />
            </div>
            <div className="mt-5 flex flex-wrap items-center justify-between gap-3 pl-15 text-xs">
              <span className="text-muted-foreground">
                {connections.data
                  ? t("plugins.connectionsCount", {
                      count: connections.data.items.filter(
                        (connection) => connection.provider === openShell.id
                      ).length,
                    })
                  : t(
                      connections.isError
                        ? "sandbox.loadError"
                        : "common.loading"
                    )}
              </span>
              <span className="font-medium text-foreground">
                {t("plugins.openProvider")}
              </span>
            </div>
          </Link>
        )}
        {!providers.isLoading && !providers.isError && !openShell && (
          <p className="py-6 text-sm text-muted-foreground">
            {t("plugins.noProviders")}
          </p>
        )}
      </section>
      <aside className="min-w-0 rounded-xl bg-muted/30 p-5 sm:p-6">
        <SandboxBindingSection spaceId={spaceId} canManage={canManage} />
      </aside>
    </div>
  )
}

function DeploymentHelp() {
  const { t, i18n } = useTranslation()
  const [copied, setCopied] = useState(false)
  const [failed, setFailed] = useState(false)
  const locale = i18n.resolvedLanguage === "zh-CN" ? "zh-CN" : "en"
  return (
    <details
      className="group mt-10 border-t pt-6"
      data-testid="openshell-deployment"
    >
      <summary className="flex cursor-pointer list-none items-start justify-between gap-4 rounded-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none [&::-webkit-details-marker]:hidden">
        <span>
          <span className="block text-sm font-medium">
            {t("plugins.deploymentTitle")}
          </span>
          <span className="mt-1.5 block max-w-2xl text-xs leading-relaxed text-muted-foreground">
            {t("plugins.deploymentDescription")}
          </span>
        </span>
        <span className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
          <span className="hidden sm:inline">
            {t("plugins.deploymentToggle")}
          </span>
          <ChevronDown
            aria-hidden="true"
            className="mt-0.5 size-4 transition-transform group-open:rotate-180 motion-reduce:transition-none"
          />
        </span>
      </summary>
      <div className="mt-6 max-w-2xl space-y-4 rounded-lg border bg-muted/20 p-4 sm:p-5">
        <p className="text-sm leading-relaxed text-muted-foreground">
          {t("sandbox.installHelp")}
        </p>
        <div className="flex items-center gap-3 rounded-md border bg-background px-3 py-2">
          <code className="min-w-0 flex-1 text-xs break-all">
            {HELP_COMMAND}
          </code>
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label={t("sandbox.copyCommand")}
            onClick={async () => {
              try {
                await writeClipboardText(HELP_COMMAND)
                setCopied(true)
                setFailed(false)
              } catch {
                setCopied(false)
                setFailed(true)
              }
            }}
          >
            {copied ? (
              <Check className="size-4" />
            ) : (
              <Copy className="size-4" />
            )}
          </Button>
        </div>
        {copied && (
          <p role="status" className="text-xs text-muted-foreground">
            {t("common.copied")}
          </p>
        )}
        {failed && (
          <p role="alert" className="text-xs text-destructive">
            {t("sandbox.copyError")}
          </p>
        )}
        <a
          href={`https://github.com/antgroup/ai-vision/blob/main/docs/deployment/${locale}/sandbox-plugins.md`}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 rounded-sm text-sm underline underline-offset-4 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          {t("plugins.deploymentAction")}
          <ExternalLink aria-hidden="true" className="size-3.5" />
        </a>
      </div>
    </details>
  )
}

function Inventory({ level, spaceId }: { level: Level; spaceId: string }) {
  const { t } = useTranslation()
  const [createPrefill, setCreatePrefill] = useState<{
    name: string
    image: string
  } | null>(null)
  const { canRead, canManage, query } = useSandboxConnectionPermissions(spaceId)
  let content: ReactNode
  if (query.isLoading)
    content = (
      <p role="status" className="text-sm text-muted-foreground">
        {t("common.loading")}
      </p>
    )
  else if (query.isError)
    content = (
      <div role="alert" className="space-y-3 text-sm">
        <p>{t("sandbox.loadError")}</p>
        <Button variant="outline" onClick={() => void query.refetch()}>
          {t("common.retry")}
        </Button>
      </div>
    )
  else if (!canRead)
    content = (
      <p role="alert" className="text-sm text-muted-foreground">
        {t("sandbox.noPermission")}
      </p>
    )
  else if (level === "catalog") content = <Catalog spaceId={spaceId} />
  else if (level === "sandbox")
    content = <SandboxDirectory spaceId={spaceId} canManage={canManage} />
  else
    content = (
      <div data-testid="sandbox-provider">
        <OpenShellBootstrap spaceId={spaceId} canManage={canManage} />
        <SandboxConnectionsSection
          spaceId={spaceId}
          canManage={canManage}
          createPrefill={createPrefill}
          onCreateClosed={() => setCreatePrefill(null)}
        />
        <details
          className="group mt-8 border-t pt-5"
          data-testid="openshell-advanced"
        >
          <summary className="flex cursor-pointer list-none items-center justify-between gap-3 rounded-sm text-sm text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none [&::-webkit-details-marker]:hidden">
            {t("plugins.bootstrap.advanced")}
            <ChevronDown
              aria-hidden="true"
              className="size-4 group-open:rotate-180"
            />
          </summary>
          <div className="mt-6">
            <OpenShellInstallation
              spaceId={spaceId}
              canManage={canManage}
              onAddConnection={setCreatePrefill}
            />
          </div>
        </details>
        <DeploymentHelp />
      </div>
    )
  return (
    <div className="mx-auto w-full max-w-[84rem] min-w-0 px-5 pt-6 pb-12 sm:px-8 sm:pt-8 lg:px-10">
      {level !== "catalog" && <Breadcrumbs level={level} spaceId={spaceId} />}
      <PageHeader level={level} />
      {content}
    </div>
  )
}

export function PluginWorkspace({ level }: { level: Level }) {
  const { currentSpace } = useSpace()
  const actor = useOptionalUser()?.user?.user_uuid
  return (
    <SpaceGuard>
      {currentSpace ? (
        <Inventory
          key={`${currentSpace.id}:${level}:${actor}`}
          level={level}
          spaceId={currentSpace.id}
        />
      ) : null}
    </SpaceGuard>
  )
}
