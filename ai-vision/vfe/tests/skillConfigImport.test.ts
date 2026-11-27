import assert from "node:assert/strict"
import test from "node:test"

import { parseSkillConfigText } from "../src/labs/skill/skillConfigImport.ts"

test("folded block scalar folds the description into a single line", () => {
  // The TypeSafe-style SKILL.md front matter: `description: >` keeps the
  // prose on indented lines; the bare `>` indicator is not the value.
  const result = parseSkillConfigText(
    [
      "---",
      "name: typesafe-ai",
      "description: >",
      "  Build AI-powered software with TypeSafe: small units of AI",
      "  intelligence you can use like programming primitives.",
      "license: MIT",
      "---",
      "",
      "# Body",
      "",
    ].join("\n")
  )
  if (typeof result === "string") {
    assert.fail(`unexpected import error: ${result}`)
  }
  assert.equal(result.configs.length, 1)
  assert.equal(result.configs[0].name, "typesafe-ai")
  assert.equal(
    result.configs[0].description,
    "Build AI-powered software with TypeSafe: small units of AI intelligence you can use like programming primitives."
  )
  assert.equal(result.configs[0].license, "MIT")
})

test("literal block scalar keeps the line breaks", () => {
  const result = parseSkillConfigText(
    [
      "---",
      "name: demo",
      "description: |",
      "  First line.",
      "  Second line.",
      "---",
      "",
    ].join("\n")
  )
  if (typeof result === "string") {
    assert.fail(`unexpected import error: ${result}`)
  }
  assert.equal(result.configs[0].description, "First line.\nSecond line.")
})

test("an indicator with an empty block is not the value", () => {
  for (const text of [
    "---\nname: demo\ndescription: >\nlicense: MIT\n---\n",
    "---\nname: demo\ndescription: >-\nlicense: MIT\n---\n",
  ]) {
    const result = parseSkillConfigText(text)
    if (typeof result === "string") {
      assert.fail(`unexpected import error: ${result}`)
    }
    assert.equal(result.configs[0].description, "")
    assert.equal(result.configs[0].license, "MIT")
  }
})

test("single-line values keep parsing as before", () => {
  const result = parseSkillConfigText(
    "---\nname: demo\ndescription: short\n---\n"
  )
  if (typeof result === "string") {
    assert.fail(`unexpected import error: ${result}`)
  }
  assert.equal(result.configs[0].description, "short")
})

test("YAML comments do not become part of the Skill name", () => {
  for (const name of [
    '"qa-audit" # Matches the Agent name',
    "'qa-audit' # Matches the Agent name",
    "qa-audit # Matches the Agent name",
  ]) {
    const text = `---\nname: ${name}\ndescription: Audit # Metadata comment\n---\n\n# Body`
    const result = parseSkillConfigText(text)
    if (typeof result === "string") assert.fail(result)
    assert.equal(result.configs[0].name, "qa-audit")
    assert.equal(result.configs[0].description, "Audit")
    assert.equal(result.configs[0].raw.content, text)
  }
})

test("quoted hashes, attached hashes, and block content stay literal", () => {
  for (const [name, expected] of [
    ["qa#one # Comment", "qa#one"],
    ["qa\u00a0#one # Comment", "qa\u00a0#one"],
    ['"qa # one" # Comment', "qa # one"],
    ['"qa\\x2daudit" # Comment', "qa-audit"],
    ['"qa\\\\x2daudit" # Comment', "qa\\x2daudit"],
    [String.raw`"qa\" # one" # Comment`, 'qa" # one'],
    ["'qa'' # one' # Comment", "qa' # one"],
  ]) {
    const result = parseSkillConfigText(
      `---\nname: ${name}\ndescription: | # A block\n  Audit records.\n  # Keep this text.\n---`
    )
    if (typeof result === "string") assert.fail(result)
    assert.equal(result.configs[0].name, expected)
    assert.equal(
      result.configs[0].description,
      "Audit records.\n# Keep this text."
    )
  }

  const literal = parseSkillConfigText(
    '---\nname: qa-audit\ndescription: "|" # A literal value\n---'
  )
  if (typeof literal === "string") assert.fail(literal)
  assert.equal(literal.configs[0].description, "|")
})

