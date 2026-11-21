import type { RepoSkillEntry } from "./skillApi"
import type { LocalImportOverwrite } from "@/components/labs/shared/localImportReview"
import { LocalImportBatchCollision } from "@/components/labs/shared/localImportBatchCollision"
import { fetchRegistrySkill, saveSkillConfigBatch } from "./skillApi"
import { parseSkillConfigText } from "./skillConfigImport"
import type { SkillConfig } from "./types"

export interface RemoteInstallResult {
  saved: number
  savedNames: string[]
  skipped: string[]
  failedEntries: RepoSkillEntry[]
  pendingConfigs: SkillConfig[]
  errors: string[]
}
const FETCH_CONCURRENCY = 4
const SAVE_BATCH_SIZE = 50

export function skillResolveErrorKey(error: unknown) {
  const code = error instanceof Error && "code" in error ? error.code : null
  switch (code) {
    case 3123:
      return "labs.skill.import.resolveErrors.unresolved"
    case 3124:
      return "labs.skill.import.resolveErrors.archiveTooLarge"
    case 3125:
      return "labs.skill.import.resolveErrors.fileTooLarge"
    case 3122:
      return "labs.skill.import.resolveErrors.githubUnavailable"
    default:
      return null
  }
}

/** Keep confirmed writes separate from pending work so retry never replays them. */
export async function installRepoSkills(
  spaceId: string,
  source: string,
  entries: RepoSkillEntry[],
  ref?: string,
  previous?: RemoteInstallResult
): Promise<RemoteInstallResult> {
  const configs: SkillConfig[] = [...(previous?.pendingConfigs ?? [])]
  const savedNames = new Set(previous?.savedNames ?? [])
  const skipped = [...(previous?.skipped ?? [])]
  const failedEntries: RepoSkillEntry[] = []
  const errors: string[] = []
  const queue = [...(previous ? previous.failedEntries : entries)]
  const worker = async (): Promise<void> => {
    while (queue.length) {
      const entry = queue.shift()
      if (!entry) break
      try {
        const content =
          entry.content ??
          (await fetchRegistrySkill(spaceId, { source, ref, path: entry.path }))
            .content
        const result = parseSkillConfigText(content)
        if (typeof result === "string") {
          skipped.push(entry.name || entry.dirname || entry.path)
          continue
        }
        for (const config of result.configs) {
          if (savedNames.has(config.name)) {
            skipped.push(entry.name || entry.dirname || entry.path)
            continue
          }
          configs.push({
            ...config,
            ...(entry.registrySnapshot ? {} : { path: entry.path }),
            raw: {
              ...config.raw,
              importSource: entry.registrySnapshot
                ? { repository: source, registry: "skills.sh" }
                : {
                    repository: source,
                    ref: ref?.trim() || "HEAD",
                    path: entry.path,
                  },
            },
          })
        }
        skipped.push(...result.skipped)
      } catch (error) {
        failedEntries.push(entry)
        errors.push(error instanceof Error ? error.message : String(error))
      }
    }
  }
  await Promise.all(
    Array.from({ length: Math.min(FETCH_CONCURRENCY, queue.length) }, worker)
  )
  const unique = new Map<string, SkillConfig>()
  for (const config of configs) {
    if (savedNames.has(config.name)) continue
    if (unique.has(config.name)) {
      skipped.push(config.name)
      continue
    }
    unique.set(config.name, config)
  }
  const saved = await saveSkillConfigBatches(spaceId, [...unique.values()])
  saved.savedNames.forEach((name) => savedNames.add(name))
  return {
    saved: savedNames.size,
    savedNames: [...savedNames],
    skipped,
    failedEntries,
    pendingConfigs: saved.pendingConfigs,
    errors: [...errors, ...saved.errors],
  }
}

/** Reuse the configured Space's upsert API and track confirmation per batch. */
export async function saveSkillConfigBatches(
  spaceId: string,
  configs: SkillConfig[],
  maxBytes?: number,
  shouldContinue = () => true,
  overwrites?: readonly LocalImportOverwrite[]
) {
  const savedNames: string[] = []
  const pendingConfigs: SkillConfig[] = []
  const errors: string[] = []
  let batchCollision: LocalImportBatchCollision | undefined
  const encoder = maxBytes ? new TextEncoder() : undefined
  const overwriteByName = new Map(overwrites?.map((item) => [item.name, item]))
  const envelopeBytes = JSON.stringify({
    skills: [],
    overwrites: overwrites && [],
  }).length
  let batch: SkillConfig[] = []
  let batchOverwrites: LocalImportOverwrite[] | undefined = overwrites && []
  let bytes = envelopeBytes

  async function flush() {
    if (!batch.length) return
    try {
      const saved = await saveSkillConfigBatch(spaceId, batch, batchOverwrites)
      const confirmed = new Set(saved.map((receipt) => receipt.requestedName))
      for (const config of batch) {
        if (confirmed.has(config.name)) savedNames.push(config.name)
        else pendingConfigs.push(config)
      }
      if (batch.some((config) => !confirmed.has(config.name)))
        errors.push("Incomplete skill import response")
    } catch (error) {
      pendingConfigs.push(...batch)
      errors.push(error instanceof Error ? error.message : String(error))
      if (
        error instanceof LocalImportBatchCollision &&
        error.matchesBatch(batch.map(({ name }) => name))
      )
        batchCollision = error
    }
    batch = []
    batchOverwrites = overwrites && []
    bytes = envelopeBytes
  }

  for (const [index, config] of configs.entries()) {
    const overwrite = overwriteByName.get(config.name)
    const itemBytes = encoder
      ? encoder.encode(JSON.stringify(config)).length
      : 0
    const overwriteBytes =
      overwrite && encoder
        ? encoder.encode(JSON.stringify(overwrite)).length
        : 0
    const size = itemBytes + overwriteBytes
    let separators =
      Number(batch.length > 0) +
      Number(!!overwrite && !!batchOverwrites?.length)
    if (
      batch.length === SAVE_BATCH_SIZE ||
      (maxBytes && bytes + size + separators > maxBytes)
    ) {
      await flush()
      separators = 0
    }
    if (batchCollision || !shouldContinue()) {
      pendingConfigs.push(...batch, ...configs.slice(index))
      return { savedNames, pendingConfigs, errors, batchCollision }
    }
    if (maxBytes && bytes + size > maxBytes) {
      pendingConfigs.push(config)
      errors.push("Skill configuration exceeds the request limit")
      continue
    }
    bytes += size + separators
    batch.push(config)
    if (overwrite) batchOverwrites?.push(overwrite)
  }
  await flush()
  return { savedNames, pendingConfigs, errors, batchCollision }
}
