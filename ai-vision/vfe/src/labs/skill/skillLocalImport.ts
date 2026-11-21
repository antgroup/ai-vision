import {
  fingerprintImportValue,
  type LocalImportCandidate,
} from "@/components/labs/shared/localImportReview"
import { parseSkillConfigText } from "./skillConfigImport"
import type { SkillConfig } from "./types"

const MAX_DIRECTORY_SKILLS = 500
const MAX_DIRECTORY_BYTES = 16 * 1024 * 1024
const MAX_SKILL_FILE_BYTES = 1024 * 1024

export const MAX_LOCAL_SKILL_BATCH_BYTES = 2 * 1024 * 1024

type SkillFile = Pick<File, "name" | "size" | "text" | "webkitRelativePath">
type RejectedFile = {
  path: string
  reason: "invalid" | "tooLarge" | "unreadable"
}

export type SkillLocalImportResult =
  | { error: "empty" | "limit" }
  | {
      candidates: LocalImportCandidate<SkillConfig>[]
      rejected: RejectedFile[]
    }

export async function readLocalSkillFiles(
  files: Iterable<SkillFile>,
  fromDirectory: boolean
): Promise<SkillLocalImportResult> {
  const selectedFiles = []
  let bytes = 0
  for (const file of files) {
    if (fromDirectory && file.name.toLowerCase() !== "skill.md") continue
    selectedFiles.push({ file, path: file.webkitRelativePath || file.name })
    if (file.size <= MAX_SKILL_FILE_BYTES) bytes += file.size
    if (
      selectedFiles.length > MAX_DIRECTORY_SKILLS ||
      bytes > MAX_DIRECTORY_BYTES
    )
      return { error: "limit" }
  }
  if (!selectedFiles.length) return { error: "empty" }

  selectedFiles.sort((a, b) => a.path.localeCompare(b.path))
  const candidates: LocalImportCandidate<SkillConfig>[] = []
  const rejected: RejectedFile[] = []
  const encoder = new TextEncoder()
  for (const { file, path } of selectedFiles) {
    if (file.size > MAX_SKILL_FILE_BYTES) {
      rejected.push({ path, reason: "tooLarge" })
      continue
    }
    let text: string
    try {
      text = await file.text()
    } catch {
      rejected.push({ path, reason: "unreadable" })
      continue
    }
    const parsed = parseSkillConfigText(text)
    const config = typeof parsed === "string" ? undefined : parsed.configs[0]
    if (
      !config ||
      config.source !== "skillmd" ||
      [...config.name].length > 128 ||
      [...config.description].length > 1024 ||
      [...path].length > 1024
    ) {
      rejected.push({ path, reason: "invalid" })
      continue
    }
    const withPath = { ...config, path }
    if (
      encoder.encode(JSON.stringify({ skills: [withPath], overwrites: [] }))
        .length > MAX_LOCAL_SKILL_BATCH_BYTES
    ) {
      rejected.push({ path, reason: "tooLarge" })
      continue
    }
    candidates.push({
      name: withPath.name,
      source: path,
      value: withPath,
      fingerprint: fingerprintImportValue(withPath.raw),
    })
  }
  return { candidates, rejected }
}
