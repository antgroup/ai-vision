import assert from "node:assert/strict"
import test from "node:test"
import { nativeUiHarness } from "./nativeUiHarness.ts"
import * as experimentFilters from "../src/components/diagnose/badcase/historicalExperimentFilter.ts"

type Query = {
  queryKey: readonly unknown[]
  enabled: boolean
  queryFn: () => Promise<{ items: { uuid: string }[]; total: number }>
}

function setup(taskUuid?: string) {
  const requests: URL[] = []
  const rows = [
    ...["old-a", "old-b", "old-c"].map((uuid) => ({
      uuid,
      task_uuid: "fixed-task",
      target_created_at: "2026-10-01 01:02:03",
    })),
    {
      uuid: "recent",
      task_uuid: "recent-task",
      target_created_at: "2026-10-28 11:00:00",
    },
  ]
  const apiHarness = nativeUiHarness({
    "@/lib/api": {
      api: {
        get: async (path: string) => {
          const url = new URL(path, "https://test.invalid")
          requests.push(url)
          const params = url.searchParams
          // Boundary fixture: exact Task reads have no implicit time cutoff;
          // explicitly supplied timestamps remain conjunctive filters.
          const items = rows.filter(
            (row) =>
              (!params.has("task_uuid") ||
                row.task_uuid === params.get("task_uuid")) &&
              (!params.has("start_time") ||
                row.target_created_at >= params.get("start_time")!) &&
              (!params.has("end_time") ||
                row.target_created_at < params.get("end_time")!)
          )
          return { data: { items, total: items.length } }
        },
      },
    },
  })
  const recordsApi = apiHarness.load("components/diagnose/recordApi.ts")
  let recordsQuery: Query
  const harness = nativeUiHarness({
    "react-router-dom": {
      useSearchParams: () => [new URLSearchParams("job=job&verdict=failed")],
    },
    "@/contexts/SpaceContext": {
      useSpace: () => ({ currentSpace: { id: "space", type: 2 } }),
    },
    "@/contexts/AgentContext": {
      useAgent: () => ({ currentAgent: { uuid: "agent" } }),
    },
    "@/contexts/UserContext": {
      useUser: () => ({ user: { user_uuid: "user" }, loading: false }),
    },
    "@/lib/schemas/space": { SpaceType: { Personal: 1, Business: 2 } },
    "./historicalExperimentFilter": experimentFilters,
    "../resourceScopes": { isUserSharedSpace: () => false },
    "./BadCaseStatusBadge": { BadCaseStatusBadge: "status" },
    "@/components/diagnose/common/JobSelector": { JobSelector: "job" },
    "../diagnoseApi": recordsApi,
    "@tanstack/react-query": {
      useQuery(query: Query) {
        if (query.queryKey[0] === "diagnosis-records") recordsQuery = query
        return {
          isLoading: false,
          isError: false,
          isSuccess: true,
          data:
            query.queryKey[0] === "diagnose-jobs"
              ? { items: [{ uuid: "job", dataset_uuid: "dataset" }] }
              : query.queryKey[0] === "diagnose-dataset"
                ? { spec: {} }
                : undefined,
        }
      },
    },
  })
  const { BadCaseListView } = harness.load<{
    BadCaseListView: (props: Record<string, unknown>) => unknown
  }>("components/diagnose/badcase/BadCaseListView.tsx")
  return {
    requests,
    query(timeRange: { start_time: string; end_time: string }) {
      BadCaseListView({
        taskUuid,
        jobUuid: "job",
        timeRange,
        selectedResultId: null,
        onSelectBadCase: () => {},
        onJobChange: () => {},
      })
      assert.equal(recordsQuery.enabled, true)
      return recordsQuery
    },
  }
}

const recentWindow = {
  start_time: "2026-10-28 10:00:00",
  end_time: "2026-10-28 13:00:00",
}

test("fixed Task record history retains older source traces across global time changes", async () => {
  const h = setup("fixed-task")
  for (const window of [
    recentWindow,
    { start_time: "2026-10-27 10:00:00", end_time: "2026-10-27 13:00:00" },
  ]) {
    const result = await h.query(window).queryFn()
    assert.deepEqual(
      result.items.map((row) => row.uuid),
      ["old-a", "old-b", "old-c"]
    )
    const params = h.requests.at(-1)!.searchParams
    assert.equal(params.get("task_uuid"), "fixed-task")
    assert.deepEqual(params.getAll("job_uuid"), ["job"])
    assert.equal(params.get("verdict"), "failed")
    assert.equal(params.has("start_time"), false)
    assert.equal(params.has("end_time"), false)
  }
})

test("ordinary record lists keep the selected source time window", async () => {
  const h = setup()
  const result = await h.query(recentWindow).queryFn()
  assert.deepEqual(
    result.items.map((row) => row.uuid),
    ["recent"]
  )
  const params = h.requests[0].searchParams
  assert.equal(params.has("task_uuid"), false)
  assert.equal(params.get("start_time"), "2026-10-28 10:00:00")
  assert.equal(params.get("end_time"), "2026-10-28 13:00:00")
})
