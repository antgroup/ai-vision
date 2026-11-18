/** Place readable call references independently from proportional durations. */
export function layoutCompareCalls<
  T extends { offsetMs: number | null; durationMs: number | null },
>(
  calls: readonly T[],
  axisMaxMs: number,
  availableWidth: number,
  includeCall: (call: T, index: number) => boolean = () => true
) {
  const width = Math.max(1, availableWidth)
  const axis = Number.isFinite(axisMaxMs) && axisMaxMs > 0 ? axisMaxMs : 1
  const clamp = (value: number) => Math.max(0, Math.min(width, value))
  const rows: number[] = []
  // Preserve source identity while omitting hidden/untimed calls before packing.
  return calls
    .flatMap((call, index) =>
      !includeCall(call, index) || call.offsetMs === null
        ? []
        : [{ ...call, offsetMs: call.offsetMs, index }]
    )
    .sort((a, b) => a.offsetMs - b.offsetMs || a.index - b.index)
    .map((call) => {
      const startX = clamp((call.offsetMs / axis) * width)
      const endX = clamp(
        ((call.offsetMs + Math.max(0, call.durationMs ?? 0)) / axis) * width
      )
      const markerWidth = Math.min(
        width,
        // Icon + gap + side letter + padding/borders, then sequence digits.
        Math.max(52, 40 + String(call.index + 1).length * 7)
      )
      const markerLeft = Math.max(
        0,
        Math.min(width - markerWidth, startX - markerWidth / 2)
      )
      const left = Math.min(startX, markerLeft)
      const right = Math.max(endX, markerLeft + markerWidth)
      let row = rows.findIndex((end) => end + 8 <= left)
      if (row < 0) row = rows.length
      rows[row] = right
      return {
        index: call.index,
        row,
        startX,
        endX,
        markerLeft,
        markerWidth,
      }
    })
    .sort((a, b) => a.index - b.index)
}
