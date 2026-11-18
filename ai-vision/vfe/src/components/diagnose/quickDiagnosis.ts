import type {
  DiagnosisRunInput,
  DiagnosisRunSelection,
  QuickRunResponse,
  TaskDTO,
} from "./types"
import { scopeFromTask } from "./diagnosisScope"

function diagnosisRequestId(): string {
  try {
    const random = globalThis.crypto
    if (typeof random?.randomUUID === "function") return random.randomUUID()
    // getRandomValues is also available on HTTP origins where randomUUID is absent.
    if (typeof random?.getRandomValues === "function") {
      const bytes = random.getRandomValues(new Uint8Array(16))
      bytes[6] = (bytes[6] & 0x0f) | 0x40
      bytes[8] = (bytes[8] & 0x3f) | 0x80
      const hex = Array.from(bytes, (byte) =>
        byte.toString(16).padStart(2, "0")
      ).join("")
      return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
    }
  } catch {
    // The request has not reached transport; the user can safely try preparation again.
  }
  throw new Error("DIAGNOSIS_RUN_REQUEST_ID_UNAVAILABLE")
}

export function canonicalQuickInput(
  input: DiagnosisRunSelection
): DiagnosisRunSelection {
  if (
    !input ||
    typeof input !== "object" ||
    Object.keys(input).some(
      (key) => key !== "trace_ids" && key !== "diagnoser_uuid"
    ) ||
    typeof input.diagnoser_uuid !== "string" ||
    !/^[A-Za-z0-9._:-]{1,128}$/.test(input.diagnoser_uuid)
  )
    throw new Error("QUICK_INPUT_INVALID")
  const ids = input.trace_ids
  if (
    !Array.isArray(ids) ||
    !ids.length ||
    ids.length > 10 ||
    new Set(ids).size !== ids.length ||
    ids.some(
      (id) => typeof id !== "string" || !/^[A-Za-z0-9._:-]{1,128}$/.test(id)
    )
  )
    throw new Error("QUICK_INPUT_INVALID")
  return { trace_ids: [...ids].sort(), diagnoser_uuid: input.diagnoser_uuid }
}

/** Own one user intent, including unknown server outcomes. Never retry with a new key. */
export class QuickRunIntent {
  body: DiagnosisRunInput | null = null
  private fingerprint = ""
  private generation = 0
  private inflight: Promise<QuickRunResponse | null> | null = null
  readonly identity: string
  private uuid: () => string
  constructor(identity: string, uuid = diagnosisRequestId) {
    this.identity = identity
    this.uuid = uuid
  }
  invalidate() {
    this.generation++
    this.inflight = null
    this.body = null
    this.fingerprint = ""
  }
  submit(
    input: DiagnosisRunSelection,
    transport: (body: DiagnosisRunInput) => Promise<QuickRunResponse>
  ): Promise<QuickRunResponse | null> {
    const canonical = canonicalQuickInput(input)
    const key = JSON.stringify(canonical)
    if (key !== this.fingerprint || !this.body) {
      this.invalidate()
      const body = {
        ...canonical,
        request_id: this.uuid(),
      }
      this.body = body
      this.fingerprint = key
    }
    return this.replay(transport)
  }
  replay(
    transport: (body: DiagnosisRunInput) => Promise<QuickRunResponse>
  ): Promise<QuickRunResponse | null> {
    if (!this.body) return Promise.resolve(null)
    if (this.inflight) return this.inflight
    const generation = this.generation
    const body = this.body!
    // Promise publication is synchronous, before transport's result or a second click.
    const pending = new Promise<QuickRunResponse>((resolve, reject) => {
      try {
        resolve(transport(body))
      } catch (error) {
        reject(error)
      }
    })
      .then(
        (response) => (generation === this.generation ? response : null),
        (error) => {
          if (generation !== this.generation) return null
          throw error
        }
      )
      .finally(() => {
        if (generation === this.generation) this.inflight = null
      })
    this.inflight = pending
    return pending
  }
}

