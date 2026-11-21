import type { LocalImportOverwrite } from "@/components/labs/shared/localImportReview"

import { parseMcpConfigEntries } from "./mcpConfigImport.ts"
import type { McpServerConfig } from "./types"

const MAX_FILES = 100
const MAX_FILE_BYTES = 1024 * 1024
const MAX_TOTAL_BYTES = 8 * 1024 * 1024
const MAX_CONFIGS = 500
const MAX_BATCH_BYTES = 2 * 1024 * 1024
const MAX_BATCH_COUNT = 50
const ENVELOPE_BYTES = '{"configs":[],"overwrites":[]}'.length
const encoder = new TextEncoder()

type McpJsonFile = Pick<File, "name" | "size" | "text">

type FileIssue = {
  file: string
  reason:
    | "notJson"
    | "tooLarge"
    | "unreadable"
    | "invalidJson"
    | "noServers"
    | "invalidServers"
  names?: string[]
}

export type McpLocalImportResult =
  | { error: "limit" }
  | { candidates: McpLocalCandidate[]; issues: FileIssue[] }

interface McpLocalCandidate {
  source: string
  config: McpServerConfig
  headerValues?: Record<string, string>
}

function configBytes(config: McpServerConfig) {
  return encoder.encode(JSON.stringify(config)).byteLength
}

function validConfig(config: McpServerConfig) {
  const { searchTools, writeTools, listTools, manageTools } =
    config.memory ?? {}
  return (
    config.serverId &&
    [...config.serverId].length <= 128 &&
    [...(config.description ?? "")].length <= 512 &&
    [...(config.command ?? "")].length <= 512 &&
    [...(config.url ?? "")].length <= 1024 &&
    (config.transport === "stdio"
      ? config.command?.trim()
      : config.url?.trim()) &&
    [searchTools, writeTools, listTools, manageTools].every(
      (names) =>
        !names ||
        new Set(names.map((name) => name.trim())).size === names.length
    ) &&
    configBytes(config) + ENVELOPE_BYTES <= MAX_BATCH_BYTES
  )
}

export async function readMcpConfigFiles(
  files: McpJsonFile[]
): Promise<McpLocalImportResult> {
  if (
    files.length > MAX_FILES ||
    files.reduce(
      (bytes, file) =>
        bytes +
        (/\.json$/i.test(file.name) && file.size <= MAX_FILE_BYTES
          ? file.size
          : 0),
      0
    ) > MAX_TOTAL_BYTES
  )
    return { error: "limit" }

  const candidates: McpLocalCandidate[] = []
  const issues: FileIssue[] = []
  let scanned = 0
  for (const file of files) {
    if (!/\.json$/i.test(file.name)) {
      issues.push({ file: file.name, reason: "notJson" })
      continue
    }
    if (file.size > MAX_FILE_BYTES) {
      issues.push({ file: file.name, reason: "tooLarge" })
      continue
    }
    let text: string
    try {
      text = await file.text()
    } catch {
      issues.push({ file: file.name, reason: "unreadable" })
      continue
    }
    let parsed: ReturnType<typeof parseMcpConfigEntries>
    try {
      parsed = parseMcpConfigEntries(text.replace(/^\uFEFF/, ""))
    } catch {
      parsed = "invalidJson"
    }
    if (typeof parsed === "string") {
      issues.push({ file: file.name, reason: parsed })
      continue
    }
    scanned += parsed.entries.length + parsed.skipped.length
    if (scanned > MAX_CONFIGS) return { error: "limit" }

    const rejected = [...parsed.skipped]
    const fileCandidates = []
    const counts = new Map<string, number>()
    for (const { config: entry, headerValues } of parsed.entries) {
      const config = { ...entry, serverId: entry.serverId.trim() }
      if (!validConfig(config)) {
        rejected.push(entry.serverId)
        continue
      }
      const key = config.serverId.toLowerCase()
      counts.set(key, (counts.get(key) ?? 0) + 1)
      fileCandidates.push({ key, config, headerValues })
    }
    const occurrences = new Map<string, number>()
    for (const { key, config, headerValues } of fileCandidates) {
      const occurrence = (occurrences.get(key) ?? 0) + 1
      occurrences.set(key, occurrence)
      candidates.push({
        source:
          counts.get(key) === 1 ? file.name : `${file.name} #${occurrence}`,
        config,
        headerValues,
      })
    }
    if (rejected.length)
      issues.push({
        file: file.name,
        reason: "invalidServers",
        names: rejected,
      })
  }

  return { candidates, issues }
}

/** The existing config endpoint accepts at most 50 items per request. */
export function mcpConfigBatches(
  configs: McpServerConfig[],
  overwrites: ReadonlyMap<string, LocalImportOverwrite>
) {
  const batches: McpServerConfig[][] = []
  let batch: McpServerConfig[] = []
  let bytes = ENVELOPE_BYTES
  for (const config of configs) {
    const overwrite = overwrites.get(config.serverId)
    const size =
      configBytes(config) +
      (overwrite ? encoder.encode(JSON.stringify(overwrite)).byteLength + 1 : 0)
    if (batch.length >= MAX_BATCH_COUNT || bytes + size + 1 > MAX_BATCH_BYTES) {
      if (batch.length) batches.push(batch)
      batch = []
      bytes = ENVELOPE_BYTES
    }
    batch.push(config)
    bytes += size + (batch.length > 1 ? 1 : 0)
  }
  if (batch.length) batches.push(batch)
  return batches
}
