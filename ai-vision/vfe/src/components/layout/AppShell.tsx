import {
  ArrowUpRight,
  Blocks,
  BookOpen,
  Bot,
  CalendarClock,
  ChartArea,
  CircleDollarSign,
  Check,
  ChevronsUpDown,
  Database,
  FlaskConical,
  Gauge,
  GitBranch,
  Globe,
  HelpCircle,
  Info,
  LogOut,
  Network,
  Moon,
  Play,
  Plug,
  Puzzle,
  Settings,
  Store,
  Sun,
} from "lucide-react"
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
} from "react"
import { matchPath, NavLink, Outlet, useLocation } from "react-router-dom"
import { useIsFetching } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import { changeLanguage, getCurrentLocale, languageOptions } from "@/i18n"

import { useProductGuide } from "@/components/product-guide/useProductGuide"
import {
  DemoDataBadge,
  DemoExamplesButton,
} from "@/components/product-guide/DemoControls"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { FeatureBetaBadge } from "@/components/FeatureBetaBadge"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { SpaceSelect } from "./SpaceSelect"
import { AgentSelector } from "@/components/header/agentselector"
import { TimeFilterControl } from "@/components/header/TimeFilterControl"
import { QUALITY_STATS_MAX_RANGE_DAYS } from "@/components/diagnose/qualityApi"
import { AutoRefreshControl } from "@/components/header/AutoRefreshControl"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { logout } from "@/lib/api"
import { documentationUrl } from "@/lib/documentation"
import { cn } from "@/lib/utils"
import { useUser } from "@/contexts/UserContext"
import { useSpace } from "@/contexts/SpaceContext"
import {
  isModelConnectionsPath,
  modelConnectionsPath,
} from "@/features/model-connections/navigation"
import { isPluginsPath, pluginsPath } from "@/features/plugins/navigation"
import { useTimeFilter } from "@/contexts/TimeFilterContext"
import { useTheme } from "./theme-provider"
import { AppLogo } from "@/components/layout/AppLogo"
import { SidebarToggle } from "./SidebarToggle"
import {
  isTimeRangeShorterThan24Hours,
  MONTH_TIME_RANGE_OPTION,
} from "@/lib/timeFilter"
import { PRICING_MAX_RANGE_DAYS } from "@/features/model-pricing/timeRange"
import { PRICING_INVENTORY_QUERY } from "@/features/model-pricing/pricingQueryInvalidation"
import {
  buildAgentDashboardUrl,
  getAgentDashboardRoute,
  isAgentDashboardPath,
} from "@/agents/lib/agentDashboardRoute"
import { NotificationBanner } from "@/components/layout/NotificationBanner"
import {
  isDiagnosisPath,
  markDiagnosisNewBadgeSeen,
  shouldShowDiagnosisNewBadge,
  subscribeDiagnosisNewBadgeState,
} from "@/components/diagnose/diagnosisNewBadgeState"

/* ── Navigation config ── */

const mainNavItems = [
  { to: "/dashboard", labelKey: "nav.dashboard", icon: ChartArea },
  { to: "/trace", labelKey: "nav.trace", icon: GitBranch },
]

const diagnosticsItems = [
  { to: "/badcase", labelKey: "nav.badcase", icon: Gauge },
  { to: "/diagnosers", labelKey: "nav.diagnosers", icon: FlaskConical },
  { to: "/diagnose-jobs", labelKey: "nav.diagnoseJobs", icon: CalendarClock },
]

const integrationItems: {
  to: string
  labelKey: string
  icon: typeof Store
  beta?: boolean
}[] = [
  { to: "/access", labelKey: "nav.access", icon: Store },
  { to: "/model-connections", labelKey: "nav.modelConnections", icon: Network },
  { to: "/plugins", labelKey: "nav.plugins", icon: Puzzle },
  {
    to: "/model-pricing",
    labelKey: "nav.modelPricing",
    icon: CircleDollarSign,
    beta: true,
  },
]

const SIDEBAR_STORAGE_KEY = "sidebar_open"

