import assert from "node:assert/strict"
import test from "node:test"
import { existsSync } from "node:fs"
import { execFileSync } from "node:child_process"
import { nativeUiHarness } from "./nativeUiHarness.ts"

const source = new URL(
  "../src/features/plugins/installation.ts",
  import.meta.url
)
const configuration = {
  label: "Sandbox operator",
  image: "registry.invalid/plugin:fixed",
  gateway_endpoint: "gateway.invalid:8080",
  workload_image: "registry.invalid/worker:fixed",
  config_dir: "/tmp/operator's $(printf unsafe)",
  docker_socket: "/var/run/docker.sock",
  docker_socket_gid: 0,
  port: 8092,
  workspace: "default",
  timeout_seconds: 900,
}

test("copied commands leave ordinary command words, options and values unquoted", async () => {
  const { buildBootstrapCommand, buildInstallationCommand } = await import(
    source.href
  )
  assert.equal(
    buildBootstrapCommand(
      {
        bootstrap_path: "/api/public/sandbox-installations/setup-one",
        bootstrap_token: "bootstrap_123-abc",
        report_token: "report_123-abc",
        enrollment_token: "enroll_123-abc",
      },
      "https://vision.invalid"
    ),
    "aiv plugin setup openshell --session-url https://vision.invalid/api/public/sandbox-installations/setup-one --bootstrap-token bootstrap_123-abc --progress-token report_123-abc --enrollment-token enroll_123-abc"
  )
  assert.equal(
    buildInstallationCommand(
      "status",
      { ...configuration, config_dir: "/opt/ai-vision/openshell" },
      "/api/public/sandbox-installations/session-one/events",
      "report_123-abc",
      "https://vision.invalid"
    ),
    "aiv plugin status openshell --state-dir /opt/ai-vision/openshell/installation --progress-url https://vision.invalid/api/public/sandbox-installations/session-one/events --progress-token report_123-abc"
  )
})

test("shell arguments preserve empty values, whitespace and metacharacters without expansion", async (t) => {
  if (process.platform === "win32")
    return t.skip("POSIX shell execution requires Linux or macOS")
  const { shellQuote } = await import(source.href)
  const values = [
    "",
    "two words",
    "operator's sandbox",
    "line one\nline two",
    "$HOME",
    "$(printf injected)",
    "`printf injected`",
    "value; printf injected",
    "*.ts",
    "~root",
    "=ls",
    "https://vision.invalid/path?a=1&b=2",
  ]
  const shells = ["/bin/sh", ...(existsSync("/bin/zsh") ? ["/bin/zsh"] : [])]
  for (const shell of shells) {
    const args = JSON.parse(
      execFileSync(
        shell,
        [
          "-c",
          `set -- ${values.map(shellQuote).join(" ")}; python3 -c 'import sys,json; print(json.dumps(sys.argv[1:]))' "$@"`,
        ],
        { encoding: "utf8" }
      )
    ) as string[]
    assert.deepEqual(args, values, `${shell} must preserve literal arguments`)
  }
})

test("host command preserves shell metacharacters, zero socket GID and real reporting flags", async (t) => {
  if (process.platform === "win32")
    return t.skip("POSIX shell execution requires Linux or macOS")
  assert.ok(existsSync(source), "installation command builder must exist")
  const { buildInstallationCommand } = await import(source.href)
  const command = buildInstallationCommand(
    "install",
    configuration,
    "/api/public/sandbox-installations/session-one/events",
    "secret'$()",
    "https://vision.invalid"
  )
  const args = JSON.parse(
    execFileSync(
      "/bin/sh",
      [
        "-c",
        `set -- ${command}; python3 -c 'import sys,json; print(json.dumps(sys.argv[1:]))' "$@"`,
      ],
      { encoding: "utf8" }
    )
  ) as string[]
  assert.deepEqual(args.slice(0, 4), ["aiv", "plugin", "install", "openshell"])
  const option = (name: string) => args[args.indexOf(name) + 1]
  assert.equal(
    option("--state-dir"),
    configuration.config_dir + "/installation"
  )
  assert.equal(
    option("--token-file"),
    configuration.config_dir + "/plugin-token"
  )
  assert.equal(option("--docker-socket-gid"), "0")
  assert.equal(option("--progress-token"), "secret'$()")
  assert.equal(
    option("--progress-url"),
    "https://vision.invalid/api/public/sandbox-installations/session-one/events"
  )
})

