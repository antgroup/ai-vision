import assert from "node:assert/strict"
import { readFileSync, existsSync } from "node:fs"
import { createRequire } from "node:module"
import { dirname, resolve } from "node:path"
import test from "node:test"
import { fileURLToPath } from "node:url"
import ts from "typescript"

// Pure-function module under test; reuse the loader pattern from
// decision.test.ts so bundler path aliases work under Node's test runner.
const nodeRequire = createRequire(import.meta.url)
const here = dirname(fileURLToPath(import.meta.url))
const sourceRoot = resolve(here, "../src")

function load<T>(file: string, cache = new Map<string, unknown>()): T {
  const path = resolve(sourceRoot, file)
  if (cache.has(path)) return cache.get(path) as T
  const exports = {}
  cache.set(path, exports)
  const code = ts.transpileModule(readFileSync(path, "utf8"), {
    fileName: path,
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText
  new Function("require", "exports", code)((name: string) => {
    if (name.startsWith("@/") || name.startsWith(".")) {
      const candidate = name.startsWith("@/")
        ? resolve(sourceRoot, name.slice(2))
        : resolve(dirname(path), name)
      const actual = [candidate, candidate + ".ts", candidate + ".tsx"].find(
        existsSync
      )
      if (!actual) throw Error(`Missing dependency ${name}`)
      return load(actual, cache)
    }
    return nodeRequire(name)
  }, exports)
  return exports as T
}

const {
  resolveSkillSpanInfo,
  resolveSkillDetailFields,
  nativeSkillName,
  resolveCountedSkillSpans,
  splitSkillFrontmatter,
  stripRepeatedTitleHeading,
} = load<typeof import("../src/labs/skill/skillDisplay.ts")>(
  "labs/skill/skillDisplay.ts"
)

const TOOL = { "gen_ai.span.kind": "TOOL" }

// --- Recognition: native, shape-derived and the negative space ------------

test("native plugin report wins (codex skill tool)", () => {
  const info = resolveSkillSpanInfo({
    ...TOOL,
    "agentic.tool.type": "skill",
    "agentic.tool.name": "visualize",
    "agentic.tool.id": "skill-visualize",
    "agentic.tool.call.arguments":
      '{"skill":"visualize","invocation":"cli","source":"file","byte_range":"0-271"}',
  })
  assert.deepEqual(info, {
    name: "visualize",
    id: "skill-visualize",
    derived: false,
  })
})

test("native report falls back to agentic.skill.name for the title", () => {
  const info = resolveSkillSpanInfo({
    "agentic.tool.type": "skill",
    "agentic.skill.name": "code-review",
  })
  assert.equal(info?.name, "code-review")
  assert.equal(info?.derived, false)
})

test("aicoder storage columns report natively without tool type", () => {
  const info = resolveSkillSpanInfo({
    "agentic.skill.name": "deploy",
    "agentic.skill.id": "s1",
  })
  assert.deepEqual(info, { name: "deploy", id: "s1", derived: false })
})

test("derived exec shape (codex cat SKILL.md) matches the path", () => {
  const info = resolveSkillSpanInfo({
    ...TOOL,
    "agentic.tool.call.arguments":
      'bash -lc "cat /root/.codex/skills/visualize/SKILL.md" >/tmp/skill.md; wc -l /tmp/skill.md',
  })
  assert.deepEqual(info, { name: "visualize", id: null, derived: true })
})

test("derived read_file shape (deepagents) parses the JSON payload", () => {
  const info = resolveSkillSpanInfo({
    ...TOOL,
    "gen_ai.tool.call.arguments": '{"file_path":"/skills/blog-post/SKILL.md"}',
  })
  assert.deepEqual(info, { name: "blog-post", id: null, derived: true })
})

test("escaped path separators still match (double-encoded payloads)", () => {
  const info = resolveSkillSpanInfo({
    ...TOOL,
    "gen_ai.tool.call.arguments": String.raw`{"file_path":"\/skills\/writer\/SKILL.md"}`,
  })
  assert.equal(info?.name, "writer")
  assert.equal(info?.derived, true)
})

test("plain reader commands retain escaped slash path recognition", () => {
  const info = resolveSkillSpanInfo({
    ...TOOL,
    "gen_ai.tool.call.arguments": String.raw`cat /root/skills\/deploy\/SKILL.md`,
  })
  assert.deepEqual(info, { name: "deploy", id: null, derived: true })
})

test("wrapped and compound reader commands still derive (real shapes)", () => {
  for (const [commandText, expected] of [
    [
      String.raw`{"cmd":"/bin/zsh -lc 'cat /root/.codex/skills/visualize/SKILL.md'"}`,
      "visualize",
    ],
    [
      String.raw`{"cmd":"bash -lc \"cat /root/.codex/skills/visualize/SKILL.md\" >/tmp/skill.md; wc -l /tmp/skill.md"}`,
      "visualize",
    ],
    ["cat /root/.codex/skills/visualize/SKILL.md | grep -n foo", "visualize"],
  ] as const) {
    const info = resolveSkillSpanInfo({
      ...TOOL,
      "agentic.tool.call.arguments": commandText,
    })
    assert.equal(info?.name, expected, commandText)
    assert.equal(info?.derived, true, commandText)
  }
})

test("non-reader shell text never derives a call", () => {
  for (const commandText of [
    String.raw`{"cmd":"printf '/skills/visualize/SKILL.md'"}`,
    String.raw`{"cmd":"node /skills/visualize/SKILL.md"}`,
    String.raw`{"cmd":"find /skills/visualize -name SKILL.md -print"}`,
    String.raw`{"cmd":"echo \"a; cat /skills/visualize/SKILL.md\""}`,
    'echo "a; cat /skills/visualize/SKILL.md"',
    "# cat /skills/visualize/SKILL.md",
  ]) {
    const info = resolveSkillSpanInfo({
      ...TOOL,
      "agentic.tool.call.arguments": commandText,
    })
    assert.equal(info, null, commandText)
  }
})

test("writer tool shapes never derive a call", () => {
  for (const toolName of ["write_file", "edit", "apply_patch"]) {
    const info = resolveSkillSpanInfo({
      ...TOOL,
      "gen_ai.tool.call.name": toolName,
      "gen_ai.tool.call.arguments":
        '{"file_path":"/skills/blog-post/SKILL.md"}',
    })
    assert.equal(info, null, toolName)
  }
})

test("producer tool-name aliases exclude skill file maintenance but retain reads", () => {
  for (const key of [
    "gen_ai.tool.name",
    "agentic.tool.raw_name",
    "agentic.tool.name",
  ]) {
    for (const toolName of ["write", "edit"]) {
      assert.equal(
        resolveSkillSpanInfo({
          ...TOOL,
          [key]: toolName,
          "agentic.tool.call.arguments": JSON.stringify({
            path: "/skills/deploy/SKILL.md",
            content: "updated instructions",
          }),
        }),
        null,
        `${key}=${toolName}`
      )
    }
    assert.deepEqual(
      resolveSkillSpanInfo({
        ...TOOL,
        [key]: "read",
        "agentic.tool.call.arguments": '{"path":"/skills/deploy/SKILL.md"}',
      }),
      { name: "deploy", id: null, derived: true },
      key
    )
  }
})

test("middleware narration never matches without the TOOL gate", () => {
  // The deepagents trace carries 112 SkillsMiddleware.* chain spans whose
  // narration mentions skills; only TOOL spans may derive a call.
  const chain = resolveSkillSpanInfo({
    "gen_ai.span.kind": "CHAIN",
    "gen_ai.tool.call.arguments":
      "SkillsMiddleware lists skills/blog-post/SKILL.md",
  })
  assert.equal(chain, null)
  const untyped = resolveSkillSpanInfo({
    "gen_ai.tool.call.arguments": "/skills/blog-post/SKILL.md",
  })
  assert.equal(untyped, null)
})

test("the TOOL gate is case-insensitive", () => {
  const info = resolveSkillSpanInfo({
    "gen_ai.span.kind": "tool",
    "gen_ai.tool.call.arguments": '{"file_path":"/skills/t/SKILL.md"}',
  })
  assert.equal(info?.name, "t")
})

test("tool result is never evidence", () => {
  // codex `cat SKILL.md >/tmp/x` leaves a line count in the output; the
  // recognition must only read the arguments side.
  const info = resolveSkillSpanInfo({
    ...TOOL,
    "agentic.tool.call.result": "271",
    "agentic.skill.output": "/skills/blog-post/SKILL.md",
  })
  assert.equal(info, null)
})

test("a skills directory listing is not a call", () => {
  // deepagents root outputs carry skills_metadata with every available
  // skill; presence is not execution.
  const listing = resolveSkillSpanInfo({
    skills_metadata:
      '[{"name":"blog-post","description":"write","path":"/skills/blog-post"}]',
  })
  assert.equal(listing, null)
})

test("span names with Skills do not match on their own", () => {
  const info = resolveSkillSpanInfo({ "gen_ai.span.kind": "TOOL" })
  assert.equal(info, null)
})

test("native derived flag is surfaced as-is", () => {
  const info = resolveSkillSpanInfo({
    "agentic.tool.type": "skill",
    "agentic.tool.name": "x",
    "agentic.tool.derived": "true",
  })
  assert.equal(info?.derived, true)
})

// --- Native fallback rejection: plugin-inferred command-text reports -------

const FALLBACK_ARGUMENTS =
  '{"skill":"visualize","invocation":"$visualize","source":"command_path_fallback","byte_range":null}'

test("a fallback native span does not report a call", () => {
  // The plugin-inferred arguments JSON carries no command text, so the
  // platform cannot re-judge it; the exec TOOL span re-derives instead.
  for (const attrs of [
    {
      ...TOOL,
      "agentic.tool.type": "skill",
      "agentic.tool.name": "visualize",
      "agentic.tool.call.arguments": FALLBACK_ARGUMENTS,
    },
    {
      "agentic.skill.name": "visualize",
      "gen_ai.tool.call.arguments": FALLBACK_ARGUMENTS,
    },
  ]) {
    assert.equal(resolveSkillSpanInfo(attrs), null, "recognition rejection")
    assert.equal(nativeSkillName(attrs), null, "merge-side rejection")
  }
})

test("semantic native sources stay authoritative", () => {
  for (const payload of [
    '{"skill":"visualize","source":"codex_text_element"}',
    '{"skill":"visualize","source":"legacy_markdown"}',
    null, // no arguments at all — cchooks writes none
    "[REDACTED]", // privacy mode drops the payload
    "cat /root/.codex/skills/visualize/SKILL.md",
  ]) {
    const attrs = {
      ...TOOL,
      "agentic.tool.type": "skill",
      "agentic.tool.name": "visualize",
      ...(payload ? { "gen_ai.tool.call.arguments": payload } : {}),
    }
    const info = resolveSkillSpanInfo(attrs)
    assert.equal(info?.name, "visualize", payload ?? "no arguments")
    assert.equal(info?.derived, false, payload ?? "no arguments")
    assert.equal(nativeSkillName(attrs), "visualize", payload ?? "no arguments")
  }
})

test("maintaining a SKILL.md is not reading it", () => {
  // Redirection onto the path itself and in-place edits maintain the file;
  // only reads count as usage.
  for (const commandText of [
    '{"cmd":"cat > /root/.codex/skills/visualize/SKILL.md"}',
    '{"cmd":"cat >> /root/.codex/skills/visualize/SKILL.md"}',
    '{"cmd":"cat >\'/root/.codex/skills/visualize/SKILL.md\'"}',
    '{"cmd":"sed -i \'s/a/b/\' /root/.codex/skills/visualize/SKILL.md"}',
    '{"cmd":"echo x > /root/.codex/skills/visualize/SKILL.md"}',
  ]) {
    const info = resolveSkillSpanInfo({
      ...TOOL,
      "agentic.tool.call.arguments": commandText,
    })
    assert.equal(info, null, commandText)
  }
})

test("reading then redirecting elsewhere still counts", () => {
  for (const commandText of [
    '{"cmd":"cat /root/.codex/skills/visualize/SKILL.md > /tmp/copy.md"}',
    '{"cmd":"cat /root/.codex/skills/visualize/SKILL.md 2>/tmp/err.log"}',
    '{"cmd":"head -n 20 /root/.codex/skills/visualize/SKILL.md"}',
  ]) {
    const info = resolveSkillSpanInfo({
      ...TOOL,
      "agentic.tool.call.arguments": commandText,
    })
    assert.equal(info?.name, "visualize", commandText)
    assert.equal(info?.derived, true, commandText)
  }
})

test("a reader and SKILL.md mention in separate commands never imply a read", () => {
  for (const command of [
    "echo /skills/deploy/SKILL.md; cat README.md",
    "printf /skills/deploy/SKILL.md | head -n 1",
    "cat README.md; echo /skills/deploy/SKILL.md",
    "bash -lc 'echo /skills/deploy/SKILL.md; cat README.md'",
  ]) {
    assert.equal(
      resolveSkillSpanInfo({
        ...TOOL,
        "agentic.tool.call.arguments": JSON.stringify({ cmd: command }),
      }),
      null,
      command
    )
  }
})

test("the name comes from the command that reads the file", () => {
  for (const command of [
    "echo /skills/wrong/SKILL.md; cat /skills/real/SKILL.md",
    "bash -lc 'echo /skills/wrong/SKILL.md; cat /skills/real/SKILL.md'",
  ]) {
    assert.equal(
      resolveSkillSpanInfo({
        ...TOOL,
        "agentic.tool.call.arguments": JSON.stringify({ cmd: command }),
      })?.name,
      "real",
      command
    )
  }
})

// --- Conversation mentions are never calls --------------------------------

const INPUT_MESSAGES =
  '[{"role":"user","content":"[$visualize:visualize](/Users/sunchong/.codex/plugins/cache/openai-bundled/visualize/1.0.49/skills/visualize/SKILL.md) 做一个交互式数据可视化 demo"}]'

test("the host invocation link in LLM input messages is not a call", () => {
  // A chat turn is not a skill invocation: the user message merely carries
  // the host's skill badge link (appearance), while the SKILL.md read on a
  // TOOL span is the usage evidence (execution). Recognizing the turn would
  // double-count one @-mention beside its own read span.
  const info = resolveSkillSpanInfo({
    "gen_ai.span.kind": "LLM",
    "gen_ai.input.messages": INPUT_MESSAGES,
  })
  assert.equal(info, null)
})

// --- Detail fields --------------------------------------------------------

test("codex SkillInvocation fields are extracted from the arguments JSON", () => {
  const fields = resolveSkillDetailFields({
    ...TOOL,
    "agentic.tool.type": "skill",
    "agentic.tool.name": "visualize",
    "agentic.tool.id": "skill-visualize",
    "agentic.tool.call.arguments":
      '{"skill":"visualize","invocation":"cli","source":"file","byte_range":"0-271"}',
  })
  assert.equal(fields?.invocation, "cli")
  assert.equal(fields?.source, "file")
  assert.equal(fields?.byteRange, "0-271")
  assert.equal(fields?.info.name, "visualize")
})

test("plain command arguments keep no structured fields but stay readable", () => {
  const fields = resolveSkillDetailFields({
    ...TOOL,
    "agentic.tool.call.arguments": "cat /skills/visualize/SKILL.md",
  })
  assert.equal(fields?.invocation, null)
  assert.equal(fields?.source, null)
  assert.equal(fields?.rawArguments, "cat /skills/visualize/SKILL.md")
})

test("failure reason, tags and tokens render only when present", () => {
  const failed = resolveSkillDetailFields({
    ...TOOL,
    "agentic.tool.type": "skill",
    "agentic.tool.name": "x",
    "error.type": "skill_failed",
    "agentic.skill.tags": "writing, review",
    "agentic.skill.input_tokens": "123",
    "agentic.skill.output_tokens": "45",
    "agentic.skill.total_tokens": "168",
  })
  assert.equal(failed?.errorType, "skill_failed")
  assert.deepEqual(failed?.tags, ["writing", "review"])
  assert.deepEqual(failed?.tokens, { input: 123, output: 45, total: 168 })

  const bare = resolveSkillDetailFields({
    ...TOOL,
    "agentic.skill.name": "x",
  })
  assert.equal(bare?.errorType, null)
  assert.equal(bare?.tags, null)
  assert.equal(bare?.tokens, null)
})

test("tag arrays parse as-is", () => {
  const fields = resolveSkillDetailFields({
    ...TOOL,
    "agentic.skill.name": "x",
    "agentic.skill.tags": ["a", "b"],
  })
  assert.deepEqual(fields?.tags, ["a", "b"])
})

test("input and output text surface from the observed columns", () => {
  const fields = resolveSkillDetailFields({
    ...TOOL,
    "agentic.tool.type": "skill",
    "agentic.tool.name": "x",
    "agentic.skill.input": "---\nname: x\n---\nbody",
    "agentic.skill.output": "271",
  })
  assert.equal(fields?.input, "---\nname: x\n---\nbody")
  assert.equal(fields?.output, "271")
})

// --- Usage merge: reads count, a covered native period does not ------------

const WINDOW_START = 1_792_108_800_000_000_000

function nativePeriod(
  spanId: string,
  name: string,
  durationMs: number,
  startMs = 0
) {
  return {
    span_id: spanId,
    start_time_unix_nano: String(WINDOW_START + startMs * 1_000_000),
    end_time_unix_nano: String(
      WINDOW_START + (startMs + durationMs) * 1_000_000
    ),
    attributes: {
      "agentic.tool.type": "skill",
      "agentic.tool.name": name,
    },
  }
}

function skillRead(
  spanId: string,
  name: string,
  startMs: number,
  durationMs: number
) {
  const start = WINDOW_START + startMs * 1_000_000
  return {
    span_id: spanId,
    start_time_unix_nano: String(start),
    end_time_unix_nano: String(start + durationMs * 1_000_000),
    attributes: {
      "gen_ai.span.kind": "TOOL",
      "agentic.tool.call.arguments": `{"file_path":"/skills/${name}/SKILL.md"}`,
    },
  }
}

function fallbackNativePeriod(
  spanId: string,
  name: string,
  durationMs: number,
  startMs = 0
) {
  return {
    span_id: spanId,
    start_time_unix_nano: String(WINDOW_START + startMs * 1_000_000),
    end_time_unix_nano: String(
      WINDOW_START + (startMs + durationMs) * 1_000_000
    ),
    attributes: {
      "agentic.tool.type": "skill",
      "agentic.tool.name": name,
      "agentic.tool.call.arguments":
        '{"skill":"' + name + '","source":"command_path_fallback"}',
    },
  }
}

test("a native period overlapping its reads merges into them", () => {
  // The plugin derives its period span from the first read: 4 reads plus
  // the covering period count as 4 uses, not 5 (Test111 shape).
  const spans = [
    nativePeriod("native", "typesafe-ai", 500),
    skillRead("r1", "typesafe-ai", 50, 10),
    skillRead("r2", "typesafe-ai", 200, 10),
    skillRead("r3", "typesafe-ai", 400, 10),
    skillRead("r4", "typesafe-ai", 450, 0),
  ]
  const { counted, nativeSeen } = resolveCountedSkillSpans(spans)
  assert.deepEqual(
    counted.map((span) => span.span_id),
    ["r1", "r2", "r3", "r4"]
  )
  assert.deepEqual(nativeSeen, new Set(["typesafe-ai"]))
})

test("a native period without reads of its own counts once", () => {
  // openai-docs shape: the plugin reports the period but nothing read the
  // SKILL.md through a tool call.
  const spans = [nativePeriod("native", "openai-docs", 500)]
  const { counted, nativeSeen } = resolveCountedSkillSpans(spans)
  assert.deepEqual(
    counted.map((span) => span.span_id),
    ["native"]
  )
  assert.deepEqual(nativeSeen, new Set(["openai-docs"]))
})

test("a native period never merges reads of a different skill", () => {
  // The merge is per skill: the dws read keeps its count and the
  // typesafe-ai period still counts once (no typesafe-ai reads exist).
  const spans = [
    nativePeriod("native", "typesafe-ai", 500),
    skillRead("dws", "dws", 200, 10),
  ]
  const { counted, nativeSeen } = resolveCountedSkillSpans(spans)
  assert.deepEqual(
    counted.map((span) => span.span_id),
    ["native", "dws"]
  )
  assert.deepEqual(nativeSeen, new Set(["typesafe-ai"]))
})

test("a native period outside its skill's reads counts independently", () => {
  const spans = [
    nativePeriod("early", "visualize", 100, -400),
    skillRead("r1", "visualize", 1000, 10),
  ]
  const { counted } = resolveCountedSkillSpans(spans)
  assert.deepEqual(
    counted.map((span) => span.span_id),
    ["early", "r1"]
  )
})

test("the reported duration backs the end when it is missing", () => {
  // A native period may report its duration instead of an end timestamp.
  const spans = [
    {
      span_id: "native",
      start_time_unix_nano: String(WINDOW_START),
      attributes: {
        "agentic.tool.type": "skill",
        "agentic.tool.name": "x",
        "agentic.tool.duration_ms": 500,
      },
    },
    skillRead("r1", "x", 200, 10),
  ]
  const { counted } = resolveCountedSkillSpans(spans)
  assert.deepEqual(
    counted.map((span) => span.span_id),
    ["r1"]
  )
})

test("a fallback native period neither counts nor merges", () => {
  // The plugin-inferred period is rejected as evidence: the read re-derives
  // from the exec TOOL span, so it counts and the skill keeps the derived
  // badge on.
  const spans = [
    fallbackNativePeriod("period", "visualize", 500),
    skillRead("r1", "visualize", 100, 10),
  ]
  const { counted, nativeSeen } = resolveCountedSkillSpans(spans)
  assert.deepEqual(
    counted.map((span) => span.span_id),
    ["r1"]
  )
  assert.deepEqual(nativeSeen, new Set())
})

test("a plugin-reported derived call retains its provenance in merged usage", () => {
  // Native classification still identifies the reported period for merging;
  // only a non-derived report can remove the skill's derived marker.
  const attrs = {
    "agentic.tool.type": "skill",
    "agentic.tool.name": "x",
    "agentic.tool.derived": "true",
  }
  assert.equal(nativeSkillName(attrs), "x")
  const { nativeSeen } = resolveCountedSkillSpans([
    { span_id: "fb", attributes: attrs },
  ])
  assert.deepEqual(nativeSeen, new Set())
})

test("SKILL.md frontmatter splits into metadata pairs and a markdown body", () => {
  const { meta, body } = splitSkillFrontmatter(
    '---\nname: visualize\nlicense: MIT\ndescription: "Create things"\n---\n\n# Visualize\n\n- item'
  )
  assert.deepEqual(meta, [
    { key: "name", value: "visualize" },
    { key: "license", value: "MIT" },
    { key: "description", value: "Create things" },
  ])
  assert.equal(body, "# Visualize\n\n- item")
})

test("a SKILL.md without frontmatter renders whole as the body", () => {
  const { meta, body } = splitSkillFrontmatter("# Just markdown\n- a")
  assert.deepEqual(meta, [])
  assert.equal(body, "# Just markdown\n- a")
})

test("frontmatter without a closing fence stays body text", () => {
  const { meta, body } = splitSkillFrontmatter("---\nname: x\nno close")
  assert.deepEqual(meta, [])
  assert.equal(body, "---\nname: x\nno close")
})

test("folded block scalar joins its indented lines", () => {
  const { meta } = splitSkillFrontmatter(
    "---\nname: x\ndescription: >\n  Build things.\n  Proactively use them.\nlicense: MIT\n---\n\nBody"
  )
  assert.deepEqual(meta, [
    { key: "name", value: "x" },
    {
      key: "description",
      value: "Build things. Proactively use them.",
    },
    { key: "license", value: "MIT" },
  ])
})

test("literal block scalar keeps line breaks and strip indicators", () => {
  const { meta } = splitSkillFrontmatter(
    "---\nnotes: |-\n  first\n  second\nname: x\n---\n\nBody"
  )
  assert.deepEqual(meta, [
    { key: "notes", value: "first\nsecond" },
    { key: "name", value: "x" },
  ])
})

test("a leading H1 repeating the skill name is dropped from the body", () => {
  // Case-insensitive: the drawer title already shows the name; an oversized
  // `# Visualize` under the metadata card would only repeat it.
  assert.equal(
    stripRepeatedTitleHeading("# Visualize\n\n- item", "visualize"),
    "- item"
  )
})

test("a leading H1 differing from the skill name stays", () => {
  assert.equal(
    stripRepeatedTitleHeading("# Charts\n\n- item", "visualize"),
    "# Charts\n\n- item"
  )
})

test("a body without a leading H1 passes through unchanged", () => {
  assert.equal(
    stripRepeatedTitleHeading("- item\n- other", "visualize"),
    "- item\n- other"
  )
})

test("native trace search finds the visible Skill name and original tool title", () => {
  const { getNativeSpanSearchFields } = load<
    typeof import("../src/components/traces-otel/spanSearch.ts")
  >("components/traces-otel/spanSearch.ts")
  for (const attributes of [
    { ...TOOL, "agentic.tool.type": "skill", "agentic.tool.name": "deploy" },
    {
      ...TOOL,
      "agentic.tool.call.arguments": '{"file_path":"/skills/deploy/SKILL.md"}',
    },
  ]) {
    const span = {
      trace_id: "trace-a",
      span_id: "span-a",
      name: "read_file",
      attributes,
    }
    assert.deepEqual(getNativeSpanSearchFields(span, "deploy"), ["name"])
    assert.deepEqual(getNativeSpanSearchFields(span, "read_file"), ["name"])
    assert.deepEqual(getNativeSpanSearchFields(span, "other"), [])
  }
})

test("provenance totals use complete counters and never extrapolate from recent history", async () => {
  const { skillProvenanceCounts } = load<
    typeof import("../src/labs/skill/skillDisplay.ts")
  >("labs/skill/skillDisplay.ts")
  assert.deepEqual(
    skillProvenanceCounts({
      calls: 205,
      derived: false,
      provenance: { inferred: 201, reported: 4 },
    }),
    { inferred: 201, reported: 4 }
  )
  assert.deepEqual(skillProvenanceCounts({ calls: 205, derived: true }), {
    inferred: 205,
    reported: 0,
  })
  assert.equal(skillProvenanceCounts({ calls: 205, derived: false }), null)
  assert.equal(
    skillProvenanceCounts({
      calls: 205,
      derived: false,
      provenance: { inferred: 100, reported: 0 },
    }),
    null
  )
})

test("Skill trace exits retain source/window and only applicable Agent instance filters", () => {
  const { skillTraceHref } = load<{
    skillTraceHref: (
      search: string,
      call: {
        traceId: string
        spanId: string
        agentProduct: string
        sourceSystem?: string
      },
      product?: string
    ) => string
  }>("labs/skill/skillDisplay.ts")
  const search =
    "?spaceId=space-a&agent=Codex&agent_uuid=codex-instance&source_system=signoz&from=2026-10-26&to=2026-10-27&skill=deploy"
  const same = new URL(
    skillTraceHref(
      search,
      { traceId: "t", spanId: "s", agentProduct: "Codex" },
      "Codex"
    ),
    "https://test.invalid"
  ).searchParams
  assert.equal(same.get("agent_uuid"), "codex-instance")
  assert.equal(same.get("source_system"), "signoz")
  assert.equal(same.get("trace_from"), "2026-10-26")
  assert.equal(same.get("trace_to"), "2026-10-27")
  assert.equal(same.get("skill"), null)
  const other = new URL(
    skillTraceHref(
      search,
      { traceId: "other", spanId: "span", agentProduct: "ClaudeCode" },
      "Codex"
    ),
    "https://test.invalid"
  ).searchParams
  assert.equal(other.get("agent_uuid"), null)
  assert.equal(other.get("agent"), "ClaudeCode")
  assert.equal(other.get("spaceId"), "space-a")
})
