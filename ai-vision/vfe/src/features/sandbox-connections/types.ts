export interface SandboxConnection {
  uuid: string
  space_uuid: string
  name: string
  provider: "openshell"
  endpoint: string
  api_key_configured: boolean
  image: string
  cpu: number
  memory: string
  timeout_seconds: number
  config_revision: number
  status: "untested" | "ready" | "unavailable" | "disabled"
  test_error?: string | null
  gmt_create?: string
  gmt_modified?: string
}

export interface SandboxProvider {
  id: "openshell"
  name: string
  protocol_version: number
  capabilities: string[]
}

export interface SandboxBinding {
  space_uuid: string
  connection_uuid: string | null
  require_sandbox: boolean
}

export interface SandboxConnectionInput {
  name: string
  provider: "openshell"
  endpoint: string
  api_key?: string
  image: string
  cpu: number
  memory: string
  timeout_seconds: number
  status?: "disabled" | "untested"
}

export interface SandboxSelection {
  mode: "inherit" | "required" | "local"
  connection_uuid?: string
  [key: string]: unknown
}

export function sandboxSelectionFromSpec(
  spec: Record<string, unknown>
): SandboxSelection {
  const selection = spec.sandbox
  if (!selection || typeof selection !== "object" || Array.isArray(selection))
    return { mode: "inherit" }
  const value = selection as Record<string, unknown>
  return {
    ...value,
    mode: ["inherit", "required", "local"].includes(String(value.mode))
      ? (value.mode as SandboxSelection["mode"])
      : "inherit",
    ...(typeof value.connection_uuid === "string"
      ? { connection_uuid: value.connection_uuid }
      : {}),
  }
}

/** Keep unedited/extension fields when the Rule editor changes visible controls. */
export function buildSandboxRuleSpec(
  previous: Record<string, unknown> | undefined,
  visible: Record<string, unknown>,
  selection: SandboxSelection,
  selectionChanged: boolean
): Record<string, unknown> {
  const result = { ...previous, ...visible }
  // Omission remains omission for existing rules unless the selector was edited.
  if (selectionChanged || !previous) result.sandbox = selection
  return result
}

export interface SandboxConnectionRuntime {
  connection_uuid: string
  config_revision: number
  image: string
  ready: boolean
  capabilities: string[]
  runtime_manifests: RuleRuntimeManifest[]
  effective_timeout_seconds: number
  effective_input_bytes?: number | null
}

export interface RuleRuntimeManifest {
  id: string
  manifest_sha256: string
  python_version?: string
  packages?: Record<string, string>
  image?: string
}
