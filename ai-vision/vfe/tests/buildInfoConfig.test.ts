import assert from "node:assert/strict"
import { test } from "node:test"
import { loadBuildInfo } from "../scripts/buildInfo.ts"

test("build injection selects the frontend identity and excludes arbitrary metadata", () => {
  const info = {
    schema_version: 1,
    component: "aivision-fe",
    product_version: "0.0.1",
    component_version: "0.0.1",
    source_sha: "a".repeat(40),
    build_id: "123.1",
    built_at: "2026-10-16T00:00:00Z",
    target_channel: "dev",
    dirty: false,
    secret: "must-not-appear",
  }
  const value = loadBuildInfo(
    "/nonexistent",
    Buffer.from(JSON.stringify(info)).toString("base64")
  )
  assert.equal(value.source_sha, "a".repeat(40))
  assert.equal(value.product_version, "0.0.1")
  assert.equal("secret" in value, false)
})

test("backend metadata cannot be injected as the frontend build", () => {
  const encoded = Buffer.from(
    JSON.stringify({ schema_version: 1, component: "aivision-be" })
  ).toString("base64")
  assert.throws(() => loadBuildInfo("/nonexistent", encoded), /frontend/)
})

test("an unversioned source archive stays unknown rather than using the package version", () => {
  const info = loadBuildInfo("/nonexistent", "")
  assert.equal(info.component, "aivision-fe")
  assert.equal(info.product_version, null)
  assert.equal(info.source_sha, null)
  assert.equal(info.dirty, null)
})

test("malformed versions and non-ISO dates fail the build instead of claiming an identity", () => {
  for (const fields of [
    { product_version: "00.0.1" },
    { product_version: "latest" },
    { product_version: "0.0.1\n" },
    { component_version: "0.1.0-rc..1" },
    { built_at: "2026-10-16X00:00:00Z" },
    { built_at: "2026-10-16T00:00:00+00:60" },
  ]) {
    const encoded = Buffer.from(
      JSON.stringify({ schema_version: 1, component: "aivision-fe", ...fields })
    ).toString("base64")
    assert.throws(
      () => loadBuildInfo("/nonexistent", encoded),
      /Invalid frontend/
    )
  }
})

for (const builtAt of [
  "2026-02-30T00:00:00Z",
  "2026-04-31T00:00:00Z",
  "1900-02-29T00:00:00Z",
  "0000-01-01T00:00:00Z",
  "2026-10-16T24:00:00Z",
  "2026-10-16T00:00:00Z\n",
]) {
  test(`build injection rejects invalid calendar time ${JSON.stringify(builtAt)}`, () => {
    const encoded = Buffer.from(
      JSON.stringify({
        schema_version: 1,
        component: "aivision-fe",
        built_at: builtAt,
      })
    ).toString("base64")
    assert.throws(
      () => loadBuildInfo("/nonexistent", encoded),
      /Invalid frontend build time/
    )
  })
}

test("build injection preserves leap dates, timezone offsets and microseconds", () => {
  for (const builtAt of [
    "2000-02-29T23:59:59Z",
    "2024-02-29T23:59:59.123456+08:00",
    "0001-01-01T00:00:00-01:30",
  ]) {
    const encoded = Buffer.from(
      JSON.stringify({
        schema_version: 1,
        component: "aivision-fe",
        product_version: "0.1.0-dev.20261016.1",
        built_at: builtAt,
      })
    ).toString("base64")
    const info = loadBuildInfo("/nonexistent", encoded)
    assert.equal(info.built_at, builtAt)
    assert.equal(info.product_version, "0.1.0-dev.20261016.1")
  }
})
