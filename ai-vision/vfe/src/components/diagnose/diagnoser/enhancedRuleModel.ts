import type { RuleRuntimeManifest } from "@/features/sandbox-connections/types"
export type { RuleRuntimeManifest } from "@/features/sandbox-connections/types"
export const RULE_RUNTIME = "python-eval-v1"
export interface RuleMaterialFile {
  path: string
  encoding: "utf-8" | "base64"
  content: string
  sha256?: string
}
export interface RuleMaterials {
  files: RuleMaterialFile[]
  expected?: unknown
  expected_json?: string
}
function validateExpectedValue(value: unknown) {
  const pending: [unknown, number][] = [[value, 0]]
  while (pending.length) {
    const [current, depth] = pending.pop()!
    if (typeof current === "number" && !Number.isFinite(current))
      throw Error("expected")
    if (current && typeof current === "object") {
      if (depth >= 64) throw Error("expected")
      Object.values(current).forEach((child) =>
        pending.push([child, depth + 1])
      )
    } else if (
      current !== null &&
      !["number", "string", "boolean"].includes(typeof current)
    )
      throw Error("expected")
  }
}
/** Validate values without reserializing the measured original JSON text. */
export function buildRuleMaterials(
  files: RuleMaterialFile[],
  expectedText: string
): RuleMaterials {
  if (!expectedText.trim()) return { files }
  const value: unknown = JSON.parse(expectedText)
  validateExpectedValue(value)
  return { files, expected_json: expectedText }
}
export function expectedTextFromMaterials(materials?: RuleMaterials): string {
  if (!materials) return ""
  if (typeof materials.expected_json === "string") {
    return materials.expected_json
  }
  return Object.hasOwn(materials, "expected")
    ? (JSON.stringify(materials.expected, null, 2) ?? "")
    : ""
}
export function materialBytes(file: RuleMaterialFile) {
  if (file.encoding === "utf-8")
    return new TextEncoder().encode(file.content).length
  return atob(file.content).length
}
export function validateMaterials(materials: RuleMaterials): string | null {
  if (materials.files.length > 20) return "fileCount"
  const paths = new Set<string>()
  let total = 0
  for (const file of materials.files) {
    if (
      !file.path ||
      new TextEncoder().encode(file.path).length > 240 ||
      /\.(zip|tar|gz|bz2|xz|7z|rar|tgz|whl)$/i.test(file.path) ||
      file.path.startsWith("/") ||
      file.path.includes("\\") ||
      file.path.includes(":") ||
      file.path.split("/").some((p) => !p || p === "." || p === "..") ||
      Array.from(file.path).some((char) => char.charCodeAt(0) < 32)
    )
      return "path"
    if (
      Array.from(paths).some(
        (path) =>
          path === file.path ||
          path.startsWith(file.path + "/") ||
          file.path.startsWith(path + "/")
      )
    )
      return "duplicate"
    paths.add(file.path)
    let size: number
    try {
      if (
        file.encoding === "base64" &&
        !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(
          file.content
        )
      )
        return "encoding"
      size = materialBytes(file)
    } catch {
      return "encoding"
    }
    if (size > 256 * 1024) return "fileSize"
    total += size
  }
  const hasExpected = Object.hasOwn(materials, "expected")
  const hasExpectedJson = Object.hasOwn(materials, "expected_json")
  if (hasExpected && hasExpectedJson) return "expected"
  if (hasExpected || hasExpectedJson) {
    try {
      const wire = hasExpectedJson
        ? materials.expected_json
        : JSON.stringify(materials.expected)
      if (typeof wire !== "string") return "expected"
      validateExpectedValue(JSON.parse(wire))
      if (hasExpected) validateExpectedValue(materials.expected)
      total += new TextEncoder().encode(wire).length
    } catch {
      return "expected"
    }
  }
  return total > 512 * 1024 ? "totalSize" : null
}
export function buildEnhancedRuleSpec(
  spec: Record<string, unknown>,
  runtime: RuleRuntimeManifest | undefined,
  materials: RuleMaterials
): Record<string, unknown> {
  return {
    ...spec,
    ...(runtime
      ? {
          rule_runtime: runtime.id,
          rule_runtime_manifest: runtime.manifest_sha256,
        }
      : {}),
    ...(materials.files.length ||
    Object.hasOwn(materials, "expected") ||
    Object.hasOwn(materials, "expected_json") ||
    Object.hasOwn(spec, "materials")
      ? { materials }
      : {}),
  }
}
export { ENHANCED_EXAMPLES } from "./enhancedRuleExamples.ts"
