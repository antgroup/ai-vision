import {
  javascriptLanguage,
  jsxLanguage,
  tsxLanguage,
  typescriptLanguage,
} from "@codemirror/lang-javascript"
import { jsonLanguage } from "@codemirror/lang-json"
import { markdownLanguage } from "@codemirror/lang-markdown"
import { pythonLanguage } from "@codemirror/lang-python"
import { StreamLanguage } from "@codemirror/language"
import { c, cpp, csharp, java } from "@codemirror/legacy-modes/mode/clike"
import { css } from "@codemirror/legacy-modes/mode/css"
import { diff } from "@codemirror/legacy-modes/mode/diff"
import { go } from "@codemirror/legacy-modes/mode/go"
import { ruby } from "@codemirror/legacy-modes/mode/ruby"
import { rust } from "@codemirror/legacy-modes/mode/rust"
import { shell } from "@codemirror/legacy-modes/mode/shell"
import { standardSQL } from "@codemirror/legacy-modes/mode/sql"
import { html, xml } from "@codemirror/legacy-modes/mode/xml"
import { yaml } from "@codemirror/legacy-modes/mode/yaml"
import { classHighlighter, highlightTree } from "@lezer/highlight"

const parsers = new Map([
  ["c", StreamLanguage.define(c).parser],
  ["cpp", StreamLanguage.define(cpp).parser],
  ["csharp", StreamLanguage.define(csharp).parser],
  ["css", StreamLanguage.define(css).parser],
  ["diff", StreamLanguage.define(diff).parser],
  ["go", StreamLanguage.define(go).parser],
  ["html", StreamLanguage.define(html).parser],
  ["java", StreamLanguage.define(java).parser],
  ["javascript", javascriptLanguage.parser],
  ["json", jsonLanguage.parser],
  ["jsx", jsxLanguage.parser],
  ["markdown", markdownLanguage.parser],
  ["python", pythonLanguage.parser],
  ["ruby", StreamLanguage.define(ruby).parser],
  ["rust", StreamLanguage.define(rust).parser],
  ["shell", StreamLanguage.define(shell).parser],
  ["sql", StreamLanguage.define(standardSQL).parser],
  ["tsx", tsxLanguage.parser],
  ["typescript", typescriptLanguage.parser],
  ["xml", StreamLanguage.define(xml).parser],
  ["yaml", StreamLanguage.define(yaml).parser],
])

export function traceCodeTokens(value: string, language: string) {
  const tokens: { from: number; to: number; classes: string }[] = []
  const parser = parsers.get(language)
  if (parser) {
    highlightTree(
      parser.parse(value),
      classHighlighter,
      (from, to, classes) => {
        tokens.push({ from, to, classes })
      }
    )
  }
  return tokens
}
