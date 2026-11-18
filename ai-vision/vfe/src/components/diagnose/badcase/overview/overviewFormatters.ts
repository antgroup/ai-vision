/** 问句预览兜底:脏 JSON / 控制符 / 乱码 → 尝试取 question/query 字段,失败截断。 */
export function sanitizeQuestionPreview(raw: string | null, max = 60): string {
  // eslint-disable-next-line no-control-regex
  const s = (raw ?? "").replace(/[\x00-\x1f]+/g, " ").trim()
  if (!s) return "—"
  if (s.startsWith("{") || s.startsWith("[")) {
    try {
      const o = JSON.parse(s)
      const picked = o.question ?? o.query ?? o.content ?? null
      if (typeof picked === "string" && picked) return picked.slice(0, max)
    } catch {
      const m = s.match(/"(?:question|query)"\s*:\s*"([^"]+)"/)
      if (m) return m[1].slice(0, max)
    }
  }
  return s.slice(0, max)
}
