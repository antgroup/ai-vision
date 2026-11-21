import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import ts from "typescript"
import * as batchCollision from "../src/components/labs/shared/localImportBatchCollision.ts"

function remoteFixture(
  count: number,
  failSecond = false,
  failFetchPath = "",
  duplicateName = false
) {
  const saved: string[][] = []
  const writeIntents: unknown[] = []
  const fetched: string[] = []
  let fail = failSecond
  let failedFetch = false
  const parserSource = readFileSync(
    new URL("../src/labs/skill/skillConfigImport.ts", import.meta.url),
    "utf8"
  )
  const parser: Record<string, unknown> = {}
  new Function(
    "require",
    "exports",
    ts.transpileModule(parserSource, {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
      },
    }).outputText
  )(() => ({}), parser)
  const source = readFileSync(
    new URL("../src/labs/skill/skillImportRemote.ts", import.meta.url),
    "utf8"
  )
  const exports: Record<string, unknown> = {}
  new Function(
    "require",
    "exports",
    ts.transpileModule(source, {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
      },
    }).outputText
  )((name: string) => {
    if (name === "./skillConfigImport") return parser
    if (name === "@/components/labs/shared/localImportBatchCollision")
      return batchCollision
    return {
      fetchRegistrySkill: async (_space: string, payload: { path: string }) => {
        fetched.push(payload.path)
        if (payload.path === failFetchPath && !failedFetch) {
          failedFetch = true
          throw new Error("fetch offline")
        }
        return {
          content: `---\nname: ${duplicateName ? "deploy" : payload.path}\n---\nbody`,
        }
      },
      saveSkillConfigBatch: async (
        _space: string,
        configs: { name: string }[],
        overwrites?: unknown
      ) => {
        saved.push(configs.map((c) => c.name))
        writeIntents.push(overwrites)
        if (fail && saved.length === 2) {
          fail = false
          throw new Error("offline")
        }
        return configs.map(({ name }) => ({ requestedName: name, name }))
      },
    }
  }, exports)
  return {
    saveBatch:
      exports.saveSkillConfigBatches as typeof import("../src/labs/skill/skillImportRemote.ts").saveSkillConfigBatches,
    install: exports.installRepoSkills as (
      ...args: unknown[]
    ) => Promise<Record<string, unknown>>,
    entries: Array.from({ length: count }, (_, i) => ({
      path: `skill-${i}`,
      name: `skill-${i}`,
      dirname: `skill-${i}`,
      description: "",
    })),
    saved,
    writeIntents,
    fetched,
  }
}

test("guarded Skill batching limits the complete request and scopes confirmations", async () => {
  const f = remoteFixture(0)
  const configs = ["one", "two"].map((name) => ({
    name,
    description: "",
    path: "SKILL.md",
    source: "skillmd",
    raw: { content: "# Definition" },
    allowedTools: [],
    license: "",
    compatibility: "",
    importedAt: "2026-11-17T00:00:00Z",
  }))
  const overwrites = [{ name: "two", version: "2026-11-17T00:00:00Z" }]
  const maxBytes = new TextEncoder().encode(
    JSON.stringify({ skills: configs, overwrites: [] })
  ).length
  const saved = await f.saveBatch(
    "space",
    configs,
    maxBytes,
    () => true,
    overwrites
  )
  assert.deepEqual(saved.savedNames, ["one", "two"])
  assert.deepEqual(f.saved, [["one"], ["two"]])
  assert.deepEqual(f.writeIntents, [[], overwrites])
})

test("remote Skill imports save more than fifty entries in bounded batches", async () => {
  const f = remoteFixture(51)
  const result = await f.install("space", "owner/repo", f.entries, "HEAD")
  assert.deepEqual(
    f.saved.map((x) => x.length),
    [50, 1]
  )
  assert.equal(result.saved, 51)
})

test("partial remote install retry does not re-fetch or re-save confirmed entries", async () => {
  const f = remoteFixture(60, true)
  const first = await f.install("space", "owner/repo", f.entries, "HEAD")
  assert.equal(first.saved, 50)
  assert.equal((first.pendingConfigs as unknown[]).length, 10)
  const retry = await f.install("space", "owner/repo", f.entries, "HEAD", first)
  assert.equal(retry.saved, 60)
  assert.equal(f.fetched.length, 60)
  assert.deepEqual(
    f.saved.map((x) => x.length),
    [50, 10, 10]
  )
})

test("remote install retries only failed fetches while preserving successful files", async () => {
  const f = remoteFixture(3, false, "skill-1")
  const first = await f.install("space", "owner/repo", f.entries, "HEAD")
  assert.equal(first.saved, 2)
  assert.equal((first.failedEntries as unknown[]).length, 1)
  const retry = await f.install("space", "owner/repo", f.entries, "HEAD", first)
  assert.equal(retry.saved, 3)
  assert.deepEqual(f.fetched, ["skill-0", "skill-1", "skill-2", "skill-1"])
  assert.deepEqual(
    f.saved.map((x) => x.length),
    [2, 1]
  )
})

test("a recovered remote file with an already saved Skill name is accounted as skipped", async () => {
  const f = remoteFixture(2, false, "skill-1", true)
  const first = await f.install("space", "owner/repo", f.entries, "HEAD")
  assert.equal(first.saved, 1)
  const retry = await f.install("space", "owner/repo", f.entries, "HEAD", first)
  assert.equal(retry.saved, 1)
  assert.deepEqual(retry.skipped, ["skill-1"])
  assert.equal((retry.failedEntries as unknown[]).length, 0)
  assert.equal((retry.pendingConfigs as unknown[]).length, 0)
  assert.deepEqual(
    f.saved.map((x) => x.length),
    [1]
  )
})
