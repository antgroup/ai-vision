export const INSTALLATION_STAGES = [
  "configuration",
  "docker",
  "image",
  "state",
  "container",
  "readiness",
] as const
export const BOOTSTRAP_STAGES = [
  "host",
  "images",
  "configuration",
  "gateway",
  "plugin",
  "readiness",
  "registration",
] as const
export type InstallationStage =
  | (typeof INSTALLATION_STAGES)[number]
  | (typeof BOOTSTRAP_STAGES)[number]
export type ManualInstallationOperation = "install" | "status"
export type InstallationOperation = ManualInstallationOperation | "bootstrap"
export type InstallationStatus =
  | "waiting"
  | "running"
  | "ready"
  | "not_ready"
  | "failed"
  | "revoked"
  | "expired"
export interface InstallationConfiguration {
  label: string
  image: string
  gateway_endpoint: string
  workload_image: string
  config_dir: string
  docker_socket: string
  docker_socket_gid: number | null
  port: number
  workspace: string
  timeout_seconds: number
  gateway_token_file?: string
  gateway_ca_file?: string
  gateway_cert_file?: string
  gateway_key_file?: string
}
export const INSTALLATION_DIAGNOSTIC_CODES = [
  "docker_version_unsupported",
  "landlock_unavailable",
  "landlock_abi_unsupported",
  "environment_probe_failed",
  "image_download_failed",
  "configuration_failed",
  "gateway_start_failed",
  "plugin_start_failed",
  "provider_not_ready",
  "registration_failed",
  "timeout",
  "canceled",
  "docker_unavailable",
  "docker_permission_denied",
  "docker_context_unsupported",
  "deployment_not_found",
  "deployment_ambiguous",
] as const
export interface InstallationDiagnostic {
  code: (typeof INSTALLATION_DIAGNOSTIC_CODES)[number]
  actual?: string
  required?: string
}
export interface InstallationStageSnapshot {
  stage: InstallationStage
  state: "pending" | "start" | "wait" | "done" | "failed"
  detail: string
  elapsed_ms: number
  diagnostic?: InstallationDiagnostic
}
export interface InstallationEvent extends Omit<
  InstallationStageSnapshot,
  "state"
> {
  seq: number
  state: "start" | "wait" | "done" | "failed"
  received_at: string
  image?: { layer: string; status: string; current: string; total: string }
}
interface InstallationSessionBase {
  uuid: string
  space_uuid: string
  provider: "openshell"
  bootstrap_plan_id: string | null
  registration_state: "pending" | "saved"
  connection_uuid: string | null
  status: InstallationStatus
  created_at: string
  updated_at: string
  expires_at: string
  last_received_at: string | null
  last_seq: number
  event_count: number
  stages: InstallationStageSnapshot[]
  events: InstallationEvent[]
}
export interface ManualInstallationSession extends InstallationSessionBase {
  mode: "manual"
  operation: ManualInstallationOperation
  configuration: InstallationConfiguration
}
export interface BootstrapInstallationSession extends InstallationSessionBase {
  mode: "bootstrap"
  operation: "bootstrap"
  configuration: { label: string; image: string; workload_image: string }
}
export type InstallationSession =
  | ManualInstallationSession
  | BootstrapInstallationSession
export interface PreparedInstallation {
  session: ManualInstallationSession
  report_token: string
  report_path: string
}
export interface BootstrapCredentials {
  bootstrap_path: string
  bootstrap_token: string
  report_token: string
  enrollment_token: string
}
export interface PreparedBootstrap extends BootstrapCredentials {
  session: BootstrapInstallationSession
  report_path: string
}
export interface BootstrapCapabilities {
  available: boolean
  reason: "catalog_unavailable" | "deployment_unavailable" | null
  target_label: "AI Vision deployment host"
  product_version: string | null
  provider_version: string | null
  stages: (typeof BOOTSTRAP_STAGES)[number][]
}
export interface BootstrapEnableResult {
  connection_uuid: string
  binding: { connection_uuid: string; require_sandbox: boolean }
}
