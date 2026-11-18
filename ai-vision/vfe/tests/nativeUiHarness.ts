import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import ts from "typescript"

const require = createRequire(import.meta.url)

export type UiNode = {
  type: string | ((props: Record<string, unknown>) => unknown)
  props: Record<string, unknown>
}

/** Keep the display/normalization code real; replace browser hooks and UI shells. */
export function nativeUiHarness(boundaries: Record<string, unknown> = {}) {
  const react = {
    ...require("react"),
    useState: (value: unknown) => [
      typeof value === "function" ? value() : value,
      () => {},
    ],
    useMemo: (callback: () => unknown) => callback(),
    useContext: () => null,
    useCallback: (callback: unknown) => callback,
    useEffect: () => {},
    useLayoutEffect: () => {},
    useRef: () => ({ current: null }),
  }
  const modules: Record<string, unknown> = {
    react: { ...react, default: react },
    "react/jsx-runtime": require("react/jsx-runtime"),
    "react-router-dom": { Link: "Link" },
    "react-i18next": {
      useTranslation: () => ({
        t: (key: string) => key,
        i18n: { language: "en-US" },
      }),
    },
    "@/i18n": { isChineseLocale: () => false },
    "@tanstack/react-query": {
      useQueryClient: () => ({ removeQueries: () => {} }),
      useQuery: () => ({ isLoading: false, error: null, refetch: () => {} }),
    },
    "react-markdown": { default: "markdown" },
    "remark-gfm": { default: () => {} },
    sonner: { toast: {} },
    "@/components/labs/shared/LabsIntroBanner": { LabsIntroBanner: () => null },
    "./api": {},
    "./TraceDetailLayout": {
      TraceDetailLayout: ({
        navigation,
        children,
      }: {
        navigation: unknown
        children: unknown
      }) => require("react").createElement("div", null, navigation, children),
    },
    "@/labs/context-compaction/ContextCompactionCard": {
      ContextCompactionCard: () => null,
    },
    "./PricingCurrency": {
      usePricingCurrency: () => ({
        currency: "USD",
        setting: "auto",
        status: "empty",
        canManage: false,
        pending: false,
        error: false,
        setCurrency: () => {},
        sync: async () => {},
        format: (amount: string | null | undefined) => ({
          amount: amount ?? null,
          currency: "USD",
          converted: false,
        }),
      }),
    },
    "@/components/ui/tooltip": {
      Tooltip: ({ children }: { children: unknown }) => children,
      TooltipTrigger: ({ children }: { children: unknown }) => children,
      TooltipContent: () => null,
    },
    "@/lib/api": { api: {}, ApiBusinessError: class extends Error {} },
    "@/contexts/UserContext": { useUser: () => ({ user: null }) },
    "@/components/diagnose/TraceDiagnosisDialog": {
      TraceDiagnosisDialog: "TraceDiagnosisDialog",
    },
    "@/components/traces/shared/TraceDocumentPreviews": {
      TraceDocumentPreviews: ({ children }: { children?: unknown }) =>
        children ?? null,
    },
    "radix-ui": require("radix-ui"),
    ...boundaries,
  }
  modules["@/features/model-pricing/PricingCurrency"] =
    modules["./PricingCurrency"]
  modules["./TraceDocumentPreviews"] =
    modules["@/components/traces/shared/TraceDocumentPreviews"]
  const actualModules: Record<string, string> = {
    "@/agents/lib/agent-display": "agents/lib/agent-display.ts",
    "./TraceOverviewExcerpt": "components/traces-otel/TraceOverviewExcerpt.tsx",
    "@/components/traces-otel/TraceOverviewExcerpt":
      "components/traces-otel/TraceOverviewExcerpt.tsx",
    "./TraceRootIO": "components/traces-otel/TraceRootIO.tsx",
    "./TraceOverviewValue": "components/traces-otel/TraceOverviewValue.tsx",
    "./traceProcessNavigation":
      "components/traces-otel/traceProcessNavigation.ts",
    "../executionMessage": "components/diagnose/executionMessage.ts",
    "./executionMessage": "components/diagnose/executionMessage.ts",
    "./importDialogStyles": "components/labs/shared/importDialogStyles.ts",
    "@/components/labs/shared/LabsAgentQueryNotice":
      "components/labs/shared/LabsAgentQueryNotice.tsx",
    "@/components/labs/shared/LabsCatalogueLayout":
      "components/labs/shared/LabsCatalogueLayout.tsx",
    "./LabsStatStrip": "components/labs/shared/LabsStatStrip.tsx",
    "./labsFormat": "components/labs/shared/labsFormat.ts",
    "./LabsMetricLabel": "components/labs/shared/LabsMetricLabel.tsx",
    "@/components/labs/shared/LabsInventoryCounts":
      "components/labs/shared/LabsInventoryCounts.tsx",
    "../../components/labs/shared/ImportRegistrySearch":
      "components/labs/shared/ImportRegistrySearch.tsx",
    "../../components/labs/shared/importDialogStyles":
      "components/labs/shared/importDialogStyles.ts",
    "@/components/dialogs/DialogContent":
      "components/dialogs/DialogContent.tsx",
    "@/components/dialogs/AlertDialogContent":
      "components/dialogs/AlertDialogContent.tsx",
    "./useDialogFocus": "components/dialogs/useDialogFocus.ts",
    "./dialogLayout": "components/dialogs/dialogLayout.ts",
    "./nativeQueryScheduler": "components/traces-otel/nativeQueryScheduler.ts",
    "@/components/traces-otel/nativeQueryScheduler":
      "components/traces-otel/nativeQueryScheduler.ts",
    "../api": "components/traces-otel/api.ts",
    "../../traces/shared/traceJson": "components/traces/shared/traceJson.ts",
    "../traces/shared/traceJson": "components/traces/shared/traceJson.ts",
    "@/labs/privacy/PrivacyMarkedText": "labs/privacy/PrivacyMarkedText.tsx",
    "./PrivacyMarkedText": "labs/privacy/PrivacyMarkedText.tsx",
    "@/labs/privacy/PrivacyMarkdown": "labs/privacy/PrivacyMarkdown.tsx",
    "@/labs/privacy/privacyMarkers": "labs/privacy/privacyMarkers.ts",
    "./privacyMarkers": "labs/privacy/privacyMarkers.ts",
    "@/labs/privacy/ReportedFieldOmissions":
      "labs/privacy/ReportedFieldOmissions.tsx",
    "./relayPrivacyPolicy": "labs/privacy/relayPrivacyPolicy.ts",
    "../../labs/privacy/relayPrivacyPolicy.ts":
      "labs/privacy/relayPrivacyPolicy.ts",
    "@/agents/shared/dashboardSectionPresentation":
      "agents/shared/dashboardSectionPresentation.ts",
    "@/agents/lib/agentDashboardRoute": "agents/lib/agentDashboardRoute.ts",
    "@/components/bizcharts/QueryStateNotice":
      "components/bizcharts/QueryStateNotice.tsx",
    "@/features/model-pricing/CostSummary":
      "features/model-pricing/CostSummary.tsx",
    "@/features/model-pricing/ReportedCost":
      "features/model-pricing/ReportedCost.tsx",
    "./presentation": "features/model-pricing/presentation.ts",
    "../traces/shared/traceUserInput.ts":
      "components/traces/shared/traceUserInput.ts",
    "../traces/shared/modelSpanPresentation.ts":
      "components/traces/shared/modelSpanPresentation.ts",
    "./traceProcess": "components/traces-otel/traceProcess.ts",
    "./spanIO.ts": "components/traces-otel/spanIO.ts",
    "./spanIO": "components/traces-otel/spanIO.ts",
    "@/lib/documentation": "lib/documentation.ts",
    "./spanModelContext": "components/traces-otel/spanModelContext.ts",
    "./spanModelContext.ts": "components/traces-otel/spanModelContext.ts",
    "./spanMessageProjection":
      "components/traces-otel/spanMessageProjection.ts",
    "./spanMessageProjection.ts":
      "components/traces-otel/spanMessageProjection.ts",
    "./ModelCallRoleBadge": "components/traces-otel/ModelCallRoleBadge.tsx",
    "./SpanAvailableTools": "components/traces-otel/SpanAvailableTools.tsx",
    "./SpanTypedContent": "components/traces-otel/SpanTypedContent.tsx",
    "./PowerContextMemoryContent":
      "components/traces-otel/PowerContextMemoryContent.tsx",
    "@/labs/memory/PowerContextRunCard": "labs/memory/PowerContextRunCard.tsx",
    "./powerContextRun": "labs/memory/powerContextRun.ts",
    "@/labs/memory/powerContextSpans": "labs/memory/powerContextSpans.ts",
    "./powerContextSpans": "labs/memory/powerContextSpans.ts",
    "@/labs/memory/powerContextSpanIcons":
      "labs/memory/powerContextSpanIcons.ts",
    "./powerContextSpanIcons": "labs/memory/powerContextSpanIcons.ts",
    "../mcp/mcpSpanInfo.ts": "labs/mcp/mcpSpanInfo.ts",
    "@/components/traces-otel/SpanTypedContent":
      "components/traces-otel/SpanTypedContent.tsx",
    "@/components/traces-otel/PowerContextMemoryContent":
      "components/traces-otel/PowerContextMemoryContent.tsx",
    "@/components/traces-otel/powerContextContent":
      "components/traces-otel/powerContextContent.ts",
    "@/components/traces-otel/spanIO": "components/traces-otel/spanIO.ts",
    "../../components/traces-otel/format.ts":
      "components/traces-otel/format.ts",
    "./powerContextContent": "components/traces-otel/powerContextContent.ts",
    "@/components/labs/shared/LabsSection":
      "components/labs/shared/LabsSection.tsx",
    "./observabilityViewDocs":
      "components/labs/shared/observabilityViewDocs.ts",
    "@/components/labs/shared/observabilityViewDocs":
      "components/labs/shared/observabilityViewDocs.ts",
    "../../../lib/documentation.ts": "lib/documentation.ts",
    "@/components/traces/shared/TraceTextContent":
      "components/traces/shared/TraceTextContent.tsx",
    "./TraceTextContent": "components/traces/shared/TraceTextContent.tsx",
    "@/components/traces/shared/TraceCodeSnippet":
      "components/traces/shared/TraceCodeSnippet.tsx",
    "./TraceCodeSnippet": "components/traces/shared/TraceCodeSnippet.tsx",
    "@/components/traces/shared/traceCodeFences":
      "components/traces/shared/traceCodeFences.ts",
    "./traceCodeFences": "components/traces/shared/traceCodeFences.ts",
    "@/components/traces/shared/traceCodeTextParts":
      "components/traces/shared/traceCodeTextParts.ts",
    "./traceCodeTextParts": "components/traces/shared/traceCodeTextParts.ts",
    "./TraceCodeHighlight": "components/traces/shared/TraceCodeHighlight.tsx",
    "./traceCodeHighlighter":
      "components/traces/shared/traceCodeHighlighter.ts",
    "@/components/traces/shared/traceHtmlLinkUrl":
      "components/traces/shared/traceHtmlLinkUrl.ts",
    "./traceHtmlLinkUrl": "components/traces/shared/traceHtmlLinkUrl.ts",
    "../../../labs/privacy/privacyMarkers.ts": "labs/privacy/privacyMarkers.ts",
    "./shared/MarkdownMedia": "components/traces-otel/shared/MarkdownMedia.tsx",
    "../traces/shared/traceUserInput":
      "components/traces/shared/traceUserInput.ts",
    "./spanContent": "components/traces-otel/spanContent.ts",
    "./spanContent.ts": "components/traces-otel/spanContent.ts",
    "./spanFilters": "components/traces-otel/spanFilters.ts",
    "./spanCostFilter": "components/traces-otel/spanCostFilter.ts",
    "../../features/model-pricing/currency":
      "features/model-pricing/currency.ts",
    "./SpanDetailErrorBoundary":
      "components/traces-otel/SpanDetailErrorBoundary.tsx",
    "@/components/labs/shared/LabsLoadMore":
      "components/labs/shared/LabsLoadMore.tsx",
    "@/components/ui/hover-card": "components/ui/hover-card.tsx",
    "@/components/ui/popover": "components/ui/popover.tsx",
    "@/components/traces/shared/traceUserInput":
      "components/traces/shared/traceUserInput.ts",
    "../ModelValidationSummary":
      "components/diagnose/ModelValidationSummary.tsx",
    "@/components/traces/shared/modelSpanPresentation":
      "components/traces/shared/modelSpanPresentation.ts",
    "./diagnosisScope": "components/diagnose/diagnosisScope.ts",
    "./TrialResultView": "components/diagnose/diagnoser/TrialResultView.tsx",
    "./RuleTrialEvidence":
      "components/diagnose/diagnoser/RuleTrialEvidence.tsx",
    "./SpecialistTrialEvidence":
      "components/diagnose/diagnoser/SpecialistTrialEvidence.tsx",
    "../specialistPolicy": "components/diagnose/specialistPolicy.ts",
    "./trialResult": "components/diagnose/diagnoser/trialResult.ts",
    "./utils": "components/traces-otel/shared/utils.ts",
    "./JsonDetailWidgets":
      "components/traces-otel/shared/JsonDetailWidgets.tsx",
    "./shared/JsonDetailWidgets":
      "components/traces-otel/shared/JsonDetailWidgets.tsx",
    "./shared/utils": "components/traces-otel/shared/utils.ts",
    "./shared/TraceContentModeToggle":
      "components/traces-otel/shared/TraceContentModeToggle.tsx",
    "./shared/TraceEnhancedContent":
      "components/traces-otel/shared/TraceEnhancedContent.tsx",
    "@/components/traces/shared/traceEnhancedVisibility":
      "components/traces/shared/traceEnhancedVisibility.ts",
    "@/components/traces/shared/TraceRecordControls":
      "components/traces/shared/TraceRecordControls.tsx",
    "./shared/MetadataTree": "components/traces-otel/shared/MetadataTree.tsx",
    "./shared/TraceBadge": "components/traces-otel/shared/TraceBadge.tsx",
    "@/components/traces-otel/shared/TraceBadge":
      "components/traces-otel/shared/TraceBadge.tsx",
    "./format": "components/traces-otel/format.ts",
    "@/labs/mcp/mcpDisplay": "labs/mcp/mcpDisplay.ts",
    "./mcpSpanInfo": "labs/mcp/mcpSpanInfo.ts",
    "@/labs/skill/skillDisplay": "labs/skill/skillDisplay.ts",
    "@/components/traces-otel/format": "components/traces-otel/format.ts",
    "@/components/traces-otel/nativeTracePreview":
      "components/traces-otel/nativeTracePreview.ts",
    "@/components/traces-otel/SessionStatusLabel":
      "components/traces-otel/SessionStatusLabel.tsx",
    "./spanSearch": "components/traces-otel/spanSearch.ts",
    "./sessionIO": "components/traces-otel/sessionIO.ts",
    "./sessionMessages": "components/traces-otel/sessionMessages.ts",
    "./SessionStructuredContent":
      "components/traces-otel/SessionStructuredContent.tsx",
    "./SessionIOSources": "components/traces-otel/SessionIOSources.tsx",
    "./usage": "components/traces-otel/usage.ts",
    "./spanUsage": "components/traces-otel/spanUsage.ts",
    "./spanKind": "components/traces-otel/spanKind.ts",
    "./traceUsage": "components/traces-otel/traceUsage.ts",
    "@/components/traces-otel/traceUsage":
      "components/traces-otel/traceUsage.ts",
    "@/lib/clipboard": "lib/clipboard.ts",
    "@/lib/utils": "lib/utils.ts",
    "@/components/labs/decision/decisionDisplay":
      "components/labs/decision/decisionDisplay.ts",
    "./decisionDisplay": "components/labs/decision/decisionDisplay.ts",
    "@/components/labs/decision/decisionText":
      "components/labs/decision/decisionText.ts",
    "@/components/labs/shared/labsFormat":
      "components/labs/shared/labsFormat.ts",
    "@/components/labs/shared/LabsMetricLabel":
      "components/labs/shared/LabsMetricLabel.tsx",
    "./types": "components/traces-otel/types.ts",
    "./types.ts": "components/traces-otel/types.ts",
    "@/agents/traces/sofaTraceTime": "agents/traces/sofaTraceTime.ts",
    "@/agents/traces/TracePagination": "agents/traces/TracePagination.tsx",
    "@/agents/traces/traceListThresholds":
      "agents/traces/traceListThresholds.ts",
    "./traceListThresholds": "agents/traces/traceListThresholds.ts",
    "./traceToolbarStyles": "agents/traces/traceToolbarStyles.ts",
    "@/components/traces/shared/traceMediaOutput":
      "components/traces/shared/traceMediaOutput.ts",
    "./traceMediaProjection":
      "components/traces/shared/traceMediaProjection.ts",
    "./traceMediaText": "components/traces/shared/traceMediaText.ts",
    "./traceEnhancedVisibility":
      "components/traces/shared/traceEnhancedVisibility.ts",
    "../../traces/shared/longJsonString":
      "components/traces/shared/longJsonString.ts",
    "./traceJson": "components/traces/shared/traceJson.ts",
    "@/components/traces/shared/traceMediaProjection":
      "components/traces/shared/traceMediaProjection.ts",
    "@/components/traces/shared/traceExport":
      "components/traces/shared/traceExport.ts",
    "@/components/traces/shared/traceJson":
      "components/traces/shared/traceJson.ts",
    "./traceJson.ts": "components/traces/shared/traceJson.ts",
    "./traceIntegrity.ts": "components/traces/shared/traceIntegrity.ts",
    "@/components/traces/ShareDialog": "components/traces/ShareDialog.tsx",
    "@/lib/formatters": "lib/formatters.ts",
    "./builtinDiagnoserPolicy": "components/diagnose/builtinDiagnoserPolicy.ts",
    "./diagnosisRunLabels": "components/diagnose/diagnosisRunLabels.ts",
  }
  const shells = new Set([
    "lucide-react",
    "@/components/product-guide/DemoControls",
    "./DashboardCost",
    "@/components/traces/shared/TraceUserInputSummary",
    "@/components/traces/shared/TraceMediaSummary",
    "./MarkdownMedia",
    "@/components/traces/shared/MarkdownMedia",
    "@/components/traces/shared/TraceMedia",
    "./SpanTree",
    "./SpanContentEnhancement",
    "./SpanFilterBar",
    "./SpanTimeline",
    "./graph",
    "./SpanDetailPanel",
    "./TraceProcessView",
    "@/components/labs/decision/DecisionSummary",
    "@/labs/memory/MemorySpanCard",
    "@/labs/memory/MemoryTraceSummary",
    "@/labs/mcp/McpTraceSummary",
    "@/labs/skill/SkillTraceSummary",
    "@/labs/skill/SkillSpanCard",
    "@/components/error/ErrorPage",
  ])
  const shell = new Proxy({}, { get: (_, key) => String(key) })
  function importModule(name: string): unknown {
    if (Object.hasOwn(modules, name)) return modules[name]
    const actual =
      actualModules[name] ?? actualModules[name.replace(/\.(?:ts|tsx)$/, "")]
    if (actual) {
      modules[name] = load(actual)
      return modules[name]
    }
    if (name.startsWith("@/components/ui/") || shells.has(name)) return shell
    if (
      name === "clsx" ||
      name === "tailwind-merge" ||
      name.startsWith("@codemirror/") ||
      name === "@lezer/highlight"
    )
      return require(name)
    throw new Error(`Unexpected UI dependency: ${name}`)
  }
  function load<T>(path: string): T {
    const source = readFileSync(
      new URL(`../src/${path}`, import.meta.url),
      "utf8"
    )
    const { outputText } = ts.transpileModule(source, {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
      },
    })
    const exports = {}
    new Function("require", "exports", outputText)(importModule, exports)
    return exports as T
  }
  return { load }
}

export function uiElements(tree: unknown, name: string): UiNode[] {
  if (Array.isArray(tree)) return tree.flatMap((node) => uiElements(node, name))
  if (!tree || typeof tree !== "object" || !("props" in tree)) return []
  const node = tree as UiNode
  const componentName =
    typeof node.type === "function" ? node.type.name : node.type
  return [
    ...(componentName === name ? [node] : []),
    ...uiElements(node.props.navigation, name),
    ...uiElements(node.props.children, name),
  ]
}

export function uiText(tree: unknown): string {
  if (Array.isArray(tree)) return tree.map(uiText).join("")
  if (tree === null || tree === undefined || typeof tree === "boolean")
    return ""
  if (typeof tree !== "object") return String(tree)
  return uiText((tree as UiNode).props?.children)
}
