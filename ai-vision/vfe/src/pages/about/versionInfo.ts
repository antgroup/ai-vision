export interface BuildInfo {
  schema_version: 1
  component: string
  product_version: string | null
  component_version: string | null
  source_sha: string | null
  build_id: string | null
  built_at: string | null
  target_channel: "dev" | "daily" | "rc" | "stable" | "unknown"
  dirty: boolean | null
}

declare const __AIVISION_BUILD_INFO__: unknown

export interface InstalledRelease {
  product_version: string
  channel: "dev" | "daily" | "rc" | "stable"
  source_sha: string
  build_id: string
  components: { "aivision-fe": BuildInfo; "aivision-be": BuildInfo }
}

export interface VersionObservation {
  backend: BuildInfo
  observed_at: string
  scope: "responding_instance"
  expected: InstalledRelease | null
}

export type BackendState =
  | { status: "loading" | "unavailable"; observation: null }
  | { status: "ready"; observation: VersionObservation }

type Comparison =
  | "matched"
  | "mixed"
  | "unknown"
  | "dirty"
  | "loading"
  | "unavailable"

function object(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function identifier(value: unknown): string | null {
  return typeof value === "string" &&
    /^[a-zA-Z0-9][a-zA-Z0-9._:+/-]{0,127}$/.test(value)
    ? value
    : null
}

// The build loader and runtime parser share the product version grammar.
export function isProductVersion(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?$/.exec(
      value
    )?.[0] === value
  )
}

export function isBuildTimestamp(value: unknown): value is string {
  if (typeof value !== "string") return false
  const match =
    /^(\d{4})-(\d{2})-(\d{2})T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/.exec(
      value
    )
  if (!match || match[0] !== value) return false
  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  if (year < 1 || month < 1 || month > 12 || day < 1) return false
  const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0)
  const daysInMonth = [
    31,
    leapYear ? 29 : 28,
    31,
    30,
    31,
    30,
    31,
    31,
    30,
    31,
    30,
    31,
  ]
  // Date.parse normalizes impossible dates and 24:00; validate the original
  // calendar fields without losing its timezone or microsecond precision.
  return day <= daysInMonth[month - 1]
}

// Construct a new object from documented public fields; never spread API or
// build-system objects into the page or copied report.
function sanitizeBuildInfo(value: unknown, component: string): BuildInfo {
  const candidate = object(value)
  const source =
    candidate?.schema_version === 1 && candidate.component === component
      ? candidate
      : {}
  const channel = source.target_channel
  return {
    schema_version: 1,
    component,
    product_version: isProductVersion(source.product_version)
      ? source.product_version
      : null,
    component_version: isProductVersion(source.component_version)
      ? source.component_version
      : null,
    source_sha:
      typeof source.source_sha === "string" &&
      /^(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$/.test(source.source_sha)
        ? source.source_sha.toLowerCase()
        : null,
    build_id: identifier(source.build_id),
    built_at: isBuildTimestamp(source.built_at) ? source.built_at : null,
    target_channel:
      channel === "dev" ||
      channel === "daily" ||
      channel === "rc" ||
      channel === "stable"
        ? channel
        : "unknown",
    dirty: typeof source.dirty === "boolean" ? source.dirty : null,
  }
}

export function frontendBuildInfo(): BuildInfo {
  return sanitizeBuildInfo(
    typeof __AIVISION_BUILD_INFO__ === "undefined"
      ? null
      : __AIVISION_BUILD_INFO__,
    "aivision-fe"
  )
}

function parseInstalledRelease(value: unknown): InstalledRelease | null {
  if (value === null || value === undefined) return null
  const source = object(value)
  const components = object(source?.components)
  const channel = source?.channel
  if (
    !source ||
    !isProductVersion(source.product_version) ||
    !(
      channel === "dev" ||
      channel === "daily" ||
      channel === "rc" ||
      channel === "stable"
    ) ||
    typeof source.source_sha !== "string" ||
    !/^(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$/.test(source.source_sha) ||
    !identifier(source.build_id) ||
    !components
  )
    throw new Error("Invalid installed release")

  const frontend = sanitizeBuildInfo(components["aivision-fe"], "aivision-fe")
  const backend = sanitizeBuildInfo(components["aivision-be"], "aivision-be")
  for (const component of [frontend, backend]) {
    if (
      !component.product_version ||
      !component.source_sha ||
      !component.build_id ||
      !component.built_at ||
      component.target_channel === "unknown" ||
      component.dirty !== false ||
      !(
        object(components[component.component])?.component_version === null ||
        component.component_version
      )
    )
      throw new Error("Invalid installed component identity")
  }
  return {
    product_version: source.product_version,
    channel,
    source_sha: source.source_sha.toLowerCase(),
    build_id: identifier(source.build_id)!,
    components: { "aivision-fe": frontend, "aivision-be": backend },
  }
}

export function installedRelease(state: BackendState): InstalledRelease | null {
  return state.observation?.expected ?? null
}

export function parseVersionObservation(value: unknown): VersionObservation {
  const envelope = object(value)
  const data = object(envelope?.data)
  const backend = object(data?.backend)
  const observedAt = isBuildTimestamp(data?.observed_at)
    ? data.observed_at
    : null
  if (
    envelope?.code !== 0 ||
    data?.scope !== "responding_instance" ||
    !observedAt ||
    backend?.schema_version !== 1 ||
    backend.component !== "aivision-be"
  ) {
    throw new Error("Invalid version response")
  }
  return {
    backend: sanitizeBuildInfo(backend, "aivision-be"),
    observed_at: observedAt,
    scope: "responding_instance",
    expected: parseInstalledRelease(data.expected),
  }
}

export function compareBuilds(
  frontend: BuildInfo,
  state: BackendState
): Comparison {
  if (state.status !== "ready") return state.status
  const backend = state.observation.backend
  if (frontend.dirty === true || backend.dirty === true) return "dirty"
  const expected = state.observation.expected
  if (!expected) return "unknown"
  const identities = [
    [frontend, expected.components["aivision-fe"]],
    [backend, expected.components["aivision-be"]],
  ] as const
  for (const [actual, target] of identities) {
    if (
      !actual.product_version ||
      !actual.source_sha ||
      !actual.build_id ||
      !actual.built_at ||
      actual.target_channel === "unknown" ||
      actual.dirty === null ||
      (target.component_version !== null && actual.component_version === null)
    )
      return "unknown"
  }
  for (const [actual, target] of identities) {
    if (
      actual.product_version !== target.product_version ||
      actual.component_version !== target.component_version ||
      actual.source_sha !== target.source_sha ||
      actual.build_id !== target.build_id ||
      actual.built_at !== target.built_at ||
      actual.target_channel !== target.target_channel
    )
      return "mixed"
  }
  return "matched"
}

export function versionReport(
  frontend: BuildInfo,
  state: BackendState
): string {
  return JSON.stringify(
    {
      schema_version: 1,
      generated_at: new Date().toISOString(),
      comparison: compareBuilds(frontend, state),
      frontend: sanitizeBuildInfo(frontend, "aivision-fe"),
      backend: state.observation
        ? sanitizeBuildInfo(state.observation.backend, "aivision-be")
        : null,
      backend_status: state.status,
      observed_at: state.observation?.observed_at ?? null,
      scope: state.observation?.scope ?? null,
      expected: state.observation?.expected ?? null,
      deployment_inventory: "not_observed",
      release_acceptance: "not_verified",
    },
    null,
    2
  )
}
