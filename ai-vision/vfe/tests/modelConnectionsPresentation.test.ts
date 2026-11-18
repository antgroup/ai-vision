import assert from "node:assert/strict"
import test from "node:test"
import { nativeUiHarness, uiElements, uiText } from "./nativeUiHarness.ts"
import {
  canManageModelConnections,
  connectionConfigurationChanged,
  connectionStatus,
  diagnoserReferencePath,
} from "../src/features/model-connections/connectionPresentation.ts"
import type { ModelConnection } from "../src/features/model-connections/types.ts"
import * as providers from "../src/features/model-connections/providers.ts"

const connection: ModelConnection = {
  uuid: "connection-1",
  name: "Shared model",
  endpoint: "https://api.openai.com/v1",
  modelName: "long/model-name",
  apiKeyConfigured: true,
  status: "available",
  updatedAt: "2026-10-23T10:00:00Z",
  diagnosticValidation: {
    status: "failed",
  } as ModelConnection["diagnosticValidation"],
}

test("a diagnostic failure cannot turn a connected shared connection into a failed connection", () => {
  assert.equal(connectionStatus(connection), "available")
})

test("explicit model manage permissions take precedence over a legacy administrator role", () => {
  assert.equal(
    canManageModelConnections({ permissions: ["space.read"], role: 1 }),
    false
  )
  assert.equal(
    canManageModelConnections({
      permissions: ["model_connection.manage"],
      role: 3,
    }),
    true
  )
  assert.equal(canManageModelConnections({ role: 2 }), true)
  assert.equal(canManageModelConnections({ role: 3 }), false)
  assert.equal(canManageModelConnections(null), false)
})

test("credential/model/address changes require impact review while names and retained keys do not", () => {
  assert.equal(
    connectionConfigurationChanged(
      connection,
      connection.endpoint + "/",
      connection.modelName,
      ""
    ),
    false
  )
  assert.equal(
    connectionConfigurationChanged(
      connection,
      connection.endpoint,
      "new-model",
      ""
    ),
    true
  )
  assert.equal(
    connectionConfigurationChanged(
      connection,
      "https://custom.example/v1",
      connection.modelName,
      ""
    ),
    true
  )
  assert.equal(
    connectionConfigurationChanged(
      connection,
      connection.endpoint,
      connection.modelName,
      "replacement-key"
    ),
    true
  )
})

function detail({
  tab = "overview",
  referencesError = false,
  items = [connection],
} = {}) {
  return nativeUiHarness({
    "react-router-dom": {
      useLocation: () => ({ state: null }),
      useSearchParams: () => [new URLSearchParams({ tab }), () => {}],
      useNavigate: () => () => {},
    },
    "@tanstack/react-query": {
      useQuery: ({ queryKey }: { queryKey: string[] }) =>
        queryKey[0] === "model-connections"
          ? { data: { items } }
          : {
              isError: referencesError,
              error: new Error("private upstream response"),
              data: undefined,
            },
    },
    "./api": {},
    "./ConnectionActions": { ConnectionActions: "ConnectionActions" },
    "./ConnectionStatus": { ConnectionStatus: "ConnectionStatus" },
    "./CopyValue": { CopyValue: "CopyValue" },
    "./providers": providers,
    "./connectionPresentation": {
      formatConnectionDate: () => "UPDATED_DATE_ONLY",
      diagnoserReferencePath,
    },
    "./useModelConnectionPermissions": {
      useModelConnectionPermissions: () => ({ canManage: false }),
    },
    "./navigation": {
      modelConnectionsPath: () => "/model-connections?spaceId=space-one",
    },
    "@/components/diagnose/ModelValidationSummary": {
      ModelValidationSummary: "ModelValidationSummary",
    },
  })
    .load<{
      ModelConnectionDetail: (props: {
        spaceId: string
        connectionUuid: string
      }) => unknown
    }>("features/model-connections/ModelConnectionDetail.tsx")
    .ModelConnectionDetail({
      spaceId: "space-one",
      connectionUuid: "connection-1",
    })
}

test("detail keeps the missing connectivity time explicit and credentials write-only", () => {
  const tree = detail()
  const text = uiText(tree)
  assert.match(text, /modelConnections.checkedAtmodelConnections.notRecorded/)
  assert.equal(text.match(/UPDATED_DATE_ONLY/g)?.length, 1)
  assert.match(text, /modelConnections.credentialConfigured/)
  assert.equal(
    uiElements(tree, "CopyValue").some(
      (node) => node.props.value === connection.endpoint
    ),
    true
  )
  assert.equal(uiElements(tree, "input").length, 0)
})

test("failed reference retrieval is unknown with retry, never zero references", () => {
  const tree = detail({ tab: "references", referencesError: true })
  assert.match(uiText(tree), /modelConnections.referencesFailed/)
  assert.doesNotMatch(
    uiText(tree),
    /modelConnections.noReferences|private upstream/
  )
  assert.equal(
    uiElements(tree, "Button").some((node) => uiText(node) === "common.retry"),
    true
  )
})

test("a connection missing in the current Space cannot render cached connection details", () => {
  const tree = detail({ items: [] })
  assert.match(uiText(tree), /modelConnections.notFound/)
  assert.doesNotMatch(uiText(tree), /Shared model|long\/model-name/)
})

test("reference links retain owning Space and exact Agent UUID", () => {
  const path = diagnoserReferencePath("space-one", {
    uuid: "diagnoser-one",
    name: "Diagnoser",
    agent_uuid: "agent-one",
    type: "custom",
    mode: "llm",
  })
  const url = new URL(path, "https://test.invalid")
  assert.equal(url.pathname, "/diagnosers")
  assert.equal(url.searchParams.get("spaceId"), "space-one")
  assert.equal(url.searchParams.get("agent_uuid"), "agent-one")
  assert.equal(url.searchParams.get("diagnoser"), "diagnoser-one")
})
