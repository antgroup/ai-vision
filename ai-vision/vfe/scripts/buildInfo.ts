import { execFileSync } from "node:child_process"
import { readFileSync } from "node:fs"
import path from "node:path"
import {
  isBuildTimestamp,
  isProductVersion,
} from "../src/pages/about/versionInfo.ts"

function git(root: string, args: string[]) {
  try {
    return execFileSync("git", ["-C", root, ...args], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim()
  } catch {
    return null
  }
}

export function loadBuildInfo(frontendDir: string, encoded = "") {
  const unknown = {
    schema_version: 1,
    component: "aivision-fe",
    product_version: null as string | null,
    component_version: null as string | null,
    source_sha: null as string | null,
    build_id: null as string | null,
    built_at: null as string | null,
    target_channel: "unknown",
    dirty: null as boolean | null,
  }
  if (encoded) {
    const data = JSON.parse(Buffer.from(encoded, "base64").toString("utf8"))
    if (!data || Array.isArray(data) || typeof data !== "object") {
      throw new Error("Invalid frontend build metadata")
    }
    // Docker builds without supplied metadata intentionally remain unknown.
    if (Object.keys(data).length === 0) return unknown
    if (data.schema_version !== 1 || data.component !== "aivision-fe") {
      throw new Error("Build metadata must identify the frontend component")
    }
    for (const field of ["product_version", "component_version"]) {
      if (
        data[field] !== null &&
        data[field] !== undefined &&
        !isProductVersion(data[field])
      ) {
        throw new Error(`Invalid frontend build field: ${field}`)
      }
    }
    const patterns: Record<string, RegExp> = {
      source_sha: /^(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$/,
      build_id: /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/,
      target_channel: /^(dev|daily|rc|stable|unknown)$/,
    }
    for (const [field, pattern] of Object.entries(patterns)) {
      if (
        data[field] !== null &&
        data[field] !== undefined &&
        (typeof data[field] !== "string" || !pattern.test(data[field]))
      ) {
        throw new Error(`Invalid frontend build field: ${field}`)
      }
    }
    if (
      data.built_at !== null &&
      data.built_at !== undefined &&
      !isBuildTimestamp(data.built_at)
    ) {
      throw new Error("Invalid frontend build time")
    }
    if (
      data.dirty !== null &&
      data.dirty !== undefined &&
      typeof data.dirty !== "boolean"
    ) {
      throw new Error("Invalid frontend dirty state")
    }
    // Explicit fields prevent accidental credentials from entering the bundle.
    return {
      ...unknown,
      product_version: data.product_version ?? null,
      component_version: data.component_version ?? null,
      source_sha: data.source_sha ?? null,
      build_id: data.build_id ?? null,
      built_at: data.built_at ?? null,
      target_channel: data.target_channel ?? "unknown",
      dirty: data.dirty ?? null,
    }
  }
  const root = path.resolve(frontendDir, "../..")
  let version: string | null = null
  try {
    const value = readFileSync(path.join(root, "VERSION"), "utf8").trim()
    if (isProductVersion(value)) version = value
  } catch {
    // Source archives and standalone frontend checkouts may omit VERSION.
  }
  const sha = git(root, ["rev-parse", "HEAD"])
  const status = git(root, [
    "status",
    "--porcelain",
    "--untracked-files=normal",
  ])
  return {
    ...unknown,
    product_version: version,
    component_version: version,
    source_sha: sha,
    built_at: sha || version ? new Date().toISOString() : null,
    target_channel: sha || version ? "dev" : "unknown",
    dirty: status === null ? null : status.length > 0,
  }
}
