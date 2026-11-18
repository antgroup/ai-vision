import { cn } from "@/lib/utils"

const widths = {
  compact: "sm:max-w-md",
  form: "sm:max-w-xl",
  detail: "sm:max-w-3xl",
  workspace: "sm:max-w-5xl",
  fullscreen: "sm:max-w-none",
} as const

export type DialogSize = keyof typeof widths

/** Keep the same 16 px clearance, including on short/dynamic viewports. */
export function dialogLayout(size: DialogSize) {
  return cn(
    "max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] max-w-[calc(100%-2rem)] min-w-0 overflow-y-auto overscroll-contain",
    widths[size],
    size === "fullscreen" && "h-[calc(100dvh-2rem)]"
  )
}
