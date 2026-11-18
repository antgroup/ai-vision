import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { resolve } from "node:path"

const indexHtml = readFileSync(
  resolve(import.meta.dirname, "../index.html"),
  "utf8"
)

assert.match(
  indexHtml,
  /<html\s+[^>]*translate=["']no["']/i,
  "the document root must opt out of browser translation"
)
assert.match(
  indexHtml,
  /<meta\s+name=["']google["']\s+content=["']notranslate["']/i,
  "the Google translation opt-out must be present"
)
assert.match(
  indexHtml,
  /<div(?=[^>]*\bid=["']root["'])(?=[^>]*\btranslate=["']no["'])[^>]*>/i,
  "the React root must opt out of browser translation"
)

console.log("browser DOM protection checks passed")
