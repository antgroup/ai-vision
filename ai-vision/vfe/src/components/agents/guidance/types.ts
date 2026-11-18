// config_guide v2 schema types for agent onboarding

export interface PrerequisitesSection {
  type: "prerequisites"
  title: string
  items: Array<{ label: string; value: string }>
}

export interface SetupStepItem {
  title: string
  command: string
}

export interface SetupMode {
  label: string
  steps: SetupStepItem[]
}

export interface PlatformConfig {
  modes: Record<string, SetupMode>
}

export interface SetupStepsSection {
  type: "setup_steps"
  title: string
  platforms: Record<string, PlatformConfig>
}

export interface VerificationSection {
  type: "verification"
  title: string
  steps: string[]
}

export type GuideSection =
  | PrerequisitesSection
  | SetupStepsSection
  | VerificationSection

export interface ConfigGuideV2 {
  schema: "v2"
  plugin_key?: string
  native_relay_onboarding?: {
    enabled: boolean
    hermes_version?: string
    relay_requirement?: string
    docs_url?: string
    otlp_endpoint?: string
  }
  sections: GuideSection[]
  warnings?: string[]
  success_message?: string
}

export interface ConfigGuideLegacy {
  setup_guide?: {
    title: string
    description: string
    type: "auto" | "manual"
  }
  limitations?: string[]
  features?: {
    preset_dashboard: boolean
    trace_supported: boolean
  }
  empty_state?: Record<string, string>
  custom_guide?: {
    title: string
    description: string
    checklist?: string[]
  }
  success_message?: string
}

export type ConfigGuide = ConfigGuideV2 | ConfigGuideLegacy

export function isConfigGuideV2(
  guide: ConfigGuide | null | undefined
): guide is ConfigGuideV2 {
  return !!guide && "schema" in guide && guide.schema === "v2"
}

export function detectPlatform(): string {
  const userAgent = navigator.userAgent.toLowerCase()
  if (userAgent.includes("win")) return "Windows"
  return "macOS/Linux"
}
