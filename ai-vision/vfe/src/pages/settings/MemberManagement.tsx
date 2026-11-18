import { useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import {
  Copy,
  Search,
  Users,
  UserPlus,
  LogOut,
  RefreshCw,
  UserMinus,
  X,
} from "lucide-react"
import { toast } from "sonner"

import { api, ApiBusinessError } from "@/lib/api"
import { useSpace } from "@/contexts/SpaceContext"
import { useUser } from "@/contexts/UserContext"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { AlertDialogContent } from "@/components/dialogs/AlertDialogContent"
import AddMemberSheet from "./AddMemberSheet"
import { copyInvitationLink } from "./invitationClipboard"

// ── Types ──

interface SpaceMember {
  user_uuid: string
  name: string
  email: string
  avatar_url: string | null
  account_status: "active" | "pending"
  is_owner: boolean
  role: number
  joined_at: string
}

interface SpaceInvitation {
  invitation_uuid: string
  email: string
  role: number
  status: string
  expires_at: string
  invited_by_uuid: string
}

interface InvitationsResponse {
  code: number
  data: { items: SpaceInvitation[] }
}

interface MembersResponse {
  code: number
  data: {
    items: SpaceMember[]
    total: number
    role: number | null
  }
}

// ── Constants ──

const ROLE_VARIANTS: Record<number, "default" | "secondary" | "outline"> = {
  1: "default",
  2: "secondary",
  3: "outline",
}

const PAGE_SIZE = 20

// ── Helpers ──

function extractServerMessage(err: unknown): string | null {
  if (err instanceof ApiBusinessError) return err.message || null
  if (!(err instanceof Error)) return null
  const match = err.message.match(/-- (.+)$/)
  if (!match) return null
  try {
    const body = JSON.parse(match[1])
    if (typeof body.message === "string" && body.message) return body.message
  } catch {
    // not JSON
  }
  return null
}

// ── Component ──

export default function MemberManagement() {
  const { t, i18n } = useTranslation()
  const ownerError = (message: string) =>
    t(`spaceSettings.owner.errors.${message}`, { defaultValue: message })

  const roleLabels: Record<number, string> = {
    1: t("spaceSettings.roles.admin"),
    2: t("spaceSettings.roles.developer"),
    3: t("spaceSettings.roles.member"),
  }
  const roleOptions = [1, 2, 3].map((role) => ({
    value: String(role),
    label: roleLabels[role],
  }))
  const { currentSpace, refreshSpaces } = useSpace()
  const { user } = useUser()
  const spaceId = currentSpace?.id ?? ""
  const queryClient = useQueryClient()

  const [search, setSearch] = useState("")
  const [roleFilter, setRoleFilter] = useState<string>("all")
  const [page, setPage] = useState(1)
  const [addSheetOpen, setAddSheetOpen] = useState(false)
  const [invitationLinks, setInvitationLinks] = useState<
    Record<string, string>
  >({})
  const [invitationAction, setInvitationAction] = useState<string | null>(null)

  // Confirmation dialog state
  const [confirmDialog, setConfirmDialog] = useState<{
    type: "role-change" | "remove" | "exit" | "transfer"
    member?: SpaceMember
    newRole?: number
  } | null>(null)
  const [actionLoading, setActionLoading] = useState(false)
  const [dialogError, setDialogError] = useState<string | null>(null)
  const isLastAdminError = dialogError === "LAST_ADMIN_REQUIRED"

  const {
    data: membersData,
    isLoading,
    isError,
  } = useQuery({
    queryKey: ["space-members", spaceId],
    queryFn: () => api.get<MembersResponse>(`/spaces/${spaceId}/members`),
    enabled: !!spaceId,
  })

  const refreshMembers = () => {
    queryClient.invalidateQueries({ queryKey: ["space-members", spaceId] })
    queryClient.invalidateQueries({ queryKey: ["settings", spaceId] })
    queryClient.invalidateQueries({ queryKey: ["space-invitations", spaceId] })
  }

  const handleAddSuccess = () => {
    setSearch("")
    setRoleFilter("all")
    setPage(1)
    refreshMembers()
  }

  const members = useMemo(
    () => (membersData?.code === 0 ? membersData.data.items : []),
    [membersData]
  )
  const myRole = membersData?.code === 0 ? membersData.data.role : null
  const isAdmin = myRole === 1
  const isOwner = members.some(
    (m) => m.user_uuid === user?.user_uuid && m.is_owner
  )
  const assignableRoles = roleOptions.filter(
    (opt) => isOwner || opt.value !== "1"
  )
  const canManageMember = (member: SpaceMember) =>
    isAdmin && !member.is_owner && (isOwner || member.role !== 1)
  const currentUserId = user?.user_uuid

  const { data: invitationsData } = useQuery({
    queryKey: ["space-invitations", spaceId],
    queryFn: () =>
      api.get<InvitationsResponse>(`/spaces/${spaceId}/invitations`),
    enabled: !!spaceId && isAdmin,
  })
  const pendingInvitations = useMemo(
    () =>
      invitationsData?.code === 0
        ? invitationsData.data.items.filter((item) => item.status === "Pending")
        : [],
    [invitationsData]
  )

  const filteredMembers = useMemo(() => {
    let result = members

    if (roleFilter !== "all") {
      const roleNum = Number(roleFilter)
      result = result.filter((m) => m.role === roleNum)
    }

    if (search.trim().length >= 2) {
      const q = search.trim().toLowerCase()
      result = result.filter(
        (m) =>
          m.name.toLowerCase().includes(q) || m.email.toLowerCase().includes(q)
      )
    }

    return result
  }, [members, roleFilter, search])

  const totalPages = Math.max(1, Math.ceil(filteredMembers.length / PAGE_SIZE))
  const pagedMembers = filteredMembers.slice(
    (page - 1) * PAGE_SIZE,
    page * PAGE_SIZE
  )

  const handleSearchChange = (value: string) => {
    setSearch(value)
    setPage(1)
  }

  const handleRoleFilterChange = (value: string) => {
    setRoleFilter(value)
    setPage(1)
  }

  const copyLink = async (link: string) => {
    if (await copyInvitationLink(link)) {
      toast.success(t("spaceSettings.invite.copied"))
    } else {
      toast.warning(t("spaceSettings.invite.copyUnavailable"))
    }
  }

  const regenerateInvitationLink = async (invitation: SpaceInvitation) => {
    setInvitationAction(invitation.invitation_uuid)
    try {
      const response = await api.post<{
        code: number
        data: { invite_url: string }
      }>(`/spaces/${spaceId}/invitations/${invitation.invitation_uuid}/resend`)
      setInvitationLinks((current) => ({
        ...current,
        [invitation.invitation_uuid]: response.data.invite_url,
      }))
      refreshMembers()
      if (await copyInvitationLink(response.data.invite_url)) {
        toast.success(t("spaceSettings.invite.regenerated"))
      } else {
        toast.warning(t("spaceSettings.invite.generatedCopyUnavailable"))
      }
    } catch (reason) {
      toast.error(
        reason instanceof Error
          ? ownerError(reason.message)
          : t("spaceSettings.invite.regenerateFailed")
      )
    } finally {
      setInvitationAction(null)
    }
  }

  const revokePendingInvitation = async (invitation: SpaceInvitation) => {
    setInvitationAction(invitation.invitation_uuid)
    try {
      await api.post(
        `/spaces/${spaceId}/invitations/${invitation.invitation_uuid}/revoke`
      )
      setInvitationLinks((current) => {
        const next = { ...current }
        delete next[invitation.invitation_uuid]
        return next
      })
      toast.success(t("spaceSettings.invite.revoked"))
      refreshMembers()
    } catch (reason) {
      toast.error(
        reason instanceof Error
          ? ownerError(reason.message)
          : t("spaceSettings.invite.revokeFailed")
      )
    } finally {
      setInvitationAction(null)
    }
  }

  const executeTransfer = async () => {
    if (!confirmDialog?.member) return
    setActionLoading(true)
    setDialogError(null)
    try {
      await api.post(`/spaces/${spaceId}/owner/transfer`, {
        target_user_uuid: confirmDialog.member.user_uuid,
      })
      void refreshSpaces()
      refreshMembers()
      setInvitationLinks({})
      setConfirmDialog(null)
      toast.success(t("spaceSettings.owner.transferred"))
    } catch (error) {
      setDialogError(
        extractServerMessage(error) || t("spaceSettings.members.networkError")
      )
      refreshMembers()
    } finally {
      setActionLoading(false)
    }
  }

  // ── Role Change ──

  const handleRoleSelectChange = (member: SpaceMember, newRole: string) => {
    const roleNum = Number(newRole)
    if (roleNum === member.role) return
    setConfirmDialog({ type: "role-change", member, newRole: roleNum })
  }

  const executeRoleChange = async () => {
    if (
      !confirmDialog?.member ||
      confirmDialog.newRole === null ||
      confirmDialog.newRole === undefined
    )
      return
    setActionLoading(true)
    setDialogError(null)
    try {
      const res = await api.patch<{ code: number; message?: string }>(
        `/spaces/${spaceId}/members/${confirmDialog.member.user_uuid}`,
        {
          role: confirmDialog.newRole,
          expected_role: confirmDialog.member.role,
        }
      )
      if (res.code === 0) {
        toast.success(
          t("spaceSettings.members.roleUpdated", {
            name: confirmDialog.member.name,
            role: roleLabels[confirmDialog.newRole],
          })
        )
        refreshMembers()
        setConfirmDialog(null)
      } else {
        setDialogError(
          res.message || t("spaceSettings.members.roleUpdateFailed")
        )
        refreshMembers()
      }
    } catch (err) {
      setDialogError(
        extractServerMessage(err) || t("spaceSettings.members.networkError")
      )
      refreshMembers()
    } finally {
      setActionLoading(false)
    }
  }

  // ── Remove Member ──

  const handleRemoveClick = (member: SpaceMember) => {
    setConfirmDialog({ type: "remove", member })
  }

  const executeRemove = async () => {
    if (!confirmDialog?.member) return
    setActionLoading(true)
    setDialogError(null)
    try {
      const res = await api.delete<{ code: number; message?: string }>(
        `/spaces/${spaceId}/members/${confirmDialog.member.user_uuid}`
      )
      if (res.code === 0) {
        toast.success(
          t("spaceSettings.members.removed", {
            name: confirmDialog.member.name,
          })
        )
        refreshMembers()
        setConfirmDialog(null)
      } else {
        setDialogError(res.message || t("spaceSettings.members.removeFailed"))
        refreshMembers()
      }
    } catch (err) {
      setDialogError(
        extractServerMessage(err) || t("spaceSettings.members.networkError")
      )
    } finally {
      setActionLoading(false)
    }
  }

  // ── Exit Space ──

  const handleExitClick = () => {
    setConfirmDialog({ type: "exit" })
  }

  const executeExit = async () => {
    setActionLoading(true)
    setDialogError(null)
    try {
      const res = await api.post<{ code: number; message?: string }>(
        `/spaces/${spaceId}/leave`
      )
      if (res.code === 0) {
        toast.success(t("spaceSettings.members.left"))
        localStorage.removeItem("selectedSpaceId")
        window.location.href = "/"
      } else {
        setDialogError(res.message || t("spaceSettings.members.leaveFailed"))
      }
    } catch (err) {
      const message = extractServerMessage(err)
      setDialogError(message || t("spaceSettings.members.networkError"))
    } finally {
      setActionLoading(false)
    }
  }

  // ── Loading ──
  if (isLoading) {
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-3">
          <Skeleton className="h-9 w-64" />
          <Skeleton className="h-9 w-48" />
        </div>
        <div className="space-y-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-14 w-full" />
          ))}
        </div>
      </div>
    )
  }

  // ── Error ──
  if (isError) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <p className="text-sm text-destructive">
          {t("spaceSettings.members.loadFailed")}
        </p>
      </div>
    )
  }

  // ── Main ──
  return (
    <div className="space-y-4">
      {isAdmin && pendingInvitations.length > 0 && (
        <div className="space-y-3 rounded-xl border px-4 py-3">
          <div>
            <h3 className="text-sm font-medium">
              {t("spaceSettings.invite.pending", {
                count: pendingInvitations.length,
              })}
            </h3>
            <p className="mt-1 text-xs text-muted-foreground">
              {t("spaceSettings.invite.notStored")}
            </p>
          </div>
          {pendingInvitations.map((invitation) => {
            const regeneratedLink = invitationLinks[invitation.invitation_uuid]
            const actionPending =
              invitationAction === invitation.invitation_uuid
            return (
              <div
                key={invitation.invitation_uuid}
                className="space-y-2 rounded-lg bg-muted/30 p-3"
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">
                      {invitation.email}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {t("spaceSettings.invite.expires", {
                        role: roleLabels[invitation.role],
                        time: new Date(invitation.expires_at).toLocaleString(
                          i18n.language
                        ),
                      })}
                    </p>
                  </div>
                  <div className="flex gap-2">
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={
                        actionPending ||
                        (invitation.role === 1 &&
                          (!isOwner ||
                            invitation.invited_by_uuid !==
                              currentSpace?.owner_uuid))
                      }
                      onClick={() => void regenerateInvitationLink(invitation)}
                      title={
                        invitation.role === 1 &&
                        invitation.invited_by_uuid !== currentSpace?.owner_uuid
                          ? t(
                              "spaceSettings.owner.errors.INVITATION_AUTHORITY_LOST"
                            )
                          : undefined
                      }
                    >
                      <RefreshCw className="mr-1.5 size-3.5" />
                      {t("spaceSettings.invite.regenerate")}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      disabled={
                        actionPending || (invitation.role === 1 && !isOwner)
                      }
                      onClick={() => void revokePendingInvitation(invitation)}
                    >
                      <X className="mr-1.5 size-3.5" />
                      {t("spaceSettings.invite.revoke")}
                    </Button>
                  </div>
                </div>
                {regeneratedLink && (
                  <div className="flex gap-2">
                    <Input
                      readOnly
                      value={regeneratedLink}
                      onFocus={(event) => event.currentTarget.select()}
                    />
                    <Button
                      type="button"
                      variant="outline"
                      size="icon"
                      aria-label={t("spaceSettings.invite.copyAria", {
                        email: invitation.email,
                      })}
                      onClick={() => void copyLink(regeneratedLink)}
                    >
                      <Copy className="size-4" />
                    </Button>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* Toolbar: search + filter + add */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative w-64">
          <Search className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder={t("spaceSettings.members.search")}
            value={search}
            onChange={(e) => handleSearchChange(e.target.value)}
            className="pl-9"
          />
        </div>

        <Select value={roleFilter} onValueChange={handleRoleFilterChange}>
          <SelectTrigger className="h-9 w-36 text-sm">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">
              {t("spaceSettings.members.all", { count: members.length })}
            </SelectItem>
            <SelectItem value="1">
              {t("spaceSettings.members.roleCount", {
                role: roleLabels[1],
                count: members.filter((m) => m.role === 1).length,
              })}
            </SelectItem>
            <SelectItem value="2">
              {t("spaceSettings.members.roleCount", {
                role: roleLabels[2],
                count: members.filter((m) => m.role === 2).length,
              })}
            </SelectItem>
            <SelectItem value="3">
              {t("spaceSettings.members.roleCount", {
                role: roleLabels[3],
                count: members.filter((m) => m.role === 3).length,
              })}
            </SelectItem>
          </SelectContent>
        </Select>

        {isAdmin && (
          <Button size="sm" onClick={() => setAddSheetOpen(true)}>
            <UserPlus className="mr-1.5 size-4" />
            {t("spaceSettings.members.invite")}
          </Button>
        )}
      </div>

      {/* Table */}
      {filteredMembers.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <Users className="mb-3 size-10 text-muted-foreground/50" />
          <p className="text-sm text-muted-foreground">
            {search || roleFilter !== "all"
              ? t("spaceSettings.members.noMatch")
              : t("spaceSettings.members.empty")}
          </p>
        </div>
      ) : (
        <>
          <Table className="w-max min-w-full">
            <TableHeader>
              <TableRow>
                <TableHead className="whitespace-nowrap">
                  {t("spaceSettings.members.member")}
                </TableHead>
                <TableHead className="sticky right-[240px] z-10 min-w-[120px] bg-background whitespace-nowrap shadow-[-8px_0_6px_-6px_rgba(0,0,0,0.08)]">
                  {t("spaceSettings.members.role")}
                </TableHead>
                <TableHead className="sticky right-[120px] z-10 min-w-[120px] bg-background whitespace-nowrap">
                  {t("spaceSettings.members.joinedAt")}
                </TableHead>
                <TableHead className="sticky right-0 z-10 min-w-[120px] bg-background whitespace-nowrap">
                  {t("spaceSettings.members.actions")}
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {pagedMembers.map((member) => {
                const subline = member.email
                const avatarSrc = member.avatar_url || ""
                const initial = (member.name || "U").slice(0, 1).toUpperCase()
                const isSelf = member.user_uuid === currentUserId

                return (
                  <TableRow key={member.user_uuid}>
                    <TableCell className="whitespace-nowrap">
                      <div className="flex items-center gap-3">
                        <Avatar className="size-9 shrink-0">
                          {avatarSrc && (
                            <AvatarImage src={avatarSrc} alt={member.name} />
                          )}
                          <AvatarFallback>{initial}</AvatarFallback>
                        </Avatar>
                        <div>
                          <div className="text-sm font-medium">
                            {member.name}
                            {member.is_owner && (
                              <Badge className="ml-2" variant="outline">
                                {t("spaceSettings.owner.label")}
                              </Badge>
                            )}
                            {isSelf && (
                              <span className="ml-1.5 text-xs text-muted-foreground">
                                {t("spaceSettings.members.me")}
                              </span>
                            )}
                            {member.account_status === "pending" && (
                              <Badge
                                variant="secondary"
                                className="ml-2 font-normal"
                              >
                                {t("spaceSettings.members.pending")}
                              </Badge>
                            )}
                          </div>
                          <div className="text-xs text-muted-foreground">
                            {subline}
                          </div>
                        </div>
                      </div>
                    </TableCell>
                    <TableCell className="sticky right-[240px] z-10 min-w-[120px] bg-background shadow-[-8px_0_6px_-6px_rgba(0,0,0,0.08)]">
                      {canManageMember(member) && !isSelf ? (
                        <Select
                          value={String(member.role)}
                          onValueChange={(v) =>
                            handleRoleSelectChange(member, v)
                          }
                        >
                          <SelectTrigger className="h-7 w-24 text-xs">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {assignableRoles.map((opt) => (
                              <SelectItem key={opt.value} value={opt.value}>
                                {opt.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      ) : (
                        <Badge
                          variant={ROLE_VARIANTS[member.role] ?? "outline"}
                          className="font-normal"
                        >
                          {roleLabels[member.role] ??
                            t("spaceSettings.roles.unknown")}
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="sticky right-[120px] z-10 min-w-[120px] bg-background whitespace-nowrap text-muted-foreground">
                      {new Date(member.joined_at).toLocaleDateString(
                        i18n.language
                      )}
                    </TableCell>
                    <TableCell className="sticky right-0 z-10 min-w-[120px] bg-background">
                      {isOwner &&
                        !isSelf &&
                        member.account_status === "active" && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() =>
                              setConfirmDialog({ type: "transfer", member })
                            }
                          >
                            {t("spaceSettings.owner.transfer")}
                          </Button>
                        )}
                      {isSelf ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 text-xs text-muted-foreground hover:text-destructive"
                          onClick={handleExitClick}
                          disabled={member.is_owner}
                          title={
                            member.is_owner
                              ? t(
                                  "spaceSettings.owner.errors.SPACE_OWNER_TRANSFER_REQUIRED"
                                )
                              : undefined
                          }
                        >
                          <LogOut className="mr-1 size-3.5" />
                          {t("spaceSettings.members.leave")}
                        </Button>
                      ) : canManageMember(member) ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 text-xs text-muted-foreground hover:text-destructive"
                          onClick={() => handleRemoveClick(member)}
                        >
                          <UserMinus className="mr-1 size-3.5" />
                          {t("spaceSettings.members.remove")}
                        </Button>
                      ) : null}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between pt-2">
              <span className="text-xs text-muted-foreground">
                {t("spaceSettings.members.pagination", {
                  count: filteredMembers.length,
                  page,
                  pages: totalPages,
                })}
              </span>
              <div className="flex items-center gap-1">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => p - 1)}
                >
                  {t("common.prev")}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page >= totalPages}
                  onClick={() => setPage((p) => p + 1)}
                >
                  {t("common.next")}
                </Button>
              </div>
            </div>
          )}
        </>
      )}

      {/* Add Member Sheet */}
      <AddMemberSheet
        open={addSheetOpen}
        onOpenChange={setAddSheetOpen}
        onSuccess={handleAddSuccess}
        currentMemberCount={
          membersData?.code === 0 ? membersData.data.total : 0
        }
      />

      {/* Confirmation Dialogs */}
      <AlertDialog
        open={!!confirmDialog}
        onOpenChange={(open) => {
          if (!open) {
            setConfirmDialog(null)
            setDialogError(null)
          }
        }}
      >
        <AlertDialogContent>
          {confirmDialog?.type === "transfer" && confirmDialog.member && (
            <>
              <AlertDialogHeader>
                <AlertDialogTitle>
                  {t("spaceSettings.owner.transfer")}
                </AlertDialogTitle>
                <AlertDialogDescription>
                  {dialogError
                    ? ownerError(dialogError)
                    : t("spaceSettings.owner.confirm", {
                        name: confirmDialog.member.name,
                      })}
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel disabled={actionLoading}>
                  {t("common.cancel")}
                </AlertDialogCancel>
                <AlertDialogAction
                  disabled={actionLoading}
                  onClick={(event) => {
                    event.preventDefault()
                    void executeTransfer()
                  }}
                >
                  {t("spaceSettings.members.confirm")}
                </AlertDialogAction>
              </AlertDialogFooter>
            </>
          )}
          {confirmDialog?.type === "role-change" && confirmDialog.member && (
            <>
              <AlertDialogHeader>
                <AlertDialogTitle>
                  {dialogError
                    ? t("spaceSettings.members.operationFailed")
                    : t("spaceSettings.members.confirmRole")}
                </AlertDialogTitle>
                <AlertDialogDescription>
                  {dialogError ? (
                    <span className="text-destructive">
                      {ownerError(dialogError)}
                    </span>
                  ) : (
                    t("spaceSettings.members.rolePrompt", {
                      name: confirmDialog.member.name,
                      from: roleLabels[confirmDialog.member.role],
                      to: roleLabels[confirmDialog.newRole!],
                    })
                  )}
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                {dialogError ? (
                  <AlertDialogCancel>
                    {t("spaceSettings.members.acknowledge")}
                  </AlertDialogCancel>
                ) : (
                  <>
                    <AlertDialogCancel disabled={actionLoading}>
                      {t("common.cancel")}
                    </AlertDialogCancel>
                    <AlertDialogAction
                      onClick={(e) => {
                        e.preventDefault()
                        executeRoleChange()
                      }}
                      disabled={actionLoading}
                    >
                      {t("spaceSettings.members.confirm")}
                    </AlertDialogAction>
                  </>
                )}
              </AlertDialogFooter>
            </>
          )}

          {confirmDialog?.type === "remove" && confirmDialog.member && (
            <>
              <AlertDialogHeader>
                <AlertDialogTitle>
                  {dialogError
                    ? t("spaceSettings.members.operationFailed")
                    : t("spaceSettings.members.confirmRemove")}
                </AlertDialogTitle>
                <AlertDialogDescription>
                  {dialogError ? (
                    <span className="text-destructive">
                      {ownerError(dialogError)}
                    </span>
                  ) : (
                    t("spaceSettings.members.removePrompt", {
                      name: confirmDialog.member.name,
                      space: currentSpace?.name,
                    })
                  )}
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                {dialogError ? (
                  <AlertDialogCancel>
                    {t("spaceSettings.members.acknowledge")}
                  </AlertDialogCancel>
                ) : (
                  <>
                    <AlertDialogCancel disabled={actionLoading}>
                      {t("common.cancel")}
                    </AlertDialogCancel>
                    <AlertDialogAction
                      onClick={(e) => {
                        e.preventDefault()
                        executeRemove()
                      }}
                      disabled={actionLoading}
                      className="text-destructive-foreground bg-destructive hover:bg-destructive/90"
                    >
                      {t("spaceSettings.members.confirmRemoveAction")}
                    </AlertDialogAction>
                  </>
                )}
              </AlertDialogFooter>
            </>
          )}

          {confirmDialog?.type === "exit" && (
            <>
              <AlertDialogHeader>
                <AlertDialogTitle>
                  {isLastAdminError
                    ? t("spaceSettings.members.lastAdmin")
                    : dialogError
                      ? t("spaceSettings.members.leaveFailed")
                      : t("spaceSettings.members.confirmLeave")}
                </AlertDialogTitle>
                <AlertDialogDescription>
                  {isLastAdminError ? null : dialogError ? (
                    <span className="text-destructive">
                      {ownerError(dialogError)}
                    </span>
                  ) : (
                    t("spaceSettings.members.leavePrompt", {
                      space: currentSpace?.name,
                    })
                  )}
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                {dialogError ? (
                  <AlertDialogCancel>
                    {t("spaceSettings.members.acknowledge")}
                  </AlertDialogCancel>
                ) : (
                  <>
                    <AlertDialogCancel disabled={actionLoading}>
                      {t("common.cancel")}
                    </AlertDialogCancel>
                    <AlertDialogAction
                      onClick={(e) => {
                        e.preventDefault()
                        executeExit()
                      }}
                      disabled={actionLoading}
                    >
                      {t("spaceSettings.members.confirmExitAction")}
                    </AlertDialogAction>
                  </>
                )}
              </AlertDialogFooter>
            </>
          )}
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
