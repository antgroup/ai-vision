import { writeClipboardText } from "@/lib/clipboard"
import { toast } from "sonner"
import { type CSSProperties, useCallback, useRef, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { useTranslation } from "react-i18next"
import type { TFunction } from "i18next"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useAgent, type AgentType } from "@/contexts/AgentContext"
import {
  BadgeCheck,
  Bot,
  Check,
  CircleCheck,
  CircleMinus,
  Copy,
  Info,
  Loader2,
  Plus,
  Store,
  Terminal,
  X,
} from "lucide-react"

import { api, throwOnApiError } from "@/lib/api"
import { useSpace } from "@/contexts/SpaceContext"
import { useUser } from "@/contexts/UserContext"
import { Button } from "@/components/ui/button"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { AlertDialogContent } from "@/components/dialogs/AlertDialogContent"
import { CardContent, CardHeader } from "@/components/ui/card"
import {
  Dialog,
  DialogClose,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog"
import { DialogContent } from "@/components/dialogs/DialogContent"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { AgentCard } from "@/components/agents/AgentCard"
import { Hint } from "@/components/error/Hint"
import { SpaceGuard } from "@/components/error/SpaceAgentGuard"
import { WarningsBar } from "@/components/agents/guidance"
import { OnboardingGuideSections } from "@/components/agents/guidance/OnboardingGuideSections"
import { HermesRelayGuide } from "./onboarding/HermesRelayGuide"
import { isHermesRelayOnboarding } from "./onboarding/hermesRelayConfiguration"
import { ApplicationConnectionDetails } from "./onboarding/ApplicationConnectionDetails"
import { EnableSteps, Fireworks } from "@/components/agents/celebration"
import {
  sortItemsByAgentProduct,
  getCardThemes,
  getMarketTheme,
  isImageIcon,
  getIconFallback,
  getCapability,
} from "@/agents/lib/agentProducts"
import { cn } from "@/lib/utils"
import { resolveAgentDisplayName } from "@/agents/lib/agent-display"
import { buildAgentDashboardUrl } from "@/agents/lib/agentDashboardRoute"
import {
  isConfigGuideV2,
  type ConfigGuide,
  type ConfigGuideLegacy,
} from "@/components/agents/guidance/types"
import {
  getAgentMarketGroups,
  getIntegrationProjectIdentity,
  isManualPluginGuide,
  type AgentIntegration,
  type FrameworkPreview,
} from "./onboarding/integrations"

// ── Grid class shared across fleet + market ──
const CARD_GRID =
  "grid grid-cols-[repeat(auto-fill,minmax(min(100%,_340px),1fr))] gap-5"
const FRAMEWORK_LOGO_IMAGE_CLASS = "size-full object-contain"
const MARKET_LOGO_CONTAINER_CLASS =
  "flex size-9 shrink-0 items-center justify-center rounded-lg bg-background dark:bg-muted"
const MARKET_LOGO_IMAGE_CLASS = "size-full object-contain p-0.5"

type AccessTab = "fleet" | "market"

const ACCESS_TAB_QUERY_KEY = "tab"
const DEFAULT_ACCESS_TAB: AccessTab = "fleet"

function isAccessTab(value: string | null): value is AccessTab {
  return value === "fleet" || value === "market"
}

function MarketLogo({ src, alt = "" }: { src: string; alt?: string }) {
  return (
    <div className={MARKET_LOGO_CONTAINER_CLASS}>
      <img src={src} alt={alt} className={MARKET_LOGO_IMAGE_CLASS} />
    </div>
  )
}

// ── Shared types ──

type AgentTypeData = AgentType & {
  config_guide?: ConfigGuide | null
}

interface FleetAgent {
  id: number
  uuid: string
  name: string
  agent_product: string
  custom_name: string | null
  description: string | null
  metadata: Record<string, unknown> | null
  status: number
  icon?: string | null
}

interface ModalAgent {
  name: string
  product: string
  setupMode: string
  status: string
  desc: string
  cliCommand?: string
  configGuide?: ConfigGuide
  capabilities: Record<string, unknown> | null
  disabledAgentUuid?: string
  agentUuid?: string
  readOnly?: boolean
}

// ── Helpers ──

function getAgentDescription(
  description: string,
  configGuide: ConfigGuide | null | undefined,
  t: TFunction
): string {
  // Localize the seeded plugin description while preserving custom content.
  if (
    configGuide &&
    isConfigGuideV2(configGuide) &&
    description === t("agentAccess.pluginDescription", { lng: "zh-CN" })
  ) {
    if (configGuide.native_relay_onboarding?.enabled)
      return t("hermesOnboarding.marketDescription")
    return t("agentAccess.pluginDescription")
  }
  return description
}

function interpolateVars<T>(obj: T, vars: Record<string, string>): T {
  if (typeof obj === "string") {
    let result: string = obj
    for (const [key, value] of Object.entries(vars)) {
      result = result.replaceAll(`{${key}}`, value)
    }
    return result as T
  }
  if (Array.isArray(obj))
    return obj.map((item) => interpolateVars(item, vars)) as T
  if (obj && typeof obj === "object") {
    return Object.fromEntries(
      Object.entries(obj).map(([key, value]) => [
        key,
        interpolateVars(value, vars),
      ])
    ) as T
  }
  return obj
}

// ── Agent Fleet Content ──

function AgentFleetContent({
  agents,
  agentTypes,
  onGoToMarket,
  onRefresh,
  onViewGuide,
}: {
  agents: FleetAgent[]
  agentTypes: AgentTypeData[]
  onGoToMarket?: () => void
  onRefresh: () => void
  onViewGuide: (agent: ModalAgent, trigger: HTMLButtonElement) => void
}) {
  const { currentSpace } = useSpace()
  const navigate = useNavigate()
  const spaceId = currentSpace?.id ?? ""
  const canManage = currentSpace?.permissions.includes("agent.manage") ?? false
  const queryClient = useQueryClient()

  const [removeTarget, setRemoveTarget] = useState<{
    uuid: string
    name: string
    product: string
    isBuiltin: boolean
  } | null>(null)

  const removeMutation = useMutation({
    mutationFn: async (agentUuid: string) => {
      return throwOnApiError(
        await api.post<{ code: number; message: string }>(
          `/spaces/${spaceId}/agents/${agentUuid}/disable`
        )
      )
    },
    onSuccess: async () => {
      setRemoveTarget(null)
      await Promise.allSettled([
        queryClient.invalidateQueries({ queryKey: ["agents", spaceId] }),
        queryClient.invalidateQueries({ queryKey: ["agentTypes"] }),
      ])
      onRefresh()
    },
  })
  const { t } = useTranslation()
  const agentTypeNameMap = new Map(
    agentTypes.flatMap((agentType) => {
      const label = resolveAgentDisplayName(agentType.agent_product, [
        agentType,
      ])
      return [
        [agentType.agent_name, label],
        [agentType.agent_product, label],
      ]
    })
  )
  const agentTypeDescMap = new Map(
    agentTypes.flatMap((agentType) => {
      const description = getAgentDescription(
        agentType.description,
        agentType.config_guide,
        t
      )
      return [
        [agentType.agent_name, description],
        [agentType.agent_product, description],
      ]
    })
  )
  const agentTypeIconMap = new Map(
    agentTypes.flatMap((agentType) => [
      [agentType.agent_name, agentType.icon],
      [agentType.agent_product, agentType.icon],
    ])
  )
  const capabilitiesMap = new Map(
    agentTypes.flatMap((agentType) => [
      [agentType.agent_name, agentType.capabilities],
      [agentType.agent_product, agentType.capabilities],
    ])
  )
  const agentTypeByProductOrName = new Map(
    agentTypes.flatMap((agentType) => [
      [agentType.agent_name, agentType],
      [agentType.agent_product, agentType],
    ])
  )

  const allFleetAgents: FleetAgent[] = sortItemsByAgentProduct(
    agents
      .filter((agent) => agent.status !== 0) // 不展示已移除的
      .map((agent) => ({
        id: agent.id,
        uuid: agent.uuid,
        name: agent.custom_name || agent.name,
        agent_product: agent.agent_product,
        custom_name: agent.custom_name,
        description: agent.description,
        metadata: agent.metadata,
        status: agent.status,
      }))
  )

  const isBuiltIn = (agentProduct: string) => {
    const category = agentTypeByProductOrName.get(agentProduct)?.category
    return category === "BUILT_IN" || category === "BUILTIN" || !category
  }

  const builtInFleet = allFleetAgents.filter(
    (agent) =>
      isBuiltIn(agent.agent_product) &&
      !getIntegrationProjectIdentity(agent, agentTypes)
  )
  const integrationFleet = allFleetAgents.filter((agent) =>
    getIntegrationProjectIdentity(agent, agentTypes)
  )
  const customFleet = allFleetAgents.filter(
    (agent) =>
      !isBuiltIn(agent.agent_product) &&
      !getIntegrationProjectIdentity(agent, agentTypes)
  )

  if (allFleetAgents.length === 0) {
    return (
      <Hint
        icon={<Bot />}
        title={t("hint.noAgent")}
        description={t("hint.noAgentDescEmpty")}
        action={
          onGoToMarket ? (
            <Button
              variant="outline"
              className="rounded-full px-6"
              onClick={onGoToMarket}
            >
              <Store className="mr-2 size-4" />
              {t("hint.goToMarket")}
            </Button>
          ) : undefined
        }
      />
    )
  }

  const renderGroup = (
    title: string,
    items: FleetAgent[],
    isApplication = false
  ) => {
    if (items.length === 0) return null
    const themes = getCardThemes(items.map((agent) => agent.agent_product))
    return (
      <div className="space-y-3">
        <h3 className="text-sm font-medium text-foreground">{title}</h3>
        <div className={CARD_GRID}>
          {items.map((agent, index) => {
            const identity = getIntegrationProjectIdentity(agent, agentTypes)
            const displayName =
              identity?.displayName ||
              agentTypeNameMap.get(agent.agent_product) ||
              resolveAgentDisplayName(agent.agent_product, [])
            const agentType = isApplication
              ? agentTypeByProductOrName.get(agent.agent_product)
              : undefined
            return (
              <AgentCard
                key={agent.id}
                agentProduct={agent.agent_product}
                agentUuid={agent.uuid}
                displayName={displayName}
                description={
                  identity
                    ? t("agentAccess.connectedVia", {
                        channel: identity.integration.displayName,
                      })
                    : agentTypeDescMap.get(agent.agent_product) ||
                      agent.description
                }
                icon={
                  identity
                    ? typeof agent.metadata?.logo === "string"
                      ? agent.metadata.logo
                      : null
                    : agent.icon || agentTypeIconMap.get(agent.agent_product)
                }
                theme={themes[index]}
                status={agent.status}
                statusText={
                  identity
                    ? agent.status === 1
                      ? t("agentAccess.connected")
                      : t("langfuseOnboarding.frameworkNotConnected")
                    : undefined
                }
                framework={identity?.framework}
                capabilities={capabilitiesMap.get(agent.agent_product)}
                onViewConnection={
                  isApplication && agent.uuid
                    ? (event) =>
                        onViewGuide(
                          {
                            name: displayName,
                            product: agent.agent_product,
                            setupMode: agentType?.setup_mode || "AUTO",
                            status:
                              agent.status === 1 ? "connected" : "pending",
                            desc:
                              agentType?.description || agent.description || "",
                            configGuide: agentType?.config_guide ?? undefined,
                            capabilities:
                              capabilitiesMap.get(agent.agent_product) ?? null,
                            agentUuid: agent.uuid,
                            readOnly: true,
                          },
                          event.currentTarget
                        )
                    : undefined
                }
                integration={
                  identity
                    ? {
                        icon: identity.integration.icon,
                        onEdit: canManage
                          ? () => {
                              const url = new URL(
                                identity.integration.onboardingPath,
                                window.location.origin
                              )
                              url.searchParams.set("agent_uuid", agent.uuid)
                              navigate(
                                `${url.pathname}${url.search}${url.hash}`
                              )
                            }
                          : undefined,
                      }
                    : undefined
                }
                onRemove={
                  canManage
                    ? () =>
                        setRemoveTarget({
                          uuid: agent.uuid,
                          name: displayName,
                          product: agent.agent_product,
                          isBuiltin:
                            !!identity || isBuiltIn(agent.agent_product),
                        })
                    : undefined
                }
              />
            )
          })}
        </div>
      </div>
    )
  }

  return (
    <>
      <div className="space-y-8">
        {renderGroup(t("langfuseOnboarding.fleetGroupTitle"), integrationFleet)}
        {renderGroup(t("agentAccess.builtInGroup"), builtInFleet)}
        {renderGroup(t("agentAccess.thirdPartyGroup"), customFleet, true)}
      </div>

      {/* Remove confirmation dialog */}
      <AlertDialog
        open={!!removeTarget}
        onOpenChange={(open) => !open && setRemoveTarget(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("agentAccess.removeTitle", { name: removeTarget?.name })}
            </AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div>
                <p>
                  {t("agentAccess.removeDescription", {
                    name: removeTarget?.name,
                  })}
                </p>
                {!removeTarget?.isBuiltin && (
                  <p className="mt-2 text-xs text-muted-foreground">
                    {t("agentAccess.removeCollectionHint")}
                  </p>
                )}
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (removeTarget) removeMutation.mutate(removeTarget.uuid)
              }}
              disabled={removeMutation.isPending}
              className="text-destructive-foreground bg-destructive hover:bg-destructive/90"
            >
              {removeMutation.isPending && (
                <Loader2 className="mr-1 size-3.5 animate-spin" />
              )}
              {t("agentAccess.confirmRemove")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

// ── Agent Market Content ──

function AgentMarketContent({
  agentTypes,
  connectedAgentMap,
  connectedAgentUuidMap,
  disabledAgentMap,
  onViewGuide,
  isLoading,
}: {
  agentTypes: AgentTypeData[]
  connectedAgentMap: Map<string, number>
  connectedAgentUuidMap: Map<string, string>
  disabledAgentMap: Map<string, string>
  onViewGuide: (agent: ModalAgent, trigger: HTMLButtonElement) => void
  isLoading: boolean
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const {
    integrations,
    builtInAgents,
    pluginAgents: customAgents,
  } = getAgentMarketGroups(agentTypes)
  const builtInThemes = getCardThemes(
    builtInAgents.map((agentType) => agentType.agent_product)
  )

  const renderMarketFrameworkIcons = (frameworks: FrameworkPreview[]) => (
    <TooltipProvider delayDuration={150}>
      <div className="mb-2 flex flex-wrap gap-1.5">
        {frameworks.map((framework) => (
          <Tooltip key={framework.id}>
            <TooltipTrigger asChild>
              <span
                aria-label={framework.marketLabel ?? framework.label}
                className={cn(
                  "flex h-8 cursor-help items-center justify-center rounded-md border border-border bg-background p-1",
                  framework.logoLayout === "wide" ? "w-16" : "w-8"
                )}
              >
                <img
                  src={framework.logo}
                  alt=""
                  aria-hidden="true"
                  className={cn(
                    FRAMEWORK_LOGO_IMAGE_CLASS,
                    framework.logoInvertInDark &&
                      "dark:brightness-0 dark:invert"
                  )}
                />
              </span>
            </TooltipTrigger>
            <TooltipContent>
              {framework.marketLabel ?? framework.label}
            </TooltipContent>
          </Tooltip>
        ))}
      </div>
    </TooltipProvider>
  )

  const renderProductFrameworkCard = ({
    testId,
    iconSrc,
    title,
    description,
    frameworks,
    kindLabel,
    actionLabel,
    onAction,
    gradientCss,
  }: {
    testId: string
    iconSrc: string | null
    title: string
    description: string
    frameworks: FrameworkPreview[]
    kindLabel: string
    actionLabel: string
    onAction: () => void
    gradientCss: string
  }) => (
    <div
      key={testId}
      className="relative min-h-[195px] overflow-hidden rounded-xl border border-border bg-card shadow-sm transition-shadow hover:shadow-md"
      data-testid={testId}
    >
      <div
        className="absolute inset-0 dark:opacity-0"
        style={{ background: gradientCss }}
      />
      <div className="relative flex h-full flex-col p-4">
        <div className="relative mb-3 flex items-center gap-2.5">
          {iconSrc ? (
            <MarketLogo src={iconSrc} />
          ) : (
            <div className={MARKET_LOGO_CONTAINER_CLASS}>
              {getIconFallback(title)}
            </div>
          )}
          <div className="flex min-w-0 items-center gap-2">
            <h3 className="truncate text-sm font-medium text-foreground">
              {title}
            </h3>
          </div>
        </div>
        <p className="mb-2 line-clamp-2 min-h-[2.8em] text-xs leading-[1.4] text-muted-foreground">
          {description}
        </p>
        <p className="mb-2 text-xs text-muted-foreground">{kindLabel}</p>
        {renderMarketFrameworkIcons(frameworks)}
        <Button
          variant="outline"
          size="sm"
          className="mt-auto w-full bg-background font-normal"
          data-testid={`${testId}-guide`}
          onClick={onAction}
        >
          {actionLabel}
        </Button>
      </div>
    </div>
  )

  const renderIntegrationCard = (
    integration: AgentIntegration,
    index: number
  ) =>
    renderProductFrameworkCard({
      testId: integration.marketTestId,
      iconSrc: integration.icon,
      title: integration.marketTitleKey
        ? t(integration.marketTitleKey)
        : integration.displayName,
      description: integration.marketDescriptionKey
        ? t(integration.marketDescriptionKey)
        : integration.description,
      frameworks: integration.frameworks
        .filter(
          (framework) => framework.marketPreview === true && !!framework.logo
        )
        .sort(
          (left, right) =>
            (left.marketSort ?? Number.MAX_SAFE_INTEGER) -
            (right.marketSort ?? Number.MAX_SAFE_INTEGER)
        ),
      kindLabel: t(`integrationKinds.${integration.kind}`),
      actionLabel: t("langfuseOnboarding.marketCardAction"),
      onAction: () => navigate(integration.onboardingPath),
      gradientCss: getMarketTheme(integration.product, index).gradientCss,
    })

  const renderMarketCard = (
    agentType: AgentTypeData,
    gradientStyle: CSSProperties
  ) => {
    const status = connectedAgentMap.get(agentType.agent_product)
    const isConnected = status === 1
    const isPending = status === 2
    const isDisabled = disabledAgentMap.has(agentType.agent_product)
    const disabledAgentUuid = disabledAgentMap.get(agentType.agent_product)
    const agentUuid = connectedAgentUuidMap.get(agentType.agent_product)
    const displayName = resolveAgentDisplayName(agentType.agent_product, [
      agentType,
    ])
    const description = getAgentDescription(
      agentType.description,
      agentType.config_guide,
      t
    )

    return (
      <div
        key={agentType.agent_name}
        className="relative w-full overflow-hidden rounded-xl border border-border bg-card shadow-sm"
      >
        <div
          className="absolute inset-0 dark:opacity-0"
          style={gradientStyle}
        />

        {isDisabled ? (
          <div className="absolute top-3 right-3 z-10">
            <span className="inline-flex items-center gap-1.5 text-xs font-normal text-muted-foreground">
              <span className="inline-block size-2 shrink-0 rounded-full bg-muted-foreground/50" />
              {t("agentAccess.removed")}
            </span>
          </div>
        ) : isConnected || isPending ? (
          <div className="absolute top-3 right-3 z-10">
            <span
              className={cn(
                "inline-flex items-center gap-1.5 text-xs font-normal",
                isPending
                  ? "text-amber-600"
                  : "text-emerald-600 dark:text-emerald-400"
              )}
            >
              <span className="inline-block size-2 shrink-0 rounded-full bg-current" />
              {isPending
                ? t("agentAccess.pending")
                : t("agentAccess.connected")}
            </span>
          </div>
        ) : null}

        <div className="relative flex h-full flex-col p-5">
          <div className="mb-4">
            {isImageIcon(agentType.icon) ? (
              <MarketLogo src={agentType.icon!} alt={displayName} />
            ) : (
              <div className={MARKET_LOGO_CONTAINER_CLASS}>
                <span className="text-xs font-medium text-foreground">
                  {getIconFallback(displayName)}
                </span>
              </div>
            )}
          </div>

          <div className="mb-4 space-y-1.5">
            <h3 className="truncate text-base font-medium text-foreground">
              {displayName}
            </h3>
            {description && description !== displayName && (
              <p className="truncate text-xs text-muted-foreground">
                {description}
              </p>
            )}
          </div>

          <Button
            variant="outline"
            size="sm"
            className="mt-auto w-full bg-background font-normal"
            aria-haspopup="dialog"
            onClick={(event) =>
              onViewGuide(
                {
                  name: displayName,
                  product: agentType.agent_product,
                  setupMode: agentType.setup_mode || "AUTO",
                  status: isDisabled
                    ? "disabled"
                    : isConnected
                      ? "connected"
                      : isPending
                        ? "pending"
                        : "available",
                  desc: agentType.description,
                  cliCommand: agentType.cli_command || undefined,
                  disabledAgentUuid,
                  agentUuid,
                  configGuide: agentType.config_guide || undefined,
                  capabilities: agentType.capabilities,
                },
                event.currentTarget
              )
            }
          >
            {t("agentAccess.viewGuide")}
          </Button>
        </div>
      </div>
    )
  }

  if (isLoading) {
    return (
      <div className={CARD_GRID}>
        {Array.from({ length: 4 }).map((_, index) => (
          <Skeleton key={index} className="h-[180px] rounded-xl" />
        ))}
      </div>
    )
  }

  return (
    <div data-product-guide="agent-market" className="space-y-8">
      {integrations.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-sm font-medium text-foreground">
            {t("langfuseOnboarding.marketGroupTitle")}
          </h3>
          <div className={CARD_GRID}>
            {integrations.map(renderIntegrationCard)}
          </div>
        </div>
      )}

      {builtInAgents.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-sm font-medium text-foreground">
            {t("agentAccess.builtInGroup")}
          </h3>
          <div className={CARD_GRID}>
            {builtInAgents.map((agentType, index) =>
              renderMarketCard(agentType, {
                background: builtInThemes[index].gradientCss,
              })
            )}
          </div>
        </div>
      )}

      {customAgents.length > 0 && (
        <div className="space-y-3">
          <div>
            <h3 className="text-sm font-medium text-foreground">
              {t("agentAccess.thirdPartyGroup")}
            </h3>
            <p className="text-xs text-muted-foreground">
              {t("agentAccess.manualCli")}
            </p>
          </div>
          <div className={CARD_GRID}>
            {customAgents.map((agentType, index) => {
              const theme = getMarketTheme(agentType.agent_product, index)
              return renderMarketCard(agentType, {
                background: theme.gradientCss,
              })
            })}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Onboarding Guide Dialog ──

function OnboardingDialog({
  modalAgent,
  onClose,
  onReturnFocus,
  onRefresh,
}: {
  modalAgent: ModalAgent
  onClose: () => void
  onReturnFocus: () => void
  onRefresh?: () => void
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { currentSpace } = useSpace()
  const { user } = useUser()
  const { refreshAgents } = useAgent()
  const spaceId = currentSpace?.id ?? ""
  const queryClient = useQueryClient()
  const canManage = currentSpace?.permissions.includes("agent.manage") ?? false
  const [agentUuid, setAgentUuid] = useState(modalAgent.agentUuid)
  const [installationAuthorization, setInstallationAuthorization] = useState<{
    installation_token: string
    expires_at: string
    base_url?: string
  } | null>(null)
  const hasInstallationCommand = Boolean(installationAuthorization)
  const isPlugin =
    isConfigGuideV2(modalAgent.configGuide) &&
    Boolean(modalAgent.configGuide.plugin_key)
  const isManualPlugin = isManualPluginGuide(
    modalAgent.setupMode,
    modalAgent.configGuide
  )
  const nativeRelayConfig = isConfigGuideV2(modalAgent.configGuide)
    ? modalAgent.configGuide.native_relay_onboarding
    : undefined
  const isNativeHermes = isHermesRelayOnboarding(
    modalAgent.product,
    nativeRelayConfig
  )
  const hermesOnboardingUnavailable =
    modalAgent.product === "Hermes" && !isNativeHermes
  const manualOnboarding = isManualPlugin || isNativeHermes
  const manualPending =
    manualOnboarding &&
    modalAgent.status !== "connected" &&
    modalAgent.status !== "disabled" &&
    (Boolean(agentUuid) || modalAgent.status === "pending")
  const configGuide = interpolateVars(modalAgent.configGuide, {
    // Use the backend's canonical onboarding origin (identical to the origin
    // catalog.json builds its artifact URLs from) so the command's --base-url
    // always passes the installer's same-origin download check. Only fall back
    // to the browser origin before the authorization response has arrived.
    baseUrl: installationAuthorization?.base_url || window.location.origin,
    spaceId,
    agentUuid: agentUuid || "",
    installationToken: installationAuthorization?.installation_token || "",
    userUuid: user?.user_uuid || "",
    agentProduct: modalAgent.product,
    name: modalAgent.name,
  })
  const [copied, setCopied] = useState(false)
  const [enablePhase, setEnablePhase] = useState<"steps" | "celebrate" | null>(
    null
  )

  const enableMutation = useMutation({
    mutationFn: async (agentProduct: string) => {
      if (!spaceId) throw new Error("Space not initialized")
      const response = throwOnApiError(
        await api.post<{
          code: number
          message: string
          data: {
            agent_uuid: string
            metadata?: Record<string, unknown>
            installation_token?: string
            expires_at?: string
            base_url?: string
          }
        }>(`/spaces/${spaceId}/agents/create`, {
          agent_product: agentProduct,
        })
      )
      if (!response.data.agent_uuid)
        throw new Error(t("agentAccess.missingAgentId"))
      if (isPlugin) {
        const authorization = throwOnApiError(
          await api.post<{
            code: number
            message: string
            data: {
              installation_token: string
              expires_at: string
              base_url?: string
            }
          }>(
            `/spaces/${spaceId}/agents/${response.data.agent_uuid}/installation-tokens`,
            {}
          )
        )
        return { ...response.data, ...authorization.data }
      }
      return response.data
    },
    onSuccess: async (agent) => {
      setAgentUuid(agent.agent_uuid)
      if (agent.installation_token && agent.expires_at) {
        setInstallationAuthorization({
          installation_token: agent.installation_token,
          expires_at: agent.expires_at,
          base_url: agent.base_url,
        })
      }
      await Promise.allSettled([
        queryClient.invalidateQueries({
          queryKey: ["agents", spaceId],
        }),
        queryClient.invalidateQueries({
          queryKey: ["agentTypes"],
        }),
        refreshAgents({ silent: true }),
      ])
      if (!isPlugin && !manualOnboarding) setEnablePhase("steps")
    },
  })

  const handleCopyCommand = async () => {
    const command =
      modalAgent.cliCommand ||
      `ai-vision connect --template "${modalAgent.name}" --instance "my-instance" --key YOUR_API_KEY`
    try {
      await writeClipboardText(command)
    } catch {
      setCopied(false)
      toast.error(t("common.copyFailed"))
      return
    }
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const handleEnable = () => {
    enableMutation.mutate(modalAgent.product)
  }

  const handleClose = () => {
    setEnablePhase(null)
    if (!modalAgent.readOnly && (isPlugin || manualOnboarding)) onRefresh?.()
    onClose()
  }

  const enableBackMutation = useMutation({
    mutationFn: async () => {
      if (!spaceId) throw new Error("Space not initialized")
      if (!modalAgent.disabledAgentUuid) throw new Error("Agent UUID missing")
      return throwOnApiError(
        await api.post<{ code: number; message: string }>(
          `/spaces/${spaceId}/agents/${modalAgent.disabledAgentUuid}/enable`
        )
      )
    },
    onSuccess: async () => {
      await Promise.allSettled([
        queryClient.invalidateQueries({ queryKey: ["agents", spaceId] }),
        queryClient.invalidateQueries({ queryKey: ["agentTypes"] }),
        refreshAgents({ silent: true }),
      ])
      onRefresh?.()
      handleClose()
    },
  })

  const description = getAgentDescription(
    modalAgent.desc,
    modalAgent.configGuide,
    t
  )

  const DialogIcon = modalAgent.readOnly ? Info : Terminal
  const dialogTitle = t(
    modalAgent.readOnly
      ? "agentAccess.connection.dialogTitle"
      : modalAgent.product === "Hermes"
        ? modalAgent.status === "disabled"
          ? "hermesOnboarding.disabledDialogTitle"
          : "hermesOnboarding.dialogTitle"
        : modalAgent.status === "connected"
          ? "agentAccess.connectedTitle"
          : "agentAccess.connectTitle",
    { name: modalAgent.name }
  )

  return (
    <Dialog open onOpenChange={(open) => !open && handleClose()}>
      <DialogContent
        size="detail"
        className="flex flex-col overflow-hidden bg-card px-0 text-card-foreground"
        showCloseButton={false}
        {...(!description ? { "aria-describedby": undefined } : {})}
        onCloseAutoFocus={(event) => {
          event.preventDefault()
          onReturnFocus()
        }}
      >
        <CardHeader className="shrink-0 pb-3">
          <div className="flex items-start justify-between gap-4">
            <div className="flex min-w-0 items-center gap-3">
              <div className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-muted">
                <DialogIcon
                  className="size-5 text-foreground"
                  aria-hidden="true"
                />
              </div>
              <div className="min-w-0 wrap-break-word">
                <DialogTitle className="leading-snug">
                  {dialogTitle}
                </DialogTitle>
                {description && (
                  <DialogDescription>{description}</DialogDescription>
                )}
              </div>
            </div>
            {!modalAgent.readOnly && (
              <DialogClose asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="shrink-0"
                  aria-label={t("common.close")}
                >
                  <X className="size-4" aria-hidden="true" />
                </Button>
              </DialogClose>
            )}
          </div>
        </CardHeader>

        <CardContent
          data-product-guide="plugin-setup"
          className="flex min-h-0 flex-col gap-4 overflow-y-auto"
        >
          {(enableMutation.error || enableBackMutation.error) && (
            <p role="alert" className="text-sm text-destructive">
              {(enableMutation.error || enableBackMutation.error)?.message}
            </p>
          )}
          {modalAgent.readOnly ? (
            <>
              <div className="min-h-0 space-y-5 overflow-y-auto pr-1">
                <ApplicationConnectionDetails
                  spaceId={spaceId}
                  agentUuid={modalAgent.agentUuid}
                />
                {isConfigGuideV2(configGuide) && (
                  <OnboardingGuideSections
                    guide={configGuide}
                    hasInstallationCommand={false}
                    agentName={modalAgent.name}
                    agentProduct={modalAgent.product}
                    readOnly
                  />
                )}
                {isPlugin && (
                  <p className="text-sm text-muted-foreground">
                    {t("agentAccess.connection.installHint")}
                  </p>
                )}
                {isConfigGuideV2(configGuide) &&
                  !!configGuide.warnings?.length && (
                    <WarningsBar warnings={configGuide.warnings} />
                  )}
              </div>
              <CapabilityBadges capabilities={modalAgent.capabilities} />
              <div className="flex shrink-0 justify-end border-t pt-4">
                <Button
                  variant="outline"
                  className="font-normal"
                  onClick={handleClose}
                >
                  {t("common.close")}
                </Button>
              </div>
            </>
          ) : hermesOnboardingUnavailable ? (
            <>
              <p role="alert" className="text-sm text-muted-foreground">
                {t("hermesOnboarding.unavailable")}
              </p>
              <div className="flex justify-end">
                <Button variant="outline" onClick={handleClose}>
                  {t("common.close")}
                </Button>
              </div>
            </>
          ) : enablePhase === "steps" ? (
            <EnableSteps onComplete={() => setEnablePhase("celebrate")} />
          ) : enablePhase === "celebrate" ? (
            <div className="flex flex-col items-center py-2">
              <div className="relative">
                <Fireworks width={400} height={180} />
                <div className="absolute inset-0 flex items-center justify-center">
                  <h3 className="animate-in text-xl font-medium text-foreground duration-500 fade-in">
                    {t("agentAccess.success")}
                  </h3>
                </div>
              </div>
              <p className="mt-2 text-sm text-muted-foreground">
                {modalAgent.configGuide?.success_message ||
                  t("agentAccess.autoCollect", { name: modalAgent.name })}
              </p>
              <div className="mt-6 flex gap-3">
                <Button
                  variant="outline"
                  className="font-normal"
                  onClick={handleClose}
                >
                  {t("common.close")}
                </Button>
                <Button
                  className="font-normal"
                  onClick={() => {
                    handleClose()
                    navigate(
                      buildAgentDashboardUrl(modalAgent.product, {
                        agentUuid: modalAgent.agentUuid || null,
                      })
                    )
                  }}
                >
                  {t("agentAccess.dashboard")}
                </Button>
              </div>
            </div>
          ) : isConfigGuideV2(configGuide) ? (
            <>
              <div className="max-h-[60vh] min-h-0 space-y-5 overflow-y-auto pr-1">
                {isNativeHermes && nativeRelayConfig ? (
                  <HermesRelayGuide
                    key={`${spaceId}:${agentUuid ?? ""}`}
                    config={{
                      ...nativeRelayConfig,
                      otlp_endpoint:
                        configGuide.native_relay_onboarding?.otlp_endpoint,
                    }}
                    agentUuid={agentUuid}
                    disabled={modalAgent.status === "disabled"}
                  />
                ) : (
                  <OnboardingGuideSections
                    guide={configGuide}
                    hasInstallationCommand={hasInstallationCommand}
                    agentName={modalAgent.name}
                    agentProduct={modalAgent.product}
                  />
                )}
                {isPlugin && !hasInstallationCommand && (
                  <p className="text-sm text-muted-foreground">
                    {canManage
                      ? t("agentAccess.generateHint", {
                          name: modalAgent.name,
                          space: currentSpace?.name,
                        })
                      : t("agentAccess.contactAdmin")}
                  </p>
                )}
                {isPlugin && hasInstallationCommand && (
                  <p className="text-sm text-muted-foreground">
                    {t("agentAccess.expiresAt", {
                      time: new Date(
                        installationAuthorization!.expires_at
                      ).toLocaleTimeString(),
                    })}
                  </p>
                )}
                {manualPending && !isNativeHermes && (
                  <p role="status" className="text-sm text-muted-foreground">
                    {t("agentAccess.manualPendingHint")}
                  </p>
                )}
                {configGuide.warnings && configGuide.warnings.length > 0 && (
                  <WarningsBar warnings={configGuide.warnings} />
                )}
              </div>

              {modalAgent.product !== "Hermes" && (
                <CapabilityBadges capabilities={modalAgent.capabilities} />
              )}

              <div className="flex shrink-0 flex-wrap justify-end gap-2 border-t pt-4">
                <Button
                  variant="outline"
                  className="font-normal"
                  onClick={handleClose}
                >
                  {t("common.close")}
                </Button>
                {isPlugin ? (
                  <Button
                    onClick={handleEnable}
                    disabled={enableMutation.isPending || !canManage}
                  >
                    {enableMutation.isPending && (
                      <Loader2 className="size-4 animate-spin" />
                    )}
                    {modalAgent.status === "disabled"
                      ? t("agentAccess.reconnectGenerate")
                      : hasInstallationCommand
                        ? t("agentAccess.regenerate")
                        : t("agentAccess.generate")}
                  </Button>
                ) : modalAgent.status === "disabled" ? (
                  <Button
                    className="font-normal"
                    onClick={() => enableBackMutation.mutate()}
                    disabled={enableBackMutation.isPending || !canManage}
                  >
                    {enableBackMutation.isPending ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <BadgeCheck className="size-4" />
                    )}
                    {enableBackMutation.isPending
                      ? t("agentAccess.addingBack")
                      : t("agentAccess.addBack")}
                  </Button>
                ) : manualPending ? (
                  <Button disabled>{t("agentAccess.pending")}</Button>
                ) : modalAgent.status !== "connected" ? (
                  <Button
                    className="font-normal"
                    onClick={handleEnable}
                    disabled={enableMutation.isPending || !canManage}
                  >
                    {enableMutation.isPending ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <Plus className="size-4" />
                    )}
                    {enableMutation.isPending
                      ? t("agentAccess.enabling")
                      : manualOnboarding
                        ? t("agentAccess.createManualProject")
                        : t("agentAccess.enable")}
                  </Button>
                ) : null}
              </div>
            </>
          ) : (
            <>
              {modalAgent.setupMode === "AUTO" ? (
                <div className="rounded-lg bg-emerald-500/10 p-4 dark:bg-emerald-500/5">
                  <p className="text-sm font-normal text-emerald-600 dark:text-emerald-400">
                    {(modalAgent.configGuide as ConfigGuideLegacy | undefined)
                      ?.setup_guide?.title || t("agentAccess.autoDiscovery")}
                  </p>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {(modalAgent.configGuide as ConfigGuideLegacy | undefined)
                      ?.setup_guide?.description ||
                      t("agentAccess.autoDiscoveryDescription")}
                  </p>
                  {(
                    modalAgent.configGuide as ConfigGuideLegacy | undefined
                  )?.limitations?.map((limitation, index) => (
                    <p
                      key={index}
                      className="mt-2 text-xs text-amber-600 dark:text-amber-400"
                    >
                      {limitation}
                    </p>
                  ))}
                </div>
              ) : (
                <>
                  <div className="rounded-lg border bg-muted p-4">
                    <code className="block text-sm break-all text-foreground">
                      {modalAgent.cliCommand ||
                        `ai-vision connect --template "${modalAgent.name}" --instance "my-instance" --key YOUR_API_KEY`}
                    </code>
                    <div className="mt-3 flex justify-end">
                      <Button
                        variant="outline"
                        size="sm"
                        className="font-normal"
                        onClick={handleCopyCommand}
                      >
                        {copied ? (
                          <Check className="size-4" />
                        ) : (
                          <Copy className="size-4" />
                        )}
                        {copied
                          ? t("common.copied")
                          : t("agentAccess.copyCommand")}
                      </Button>
                    </div>
                  </div>
                  <p className="text-sm text-muted-foreground">
                    {t("agentAccess.replaceParameters")}
                  </p>
                </>
              )}

              <CapabilityBadges capabilities={modalAgent.capabilities} />

              <div className="flex flex-wrap justify-end gap-2 border-t pt-4">
                <Button
                  variant="outline"
                  className="font-normal"
                  onClick={handleClose}
                >
                  {t("common.close")}
                </Button>
                {modalAgent.status === "disabled" ? (
                  <Button
                    className="font-normal"
                    onClick={() => enableBackMutation.mutate()}
                    disabled={enableBackMutation.isPending || !canManage}
                  >
                    {enableBackMutation.isPending ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <BadgeCheck className="size-4" />
                    )}
                    {enableBackMutation.isPending
                      ? t("agentAccess.addingBack")
                      : t("agentAccess.addBack")}
                  </Button>
                ) : modalAgent.setupMode === "AUTO" &&
                  modalAgent.status !== "connected" ? (
                  <Button
                    className="font-normal"
                    onClick={handleEnable}
                    disabled={enableMutation.isPending || !canManage}
                  >
                    {enableMutation.isPending ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <Plus className="size-4" />
                    )}
                    {enableMutation.isPending
                      ? t("agentAccess.enabling")
                      : t("agentAccess.enable")}
                  </Button>
                ) : modalAgent.setupMode !== "AUTO" &&
                  modalAgent.status !== "connected" &&
                  modalAgent.status !== "disabled" ? (
                  <Button className="font-normal" onClick={handleCopyCommand}>
                    {copied ? (
                      <Check className="size-4" />
                    ) : (
                      <Copy className="size-4" />
                    )}
                    {copied ? t("common.copied") : t("agentAccess.copyCommand")}
                  </Button>
                ) : null}
              </div>
            </>
          )}
        </CardContent>
      </DialogContent>
    </Dialog>
  )
}

function CapabilityBadges({
  capabilities,
}: {
  capabilities: Record<string, unknown> | null
}) {
  const { t } = useTranslation()
  const dashboardSupported = getCapability(capabilities, "supports_dashboard")
  const traceSupported = getCapability(capabilities, "supports_trace")

  return (
    <div className="flex flex-wrap gap-2">
      <div
        className={cn(
          "flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-normal",
          dashboardSupported
            ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
            : "bg-muted text-muted-foreground"
        )}
      >
        {dashboardSupported ? (
          <CircleCheck className="size-3.5" />
        ) : (
          <CircleMinus className="size-3.5" />
        )}
        {t("agentAccess.dashboard")}
      </div>
      <div
        className={cn(
          "flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-normal",
          traceSupported
            ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
            : "bg-muted text-muted-foreground"
        )}
      >
        {traceSupported ? (
          <CircleCheck className="size-3.5" />
        ) : (
          <CircleMinus className="size-3.5" />
        )}
        {t("agentAccess.trace")}
      </div>
    </div>
  )
}

// ── Main Page ──

export default function AgentsPage() {
  const { currentSpace } = useSpace()
  return <SpaceAgentsPage key={currentSpace?.id ?? ""} />
}

function SpaceAgentsPage() {
  const { t } = useTranslation()
  const {
    agents: contextAgents,
    agentTypes: globalAgentTypes,
    loading: agentContextLoading,
    disabledAgents: contextDisabledAgents,
    refreshAgents,
  } = useAgent()
  const [searchParams, setSearchParams] = useSearchParams()
  const tabFromUrl = searchParams.get(ACCESS_TAB_QUERY_KEY)
  const activeTab: AccessTab = isAccessTab(tabFromUrl)
    ? tabFromUrl
    : DEFAULT_ACCESS_TAB
  const setActiveTab = useCallback(
    (nextTab: AccessTab) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          if (nextTab === DEFAULT_ACCESS_TAB) {
            next.delete(ACCESS_TAB_QUERY_KEY)
          } else {
            next.set(ACCESS_TAB_QUERY_KEY, nextTab)
          }
          return next
        },
        { replace: true }
      )
    },
    [setSearchParams]
  )
  const [modalAgent, setModalAgent] = useState<ModalAgent | null>(null)
  const guideTrigger = useRef<HTMLButtonElement | null>(null)
  const openGuide = (agent: ModalAgent, trigger: HTMLButtonElement) => {
    guideTrigger.current = trigger
    setModalAgent(agent)
  }

  const agents: FleetAgent[] = contextAgents.map((a) => ({
    id: a.id ?? 0,
    uuid: String(a.uuid ?? ""),
    name: (a.name as string) ?? "",
    agent_product: a.agent_product,
    icon: (a.icon as string | null | undefined) ?? null,
    custom_name: (a.custom_name as string | null) ?? null,
    description: a.description ?? null,
    metadata:
      (a.metadata as Record<string, unknown> | null | undefined) ?? null,
    status: (a.status as number) ?? 1,
  }))
  // Projects already belong to the current space. Keep the full catalog for
  // their identity, including entries no longer offered in the market.
  const agentTypes = globalAgentTypes as AgentTypeData[]
  const agentTypesLoading = agentContextLoading

  const connectedAgentMap = new Map<string, number>()
  const connectedAgentUuidMap = new Map<string, string>()
  for (const agent of agents) {
    if (agent.status !== 0) {
      connectedAgentMap.set(agent.agent_product, agent.status)
      connectedAgentUuidMap.set(agent.agent_product, agent.uuid)
    }
  }

  const disabledAgentMap = new Map<string, string>()
  for (const da of contextDisabledAgents) {
    disabledAgentMap.set(da.agent_product, da.uuid)
  }

  const handleRefresh = useCallback(() => {
    refreshAgents({ silent: true })
  }, [refreshAgents])

  const tabs: { key: AccessTab; label: string }[] = [
    { key: "fleet", label: t("agentAccess.fleetTab") },
    { key: "market", label: t("agentAccess.marketTab") },
  ]

  return (
    <SpaceGuard>
      <div data-product-guide="access-page" className="flex flex-col gap-6 p-6">
        {/* Header: independent tab buttons */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            {tabs.map((tab) => (
              <Button
                key={tab.key}
                data-product-guide={
                  tab.key === "market" ? "agent-market-tab" : undefined
                }
                variant={activeTab === tab.key ? "default" : "outline"}
                size="sm"
                className={
                  activeTab === tab.key ? "font-medium" : "font-normal"
                }
                onClick={() => setActiveTab(tab.key)}
              >
                {tab.label}
              </Button>
            ))}
          </div>
        </div>

        {/* Content */}
        {activeTab === "fleet" ? (
          agentContextLoading ? (
            <div className={CARD_GRID}>
              {Array.from({ length: 4 }).map((_, index) => (
                <Skeleton key={index} className="h-[180px] rounded-xl" />
              ))}
            </div>
          ) : (
            <AgentFleetContent
              agents={agents}
              agentTypes={agentTypes}
              onGoToMarket={() => setActiveTab("market")}
              onRefresh={handleRefresh}
              onViewGuide={openGuide}
            />
          )
        ) : (
          <AgentMarketContent
            agentTypes={agentTypes}
            connectedAgentMap={connectedAgentMap}
            connectedAgentUuidMap={connectedAgentUuidMap}
            disabledAgentMap={disabledAgentMap}
            onViewGuide={openGuide}
            isLoading={agentTypesLoading}
          />
        )}

        {/* Onboarding guide dialog */}
        {modalAgent && (
          <OnboardingDialog
            modalAgent={modalAgent}
            onClose={() => setModalAgent(null)}
            onReturnFocus={() => guideTrigger.current?.focus()}
            onRefresh={handleRefresh}
          />
        )}
      </div>
    </SpaceGuard>
  )
}