test("front matter imports the humanizer-style allowed-tools list and preserves source", () => {
  const text = [
    "---",
    "name: humanizer-zh",
    "description: Edit Chinese prose.",
    "allowed-tools:",
    "  - Read",
    "  - Write",
    "  - Edit",
    "  - AskUserQuestion",
    "license: MIT",
    "---",
    "",
    "# Humanizer-zh",
    "",
  ].join("\n")
  const result = parseSkillConfigText(text)
  if (typeof result === "string")
    assert.fail(`unexpected import error: ${result}`)
  assert.deepEqual(result.configs[0].allowedTools, [
    "Read",
    "Write",
    "Edit",
    "AskUserQuestion",
  ])
  assert.equal(result.configs[0].license, "MIT")
  assert.equal(result.configs[0].raw.content, text.trim())
})

test("front matter accepts flat string lists for every allowed-tools field alias", () => {
  for (const key of ["allowed-tools", "allowed_tools", "allowedTools"]) {
    const result = parseSkillConfigText(
      [
        "---",
        "name: demo",
        `${key}:`,
        "- Read",
        "- 'Bash(git:*)'",
        "",
        "# List comment",
        '- "Write"',
        "compatibility: pi",
        "---",
      ].join("\n")
    )
    if (typeof result === "string")
      assert.fail(`unexpected import error: ${result}`)
    assert.deepEqual(
      result.configs[0].allowedTools,
      ["Read", "Bash(git:*)", "Write"],
      key
    )
    assert.equal(result.configs[0].compatibility, "pi")
  }
})

test("existing inline allowed-tools and JSON arrays retain their values", () => {
  for (const text of [
    "---\nname: demo\nallowed-tools: Read, Write, Bash(git:*)\n---",
    JSON.stringify({
      skills: [
        { name: "demo", allowedTools: ["Read", "Write", "Bash(git:*)"] },
      ],
    }),
  ]) {
    const result = parseSkillConfigText(text)
    if (typeof result === "string")
      assert.fail(`unexpected import error: ${result}`)
    assert.deepEqual(result.configs[0].allowedTools, [
      "Read",
      "Write",
      "Bash(git:*)",
    ])
  }
})

test("unsupported front matter tool collections are explicitly rejected", () => {
  for (const list of [
    "  - name: Read",
    "  - [Read, Write]",
    "  - Read\n    - Write",
    '  - "Read',
  ]) {
    const text = `---\nname: demo\nallowed-tools:\n${list}\nlicense: MIT\n---\n\n# Body`
    const result = parseSkillConfigText(text)
    assert.equal(result, "unsupportedYaml", list)
  }
})

test("missing lists and non-string JSON tool entries remain safe", () => {
  for (const text of [
    "---\nname: demo\nallowed-tools:\nlicense: MIT\n---",
    "---\nname: demo\nallowed-tools:\n  - ''\n  - \"\"\n---",
    "---\nname: demo\nallowed-tools:\n  - # No tool value\n---",
  ]) {
    const result = parseSkillConfigText(text)
    if (typeof result === "string")
      assert.fail(`unexpected import error: ${result}`)
    assert.deepEqual(result.configs[0].allowedTools, [])
  }
  const result = parseSkillConfigText(
    JSON.stringify([
      { name: "demo", allowedTools: ["Read", null, 2, {}, "", "Write"] },
    ])
  )
  if (typeof result === "string")
    assert.fail(`unexpected import error: ${result}`)
  assert.deepEqual(result.configs[0].allowedTools, ["Read", "Write"])
  assert.equal(
    parseSkillConfigText("---\nallowed-tools:\n  - Read\n---"),
    "invalidFormat"
  )
})