export function quickOutcome(
  task: TaskDTO | null | undefined,
  filterEmpty = false
): string {
  if (!task) return "not_run"
  if (["pending", "dispatched", "running"].includes(task.status))
    return "running"
  if (task.status === "failed" || task.status === "canceled") return "failed"
  if (task.stats?.source.total === 0) return "no_sample"
  if (!task.stats || task.definition_source !== "task_snapshot")
    return "unknown"
  if (filterEmpty) return "filter_empty"
  const metrics = Object.entries(task.stats.diagnosers).flatMap(
    ([uuid, entries]) =>
      Object.values(entries).map((count) => ({
        ...count,
        role: task.diagnoser_definitions?.[uuid]?.score_role,
      }))
  )
  if (
    !metrics.length ||
    metrics.some((m) => m.role !== "quality" && m.role !== "signal")
  )
    return "unknown"
  const good = metrics.reduce((n, m) => n + m.good, 0),
    bad = metrics.reduce((n, m) => n + m.bad, 0)
  const skipped = metrics.reduce((n, m) => n + (m.skipped ?? 0), 0),
    failed = metrics.reduce((n, m) => n + m.failed, 0)
  if (scopeFromTask(task.stats, task).sourceIncomplete) {
    const allFailed =
      task.stats.schema_version === 3 &&
      failed > 0 &&
      metrics.every(
        (count) =>
          Number.isSafeInteger(count.total) &&
          count.total >= 0 &&
          count.failed === count.total &&
          count.good === 0 &&
          count.bad === 0 &&
          (!Object.hasOwn(count, "skipped") || count.skipped === 0)
      )
    return allFailed ? "failed" : "mixed"
  }
  if (task.stats.source_complete !== true) return "unknown"
  if (skipped && !good && !bad && !failed) return "skipped"
  if (failed && !good && !bad && !skipped) return "failed"
  if (skipped || failed) return "mixed"
  const quality = metrics.filter((m) => m.role === "quality")
  if (!quality.length) return "signals"
  if (quality.some((m) => m.bad > 0)) return "bad"
  return quality.every((m) => m.good > 0) ? "good" : "unknown"
}

/** Only complete frozen single-metric checks prove one emitted record per outcome. */
export function expectedQuickRecords(
  task: TaskDTO,
  verdict: string
): number | null {
  if (
    !task.quick_run ||
    task.definition_source !== "task_snapshot" ||
    task.stats?.schema_version !== 3 ||
    task.stats.source_complete !== true ||
    !["all", "good", "bad", "failed"].includes(verdict)
  )
    return null
  const entries = Object.entries(task.stats.diagnosers)
  const definitions = task.diagnoser_definitions ?? {}
  if (!entries.length || entries.length !== Object.keys(definitions).length)
    return null
  let expected = 0
  for (const [uuid, counters] of entries) {
    const definition = definitions[uuid]
    const names = Object.keys(counters)
    if (
      definition?.uuid !== uuid ||
      definition.metrics?.length !== 1 ||
      names.length !== 1 ||
      names[0] !== definition.metrics[0].name
    )
      return null
    const count = counters[names[0]]
    const skipped = Object.hasOwn(count, "skipped") ? count.skipped : 0
    const values = [count.total, count.good, count.bad, count.failed, skipped]
    if (
      values.some((n) => !Number.isSafeInteger(n) || (n ?? -1) < 0) ||
      count.good + count.bad + count.failed !== count.total
    )
      return null
    expected +=
      verdict === "all"
        ? count.good + count.bad + count.failed
        : count[verdict]!
  }
  return Number.isSafeInteger(expected) ? expected : null
}

export function sourceSpanId(record: {
  metrics?: Array<{ evidence?: Record<string, unknown> }>
}): string | null {
  for (const metric of record.metrics ?? []) {
    const spans = metric.evidence?.bad_spans
    if (!Array.isArray(spans)) continue
    for (const span of spans)
      if (span && typeof span.span_id === "string" && span.span_id)
        return span.span_id
  }
  return null
}
