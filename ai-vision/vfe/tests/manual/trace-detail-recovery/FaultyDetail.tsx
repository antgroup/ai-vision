import type { ComponentProps } from "react"
import { SpanDetailPanel as OriginalDetail } from "@/components/traces-otel/SpanDetailPanel"
import { fault } from "./fault"

export function SpanDetailPanel(props: ComponentProps<typeof OriginalDetail>) {
  if (props.spanId === "fault" && fault.armed) {
    throw new Error("Controlled render fault; synthetic acceptance only")
  }
  return <OriginalDetail {...props} />
}
