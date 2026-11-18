import { writeClipboardText } from "@/lib/clipboard"
/** Clipboard access is optional: the invitation remains usable if denied. */
export async function copyInvitationLink(url: string): Promise<boolean> {
  try {
    await writeClipboardText(url)
    return true
  } catch {
    return false
  }
}
