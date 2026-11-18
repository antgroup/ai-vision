import { writeClipboardText } from "@/lib/clipboard"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Navigate, useSearchParams } from "react-router-dom"
import { Check, Copy, KeyRound, Pencil, X } from "lucide-react"
import { toast } from "sonner"

import { api } from "@/lib/api"
import { modelConnectionsPath } from "@/features/model-connections/navigation"
import { cn } from "@/lib/utils"
import { useSpace } from "@/contexts/SpaceContext"
import { SpaceGuard } from "@/components/error/SpaceAgentGuard"
import { useUser } from "@/contexts/UserContext"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Card, CardContent } from "@/components/ui/card"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Sheet, SheetContent } from "@/components/ui/sheet"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
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
import MemberManagement from "./MemberManagement"
import OperationLogs from "./OperationLogs"
import UnifiedKeysSection from "./UnifiedKeysSection"
import {
  extractServerMessage,
  type CredentialInfo,
  type RevealedCredential,
} from "./credentialUtils"

// ── Types ──

interface SpaceSettings {
  id: string
  name: string
  type: number
  description: string | null
  billing_owner: string | null
  langfuse?: {
    project_id?: string
    project_name?: string
    mapped: boolean
  }
  observation?: {
    endpoint_name: string | null
    project_id: string | null
    project_name: string | null
    provision_status: number
    last_error: string | null
  }
  write_key?: CredentialInfo
  read_key?: CredentialInfo
  role: number
  permissions: string[]
}

interface RevealModalState {
  title: string
  credential: RevealedCredential
}

interface SpaceMember {
  user_uuid: string
  name: string
  email: string
  avatar_url: string | null
  role: number
  joined_at: string
}

const ROLE_VARIANTS: Record<number, "default" | "secondary" | "outline"> = {
  1: "default",
  2: "secondary",
  3: "outline",
}

function isSuccess<T>(response: unknown): response is { code: 0; data: T } {
  return (
    typeof response === "object" &&
    response !== null &&
    (response as Record<string, unknown>).code === 0
  )
}

// ── Page ──

export default function SettingsPage() {
  const { currentSpace } = useSpace()
  const [searchParams] = useSearchParams()

  if (searchParams.get("tab") === "models") {
    return (
      <Navigate
        to={modelConnectionsPath(
          searchParams.get("spaceId") ?? currentSpace?.id
        )}
        replace
      />
    )
  }

  return <SpaceSettingsPage />
}

