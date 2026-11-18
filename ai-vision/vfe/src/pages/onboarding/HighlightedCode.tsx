import { useMemo, type ReactNode } from "react"
import { pythonLanguage } from "@codemirror/lang-python"
import { typescriptLanguage } from "@codemirror/lang-javascript"
import { StreamLanguage } from "@codemirror/language"
import { shell } from "@codemirror/legacy-modes/mode/shell"
import { go } from "@codemirror/legacy-modes/mode/go"
import { classHighlighter, highlightTree } from "@lezer/highlight"

export type CodeLanguage = "python" | "typescript" | "go" | "shell" | "dotenv"

const shellParser = StreamLanguage.define(shell).parser
const parsers = {
  python: pythonLanguage.parser,
  typescript: typescriptLanguage.parser,
  go: StreamLanguage.define(go).parser,
  shell: shellParser,
  dotenv: shellParser,
}

export function HighlightedCode({
  value,
  language,
  label,
}: {
  value: string
  language: CodeLanguage
  label: string
}) {
  const content = useMemo(() => {
    const nodes: ReactNode[] = []
    let cursor = 0
    highlightTree(
      parsers[language].parse(value),
      classHighlighter,
      (from, to, classes) => {
        if (from > cursor) nodes.push(value.slice(cursor, from))
        nodes.push(
          <span key={from} className={classes}>
            {value.slice(from, to)}
          </span>
        )
        cursor = to
      }
    )
    if (cursor < value.length) nodes.push(value.slice(cursor))
    return nodes
  }, [language, value])

  return (
    <pre
      className="max-h-96 overflow-auto p-4 text-xs leading-5 text-foreground [&_.tok-atom]:text-amber-700 dark:[&_.tok-atom]:text-amber-300 [&_.tok-bool]:text-amber-700 dark:[&_.tok-bool]:text-amber-300 [&_.tok-comment]:text-muted-foreground [&_.tok-keyword]:text-violet-700 dark:[&_.tok-keyword]:text-violet-300 [&_.tok-meta]:text-sky-700 dark:[&_.tok-meta]:text-sky-300 [&_.tok-number]:text-amber-700 dark:[&_.tok-number]:text-amber-300 [&_.tok-propertyName]:text-sky-700 dark:[&_.tok-propertyName]:text-sky-300 [&_.tok-string]:text-emerald-700 dark:[&_.tok-string]:text-emerald-300 [&_.tok-string2]:text-emerald-700 dark:[&_.tok-string2]:text-emerald-300 [&_.tok-typeName]:text-sky-700 dark:[&_.tok-typeName]:text-sky-300 [&_.tok-variableName2]:text-sky-700 dark:[&_.tok-variableName2]:text-sky-300"
      data-language={language}
      aria-label={label}
      tabIndex={0}
    >
      <code className="font-mono">{content}</code>
    </pre>
  )
}