test("a nested metadata list is not the root allowed-tools field", () => {
  const result = parseSkillConfigText(
    [
      "---",
      "name: demo",
      "metadata:",
      "  allowed-tools:",
      "    - Write",
      "---",
    ].join("\n")
  )
  if (typeof result === "string")
    assert.fail(`unexpected import error: ${result}`)
  assert.deepEqual(result.configs[0].allowedTools, [])
})

test("uniformly indented front matter keeps inline and list tools compatible", () => {
  for (const tools of [
    "  allowed-tools: Read, Write",
    "  allowed-tools:\n    - Read\n    - Write",
  ]) {
    const result = parseSkillConfigText(
      `---\n  name: demo\n${tools}\n  license: MIT\n---`
    )
    if (typeof result === "string")
      assert.fail(`unexpected import error: ${result}`)
    assert.deepEqual(result.configs[0].allowedTools, ["Read", "Write"])
    assert.equal(result.configs[0].license, "MIT")
  }
})

test("YAML manifests retain every item, inline arrays, and original fields", () => {
  const result = parseSkillConfigText(
    [
      "skills:",
      "  - name: alpha",
      "    description: First skill",
      "    allowed-tools: [Read, 'Bash(git:*)']",
      "    custom: preserved",
      "  - name: beta",
      "    allowed-tools:",
      "      - Write",
      "      - Edit",
    ].join("\n")
  )
  if (typeof result === "string") assert.fail(result)
  assert.deepEqual(
    result.configs.map((config) => config.name),
    ["alpha", "beta"]
  )
  assert.deepEqual(result.configs[0].allowedTools, ["Read", "Bash(git:*)"])
  assert.deepEqual(result.configs[1].allowedTools, ["Write", "Edit"])
  assert.equal(
    (result.configs[0].raw.entry as Record<string, unknown>).custom,
    "preserved"
  )
  assert.equal(result.inputCount, 2)
  assert.deepEqual(result.skipped, [])
})

test("inline YAML tool arrays also work in SKILL.md without changing raw content", () => {
  const text =
    "---\nname: demo\nallowed-tools: [Read, \"Bash(git:*)\", 'Tool, with comma']\n---"
  const result = parseSkillConfigText(text)
  if (typeof result === "string") assert.fail(result)
  assert.deepEqual(result.configs[0].allowedTools, [
    "Read",
    "Bash(git:*)",
    "Tool, with comma",
  ])
  assert.equal(result.configs[0].raw.content, text)
})

test("unsupported YAML manifest structures are rejected instead of silently merged", () => {
  for (const text of [
    "skills:\n  - name: alpha\n    metadata:\n      custom: lost",
    "skills:\n  - name: alpha\n    name: beta",
    "skills:\n  - name: alpha\n    allowed-tools: [Read, [Write]]",
    "skills:\n  - name: alpha\n    description: *alias",
    "skills:\n  - name: alpha\n    allowed-tools:\n      - name: Read",
  ])
    assert.equal(parseSkillConfigText(text), "unsupportedYaml", text)
})

test("manifest preview counts rejected missing and duplicate names", () => {
  const result = parseSkillConfigText(
    JSON.stringify([
      { name: "alpha" },
      { description: "Missing name" },
      { name: "alpha" },
      { name: "beta" },
    ])
  )
  if (typeof result === "string") assert.fail(result)
  assert.equal(result.inputCount, 4)
  assert.deepEqual(
    result.configs.map((config) => config.name),
    ["alpha", "beta"]
  )
  assert.equal(result.rejected.length, 2)
  assert.deepEqual(
    result.rejected.map((item) => item.reason),
    ["missingName", "duplicateName"]
  )
})

test("manifest literal blocks preserve blank lines and comment-looking source content", () => {
  const result = parseSkillConfigText(
    "skills:\n  - name: alpha\n    description: |\n      First line\n\n      # heading\n      Third line\n"
  )
  assert.notEqual(typeof result, "string")
  if (typeof result === "string") return
  assert.equal(
    result.configs[0].description,
    "First line\n\n# heading\nThird line"
  )
  assert.equal(
    (result.configs[0].raw.entry as Record<string, unknown>).description,
    "First line\n\n# heading\nThird line"
  )
})