test("status command inspects the original installation without reinstall options", async () => {
  assert.ok(existsSync(source), "installation command builder must exist")
  const { buildInstallationCommand, mayPrepareInstallationAgain } =
    await import(source.href)
  const command = buildInstallationCommand(
    "status",
    configuration,
    "/api/public/sandbox-installations/session-one/events",
    "one-use",
    "https://vision.invalid"
  )
  assert.match(command, /^aiv plugin status openshell /)
  assert.doesNotMatch(command, /--image|--token-file|--timeout/)
  assert.match(command, /--state-dir/)
  assert.equal(
    mayPrepareInstallationAgain({
      status: "failed",
      operation: "install",
      stages: [{ stage: "image", state: "failed" }],
    }),
    true
  )
  for (const status of [
    "ready",
    "not_ready",
    "running",
    "waiting",
    "expired",
    "revoked",
  ])
    assert.equal(
      mayPrepareInstallationAgain({ status, operation: "install", stages: [] }),
      false
    )
  assert.equal(
    mayPrepareInstallationAgain({
      status: "failed",
      operation: "install",
      stages: [{ stage: "container", state: "start" }],
    }),
    false
  )
  assert.equal(
    mayPrepareInstallationAgain({
      status: "revoked",
      operation: "install",
      last_seq: 0,
      stages: [],
    }),
    true
  )
  assert.equal(
    mayPrepareInstallationAgain({
      status: "revoked",
      operation: "status",
      last_seq: 0,
      stages: [],
    }),
    false
  )
})

test("configuration rejects unsafe paths and incomplete TLS pairs without losing GID zero", async () => {
  assert.ok(existsSync(source), "installation validation must exist")
  const { validInstallationConfiguration } = await import(source.href)
  assert.equal(validInstallationConfiguration(configuration), true)
  for (const config_dir of [
    "/",
    "/tmp/..",
    "/tmp/../.",
    "relative",
    "/tmp/a,b",
    "/tmp/a\nb",
  ])
    assert.equal(
      validInstallationConfiguration({ ...configuration, config_dir }),
      false
    )
  assert.equal(
    validInstallationConfiguration({
      ...configuration,
      gateway_cert_file: "/tmp/cert",
    }),
    false
  )
  assert.equal(
    validInstallationConfiguration({ ...configuration, timeout_seconds: 59 }),
    false
  )
  for (const workspace of ["team/prod", "Team Sandbox"])
    assert.equal(
      validInstallationConfiguration({ ...configuration, workspace }),
      true
    )
  assert.equal(
    validInstallationConfiguration({
      ...configuration,
      gateway_endpoint: "[fe80::1%eth0]:8080",
    }),
    true
  )
  for (const gateway_endpoint of [
    "gateway.invalid:0",
    "gateway.invalid:99999",
    "gateway.invalid:008080",
    "https://gateway.invalid:8080",
    "gateway.invalid:80/path",
  ])
    assert.equal(
      validInstallationConfiguration({ ...configuration, gateway_endpoint }),
      gateway_endpoint === "gateway.invalid:008080"
    )
})

test("installation browser API uses encoded Space/session paths and sends only explicit operation and configuration", async () => {
  const calls: { method: string; url: string; body?: unknown }[] = []
  const method = (name: string) => async (url: string, body?: unknown) => {
    calls.push({ method: name, url, body })
    return { code: 0, data: {} }
  }
  const api = nativeUiHarness({
    "@/lib/api": {
      api: {
        get: method("GET"),
        post: method("POST"),
        delete: method("DELETE"),
      },
      throwOnApiError: (response: unknown) => response,
    },
  }).load<typeof import("../src/features/plugins/installationApi.ts")>(
    "features/plugins/installationApi.ts"
  )
  await api.listInstallations("team/a")
  await api.getInstallation("team/a", "job/a")
  await api.prepareInstallation("team/a", "status", configuration)
  await api.revokeInstallation("team/a", "job/a")
  assert.deepEqual(
    calls.map(({ method, url }) => [method, url]),
    [
      ["GET", "/spaces/team%2Fa/integrations/sandbox-installations"],
      ["GET", "/spaces/team%2Fa/integrations/sandbox-installation/job%2Fa"],
      ["POST", "/spaces/team%2Fa/integrations/sandbox-installations"],
      ["DELETE", "/spaces/team%2Fa/integrations/sandbox-installation/job%2Fa"],
    ]
  )
  assert.deepEqual(calls[2].body, { operation: "status", configuration })
})

