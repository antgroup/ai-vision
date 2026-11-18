import type { RepoSkillEntry } from "./skillApi"
import { fetchRegistrySkill, saveSkillConfigs } from "./skillApi"
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
  const pendingConfigs: SkillConfig[] = []
  const candidates = [...unique.values()]
  for (let start = 0; start < candidates.length; start += SAVE_BATCH_SIZE) {
    const batch = candidates.slice(start, start + SAVE_BATCH_SIZE)
    try {
      const saved = await saveSkillConfigs(spaceId, batch)
      const confirmed = new Set(saved.map((config) => config.name))
      for (const config of batch) {
        if (confirmed.has(config.name)) savedNames.add(config.name)
        else pendingConfigs.push(config)
      }
      if (batch.some((config) => !confirmed.has(config.name)))
        errors.push("Incomplete skill import response")
    } catch (error) {
      pendingConfigs.push(...batch)
      errors.push(error instanceof Error ? error.message : String(error))
    }
  }
  return {
    saved: savedNames.size,
    savedNames: [...savedNames],
    skipped,
    failedEntries,
    pendingConfigs,
    errors,
  }
}
