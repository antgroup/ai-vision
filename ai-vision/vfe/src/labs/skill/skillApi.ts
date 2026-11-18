import { fetchWithSession } from "@/lib/api"
import { getPresetTimeWindow } from "@/lib/timeFilter"

import type {
  SkillConfig,
  SkillObservabilityResult,
  SkillQueryParams,
  SkillHistoryFilters,
  SkillHistoryPage,
} from "./types"

export class SkillApiError extends Error {
  code: number | null
  constructor(message: string, code: number | null = null) {
    super(message)
    this.code = code
  }
}

function resolveScanWindow(params: SkillQueryParams) {
  if (params.from && params.to) {
    return { fromTimestamp: params.from, toTimestamp: params.to }
  }
  const window = getPresetTimeWindow(params.timeRange)
  return { fromTimestamp: window.from, toTimestamp: window.to }
}

async function skillFetch(
  path: string,
  init: RequestInit = {}
): Promise<unknown> {
  const response = await fetchWithSession(path, init)
  if (!response.ok) {
    // Error bodies are still the {code, message} envelope; surface the
    // backend message when present.
    const text = await response.text().catch(() => "")
    let message = ""
    let code: number | null = null
    try {
      const body = JSON.parse(text)
      message = body?.message ?? ""
      code = typeof body?.code === "number" ? body.code : null
    } catch {
      // keep empty: fall through to the generic error below
    }
    throw new SkillApiError(
      message || `Skill API error ${response.status}`,
      code
    )
  }
  const body = await response.json()
  if (body.code !== 0)
    throw new SkillApiError(body.message || "Skill API error", body.code)
  return body.data
}

/** Aggregated skill calls and containment edges for the selected window. */
export async function listSkillStats(
  params: SkillQueryParams,
  signal?: AbortSignal
): Promise<SkillObservabilityResult> {
  const search = new URLSearchParams({
    agent_product: params.agentProduct,
    ...resolveScanWindow(params),
    ...(params.agentUuid ? { agent_uuid: params.agentUuid } : {}),
  })
  return (await skillFetch(
    `/api/v2/spaces/${encodeURIComponent(params.spaceId)}/labs/skill/observations?${search}`,
    { signal }
  )) as SkillObservabilityResult
}

const configUrl = (spaceId: string) =>
  `/api/v2/spaces/${encodeURIComponent(spaceId)}/labs/skill/configs`

export async function listSkillConfigs(
  spaceId: string,
  signal?: AbortSignal
): Promise<SkillConfig[]> {
  const data = await skillFetch(configUrl(spaceId), { signal })
  return Array.isArray(data) ? (data as SkillConfig[]) : []
}

/** Upserts by `name`; configs are already sanitized by the import parser. */
export async function saveSkillConfigs(
  spaceId: string,
  skills: SkillConfig[]
): Promise<SkillConfig[]> {
  const data = await skillFetch(configUrl(spaceId), {
    method: "PUT",
    body: JSON.stringify({ skills }),
  })
  return Array.isArray(data) ? (data as SkillConfig[]) : []
}

export async function deleteSkillConfig(
  spaceId: string,
  name: string
): Promise<void> {
  await skillFetch(`${configUrl(spaceId)}/${encodeURIComponent(name)}`, {
    method: "DELETE",
  })
}

/** skills.sh 公共注册表搜索结果条目（后端代理公开搜索端点）。 */
export interface RegistrySkill {
  /** "owner/repo/skillId"；页面链接即 https://skills.sh/{id}。 */
  id: string
  source: string
  skillId: string
  name: string
  installs: number
  url: string
}

export interface RegistrySearchResult {
  query: string
  searchType: string
  count: number
  skills: RegistrySkill[]
}

/** 仓库内一个 SKILL.md（路径为仓库相对路径）。 */
export interface RepoSkillEntry {
  path: string
  dirname: string
  name: string
  description: string
  /** Included when resolving an explicit file or a selected registry result. */
  content?: string
  /** The registry snapshot has no verified Git revision or repository path. */
  registrySnapshot?: boolean
}

export interface RepoResolveResult {
  source: string
  branch: string
  subdir: string
  skills: RepoSkillEntry[]
}

export interface RegistrySkillFile {
  path: string
  name: string
  description: string
  content: string
}

const registryUrl = (spaceId: string) =>
  `/api/v2/spaces/${encodeURIComponent(spaceId)}/labs/skill/registry`

/** skills.sh 公共注册表搜索（query 至少 2 个字）。 */
export async function searchSkillRegistry(
  spaceId: string,
  query: string
): Promise<RegistrySearchResult> {
  const search = new URLSearchParams({ q: query })
  return (await skillFetch(
    `${registryUrl(spaceId)}/search?${search}`
  )) as RegistrySearchResult
}

/** Resolve a GitHub repository, explicit SKILL.md link, or registry selection. */
export async function resolveSkillRepo(
  spaceId: string,
  payload: { url?: string; source?: string; ref?: string; skillId?: string }
): Promise<RepoResolveResult> {
  return (await skillFetch(`${registryUrl(spaceId)}/resolve`, {
    method: "POST",
    body: JSON.stringify(payload),
  })) as RepoResolveResult
}

/** 取回仓库内单个 SKILL.md 全文。 */
export async function fetchRegistrySkill(
  spaceId: string,
  payload: { source: string; ref?: string; path: string }
): Promise<RegistrySkillFile> {
  return (await skillFetch(`${registryUrl(spaceId)}/fetch`, {
    method: "POST",
    body: JSON.stringify(payload),
  })) as RegistrySkillFile
}

/** Pages the complete fail-closed candidate window, not the recentCalls preview. */
export async function listSkillCallHistory(
  params: SkillQueryParams,
  name: string,
  filters: SkillHistoryFilters
): Promise<SkillHistoryPage> {
  const search = new URLSearchParams({
    agent_product: params.agentProduct,
    ...resolveScanWindow(params),
    history_skill: name,
    page: String(filters.page),
    limit: String(filters.limit),
    status: filters.status,
    provenance: filters.provenance,
    sort: filters.sort,
    ...(params.agentUuid ? { agent_uuid: params.agentUuid } : {}),
  })
  return (await skillFetch(
    `/api/v2/spaces/${encodeURIComponent(params.spaceId)}/labs/skill/observations?${search}`
  )) as SkillHistoryPage
}