test("Sandbox permissions cannot reuse a previous manager's cached settings for a new actor", () => {
  let actor: string | undefined = "readonly-actor"
  let options: { queryKey: unknown[]; enabled: boolean } | undefined
  const hook = nativeUiHarness({
    "@/contexts/UserContext": {
      useOptionalUser: () => ({ user: actor ? { user_uuid: actor } : null }),
    },
    "@tanstack/react-query": {
      useQuery: (value: typeof options) => {
        options = value
        return {
          data: value?.queryKey.includes("readonly-actor")
            ? { code: 0, data: { permissions: ["sandbox_connection.read"] } }
            : {
                code: 0,
                data: {
                  permissions: [
                    "sandbox_connection.read",
                    "sandbox_connection.manage",
                  ],
                },
              },
        }
      },
    },
    "@/lib/api": { api: {}, throwOnApiError: (value: unknown) => value },
  }).load<
    typeof import("../src/features/sandbox-connections/useSandboxConnectionPermissions.ts")
  >("features/sandbox-connections/useSandboxConnectionPermissions.ts")
  assert.equal(
    hook.useSandboxConnectionPermissions("space-one").canManage,
    false
  )
  assert.deepEqual(options?.queryKey, [
    "settings",
    "space-one",
    "readonly-actor",
  ])
  actor = undefined
  hook.useSandboxConnectionPermissions("space-one")
  assert.equal(options?.enabled, false)
})

test("bootstrap command has only the setup session and three safely quoted capabilities", async (t) => {
  if (process.platform === "win32")
    return t.skip("POSIX shell execution requires Linux or macOS")
  const helpers = await import(source.href)
  assert.equal(
    typeof helpers.buildBootstrapCommand,
    "function",
    "zero-input setup command builder must exist"
  )
  const command = helpers.buildBootstrapCommand(
    {
      bootstrap_path: "/api/public/sandbox-installations/setup-one",
      bootstrap_token: "bootstrap'$()",
      report_token: "report'",
      enrollment_token: "enroll'",
    },
    "https://vision.invalid"
  )
  const args = JSON.parse(
    execFileSync(
      "/bin/sh",
      [
        "-c",
        `set -- ${command}; python3 -c 'import sys,json; print(json.dumps(sys.argv[1:]))' "$@"`,
      ],
      { encoding: "utf8" }
    )
  ) as string[]
  assert.deepEqual(args, [
    "aiv",
    "plugin",
    "setup",
    "openshell",
    "--session-url",
    "https://vision.invalid/api/public/sandbox-installations/setup-one",
    "--bootstrap-token",
    "bootstrap'$()",
    "--progress-token",
    "report'",
    "--enrollment-token",
    "enroll'",
  ])
})

test("bootstrap readiness keeps polling until a saved connection UUID is confirmed", async () => {
  const helpers = await import(source.href)
  assert.equal(
    typeof helpers.bootstrapIsSettled,
    "function",
    "bootstrap lifecycle must distinguish registration"
  )
  assert.equal(
    helpers.bootstrapIsSettled({
      status: "ready",
      registration_state: "pending",
      connection_uuid: null,
    }),
    false
  )
  assert.equal(
    helpers.bootstrapIsSettled({
      status: "ready",
      registration_state: "saved",
      connection_uuid: null,
    }),
    false
  )
  assert.equal(
    helpers.bootstrapIsSettled({
      status: "ready",
      registration_state: "saved",
      connection_uuid: "connection-a",
    }),
    true
  )
  for (const status of ["failed", "not_ready", "revoked", "expired"])
    assert.equal(
      helpers.bootstrapIsSettled({
        status,
        registration_state: "pending",
        connection_uuid: null,
      }),
      true
    )
})

test("default preparation sends no technical configuration and enabling is an explicit scoped POST", async () => {
  const calls: { method: string; url: string; body?: unknown }[] = []
  const method = (name: string) => async (url: string, body?: unknown) => {
    calls.push({ method: name, url, body })
    return { code: 0, data: {} }
  }
  const api = nativeUiHarness({
    "@/lib/api": {
      api: { get: method("GET"), post: method("POST") },
      throwOnApiError: (value: unknown) => value,
    },
  }).load<typeof import("../src/features/plugins/installationApi.ts")>(
    "features/plugins/installationApi.ts"
  )
  assert.equal(
    typeof api.prepareBootstrap,
    "function",
    "default zero-form preparation must exist"
  )
  await api.getBootstrapCapabilities("team/a")
  await api.prepareBootstrap("team/a")
  await api.enableBootstrap("team/a", "job/a")
  assert.deepEqual(calls, [
    {
      method: "GET",
      url: "/spaces/team%2Fa/integrations/sandbox-bootstrap",
      body: undefined,
    },
    {
      method: "POST",
      url: "/spaces/team%2Fa/integrations/sandbox-installations",
      body: { operation: "bootstrap" },
    },
    {
      method: "POST",
      url: "/spaces/team%2Fa/integrations/sandbox-installation/job%2Fa/enable",
      body: {},
    },
  ])
})
