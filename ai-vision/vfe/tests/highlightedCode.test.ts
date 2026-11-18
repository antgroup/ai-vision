import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import { createElement, type ComponentType } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { JSDOM } from "jsdom"
import ts from "typescript"

const require = createRequire(import.meta.url)
const exports: Record<string, unknown> = {}
const source = readFileSync(
  new URL("../src/pages/onboarding/HighlightedCode.tsx", import.meta.url),
  "utf8"
)
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText
new Function("require", "exports", compiled)(require, exports)
const HighlightedCode = exports.HighlightedCode as ComponentType<{
  value: string
  language: string
  label: string
}>

for (const [language, value] of [
  [
    "go",
    '// Preserve Go source\npackage main\nimport "fmt"\nfunc main() { fmt.Println("<script>not markup</script>") }\n',
  ],
  [
    "python",
    '# Keep business callbacks\nfrom langfuse import get_client\ntext = """line one\n<script>unsafe()</script>\nline three"""\nwith get_client().start_as_current_observation(name="request"):\n    print(42)\n',
  ],
  [
    "shell",
    'pip install "langfuse>=4.16.0,<5" \\\n  "langchain>=1.4.3,<2"\n# Set in this terminal\nexport LANGFUSE_SECRET_KEY="<SPACE_KEY>"\n',
  ],
  [
    "dotenv",
    '# Loaded by the application\nLANGFUSE_PUBLIC_KEY="<AGENT_UUID>"\nLANGFUSE_SECRET_KEY="<SPACE_KEY>"\n',
  ],
  [
    "typescript",
    '// A typed callback\nconst enabled: boolean = true;\nconsole.log("<script>not markup</script>");\n',
  ],
]) {
  test(`${language} is highlighted without changing code text or interpreting markup`, () => {
    const dom = new JSDOM(
      renderToStaticMarkup(
        createElement(HighlightedCode, { value, language, label: "Example" })
      )
    )
    try {
      const pre = dom.window.document.querySelector("pre")!
      assert.equal(pre.dataset.language, language)
      assert.equal(pre.textContent, value)
      assert.ok(
        pre.querySelector('span[class^="tok-"]'),
        "actual grammar tokens are styled"
      )
      assert.ok(
        pre.querySelector(".tok-comment"),
        "comments remain distinguishable"
      )
      assert.ok(
        pre.querySelector(".tok-string"),
        "strings remain distinguishable"
      )
      assert.equal(pre.querySelector("script"), null)
      assert.equal(pre.tabIndex, 0, "long code remains keyboard-scrollable")
    } finally {
      dom.window.close()
    }
  })
}