function SpaceSettingsPage() {
  const { t } = useTranslation()
  const roleLabels: Record<number, string> = {
    1: t("spaceSettings.roles.admin"),
    2: t("spaceSettings.roles.developer"),
    3: t("spaceSettings.roles.member"),
  }
  const { currentSpace, refreshSpaces, loading: spaceLoading } = useSpace()
  const { user, loading: userLoading } = useUser()
  const queryClient = useQueryClient()
  const [searchParams] = useSearchParams()
  const spaceId = currentSpace?.id ?? ""
  const [copyState, setCopyState] = useState<string | null>(null)
  const [revealModal, setRevealModal] = useState<RevealModalState | null>(null)

  // inline edit state for space name
  const [editingName, setEditingName] = useState(false)
  const [nameValue, setNameValue] = useState("")
  const [nameConfirmOpen, setNameConfirmOpen] = useState(false)
  const [nameSaving, setNameSaving] = useState(false)

  const settingsQuery = useQuery({
    queryKey: ["settings", spaceId],
    queryFn: async () => {
      const response = await api.get<{ code: number; data: SpaceSettings }>(
        `/spaces/${spaceId}/settings`
      )
      if (!isSuccess<SpaceSettings>(response) || !response.data) {
        throw new Error(t("error.pageLoadFailed"))
      }
      return response
    },
    enabled: !!spaceId,
  })

  const settings = isSuccess<SpaceSettings>(settingsQuery.data)
    ? settingsQuery.data.data
    : null

  const { data: spaceMembersData } = useQuery({
    queryKey: ["space-members", spaceId],
    queryFn: () =>
      api.get<{
        code: number
        data: { items: SpaceMember[]; total: number; role: number | null }
      }>(`/spaces/${spaceId}/members`),
    enabled: !!spaceId && settings?.type === 2,
    staleTime: 30_000,
  })

  const myRole =
    (spaceMembersData?.code === 0 ? spaceMembersData.data.role : null) ??
    settings?.role ??
    null
  const isAdmin = myRole === 1

  const userName = user?.name || ""
  const userEmail = user?.email || ""
  const avatarSrc = user?.avatar_url || ""
  const userInitial = (userName || userEmail || "U").slice(0, 1).toUpperCase()

  const copyText = async (text: string, key: string) => {
    try {
      await writeClipboardText(text)
    } catch {
      setCopyState((current) => (current === key ? null : current))
      toast.error(t("common.copyFailed"))
      return
    }
    setCopyState(key)
    setTimeout(() => {
      setCopyState((current) => (current === key ? null : current))
    }, 1800)
  }

  const handleRevealCredential = async (
    credentialId: number,
    title: string
  ) => {
    try {
      const response = await api.post<{
        code: number
        data?: RevealedCredential
        message?: string
      }>(`/spaces/${spaceId}/credentials/${credentialId}/reveal`)
      if (response.code === 0 && response.data) {
        setRevealModal({
          title,
          credential: response.data,
        })
      } else {
        toast.error(response.message || t("spaceSettings.page.keyLoadFailed"))
        queryClient.invalidateQueries({ queryKey: ["settings", spaceId] })
        queryClient.invalidateQueries({ queryKey: ["space-members", spaceId] })
      }
    } catch (err) {
      const msg = extractServerMessage(err)
      toast.error(msg || t("spaceSettings.page.networkError"))
      queryClient.invalidateQueries({ queryKey: ["settings", spaceId] })
      queryClient.invalidateQueries({ queryKey: ["space-members", spaceId] })
    }
  }

  const renderCopyButton = (
    text: string,
    key: string,
    options?: {
      label?: string
      className?: string
      iconOnly?: boolean
    }
  ) => (
    <Button
      variant="ghost"
      size={options?.iconOnly ? "icon" : "sm"}
      className={
        options?.iconOnly
          ? "size-8 rounded-full"
          : `h-8 rounded-full px-3 text-xs font-normal ${options?.className || ""}`
      }
      aria-label={
        copyState === key
          ? t("common.copied")
          : options?.label || t("common.copy")
      }
      onClick={() => copyText(text, key)}
    >
      {copyState === key ? (
        <Check className="size-3.5 text-emerald-500" />
      ) : (
        <Copy className="size-3.5" />
      )}
      {!options?.iconOnly && (
        <span>
          {copyState === key
            ? t("common.copied")
            : options?.label || t("common.copy")}
        </span>
      )}
    </Button>
  )

  const handleStartEditName = () => {
    setNameValue(settings?.name || "")
    setEditingName(true)
  }

  const handleCancelEditName = () => {
    setEditingName(false)
    setNameValue("")
  }

  const handleSaveName = () => {
    if (nameConfirmOpen) return
    const trimmed = nameValue.trim()
    if (!trimmed) {
      toast.error(t("spaceSettings.page.emptyName"))
      return
    }
    if (trimmed.length > 128) {
      toast.error(t("spaceSettings.page.nameTooLong"))
      return
    }
    if (trimmed === settings?.name) {
      setEditingName(false)
      return
    }
    setNameConfirmOpen(true)
  }

  const executeNameSave = async () => {
    const trimmed = nameValue.trim()
    setNameSaving(true)
    setNameConfirmOpen(false)
    try {
      const res = await api.put<{ code: number; message?: string }>(
        `/spaces/${spaceId}/settings`,
        { name: trimmed }
      )
      if (res.code === 0) {
        toast.success(t("spaceSettings.page.nameUpdated"))
        setEditingName(false)
        queryClient.invalidateQueries({ queryKey: ["settings", spaceId] })
        refreshSpaces()
      } else {
        toast.error(res.message || t("spaceSettings.page.updateFailed"))
      }
    } catch (err) {
      const msg = extractServerMessage(err)
      toast.error(msg || t("spaceSettings.page.networkError"))
    } finally {
      setNameSaving(false)
    }
  }

  if (userLoading || spaceLoading || settingsQuery.isLoading) {
    return (
      <div className="flex flex-col gap-6 p-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-64 w-full" />
      </div>
    )
  }

  if (!settings) {
    return (
      <SpaceGuard>
        <div className="flex flex-col gap-6 p-6">
          <h1 className="text-xl font-medium">
            {t("spaceSettings.page.title")}
          </h1>
          {spaceId ? (
            <Alert variant="destructive">
              <AlertDescription className="flex flex-wrap items-center justify-between gap-3">
                {t("error.pageLoadFailed")}
                <Button
                  variant="outline"
                  size="sm"
                  disabled={settingsQuery.isFetching}
                  onClick={() => void settingsQuery.refetch()}
                >
                  {t("common.retry")}
                </Button>
              </AlertDescription>
            </Alert>
          ) : (
            <p className="text-sm text-muted-foreground">
              {t("common.noData")}
            </p>
          )}
        </div>
      </SpaceGuard>
    )
  }

  const isBusinessSpace = settings.type === 2
  const requestedTab = searchParams.get("tab")
  const defaultTab =
    isBusinessSpace && (requestedTab === "members" || requestedTab === "logs")
      ? requestedTab
      : "info"

  const userInfoCard = (
    <Card>
      <CardContent className="px-5 py-4">
        <h3 className="mb-3 text-base font-medium text-foreground">
          {t("spaceSettings.page.myInfo")}
        </h3>
        <div className="flex flex-wrap items-center gap-3">
          <Avatar className="size-12 shrink-0 rounded-lg">
            {avatarSrc && <AvatarImage src={avatarSrc} alt={userName} />}
            <AvatarFallback className="rounded-lg text-base">
              {userInitial}
            </AvatarFallback>
          </Avatar>
          <dl className="min-w-0 flex-1 basis-40 space-y-1">
            <div>
              <dt className="sr-only">{t("spaceSettings.page.name")}</dt>
              <dd className="text-sm font-medium break-words">
                {userName || "—"}
              </dd>
            </div>
            <div>
              <dt className="sr-only">{t("spaceSettings.page.email")}</dt>
              <dd className="text-xs break-all text-muted-foreground">
                {userEmail || "—"}
              </dd>
            </div>
          </dl>
          {myRole ? (
            <Badge
              variant={ROLE_VARIANTS[myRole] ?? "outline"}
              className="font-normal"
            >
              <span className="sr-only">
                {t("spaceSettings.page.permission")}:{" "}
              </span>
              {roleLabels[myRole] || t("spaceSettings.roles.unknown")}
            </Badge>
          ) : null}
        </div>
      </CardContent>
    </Card>
  )

  // ── Business space: 3-layer layout ──
  const businessInfoContent = (
    <div className="space-y-4">
      {/* Layer 1: 空间信息 */}
      <Card>
        <CardContent className="px-5 py-4">
          <h3 className="mb-3 text-base font-medium text-foreground">
            {t("spaceSettings.page.spaceInfo")}
          </h3>
          <div className="grid grid-cols-2 gap-x-8 gap-y-3 md:grid-cols-12">
            <FieldItem
              className="md:col-span-3"
              label={t("spaceSettings.page.spaceName")}
              value={
                editingName ? (
                  <div className="flex items-center gap-1.5">
                    <Input
                      aria-label={t("spaceSettings.page.spaceName")}
                      value={nameValue}
                      onChange={(e) => setNameValue(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") handleSaveName()
                        if (e.key === "Escape") handleCancelEditName()
                      }}
                      className="h-7 w-40 text-sm"
                      maxLength={128}
                      autoFocus
                      disabled={nameSaving}
                    />
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-6"
                      aria-label={t("common.save")}
                      onClick={handleSaveName}
                      disabled={nameSaving}
                    >
                      <Check className="size-3.5 text-emerald-500" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-6"
                      aria-label={t("common.cancel")}
                      onClick={handleCancelEditName}
                      disabled={nameSaving}
                    >
                      <X className="size-3.5" />
                    </Button>
                  </div>
                ) : (
                  <span className="inline-flex items-center gap-1.5">
                    {settings?.name || "-"}
                    {isAdmin && (
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-5"
                        aria-label={t("spaceSettings.page.editSpaceName")}
                        onClick={handleStartEditName}
                      >
                        <Pencil className="size-3" />
                      </Button>
                    )}
                  </span>
                )
              }
            />
            <FieldItem
              className="md:col-span-2"
              label={t("spaceSettings.page.spaceType")}
              value={
                <Badge variant="outline" className="font-normal">
                  {t("spaceSettings.page.business")}
                </Badge>
              }
            />
            <FieldItem
              className="col-span-2 md:col-span-5"
              label={t("spaceSettings.page.spaceId")}
              value={
                spaceId ? (
                  <span className="inline-flex max-w-full items-center gap-1">
                    <code className="text-xs whitespace-nowrap">{spaceId}</code>
                    {renderCopyButton(spaceId, "space-id", { iconOnly: true })}
                  </span>
                ) : (
                  "-"
                )
              }
            />
            {settings?.observation && (
              <>
                <FieldItem
                  className="md:col-span-3"
                  label={t("spaceSettings.observationEndpoint")}
                  value={settings.observation.endpoint_name}
                />
                <FieldItem
                  className="md:col-span-3"
                  label={t("spaceSettings.observationProject")}
                  value={settings.observation.project_name}
                />
                <FieldItem
                  className="md:col-span-2"
                  label={t("spaceSettings.page.initialStatus")}
                  value={
                    <Badge
                      className="font-normal"
                      variant={
                        settings.observation.provision_status === 1
                          ? "default"
                          : settings.observation.provision_status === 2
                            ? "destructive"
                            : "secondary"
                      }
                    >
                      {settings.observation.provision_status === 1
                        ? t("spaceSettings.page.ready")
                        : settings.observation.provision_status === 2
                          ? t("spaceSettings.page.failed")
                          : t("spaceSettings.page.initializing")}
                    </Badge>
                  }
                />
              </>
            )}
          </div>
        </CardContent>
      </Card>

      {userInfoCard}

      {/* Layer 3: API Keys — 统一列表 */}
      <UnifiedKeysSection
        key={spaceId}
        writeKey={settings?.write_key}
        readKey={settings?.read_key}
        canManageKey={
          settings?.permissions.includes("space.key.manage") ?? false
        }
        canRevealKey={isAdmin}
        onReveal={handleRevealCredential}
        onCopy={(text, key) => copyText(text, key)}
        copyState={copyState}
      />
    </div>
  )

  // ── 个人空间：与业务空间同构的双 Card 结构 ──
  const personalInfoContent = (
    <div className="space-y-4">
      {/* Layer 1: 空间信息 */}
      <Card>
        <CardContent className="px-5 py-4">
          <h3 className="mb-3 text-base font-medium text-foreground">
            {t("spaceSettings.page.spaceInfo")}
          </h3>
          <div className="grid grid-cols-2 gap-x-8 gap-y-3 md:grid-cols-12">
            <FieldItem
              className="md:col-span-3"
              label={t("spaceSettings.page.spaceName")}
              value={
                editingName ? (
                  <div className="flex items-center gap-1.5">
                    <Input
                      aria-label={t("spaceSettings.page.spaceName")}
                      value={nameValue}
                      onChange={(e) => setNameValue(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") handleSaveName()
                        if (e.key === "Escape") handleCancelEditName()
                      }}
                      className="h-7 w-40 text-sm"
                      maxLength={128}
                      autoFocus
                      disabled={nameSaving}
                    />
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-6"
                      aria-label={t("common.save")}
                      onClick={handleSaveName}
                      disabled={nameSaving}
                    >
                      <Check className="size-3.5 text-emerald-500" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-6"
                      aria-label={t("common.cancel")}
                      onClick={handleCancelEditName}
                      disabled={nameSaving}
                    >
                      <X className="size-3.5" />
                    </Button>
                  </div>
                ) : (
                  <span className="inline-flex items-center gap-1.5">
                    {settings?.name || "-"}
                    {isAdmin && (
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-5"
                        aria-label={t("spaceSettings.page.editSpaceName")}
                        onClick={handleStartEditName}
                      >
                        <Pencil className="size-3" />
                      </Button>
                    )}
                  </span>
                )
              }
            />
            <FieldItem
              className="md:col-span-2"
              label={t("spaceSettings.page.spaceType")}
              value={
                <Badge variant="outline" className="font-normal">
                  {t("spaceSettings.page.personal")}
                </Badge>
              }
            />
            <FieldItem
              className="col-span-2 md:col-span-5"
              label={t("spaceSettings.page.spaceId")}
              value={
                spaceId ? (
                  <span className="inline-flex max-w-full items-center gap-1">
                    <code className="text-xs whitespace-nowrap">{spaceId}</code>
                    {renderCopyButton(spaceId, "space-id", { iconOnly: true })}
                  </span>
                ) : (
                  "-"
                )
              }
            />
            <FieldItem
              className="md:col-span-2"
              label={t("spaceSettings.page.editPermission")}
              value={
                settings?.permissions.includes("space.settings.manage")
                  ? t("spaceSettings.page.manageable")
                  : t("spaceSettings.page.readOnly")
              }
            />
          </div>
        </CardContent>
      </Card>

      {userInfoCard}

      {/* Layer 3: API Keys — 统一列表 */}
      <UnifiedKeysSection
        key={spaceId}
        canManageKey={
          settings?.permissions.includes("space.key.manage") ?? false
        }
        onReveal={handleRevealCredential}
        onCopy={(text, key) => copyText(text, key)}
        copyState={copyState}
      />
    </div>
  )

  return (
    <SpaceGuard>
      <div className="flex flex-col gap-6 p-6">
        <h1 className="text-xl font-medium">{t("spaceSettings.page.title")}</h1>

        <Tabs defaultValue={defaultTab}>
          <TabsList variant="line">
            <TabsTrigger value="info">
              {t("spaceSettings.page.basic")}
            </TabsTrigger>
            {isBusinessSpace && (
              <TabsTrigger value="members">
                {t("spaceSettings.page.members")}
              </TabsTrigger>
            )}
            {isBusinessSpace && (
              <TabsTrigger value="logs">
                {t("spaceSettings.page.logs")}
              </TabsTrigger>
            )}
          </TabsList>
          <TabsContent value="info">
            {isBusinessSpace ? businessInfoContent : personalInfoContent}
          </TabsContent>

          {isBusinessSpace && (
            <TabsContent value="members">
              <MemberManagement />
            </TabsContent>
          )}
          {isBusinessSpace && (
            <TabsContent value="logs">
              <OperationLogs />
            </TabsContent>
          )}
        </Tabs>

        {/* Credential reveal sheet */}
        <Sheet
          open={isAdmin && !!revealModal}
          onOpenChange={(open) => !open && setRevealModal(null)}
        >
          <SheetContent
            side="right"
            className="w-full max-w-[560px] overflow-y-auto p-0"
            showCloseButton={false}
          >
            {isAdmin && revealModal && (
              <div className="flex min-h-full flex-col">
                <div className="border-b px-6 py-5">
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 text-sm font-normal text-muted-foreground">
                        <KeyRound className="size-4" />
                        {t("spaceSettings.credentials.title")}
                      </div>
                      <h3 className="mt-2 text-xl font-medium">
                        {revealModal.title}
                      </h3>
                      <p className="mt-1 text-sm text-muted-foreground">
                        {t("spaceSettings.page.secretHint")}
                      </p>
                    </div>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-9 shrink-0 rounded-full"
                      onClick={() => setRevealModal(null)}
                    >
                      <X className="size-4" />
                    </Button>
                  </div>
                </div>

                <div className="space-y-4 px-6 py-6">
                  <div className="grid gap-4 sm:grid-cols-2">
                    <div className="rounded-xl border bg-card p-4">
                      <div className="text-xs tracking-widest text-muted-foreground uppercase">
                        {t("spaceSettings.credentials.scope")}
                      </div>
                      <div className="mt-3 text-base font-medium">
                        {revealModal.credential.scope === "WRITE"
                          ? t("spaceSettings.keys.write")
                          : t("spaceSettings.keys.read")}
                      </div>
                    </div>
                    <div className="rounded-xl border bg-card p-4">
                      <div className="text-xs tracking-widest text-muted-foreground uppercase">
                        {t("spaceSettings.credentials.quickAction")}
                      </div>
                      <div className="mt-3">
                        {renderCopyButton(
                          `Public Key: ${revealModal.credential.public_key}\nSecret Key: ${revealModal.credential.secret_key}`,
                          `all-${revealModal.credential.scope}`,
                          {
                            label: t("spaceSettings.page.copyGroup"),
                            className:
                              "border bg-background text-foreground hover:bg-accent",
                          }
                        )}
                      </div>
                    </div>
                  </div>

                  <div className="space-y-3">
                    <div className="rounded-xl border bg-card p-4">
                      <div className="flex items-center justify-between gap-3">
                        <div className="text-xs tracking-widest text-muted-foreground uppercase">
                          {t("spaceSettings.credentials.publicKey")}
                        </div>
                        {renderCopyButton(
                          revealModal.credential.public_key,
                          `public-${revealModal.credential.scope}`,
                          {
                            label: t("common.copy"),
                            className:
                              "border bg-background text-foreground hover:bg-accent",
                          }
                        )}
                      </div>
                      <pre className="mt-3 overflow-x-auto rounded-lg bg-muted p-3 font-mono text-sm leading-6 break-all whitespace-pre-wrap">
                        {revealModal.credential.public_key}
                      </pre>
                    </div>

                    <div className="rounded-xl border bg-card p-4">
                      <div className="flex items-center justify-between gap-3">
                        <div className="text-xs tracking-widest text-muted-foreground uppercase">
                          {t("spaceSettings.credentials.secretKey")}
                        </div>
                        {renderCopyButton(
                          revealModal.credential.secret_key,
                          `secret-${revealModal.credential.scope}`,
                          {
                            label: t("common.copy"),
                            className:
                              "border bg-background text-foreground hover:bg-accent",
                          }
                        )}
                      </div>
                      <pre className="mt-3 overflow-x-auto rounded-lg bg-muted p-3 font-mono text-sm leading-6 break-all whitespace-pre-wrap">
                        {revealModal.credential.secret_key}
                      </pre>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </SheetContent>
        </Sheet>
        {/* Name change confirmation dialog */}
        <AlertDialog open={nameConfirmOpen} onOpenChange={setNameConfirmOpen}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>
                {t("spaceSettings.page.confirmRename")}
              </AlertDialogTitle>
              <AlertDialogDescription asChild>
                <div className="space-y-2 text-sm text-muted-foreground">
                  <p>
                    {t("spaceSettings.page.oldName")}
                    <span className="font-medium text-foreground">
                      {settings?.name}
                    </span>
                  </p>
                  <p>
                    {t("spaceSettings.page.newName")}
                    <span className="font-medium text-foreground">
                      {nameValue.trim()}
                    </span>
                  </p>
                </div>
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
              <AlertDialogAction onClick={executeNameSave}>
                {t("spaceSettings.page.confirmUpdate")}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    </SpaceGuard>
  )
}

// ── Helper components ──

function FieldItem({
  className,
  label,
  value,
}: {
  className?: string
  label: string
  value?: string | null | React.ReactNode
}) {
  return (
    <div className={cn("min-w-0", className)}>
      <span className="text-xs text-muted-foreground">{label}</span>
      <div className="mt-0.5 text-sm text-foreground">
        {typeof value === "string" || value === null || value === undefined
          ? value || "-"
          : value}
      </div>
    </div>
  )
}
