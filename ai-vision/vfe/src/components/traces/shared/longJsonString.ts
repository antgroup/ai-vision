/** Find a long JSON string without a repeated-alternative regexp stack. */
export function longJsonString(line: string): [string, string, string] | null {
  let start = 0
  while (/\s/.test(line[start] ?? "") && start < line.length) start++
  if (line[start] !== '"') return null
  const endOfString = (from: number) => {
    for (let i = from + 1; i < line.length; i++) {
      if (line[i] === "\\") i++
      else if (line[i] === '"') return i
    }
    return -1
  }
  let end = endOfString(start)
  if (end < 0) return null
  let next = end + 1
  while (next < line.length && /\s/.test(line[next])) next++
  if (line[next] === ":") {
    start = next + 1
    while (start < line.length && /\s/.test(line[start])) start++
    if (line[start] !== '"') return null
    end = endOfString(start)
  }
  if (end < 0 || end - start <= 201) return null
  return [line.slice(0, start), line.slice(start, end + 1), line.slice(end + 1)]
}
