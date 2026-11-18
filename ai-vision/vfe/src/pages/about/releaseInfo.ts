import {
  isBuildTimestamp,
  isProductVersion,
  type InstalledRelease,
} from "./versionInfo"

export const RELEASES_URL = "https://github.com/antgroup/ai-vision/releases"

export interface ReleaseInfo {
  version: string
  tag_name: string
  name: string
  body: string
  url: string
  published_at: string
}

export type ReleaseState =
  | {
      status: "loading" | "unavailable" | "no_release" | "disabled"
      latest: null
      checked_at: string | null
    }
  | { status: "available"; latest: ReleaseInfo; checked_at: string | null }

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

export function parseReleaseResponse(value: unknown): ReleaseState {
  const envelope = record(value)
  const data = record(envelope?.data)
  const status = data?.status
  if (
    envelope?.code !== 0 ||
    !data ||
    !(data.checked_at === null || isBuildTimestamp(data.checked_at))
  )
    throw new Error("Invalid release response")

  const checked_at = data.checked_at as string | null
  if (
    status === "no_release" ||
    status === "unavailable" ||
    status === "disabled"
  ) {
    if (data.latest !== null) throw new Error("Invalid release response")
    return { status, latest: null, checked_at }
  }
  const latest = record(data.latest)
  if (
    status !== "available" ||
    !latest ||
    !isProductVersion(latest.version) ||
    latest.version.includes("-") ||
    !(
      latest.tag_name === latest.version ||
      latest.tag_name === `v${latest.version}`
    ) ||
    latest.url !== `${RELEASES_URL}/tag/${latest.tag_name}` ||
    typeof latest.name !== "string" ||
    latest.name.length > 512 ||
    typeof latest.body !== "string" ||
    latest.body.length > 131_072 ||
    !isBuildTimestamp(latest.published_at)
  )
    throw new Error("Invalid release response")

  return {
    status: "available",
    checked_at,
    latest: {
      version: latest.version,
      tag_name: latest.tag_name as string,
      name: latest.name,
      body: latest.body,
      url: latest.url,
      published_at: latest.published_at,
    },
  }
}

export function updateStatus(
  installed: InstalledRelease | null,
  release: ReleaseState
) {
  if (release.status !== "available") return release.status
  if (!installed) return "unknown"
  const current = installed.product_version.split("-")[0].split(".").map(BigInt)
  const latest = release.latest.version.split(".").map(BigInt)
  for (let index = 0; index < 3; index++) {
    if (current[index] < latest[index]) return "update"
    if (current[index] > latest[index]) return "ahead"
  }
  if (installed.channel !== "stable" || installed.product_version.includes("-"))
    return "preview"
  return "current"
}
