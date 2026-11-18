import type { ComponentProps } from "react"
import { AlertDialogContent as PrimitiveContent } from "@/components/ui/alert-dialog"
import { cn } from "@/lib/utils"
import { dialogLayout } from "./dialogLayout"

import { useDialogFocus } from "./useDialogFocus"

export function AlertDialogContent({
  className,
  onOpenAutoFocus,
  onCloseAutoFocus,
  ...props
}: Omit<ComponentProps<typeof PrimitiveContent>, "size">) {
  const focusHandlers = useDialogFocus(onOpenAutoFocus, onCloseAutoFocus)
  return (
    <PrimitiveContent
      {...props}
      {...focusHandlers}
      size="default"
      data-dialog-size="compact"
      className={cn(
        dialogLayout("compact"),
        "data-[size=default]:max-w-[calc(100%-2rem)] data-[size=default]:sm:max-w-md",
        className
      )}
    />
  )
}
