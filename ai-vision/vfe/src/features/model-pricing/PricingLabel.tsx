import type { ComponentProps } from "react"

export function PricingLabel({
  className = "",
  ...props
}: ComponentProps<"label">) {
  return <label className={`text-sm font-medium ${className}`} {...props} />
}
