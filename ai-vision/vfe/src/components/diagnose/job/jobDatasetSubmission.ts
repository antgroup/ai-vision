import { ApiBusinessError } from "@/lib/api"

import { createDataset, listDatasets, updateDataset } from "../jobApi"
import type { DatasetDTO } from "../types"
import {
  type DatasetTemplate,
  type DrawerMode,
  type JobFormData,
} from "./jobFormModel"

export async function prepareDatasetForSubmit({
  mode,
  spaceId,
  agentUuid,
  form,
  defaultDatasetTemplate,
  selectedDataset,
  usedDatasetUuids,
}: {
  mode: DrawerMode
  spaceId: string
  agentUuid: string
  form: JobFormData
  defaultDatasetTemplate: DatasetTemplate | null
  selectedDataset: DatasetDTO | null
  usedDatasetUuids: readonly string[]
}): Promise<DatasetDTO> {
  if (mode === "create") {
    if (!defaultDatasetTemplate) throw new Error("无法解析 Dataset 默认参数")
    const spec = buildDatasetSpec(defaultDatasetTemplate.spec, form)
    const name = buildDatasetName(form.name)
    const payload = {
      name,
      description: form.description.trim() || undefined,
      type: defaultDatasetTemplate.type,
      spec,
    }
    try {
      return await createDataset(spaceId, agentUuid, payload)
    } catch (error) {
      if (!isDatasetNameConflict(error)) throw error

      // A previous Job validation failure may have already created this form's
      // task-only Dataset. Reuse only an orphan so an existing Job cannot have
      // its scan configuration changed by a conflicting create attempt.
      const { items } = await listDatasets(spaceId, agentUuid)
      const existing = items.find((dataset) => dataset.name === name)
      if (!existing || usedDatasetUuids.includes(existing.uuid)) throw error

      return await updateDataset(spaceId, existing.uuid, {
        description: payload.description,
        spec,
      })
    }
  }

  if (!selectedDataset) throw new Error("暂无可用 Dataset")
  const spec = buildDatasetSpec(selectedDataset.spec ?? {}, form)
  return updateDataset(spaceId, selectedDataset.uuid, { spec })
}

function buildDatasetSpec(
  baseSpec: Record<string, unknown>,
  form: JobFormData
): Record<string, unknown> {
  const spec = { ...baseSpec }
  const baseQuery = isRecord(baseSpec.query) ? baseSpec.query : {}
  const query = { ...baseQuery }
  query.limit = form.maxTraceCount
  query.time_range = form.timeRange
  query.time_offset = form.timeOffset
  query.where = form.whereFilter.trim()
  spec.query = query

  return spec
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value)
}

function buildDatasetName(jobName: string): string {
  const base = jobName.trim() || "diagnose-task"
  const suffix = "-任务专用"
  return `${base.slice(0, 64 - suffix.length)}${suffix}`
}

function isDatasetNameConflict(error: unknown): boolean {
  return error instanceof ApiBusinessError && error.statusCode === 409
}
