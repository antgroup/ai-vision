import { useState } from "react"
import { useTranslation } from "react-i18next"
import { Check, Copy } from "lucide-react"
import { toast } from "sonner"

import { useSpace } from "@/contexts/SpaceContext"
import { api } from "@/lib/api"
import { copyInvitationLink } from "./invitationClipboard"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { DialogContent } from "@/components/dialogs/DialogContent"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSuccess?: () => void
  currentMemberCount?: number
}

interface CreatedInvitation {
  invitation_uuid: string
  email: string
  invite_url: string
}

export default function AddMemberSheet({
  open,
  onOpenChange,
  onSuccess,
}: Props) {
  const { t } = useTranslation()
  const { currentSpace } = useSpace()
  const [emails, setEmails] = useState("")
  const [role, setRole] = useState("3")
  const [submitting, setSubmitting] = useState(false)
  const [createdInvitations, setCreatedInvitations] = useState<
    CreatedInvitation[]
  >([])
  const [linksOpen, setLinksOpen] = useState(false)
  const [copiedInvitation, setCopiedInvitation] = useState<string | null>(null)

  function handleLinksOpenChange(nextOpen: boolean) {
    setLinksOpen(nextOpen)
    if (!nextOpen) {
      setCreatedInvitations([])
      setCopiedInvitation(null)
    }
  }

  async function copyInvitation(invitation: CreatedInvitation) {
    if (await copyInvitationLink(invitation.invite_url)) {
      setCopiedInvitation(invitation.invitation_uuid)
      toast.success(t("spaceSettings.invite.copied"))
    } else {
      setCopiedInvitation(null)
      toast.warning(t("spaceSettings.invite.copyUnavailable"))
    }
  }

  async function submit() {
    const values = emails
      .split(/[\s,;]+/)
      .map((email) => email.trim().toLowerCase())
      .filter(Boolean)
    if (!currentSpace || values.length === 0) return
    setSubmitting(true)
    try {
      const response = await api.post<{
        code: number
        data: { items: CreatedInvitation[] }
      }>(`/spaces/${currentSpace.id}/invitations`, {
        emails: values,
        role:
          role === "1" && !currentSpace.can_manage_admins ? 3 : Number(role),
      })
      setCreatedInvitations(response.data.items)
      setEmails("")
      onOpenChange(false)
      setLinksOpen(true)
      onSuccess?.()
    } catch (reason) {
      toast.error(
        reason instanceof Error
          ? t(`spaceSettings.owner.errors.${reason.message}`, {
              defaultValue: reason.message,
            })
          : t("spaceSettings.invite.createFailed")
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <>
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent>
          <SheetHeader>
            <SheetTitle>
              {t("spaceSettings.invite.title", { space: currentSpace?.name })}
            </SheetTitle>
            <SheetDescription>
              {t("spaceSettings.invite.description")}
            </SheetDescription>
          </SheetHeader>
          <div className="space-y-4 px-4 py-6">
            <Input
              aria-label={t("spaceSettings.page.email")}
              value={emails}
              onChange={(event) => setEmails(event.target.value)}
              placeholder="alice@example.com, bob@example.com"
            />
            <Select value={role} onValueChange={setRole}>
              <SelectTrigger aria-label={t("spaceSettings.members.role")}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {currentSpace?.can_manage_admins && (
                  <SelectItem value="1">
                    {t("spaceSettings.roles.admin")}
                  </SelectItem>
                )}
                <SelectItem value="2">
                  {t("spaceSettings.roles.developer")}
                </SelectItem>
                <SelectItem value="3">
                  {t("spaceSettings.roles.member")}
                </SelectItem>
              </SelectContent>
            </Select>
          </div>
          <SheetFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              {t("common.cancel")}
            </Button>
            <Button onClick={submit} disabled={submitting || !emails.trim()}>
              {submitting
                ? t("spaceSettings.invite.sending")
                : t("spaceSettings.invite.generate")}
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>

      <Dialog open={linksOpen} onOpenChange={handleLinksOpenChange}>
        <DialogContent size="form">
          <DialogHeader>
            <DialogTitle>{t("spaceSettings.invite.generated")}</DialogTitle>
            <DialogDescription>
              {t("spaceSettings.invite.expiry")}
            </DialogDescription>
          </DialogHeader>
          <div className="max-h-[50vh] space-y-4 overflow-y-auto py-2">
            {createdInvitations.map((invitation) => (
              <div key={invitation.invitation_uuid} className="space-y-2">
                <p className="truncate text-sm font-medium">
                  {invitation.email}
                </p>
                <div className="flex gap-2">
                  <Input
                    readOnly
                    value={invitation.invite_url}
                    onFocus={(event) => event.currentTarget.select()}
                  />
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    aria-label={t("spaceSettings.invite.copyAria", {
                      email: invitation.email,
                    })}
                    onClick={() => void copyInvitation(invitation)}
                  >
                    {copiedInvitation === invitation.invitation_uuid ? (
                      <Check className="size-4" />
                    ) : (
                      <Copy className="size-4" />
                    )}
                  </Button>
                </div>
              </div>
            ))}
          </div>
          <DialogFooter>
            <Button onClick={() => handleLinksOpenChange(false)}>
              {t("spaceSettings.invite.done")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