const sidebarLinkClass = "h-auto min-h-9 py-1.5 leading-tight md:min-h-8"
const sidebarLabelClass =
  "min-w-0 text-balance whitespace-normal! [overflow-wrap:anywhere] group-data-[collapsible=icon]:sr-only"
const sidebarLabelWithBadgeClass =
  "flex min-w-0 flex-1 flex-wrap items-center gap-1 whitespace-normal! group-data-[collapsible=icon]:sr-only"

function HelpMenu() {
  const guide = useProductGuide()
  const { t } = useTranslation()
  const { currentSpace } = useSpace()

  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label={t("about.helpMenu")}>
          <HelpCircle />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuItem onSelect={() => guide?.start()}>
          <HelpCircle />
          {t(guide?.resumable ? "productGuide.continue" : "productGuide.title")}
        </DropdownMenuItem>
        {guide?.resumable && (
          <DropdownMenuItem onSelect={() => guide.restart()}>
            {t("productGuide.restart")}
          </DropdownMenuItem>
        )}
        {guide?.canBrowse && (
          <DropdownMenuItem onSelect={() => guide.browse()}>
            {t("productGuide.dashboardAction")}
          </DropdownMenuItem>
        )}
        {guide?.canStartDemo && (
          <DropdownMenuItem onSelect={() => guide.openDemo()}>
            <Play />
            {t("productGuide.demo.entry")}
          </DropdownMenuItem>
        )}
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild className="font-normal">
          <NavLink
            to={
              currentSpace
                ? `/access?${new URLSearchParams({ spaceId: currentSpace.id })}`
                : "/access"
            }
          >
            <Store />
            {t("nav.access")}
          </NavLink>
        </DropdownMenuItem>
        <DropdownMenuItem asChild className="font-normal">
          <NavLink to="/receipt">
            <Bot />
            {t("common.monthlyReview")}
          </NavLink>
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild className="font-normal">
          <NavLink to="/about">
            <Info />
            {t("about.menu")}
          </NavLink>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function ActiveDemoDataBadge({ stackOnPhone }: { stackOnPhone: boolean }) {
  const guide = useProductGuide()
  return guide?.isDemoAgent ? (
    <div
      className={cn(
        "contents",
        stackOnPhone && "max-sm:col-start-1 max-sm:row-start-2 max-sm:flex"
      )}
    >
      <DemoDataBadge />
    </div>
  ) : null
}

function buildSharedNavigationSearch(search: string) {
  const params = new URLSearchParams(search)
  const next = new URLSearchParams()

  for (const key of ["agent", "spaceId", "range", "from", "to"]) {
    const value = params.get(key)
    if (value) next.set(key, value)
  }

  const value = next.toString()
  return value ? `?${value}` : ""
}

function buildQualityNavigationSearch(
  search: string,
  timeRange: string,
  isCustom: boolean,
  fromTimestamp: string,
  toTimestamp: string
) {
  const params = new URLSearchParams(search)
  if (isTimeRangeShorterThan24Hours(timeRange, fromTimestamp, toTimestamp)) {
    params.set("range", "24h")
    params.delete("from")
    params.delete("to")
  } else if (isCustom && fromTimestamp && toTimestamp) {
    params.delete("range")
    params.set("from", fromTimestamp)
    params.set("to", toTimestamp)
  } else {
    params.set(
      "range",
      timeRange === MONTH_TIME_RANGE_OPTION.value ? "7d" : timeRange
    )
    params.delete("from")
    params.delete("to")
  }
  return buildSharedNavigationSearch(params.toString())
}

function isMainNavActive(pathname: string, target: string) {
  if (target === "/dashboard") {
    return (
      pathname === "/" ||
      pathname === "/dashboard" ||
      pathname.startsWith("/dashboard/") ||
      isAgentDashboardPath(pathname)
    )
  }

  return pathname === target || pathname.startsWith(`${target}/`)
}

/* ── User dropdown ── */

function UserMenu() {
  const { user } = useUser()
  const userName = user?.name || "AI Vision"
  const userEmail = user?.email || ""
  const avatarSrc = user?.avatar_url || ""
  const userInitial = (userName || userEmail || "AI").slice(0, 1).toUpperCase()
  const { t } = useTranslation()
  const { theme, setTheme } = useTheme()
  const currentLanguage = getCurrentLocale()

  const currentTheme = theme || "system"

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <SidebarMenuButton
          size="lg"
          aria-label={userName}
          className="h-11 data-[state=open]:bg-sidebar-accent"
        >
          <span className="relative shrink-0">
            <Avatar className="size-8 rounded-lg">
              {avatarSrc && <AvatarImage src={avatarSrc} alt={userName} />}
              <AvatarFallback className="rounded-lg">
                {userInitial}
              </AvatarFallback>
            </Avatar>
          </span>
          <div className="grid min-w-0 flex-1 text-left text-sm leading-tight group-data-[collapsible=icon]:hidden">
            <span className="truncate font-medium">{userName}</span>
          </div>
          <ChevronsUpDown className="ml-auto size-4 group-data-[collapsible=icon]:hidden" />
        </SidebarMenuButton>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        className="w-[--radix-dropdown-menu-trigger-width] min-w-56"
        side="bottom"
        align="end"
        sideOffset={4}
      >
        <DropdownMenuLabel className="p-0 font-normal">
          <div className="flex items-center gap-2 px-1 py-1.5 text-left text-sm">
            <Avatar className="size-8 rounded-lg">
              {avatarSrc && <AvatarImage src={avatarSrc} alt={userName} />}
              <AvatarFallback className="rounded-lg">
                {userInitial}
              </AvatarFallback>
            </Avatar>
            <div className="grid flex-1 text-left text-sm leading-tight">
              <span className="truncate font-medium">{userName}</span>
              <span className="truncate text-xs text-muted-foreground">
                {userEmail || "-"}
              </span>
            </div>
          </div>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          <DropdownMenuSub>
            <DropdownMenuSubTrigger>
              <Globe />
              {t("user.language")}
            </DropdownMenuSubTrigger>
            <DropdownMenuSubContent>
              {languageOptions.map(({ locale, labelKey }) => (
                <DropdownMenuItem
                  key={locale}
                  onClick={() => void changeLanguage(locale)}
                >
                  {t(labelKey)}
                  <Check
                    className={`ml-auto size-4 ${currentLanguage === locale ? "opacity-100" : "opacity-0"}`}
                  />
                </DropdownMenuItem>
              ))}
            </DropdownMenuSubContent>
          </DropdownMenuSub>
          <DropdownMenuSub>
            <DropdownMenuSubTrigger>
              <Sun className="dark:hidden" />
              <Moon className="hidden dark:block" />
              {t("user.theme")}
            </DropdownMenuSubTrigger>
            <DropdownMenuSubContent>
              <DropdownMenuItem onClick={() => setTheme("light")}>
                <Sun />
                {t("theme.light")}
                <Check
                  className={`ml-auto size-4 ${currentTheme === "light" ? "opacity-100" : "opacity-0"}`}
                />
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => setTheme("dark")}>
                <Moon />
                {t("theme.dark")}
                <Check
                  className={`ml-auto size-4 ${currentTheme === "dark" ? "opacity-100" : "opacity-0"}`}
                />
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => setTheme("system")}>
                {t("theme.system")}
                <Check
                  className={`ml-auto size-4 ${currentTheme === "system" ? "opacity-100" : "opacity-0"}`}
                />
              </DropdownMenuItem>
            </DropdownMenuSubContent>
          </DropdownMenuSub>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          variant="destructive"
          onClick={() => {
            void logout()
          }}
        >
          <LogOut />
          {t("common.logout")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/* ── Shell ── */

function SidebarRouteSync() {
  const location = useLocation()
  const { setOpenMobile } = useSidebar()

  useEffect(() => {
    setOpenMobile(false)
  }, [location.key, setOpenMobile])

  return null
}

export function AppShell() {
  const location = useLocation()
  const guide = useProductGuide()
  const { t, i18n } = useTranslation()
  const { user } = useUser()
  const { currentSpace } = useSpace()
  const { timeRange, isCustom, fromTimestamp, toTimestamp, setTimeRange } =
    useTimeFilter()
  const shouldExpandQualityTimeRange = isTimeRangeShorterThan24Hours(
    timeRange,
    isCustom ? fromTimestamp : undefined,
    isCustom ? toTimestamp : undefined
  )

  const diagnosisNewUserKey = user?.user_uuid
  const isOnDiagnosisPage = isDiagnosisPath(location.pathname)
  const showDiagnosticsNewBadge = useSyncExternalStore(
    subscribeDiagnosisNewBadgeState,
    () =>
      !isOnDiagnosisPage && shouldShowDiagnosisNewBadge(diagnosisNewUserKey),
    () => false
  )

  useEffect(() => {
    if (diagnosisNewUserKey && isOnDiagnosisPage) {
      markDiagnosisNewBadgeSeen(diagnosisNewUserKey)
    }
  }, [diagnosisNewUserKey, isOnDiagnosisPage])

  const [sidebarOpen, setSidebarOpen] = useState(() => {
    const stored = localStorage.getItem(SIDEBAR_STORAGE_KEY)
    return stored === null ? true : stored !== "false"
  })
  const handleSidebarChange = useCallback((open: boolean) => {
    setSidebarOpen(open)
    localStorage.setItem(SIDEBAR_STORAGE_KEY, String(open))
  }, [])
  const sidebarActionLabel = t(
    sidebarOpen ? "nav.collapseSidebar" : "nav.expandSidebar"
  )

  const isPricingPage = Boolean(matchPath("/model-pricing", location.pathname))
  const isTracePage = Boolean(matchPath("/trace", location.pathname))
  const isQualityPage = Boolean(matchPath("/badcase", location.pathname))
  const isPricingOverview =
    isPricingPage && new URLSearchParams(location.search).get("tab") !== "rules"
  const pricingRefreshPending =
    useIsFetching({
      queryKey: [PRICING_INVENTORY_QUERY, currentSpace?.id],
    }) > 0
  const isManagementPage =
    location.pathname === "/access" ||
    location.pathname === "/access/langfuse-onboarding" ||
    location.pathname === "/access/relay-onboarding" ||
    location.pathname === "/settings" ||
    isPricingPage ||
    location.pathname === "/sandbox-connections" ||
    isPluginsPath(location.pathname) ||
    isModelConnectionsPath(location.pathname)
  const showObservationControls =
    !isManagementPage && location.pathname !== "/downloads"
  const showTimeControls = showObservationControls || isPricingOverview
  const showProjectSelector =
    showObservationControls && location.pathname !== "/receipt"
  const stackOnPhone = showProjectSelector && guide?.canStartDemo === true
  const navigationSpaceId =
    currentSpace?.id ?? new URLSearchParams(location.search).get("spaceId")
  const spaceNavigationSearch = navigationSpaceId
    ? `?${new URLSearchParams({ spaceId: navigationSpaceId })}`
    : ""
  const sharedNavigationSearch = useMemo(
    () => buildSharedNavigationSearch(location.search),
    [location.search]
  )
  const pricingNavigationParams = new URLSearchParams(sharedNavigationSearch)
  pricingNavigationParams.delete("agent")
  if (navigationSpaceId)
    pricingNavigationParams.set("spaceId", navigationSpaceId)
  const pricingNavigationSearch = pricingNavigationParams.toString()
  const qualityNavigationSearch = useMemo(
    () =>
      buildQualityNavigationSearch(
        location.search,
        timeRange,
        isCustom,
        fromTimestamp,
        toTimestamp
      ),
    [fromTimestamp, isCustom, location.search, timeRange, toTimestamp]
  )
  const currentDashboardRoute = getAgentDashboardRoute(
    location.pathname,
    location.search
  )
  const dashboardLink = currentDashboardRoute
    ? buildAgentDashboardUrl(currentDashboardRoute.agentProduct, {
        search: location.search,
        spaceId: currentDashboardRoute.spaceId,
        agentUuid: currentDashboardRoute.agentUuid,
      })
    : `/dashboard${sharedNavigationSearch}`
  const mainNavTargets = useMemo(
    () => ({
      "/dashboard": dashboardLink,
      "/trace": `/trace${sharedNavigationSearch}`,
    }),
    [dashboardLink, sharedNavigationSearch]
  )

  return (
    <SidebarProvider
      open={sidebarOpen}
      onOpenChange={handleSidebarChange}
      className="h-svh max-h-svh !min-h-svh"
      style={
        {
          "--sidebar-width": "clamp(180px, 13vw, 256px)",
        } as React.CSSProperties
      }
    >
      <SidebarRouteSync />
      <Sidebar collapsible="icon" className="motion-reduce:transition-none">
        {/* Logo */}
        <SidebarHeader className="flex-row items-center">
          <SidebarMenu className="flex-1">
            <SidebarMenuItem>
              <SidebarMenuButton size="lg" asChild>
                <AppLogo
                  aria-label="AI Vision"
                  className="group-data-[collapsible=icon]:[&_svg]:hidden"
                />
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
          <SidebarToggle inDrawer />
        </SidebarHeader>

        <SidebarContent className="gap-0 group-data-[collapsible=icon]:overflow-y-auto">
          {/* Observation */}
          <SidebarGroup className="py-1">
            <SidebarGroupLabel className="h-6 group-data-[collapsible=icon]:-mt-6">
              {t("nav.observation")}
            </SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu className="gap-0.5">
                {mainNavItems.map((item) => (
                  <SidebarMenuItem key={item.to}>
                    <SidebarMenuButton
                      asChild
                      className={sidebarLinkClass}
                      tooltip={t(item.labelKey)}
                      isActive={isMainNavActive(location.pathname, item.to)}
                    >
                      <NavLink
                        to={
                          mainNavTargets[
                            item.to as keyof typeof mainNavTargets
                          ] ?? item.to
                        }
                      >
                        <item.icon />
                        <span className={sidebarLabelClass}>
                          {t(item.labelKey)}
                        </span>
                      </NavLink>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
                <SidebarMenuItem>
                  <SidebarMenuButton
                    asChild
                    className={sidebarLinkClass}
                    tooltip={`${t("nav.labsMcp")} · ${t("labs.common.beta")}`}
                    isActive={location.pathname === "/labs/mcp"}
                  >
                    <NavLink
                      to={`/labs/mcp${sharedNavigationSearch}`}
                      aria-label={`${t("nav.labsMcpShort")} · ${t("labs.common.beta")}`}
                      title={`${t("nav.labsMcp")} · ${t("labs.common.beta")}`}
                    >
                      <Plug />
                      <span className={sidebarLabelWithBadgeClass}>
                        <span className={sidebarLabelClass}>
                          {t("nav.labsMcpShort")}
                        </span>
                        <FeatureBetaBadge />
                      </span>
                    </NavLink>
                  </SidebarMenuButton>
                </SidebarMenuItem>
                <SidebarMenuItem>
                  <SidebarMenuButton
                    asChild
                    className={sidebarLinkClass}
                    tooltip={`${t("nav.labsSkill")} · ${t("labs.common.beta")}`}
                    isActive={location.pathname === "/labs/skill"}
                  >
                    <NavLink
                      to={`/labs/skill${sharedNavigationSearch}`}
                      aria-label={`${t("nav.labsSkillShort")} · ${t("labs.common.beta")}`}
                      title={`${t("nav.labsSkill")} · ${t("labs.common.beta")}`}
                    >
                      <Blocks />
                      <span className={sidebarLabelWithBadgeClass}>
                        <span className={sidebarLabelClass}>
                          {t("nav.labsSkillShort")}
                        </span>
                        <FeatureBetaBadge />
                      </span>
                    </NavLink>
                  </SidebarMenuButton>
                </SidebarMenuItem>
                <SidebarMenuItem>
                  <SidebarMenuButton
                    asChild
                    className={sidebarLinkClass}
                    tooltip={`${t("nav.labsMemory")} · ${t("labs.common.beta")}`}
                    isActive={location.pathname === "/labs/memory"}
                  >
                    <NavLink
                      to={`/labs/memory${sharedNavigationSearch}`}
                      aria-label={`${t("nav.labsMemoryShort")} · ${t("labs.common.beta")}`}
                      title={`${t("nav.labsMemory")} · ${t("labs.common.beta")}`}
                    >
                      <Database />
                      <span className={sidebarLabelWithBadgeClass}>
                        <span className={sidebarLabelClass}>
                          {t("nav.labsMemoryShort")}
                        </span>
                        <Badge className="h-4 shrink-0 rounded-[6px] border-0 bg-[var(--color-bg-labs-tag)] px-1 py-0 text-[10px] leading-none font-normal text-[var(--color-labs)] shadow-none">
                          {t("labs.common.beta")}
                        </Badge>
                      </span>
                    </NavLink>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>

          <SidebarGroup className="py-1">
            <SidebarGroupLabel className="h-6 gap-1 group-data-[collapsible=icon]:-mt-6">
              <span>{t("nav.diagnostics")}</span>
              {showDiagnosticsNewBadge ? (
                <Badge className="h-4 min-w-[29px] rounded-full border-0 bg-[linear-gradient(105deg,#f5c8d8_0%,#e7cde5_50%,#d3dcf3_100%)] px-1 py-0 text-[9px] leading-none font-semibold tracking-[0.02em] text-[#753d68] shadow-none transition-none">
                  {t("common.newFeature")}
                </Badge>
              ) : null}
            </SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu className="gap-0.5">
                {diagnosticsItems.map((item) => (
                  <SidebarMenuItem key={item.to}>
                    <SidebarMenuButton
                      asChild
                      className={sidebarLinkClass}
                      tooltip={t(item.labelKey)}
                      isActive={isMainNavActive(location.pathname, item.to)}
                    >
                      <NavLink
                        to={`${item.to}${item.to === "/badcase" ? qualityNavigationSearch : sharedNavigationSearch}`}
                        onClick={() => {
                          if (
                            item.to === "/badcase" &&
                            shouldExpandQualityTimeRange
                          ) {
                            setTimeRange("24h")
                          }
                        }}
                      >
                        <item.icon />
                        <span className={sidebarLabelClass}>
                          {t(item.labelKey)}
                        </span>
                      </NavLink>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>

          <SidebarGroup className="py-1">
            <SidebarGroupLabel className="h-6 group-data-[collapsible=icon]:-mt-6">
              {t("nav.integrations")}
            </SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu className="gap-0.5">
                {integrationItems.map((item) => (
                  <SidebarMenuItem key={item.to}>
                    <SidebarMenuButton
                      asChild
                      className={sidebarLinkClass}
                      tooltip={
                        item.beta
                          ? `${t(item.labelKey)} · ${t("modelPricing.beta")}`
                          : t(item.labelKey)
                      }
                      isActive={
                        item.to === "/plugins"
                          ? isPluginsPath(location.pathname) ||
                            location.pathname === "/sandbox-connections"
                          : location.pathname === item.to ||
                            location.pathname.startsWith(`${item.to}/`)
                      }
                    >
                      <NavLink
                        data-product-guide={
                          item.to === "/access" ? "access" : undefined
                        }
                        aria-label={
                          item.beta
                            ? `${t(item.labelKey)} · ${t("modelPricing.beta")}`
                            : undefined
                        }
                        to={
                          item.to === "/model-connections"
                            ? modelConnectionsPath(navigationSpaceId)
                            : item.to === "/plugins"
                              ? pluginsPath(navigationSpaceId)
                              : item.to === "/model-pricing"
                                ? `${item.to}${pricingNavigationSearch ? `?${pricingNavigationSearch}` : ""}`
                                : `${item.to}${spaceNavigationSearch}`
                        }
                      >
                        <item.icon />
                        {item.beta ? (
                          <span className={sidebarLabelWithBadgeClass}>
                            <span className={sidebarLabelClass}>
                              {t(item.labelKey)}
                            </span>
                            <FeatureBetaBadge />
                          </span>
                        ) : (
                          <span className={sidebarLabelClass}>
                            {t(item.labelKey)}
                          </span>
                        )}
                      </NavLink>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
          <SidebarGroup className="mt-auto py-1">
            <SidebarGroupContent>
              <SidebarMenu>
                <SidebarMenuItem>
                  <SidebarMenuButton
                    asChild
                    className={sidebarLinkClass}
                    tooltip={t("nav.settings")}
                    isActive={isMainNavActive(location.pathname, "/settings")}
                  >
                    <NavLink to={`/settings${spaceNavigationSearch}`}>
                      <Settings />
                      <span className={sidebarLabelClass}>
                        {t("nav.settings")}
                      </span>
                    </NavLink>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>

        {/* Documentation and user footer */}
        <SidebarFooter>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton asChild tooltip={t("nav.documentationNewTab")}>
                <a
                  href={documentationUrl(
                    "introduction",
                    i18n.resolvedLanguage || i18n.language
                  )}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label={t("nav.documentationNewTab")}
                >
                  <BookOpen aria-hidden="true" />
                  <span className="min-w-0 truncate">
                    {t("nav.documentation")}
                  </span>
                  <ArrowUpRight
                    className="ml-auto size-3! text-muted-foreground group-data-[collapsible=icon]:hidden"
                    aria-hidden="true"
                  />
                </a>
              </SidebarMenuButton>
            </SidebarMenuItem>
            <SidebarMenuItem>
              <UserMenu />
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarFooter>

        <SidebarRail
          aria-label={sidebarActionLabel}
          title={sidebarActionLabel}
        />
      </Sidebar>

      <SidebarInset className="flex min-w-0 flex-col overflow-hidden">
        <div className="@container/shell shrink-0">
          <header
            data-product-guide="scope"
            className="grid min-h-16 grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-2 border-b px-3 py-3 sm:px-4 @min-[52rem]/shell:grid-cols-[minmax(0,1fr)_minmax(0,auto)_auto]"
          >
            <div
              className={cn(
                "flex min-w-0 items-center gap-2",
                stackOnPhone && "max-sm:col-span-2"
              )}
            >
              <SidebarToggle />
              <SpaceSelect />
              {showProjectSelector && <AgentSelector />}
            </div>
            {showTimeControls && (
              <div
                className={cn(
                  "col-span-2 row-start-2 flex min-w-0 flex-wrap items-center gap-1.5 sm:gap-2 @min-[52rem]/shell:col-span-1 @min-[52rem]/shell:col-start-2 @min-[52rem]/shell:row-start-1 @min-[52rem]/shell:justify-end",
                  stackOnPhone && "max-sm:contents"
                )}
              >
                {showObservationControls && (
                  <ActiveDemoDataBadge stackOnPhone={stackOnPhone} />
                )}
                <div
                  className={cn(
                    "contents",
                    stackOnPhone &&
                      "max-sm:col-span-2 max-sm:row-start-3 max-sm:flex max-sm:min-w-0 max-sm:flex-wrap max-sm:items-center max-sm:gap-1.5"
                  )}
                >
                  <div className="max-w-full min-w-0" data-product-guide="time">
                    <TimeFilterControl
                      includeMonth={
                        isPricingOverview ||
                        isTracePage ||
                        (!isQualityPage &&
                          timeRange === MONTH_TIME_RANGE_OPTION.value)
                      }
                      maxRangeBasis={isPricingOverview ? "elapsed" : undefined}
                      maxRangeDays={
                        isPricingOverview
                          ? PRICING_MAX_RANGE_DAYS
                          : isQualityPage &&
                              new URLSearchParams(location.search).get(
                                "view"
                              ) !== "list"
                            ? QUALITY_STATS_MAX_RANGE_DAYS
                            : undefined
                      }
                    />
                  </div>
                  <AutoRefreshControl
                    refreshPending={isPricingOverview && pricingRefreshPending}
                  />
                </div>
              </div>
            )}
            <div
              className={cn(
                "col-start-2 row-start-1 flex items-center gap-1 @min-[52rem]/shell:col-start-3",
                stackOnPhone && "max-sm:row-start-2"
              )}
            >
              <DemoExamplesButton />
              <HelpMenu />
            </div>
          </header>
        </div>

        <main className="min-w-0 flex-1 overflow-auto bg-[var(--content-bg)]">
          <NotificationBanner />
          <Outlet />
        </main>
      </SidebarInset>
    </SidebarProvider>
  )
}
