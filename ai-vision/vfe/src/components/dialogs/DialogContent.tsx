import type { ComponentProps } from "react"
import { DialogContent as PrimitiveContent } from "@/components/ui/dialog"
import { cn } from "@/lib/utils"
import { dialogLayout, type DialogSize } from "./dialogLayout"

import { useDialogFocus } from "./useDialogFocus"

type DialogContentProps = ComponentProps<typeof PrimitiveContent> & {
  size?: DialogSize
  /** A bounded workspace for panes that manage their own scrolling. */
  fillHeight?: boolean
}

export function DialogContent({
  size = "form",
  fillHeight = false,
  className,
  onOpenAutoFocus,
  onCloseAutoFocus,
  ...props
}: DialogContentProps) {
  const focusHandlers = useDialogFocus(onOpenAutoFocus, onCloseAutoFocus)
  return (
    <PrimitiveContent
      data-dialog-size={size}
      className={cn(
        dialogLayout(size),
        fillHeight && "h-[min(52rem,calc(100dvh-2rem))]",
        className
      )}
      {...props}
      {...focusHandlers}
    />
  )
}
