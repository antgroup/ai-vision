import type {
  InstallationConfiguration,
  ManualInstallationOperation,
  InstallationSession,
  BootstrapCredentials,
  BootstrapInstallationSession,
} from "./installationTypes.ts"

export const DEFAULT_INSTALLATION_CONFIGURATION: InstallationConfiguration = {
  label: "",
  image: "",
  gateway_endpoint: "",
  workload_image: "",
  config_dir: "",
  docker_socket: "/var/run/docker.sock",
  docker_socket_gid: null,
  port: 8092,
  workspace: "default",
  timeout_seconds: 900,
}

export function shellQuote(value: string) {
  // zsh expands a leading '=' even when every character is otherwise safe.
  if (!value.startsWith("=") && /^[A-Za-z0-9_@%+=:,./-]+$/.test(value))
    return value
  return "'" + value.replaceAll("'", "'\"'\"'") + "'"
}

export function installationStateDirectory(
  configuration: InstallationConfiguration
) {
  return configuration.config_dir.replace(/\/+$/, "") + "/installation"
}

export function buildInstallationCommand(
  operation: ManualInstallationOperation,
  configuration: InstallationConfiguration,
  reportPath: string,
  reportToken: string,
  origin: string
) {
  const directory = configuration.config_dir.replace(/\/+$/, "")
  const args = ["aiv", "plugin", operation, "openshell"]
  const option = (name: string, value: string | number) =>
    args.push(name, String(value))
  if (operation === "install") {
    option("--image", configuration.image)
    option("--gateway-endpoint", configuration.gateway_endpoint)
    option("--workload-image", configuration.workload_image)
    option("--token-file", directory + "/plugin-token")
    option("--policy-file", directory + "/policy.yaml")
    option("--journal-dir", directory + "/journal")
    option("--docker-socket", configuration.docker_socket)
    if (configuration.docker_socket_gid !== null)
      option("--docker-socket-gid", configuration.docker_socket_gid)
    option("--port", configuration.port)
    option("--workspace", configuration.workspace)
    option("--timeout", `${configuration.timeout_seconds}s`)
    for (const [key, flag] of [
      ["gateway_token_file", "--gateway-token-file"],
      ["gateway_ca_file", "--gateway-ca-file"],
      ["gateway_cert_file", "--gateway-cert-file"],
      ["gateway_key_file", "--gateway-key-file"],
    ] as const)
      if (configuration[key]) option(flag, configuration[key])
  }
  option("--state-dir", installationStateDirectory(configuration))
  option("--progress-url", new URL(reportPath, origin).href)
  option("--progress-token", reportToken)
  return args.map(shellQuote).join(" ")
}

export function buildBootstrapCommand(
  credentials: BootstrapCredentials,
  origin: string
) {
  return [
    "aiv",
    "plugin",
    "setup",
    "openshell",
    "--session-url",
    new URL(credentials.bootstrap_path, origin).href,
    "--bootstrap-token",
    credentials.bootstrap_token,
    "--progress-token",
    credentials.report_token,
    "--enrollment-token",
    credentials.enrollment_token,
  ]
    .map(shellQuote)
    .join(" ")
}

export function bootstrapIsSettled(
  session: Pick<
    BootstrapInstallationSession,
    "status" | "registration_state" | "connection_uuid"
  >
) {
  if (session.status === "waiting" || session.status === "running") return false
  if (session.status === "ready")
    return session.registration_state === "saved" && !!session.connection_uuid
  return true
}

export function installationIsTerminal(
  session: Pick<InstallationSession, "status">
) {
  return session.status !== "waiting" && session.status !== "running"
}

export function mayPrepareInstallationAgain(
  session: Pick<InstallationSession, "status" | "operation"> & {
    stages: { stage: string; state: string }[]
    last_seq?: number
  }
) {
  return (
    session.operation === "install" &&
    (((session.status === "revoked" || session.status === "expired") &&
      session.last_seq === 0) ||
      (session.status === "failed" &&
        !session.stages.some(
          (stage) =>
            ["container", "readiness"].includes(stage.stage) &&
            stage.state !== "pending"
        )))
  )
}

export function validInstallationConfiguration(
  value: InstallationConfiguration
) {
  const text = (input: string, limit: number) =>
    !!input && input.length <= limit && !/\p{Cc}/u.test(input)
  const path = (input: string) => {
    const segments: string[] = []
    for (const segment of input.split("/")) {
      if (segment === "..") segments.pop()
      else if (segment && segment !== ".") segments.push(segment)
    }
    return (
      input.startsWith("/") &&
      segments.length > 0 &&
      text(input, 1024) &&
      !input.includes(",")
    )
  }
  const image = (text: string) =>
    /^[A-Za-z0-9][A-Za-z0-9._/@:-]{0,511}$/.test(text)
  const gateway = value.gateway_endpoint.match(
    /^(?:\[[^[\]\s]+\]|[^:\s]+):([0-9]+)$/
  )
  return (
    !!value.label.trim() &&
    text(value.label, 128) &&
    image(value.image) &&
    image(value.workload_image) &&
    text(value.gateway_endpoint, 512) &&
    !/[/@?#]/.test(value.gateway_endpoint) &&
    !!gateway &&
    Number(gateway[1]) >= 1 &&
    Number(gateway[1]) <= 65535 &&
    path(value.config_dir) &&
    path(value.docker_socket) &&
    (value.docker_socket_gid === null ||
      (Number.isInteger(value.docker_socket_gid) &&
        value.docker_socket_gid >= 0 &&
        value.docker_socket_gid <= 2147483647)) &&
    Number.isInteger(value.port) &&
    value.port >= 1 &&
    value.port <= 65535 &&
    text(value.workspace, 128) &&
    Number.isInteger(value.timeout_seconds) &&
    value.timeout_seconds >= 60 &&
    value.timeout_seconds <= 1800 &&
    !!value.gateway_cert_file === !!value.gateway_key_file &&
    [
      value.gateway_token_file,
      value.gateway_ca_file,
      value.gateway_cert_file,
      value.gateway_key_file,
    ].every((text) => !text || path(text))
  )
}
