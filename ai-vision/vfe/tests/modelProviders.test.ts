import assert from "node:assert/strict"
import test from "node:test"
import {
  getModelProviders,
  inferModelProvider,
  isModelDiscoverySupported,
} from "../src/features/model-connections/providers.ts"

test("locale changes prioritize relevant providers without hiding alternatives", () => {
  const zh = getModelProviders("zh-CN").map((provider) => provider.id)
  const en = getModelProviders("en-US").map((provider) => provider.id)
  const ja = getModelProviders("ja-JP").map((provider) => provider.id)
  assert.ok(zh.indexOf("dashscope") < zh.indexOf("openai"))
  assert.ok(en.indexOf("openai") < en.indexOf("dashscope"))
  assert.ok(ja.indexOf("gemini") < ja.indexOf("dashscope"))
  assert.deepEqual([...zh].sort(), [...en].sort())
  assert.deepEqual([...ja].sort(), [...en].sort())
  assert.equal(zh.at(-1), "custom")
  assert.equal(en.at(-1), "custom")
  assert.equal(ja.at(-1), "custom")
})

test("editing recognizes a regional connection without changing its endpoint", () => {
  const inferred = inferModelProvider(
    " https://dashscope-intl.aliyuncs.com/compatible-mode/v1/ "
  )
  assert.equal(inferred?.provider.id, "dashscope")
  assert.equal(inferred?.region.id, "singapore")
  assert.equal(
    inferred?.region.baseUrl,
    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
  )
  getModelProviders("ja-JP").reverse()
  assert.equal(getModelProviders("zh-CN").at(-1)?.id, "custom")
})

test("new connections offer a suitable region while saved region inference is stable", () => {
  assert.equal(
    getModelProviders("ja-JP").find((provider) => provider.id === "dashscope")
      ?.regions[0].id,
    "singapore"
  )
  assert.equal(
    getModelProviders("en-US").find((provider) => provider.id === "glm")
      ?.regions[0].id,
    "international"
  )
  assert.equal(
    getModelProviders("zh-CN").find((provider) => provider.id === "dashscope")
      ?.regions[0].id,
    "china"
  )
  assert.equal(
    inferModelProvider("https://open.bigmodel.cn/api/paas/v4")?.region.id,
    "china"
  )
})

test("lookalike and modified URLs are custom and cannot trigger discovery", () => {
  for (const endpoint of [
    "https://api.openai.com.evil.example/v1",
    "https://api.openai.com@evil.example/v1",
    "https://api.openai.com/v1?redirect=https://evil.example",
    "https://api.openai.com/v1/other",
    "http://127.0.0.1:8000/v1",
    "",
  ]) {
    assert.equal(inferModelProvider(endpoint), undefined, endpoint)
    assert.equal(isModelDiscoverySupported(endpoint), false, endpoint)
  }
  assert.equal(isModelDiscoverySupported(" https://api.openai.com/v1/ "), true)
})

test("Anthropic and NVIDIA have hosted discovery endpoints and appear in every locale", () => {
  for (const locale of ["en-US", "ja-JP", "zh-CN"]) {
    const providers = getModelProviders(locale)
    const ids = providers.map((provider) => provider.id)
    assert.ok(ids.includes("anthropic"))
    assert.ok(ids.includes("nvidia"))
    if (locale !== "zh-CN") {
      assert.deepEqual(ids.slice(0, 3), ["openai", "anthropic", "gemini"])
    }
    const anthropic = providers.find((provider) => provider.id === "anthropic")!
    assert.equal(anthropic.regions[0].baseUrl, "https://api.anthropic.com/v1")
    assert.equal(
      anthropic.regions[0].apiKeyUrl,
      "https://platform.claude.com/settings/keys"
    )
    assert.equal(isModelDiscoverySupported(anthropic.regions[0].baseUrl), true)
    const nvidia = providers.find((provider) => provider.id === "nvidia")!
    assert.equal(
      nvidia.regions[0].baseUrl,
      "https://integrate.api.nvidia.com/v1"
    )
    assert.equal(
      nvidia.regions[0].apiKeyUrl,
      "https://build.nvidia.com/settings/api-keys"
    )
    assert.equal(isModelDiscoverySupported(nvidia.regions[0].baseUrl), true)
  }
})
