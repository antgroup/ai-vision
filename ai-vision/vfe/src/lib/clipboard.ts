function copyWithSelection(text: string): boolean {
  if (typeof document.execCommand !== "function") return false

  const focused = document.activeElement
  const selection = document.getSelection()
  const ranges = selection
    ? Array.from({ length: selection.rangeCount }, (_, index) =>
        selection.getRangeAt(index).cloneRange()
      )
    : []
  const dialogs = document.querySelectorAll(
    'dialog[open], [aria-modal="true"], [role="dialog"][data-state="open"], [role="alertdialog"][data-state="open"]'
  )
  const container =
    focused?.closest('dialog, [role="dialog"], [role="alertdialog"]') ??
    dialogs[dialogs.length - 1] ??
    document.fullscreenElement ??
    document.body
  const field = document.createElement("textarea")
  field.value = " "
  field.readOnly = true
  field.tabIndex = -1
  field.setAttribute("aria-hidden", "true")
  field.style.cssText =
    "position:fixed;top:0;left:0;width:1px;height:1px;opacity:0;pointer-events:none"
  let wroteText = false
  // Keep raw content out of the temporary DOM node and preserve exact text.
  field.addEventListener("copy", (event) => {
    event.preventDefault()
    if (!event.clipboardData) return
    event.clipboardData.setData("text/plain", text)
    wroteText = true
  })
  container.append(field)
  try {
    field.focus({ preventScroll: true })
    if (document.activeElement !== field) return false
    field.select()
    return document.execCommand("copy") && wroteText
  } finally {
    field.remove()
    if (focused instanceof HTMLElement && focused.isConnected) {
      focused.focus({ preventScroll: true })
    }
    if (selection && ranges.length) {
      selection.removeAllRanges()
      ranges.forEach((range) => selection.addRange(range))
    }
  }
}

/** Call from a user action. Resolves only if the browser copy succeeds. */
export async function writeClipboardText(text: string): Promise<void> {
  try {
    const clipboard = navigator.clipboard
    if (typeof clipboard?.writeText === "function") {
      await clipboard.writeText(text)
      return
    }
  } catch {
    // A secure context can still reject clipboard access.
  }

  if (!copyWithSelection(text)) throw new Error("Clipboard write failed")
}
