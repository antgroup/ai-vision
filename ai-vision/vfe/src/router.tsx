import { createBrowserRouter, Navigate, useLocation } from "react-router-dom"

import { AuthGuard } from "@/components/layout/AuthGuard"
import { ErrorFallback } from "@/components/error/ErrorFallback"
import { ProductGuideProvider } from "@/components/product-guide/ProductGuide"
import { AppShell } from "@/components/layout/AppShell"
import { SpaceProvider } from "@/contexts/SpaceContext"
import { AgentProvider } from "@/contexts/AgentContext"
import { TimeFilterProvider } from "@/contexts/TimeFilterContext"
import { UserProvider } from "@/contexts/UserContext"
import { LocalAuthPage } from "@/pages/LocalAuthPage"
import { DashboardPage } from "@/pages/DashboardPage"
import AccessPage from "@/pages/AgentsPage"
import { LangfuseOnboardingPage } from "@/pages/LangfuseOnboardingPage"
import { RelayOnboardingPage } from "@/pages/RelayOnboardingPage"
import SettingsPage from "@/pages/settings/SettingsPage"
import ModelConnectionsPage from "@/pages/ModelConnectionsPage"
import SandboxConnectionsPage from "@/pages/SandboxConnectionsPage"
import PluginsPage, {
  SandboxPluginPage,
  OpenShellPluginPage,
} from "@/pages/PluginsPage"
import ModelPricingPage from "@/pages/ModelPricingPage"
import { SpacePricingCurrencyProvider } from "@/features/model-pricing/PricingCurrency"
import ModelConnectionDetailPage from "@/pages/ModelConnectionDetailPage"
import ObservationLinkPage from "@/pages/ObservationLinkPage"
import TraceLinkPage from "@/pages/TraceLinkPage"
import SharePage from "@/pages/SharePage"
import NotFoundPage from "@/pages/NotFoundPage"
import DownloadPage from "@/pages/DownloadPage"
import ReceiptPage from "@/pages/receipt/ReceiptPage"
import ReceiptSpaPage from "@/pages/receipt/ReceiptSpaPage"
import { TraceEntry } from "@/agents/traces"
import { BadCasePage } from "@/pages/diagnose/BadCasePage"
import { DiagnoserPage } from "@/pages/diagnose/DiagnoserPage"
import { JobPage } from "@/pages/diagnose/JobPage"
import { McpPage } from "@/labs/mcp/McpPage"
import AboutPage from "@/pages/about/AboutPage"
import { MemoryPage } from "@/labs/memory/MemoryPage"
import { SkillPage } from "@/labs/skill/SkillPage"
import { TraceComparePage } from "@/pages/traces-otel/TraceComparePage"
import { MemoryObservabilityDesignPage } from "@/pages/design/MemoryObservabilityDesignPage"

// eslint-disable-next-line react-refresh/only-export-components
function RootRedirect() {
  const location = useLocation()
  return <Navigate to={`/dashboard${location.search}`} replace />
}

export const router = createBrowserRouter([
  {
    path: "/about",
    element: (
      <AuthGuard>
        <AboutPage />
      </AuthGuard>
    ),
  },
  {
    path: "/login",
    element: <LocalAuthPage mode="login" />,
  },
  {
    path: "/setup",
    element: <Navigate to="/register" replace />,
  },
  {
    path: "/register",
    element: <LocalAuthPage mode="register" />,
  },
  {
    path: "/invitations/accept",
    element: <LocalAuthPage mode="invite" />,
  },
  {
    path: "/invite",
    element: <LocalAuthPage mode="invite" />,
  },
  {
    path: "/trace-link",
    element: <TraceLinkPage />,
  },
  {
    path: "/observation-link",
    element: <ObservationLinkPage />,
  },
  {
    path: "/share/trace",
    element: <SharePage />,
  },
  {
    path: "/share/session",
    element: <SharePage />,
  },
  {
    path: "/",
    errorElement: <ErrorFallback />,
    element: (
      <AuthGuard>
        <UserProvider>
          <SpaceProvider>
            <SpacePricingCurrencyProvider>
              <AgentProvider>
                <TimeFilterProvider>
                  <ProductGuideProvider>
                    <AppShell />
                  </ProductGuideProvider>
                </TimeFilterProvider>
              </AgentProvider>
            </SpacePricingCurrencyProvider>
          </SpaceProvider>
        </UserProvider>
      </AuthGuard>
    ),
    children: [
      {
        index: true,
        element: <RootRedirect />,
      },
      {
        path: "dashboard",
        element: <DashboardPage />,
      },
      {
        path: "trace",
        element: <TraceEntry />,
      },
      {
        path: "badcase",
        element: <BadCasePage />,
      },
      {
        path: "diagnosers",
        element: <DiagnoserPage />,
      },
      {
        path: "diagnose-jobs",
        element: <JobPage />,
      },
      {
        path: "labs/mcp",
        element: <McpPage />,
      },
      {
        path: "labs/skill",
        element: <SkillPage />,
      },
      {
        path: "labs/memory",
        element: <MemoryPage />,
      },
      {
        path: "design/memory-observability",
        element: <MemoryObservabilityDesignPage />,
      },
      {
        path: "trace/compare",
        element: <TraceComparePage />,
      },
      {
        path: "access",
        element: <AccessPage />,
      },
      {
        path: "access/langfuse-onboarding",
        element: <LangfuseOnboardingPage />,
      },
      {
        path: "access/relay-onboarding",
        element: <RelayOnboardingPage />,
      },
      {
        path: "model-pricing",
        element: <ModelPricingPage />,
      },
      {
        path: "model-connections",
        element: <ModelConnectionsPage />,
      },
      {
        path: "plugins",
        element: <PluginsPage />,
      },
      {
        path: "plugins/sandbox",
        element: <SandboxPluginPage />,
      },
      {
        path: "plugins/sandbox/openshell",
        element: <OpenShellPluginPage />,
      },
      {
        path: "sandbox-connections",
        element: <SandboxConnectionsPage />,
      },
      {
        path: "model-connections/:connectionUuid",
        element: <ModelConnectionDetailPage />,
      },
      {
        path: "settings",
        element: <SettingsPage />,
      },
      {
        path: "downloads",
        element: <DownloadPage />,
      },
      {
        path: "receipt",
        element: <ReceiptPage />,
      },
    ],
  },
  {
    path: "/receipt-spa",
    element: (
      <AuthGuard>
        <UserProvider>
          <SpaceProvider>
            <SpacePricingCurrencyProvider>
              <AgentProvider>
                <TimeFilterProvider>
                  <ReceiptSpaPage />
                </TimeFilterProvider>
              </AgentProvider>
            </SpacePricingCurrencyProvider>
          </SpaceProvider>
        </UserProvider>
      </AuthGuard>
    ),
  },
  {
    path: "*",
    element: <NotFoundPage />,
  },
])
