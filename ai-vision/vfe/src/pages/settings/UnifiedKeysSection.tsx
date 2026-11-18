import { writeClipboardText } from "@/lib/clipboard"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { Check, Copy, Eye, KeyRound, Plus, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { api } from "@/lib/api"
import { useSpace } from "@/contexts/SpaceContext"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Dialog,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { DialogContent } from "@/components/dialogs/DialogContent"
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { CredentialInfo, RevealedCredential } from "./credentialUtils"
import { extractServerMessage } from "./credentialUtils"
import type { SpaceKeyItem, SpaceKeyListResponse } from "./SpaceKeysManagement"

interface SpaceKeyCreateResponse {
  code: number
  data: { id: number; key: string; key_prefix: string; name: string }
}

interface UnifiedKeysSectionProps {
  writeKey?: CredentialInfo | null
  readKey?: CredentialInfo | null
  canManageKey?: boolean
  canRevealKey?: boolean
  onReveal?: (credentialId: number, title: string) => void
  onCopy?: (text: string, key: string) => void
  copyState?: string | null
}

export default function UnifiedKeysSection({
  writeKey,
  readKey,
  canManageKey = false,
  canRevealKey = false,
  onReveal,
  onCopy,
  copyState,
}: UnifiedKeysSectionProps) {
  const { t } = useTranslation()
  const { currentSpace } = useSpace()
  const queryClient = useQueryClient()
  const spaceId = currentSpace?.id ?? ""

  const [createOpen, setCreateOpen] = useState(false)
  const [keyName, setKeyName] = useState("")
  const [createdKey, setCreatedKey] = useState<string | null>(null)
  const [copiedKey, setCopiedKey] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<SpaceKeyItem | null>(null)
  const [copying, setCopying] = useState<string | null>(null)

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["space-keys", spaceId],
    queryFn: () =>
      api.get<SpaceKeyListResponse>(`/spaces/${spaceId}/space-keys`),
    enabled: !!spaceId,
  })

  const keys = data?.code === 0 ? (data.data?.items ?? []) : []

  const credentials: { credential: CredentialInfo; label: string }[] = []
  if (writeKey)
    credentials.push({
      credential: writeKey,
      label: t("spaceSettings.keys.write"),
    })
  if (readKey)
    credentials.push({
      credential: readKey,
      label: t("spaceSettings.keys.read"),
    })

  const createMutation = useMutation({
    mutationFn: (name: string) =>
      api.post<SpaceKeyCreateResponse>(`/spaces/${spaceId}/space-keys`, {
        name,
      }),
    onSuccess: (res) => {
      if (res.code === 0 && res.data) {
        setCreatedKey(res.data.key)
        queryClient.invalidateQueries({ queryKey: ["space-keys", spaceId] })
      } else {
        toast.error(
          (res as { message?: string }).message ||
            t("spaceSettings.keys.createFailed")
        )
      }
    },
    onError: (err) =>
      toast.error(
        extractServerMessage(err) || t("spaceSettings.page.networkError")
      ),
  })

  const spaceKeysQueryKey = ["space-keys", spaceId] as const

  const toggleMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api.put<{ code: number }>(`/spaces/${spaceId}/space-keys/${id}`, {
        enabled,
      }),
    onMutate: async ({ id, enabled }) => {
      await queryClient.cancelQueries({ queryKey: spaceKeysQueryKey })
      const prev =
        queryClient.getQueryData<SpaceKeyListResponse>(spaceKeysQueryKey)
      queryClient.setQueryData<SpaceKeyListResponse>(
        spaceKeysQueryKey,
        (old) => {
          if (!old || old.code !== 0) return old
          return {
            ...old,
            data: {
              ...old.data,
              items: old.data.items.map((k) =>
                k.id === id ? { ...k, enabled } : k
              ),
            },
          }
        }
      )
      return { prev }
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.prev) queryClient.setQueryData(spaceKeysQueryKey, ctx.prev)
      toast.error(
        extractServerMessage(_err) || t("spaceSettings.keys.operationFailed")
      )
    },
    onSuccess: (res, _vars, ctx) => {
      if (res.code !== 0) {
        if (ctx?.prev) queryClient.setQueryData(spaceKeysQueryKey, ctx.prev)
        toast.error(t("spaceSettings.keys.operationFailed"))
      }
    },
  })

  const isTogglingKey = (id: number) =>
    toggleMutation.isPending && toggleMutation.variables?.id === id

  const deleteMutation = useMutation({
    mutationFn: (id: number) =>
      api.delete<{ code: number }>(`/spaces/${spaceId}/space-keys/${id}`),
    onSuccess: (res) => {
      if (res.code === 0) {
        toast.success(t("spaceSettings.keys.deleted"))
        queryClient.invalidateQueries({ queryKey: ["space-keys", spaceId] })
        setDeleteTarget(null)
      } else {
        toast.error(t("spaceSettings.keys.deleteFailed"))
      }
    },
    onError: (err) =>
      toast.error(
        extractServerMessage(err) || t("spaceSettings.keys.deleteFailed")
      ),
  })

  const handleCopyCredential = async (
    credentialId: number,
    field: "public_key" | "secret_key",
    copyKey: string
  ) => {
    if (!canRevealKey) return
    setCopying(copyKey)
    try {
      const response = await api.post<{
        code: number
        data?: RevealedCredential
        message?: string
      }>(`/spaces/${spaceId}/credentials/${credentialId}/reveal?intent=copy`)
      if (response.code === 0 && response.data) {
        onCopy?.(response.data[field], copyKey)
      } else {
        toast.error(response.message || t("spaceSettings.page.keyLoadFailed"))
      }
    } catch (err) {
      toast.error(
        extractServerMessage(err) || t("spaceSettings.page.networkError")
      )
    } finally {
      setCopying(null)
    }
  }

  const handleCreate = () => {
    if (!canManageKey) return
    createMutation.mutate(keyName)
  }

  const handleCloseCreate = () => {
    setCreateOpen(false)
    setKeyName("")
    setCreatedKey(null)
    setCopiedKey(false)
  }

  const handleCopyKey = async () => {
    if (!createdKey) return
    try {
      await writeClipboardText(createdKey)
    } catch {
      setCopiedKey(false)
      toast.error(t("common.copyFailed"))
      return
    }
    setCopiedKey(true)
    setTimeout(() => setCopiedKey(false), 2000)
  }

  const hasRows = credentials.length > 0 || keys.length > 0
  const isEmpty = !isError && !hasRows

  if (isLoading) {
    return (
      <Card>
        <CardContent className="px-5 py-4">
          <Skeleton className="h-6 w-32" />
          <Skeleton className="mt-4 h-48 w-full" />
        </CardContent>
      </Card>
    )
  }

  return (
    <>
      <Card>
        <CardContent className="px-5 py-4">
          <div className="flex items-center justify-between">
            <h3 className="text-base font-medium text-foreground">
              {t("spaceSettings.keys.title")}
            </h3>
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span>
                    <Button
                      size="sm"
                      disabled={!canManageKey}
                      onClick={() => {
                        setCreatedKey(null)
                        setKeyName("")
                        setCreateOpen(true)
                      }}
                    >
                      <Plus className="mr-1 size-4" />
                      {t("spaceSettings.keys.create")}
                    </Button>
                  </span>
                </TooltipTrigger>
                {!canManageKey && (
                  <TooltipContent>
                    <p>{t("spaceSettings.keys.adminCreateOnly")}</p>
                  </TooltipContent>
                )}
              </Tooltip>
            </TooltipProvider>
          </div>

          {isError && (
            <div className="mt-3 flex items-center justify-between rounded border border-destructive/30 bg-destructive/5 px-3 py-2">
              <p className="text-sm text-destructive">
                {t("spaceSettings.keys.loadFailed")}
              </p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => void refetch()}
              >
                {t("common.retry")}
              </Button>
            </div>
          )}

          {isEmpty ? (
            <div className="flex flex-col items-center gap-2 py-12 text-muted-foreground">
              <KeyRound className="size-8" />
              <p className="text-sm">{t("spaceSettings.keys.empty")}</p>
            </div>
          ) : hasRows ? (
            <Table className="mt-3">
              <TableHeader>
                <TableRow>
                  <TableHead>{t("spaceSettings.keys.key")}</TableHead>
                  <TableHead>{t("spaceSettings.keys.name")}</TableHead>
                  <TableHead>{t("spaceSettings.keys.type")}</TableHead>
                  <TableHead>{t("spaceSettings.keys.status")}</TableHead>
                  <TableHead>{t("spaceSettings.keys.lastUsed")}</TableHead>
                  <TableHead className="text-right">
                    {t("spaceSettings.keys.actions")}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {credentials.map(({ credential, label }) => (
                  <TableRow key={`cred-${credential.id}`}>
                    <TableCell>
                      <code className="rounded bg-muted px-1.5 py-0.5 text-xs">
                        {credential.public_key.slice(0, 12)}********
                      </code>
                    </TableCell>
                    <TableCell className="text-sm">{label}</TableCell>
                    <TableCell>
                      <Badge
                        variant={
                          credential.scope === "WRITE" ? "default" : "outline"
                        }
                        className="text-xs font-normal"
                      >
                        {credential.scope === "WRITE"
                          ? t("spaceSettings.keys.write")
                          : t("spaceSettings.keys.query")}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <span className="inline-flex items-center gap-1.5 text-xs text-emerald-600">
                        <span className="size-1.5 rounded-full bg-emerald-500" />
                        {t("common.enabled")}
                      </span>
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {credential.last_used_at || "-"}
                    </TableCell>
                    <TableCell className="text-right">
                      {canRevealKey && (
                        <div className="flex items-center justify-end gap-1">
                          <Button
                            variant="outline"
                            size="sm"
                            className="h-7 rounded-full px-3 text-xs font-normal"
                            onClick={() => onReveal?.(credential.id, label)}
                          >
                            <Eye className="mr-1 size-3" />
                            {t("spaceSettings.keys.reveal")}
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 rounded-full px-3 text-xs font-normal"
                            onClick={() =>
                              handleCopyCredential(
                                credential.id,
                                "public_key",
                                `pk-${credential.scope}`
                              )
                            }
                            disabled={copying === `pk-${credential.scope}`}
                          >
                            {copyState === `pk-${credential.scope}` ? (
                              <Check className="mr-1 size-3 text-emerald-500" />
                            ) : (
                              <Copy className="mr-1 size-3" />
                            )}
                            {copyState === `pk-${credential.scope}`
                              ? t("common.copied")
                              : t("common.copy")}
                          </Button>
                        </div>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
                {credentials.length > 0 && keys.length > 0 && (
                  <TableRow>
                    <TableCell colSpan={6} className="p-0">
                      <Separator />
                    </TableCell>
                  </TableRow>
                )}
                {keys.map((item) => (
                  <TableRow key={`sk-${item.id}`}>
                    <TableCell>
                      <code className="rounded bg-muted px-1.5 py-0.5 text-xs">
                        {item.key_prefix}********
                      </code>
                    </TableCell>
                    <TableCell className="text-sm">
                      {item.name || "-"}
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className="text-xs font-normal">
                        {t("spaceSettings.keys.query")}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <span
                        className={
                          "inline-flex items-center gap-1.5 text-xs " +
                          (item.enabled
                            ? "text-emerald-600"
                            : "text-muted-foreground")
                        }
                      >
                        <span
                          className={
                            "size-1.5 rounded-full " +
                            (item.enabled
                              ? "bg-emerald-500"
                              : "bg-muted-foreground/40")
                          }
                        />
                        {item.enabled
                          ? t("common.enabled")
                          : t("common.disabled")}
                      </span>
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {item.last_used_at || "-"}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-1">
                        <TooltipProvider>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <span>
                                <Button
                                  variant={item.enabled ? "outline" : "default"}
                                  size="sm"
                                  className="h-7 rounded-full px-3 text-xs font-normal"
                                  disabled={
                                    !canManageKey || isTogglingKey(item.id)
                                  }
                                  aria-busy={isTogglingKey(item.id)}
                                  onClick={() =>
                                    toggleMutation.mutate({
                                      id: item.id,
                                      enabled: !item.enabled,
                                    })
                                  }
                                >
                                  {item.enabled
                                    ? t("common.disabled")
                                    : t("common.enabled")}
                                </Button>
                              </span>
                            </TooltipTrigger>
                            {!canManageKey && (
                              <TooltipContent>
                                <p>
                                  {t("spaceSettings.keys.adminOperateOnly")}
                                </p>
                              </TooltipContent>
                            )}
                          </Tooltip>
                        </TooltipProvider>
                        <TooltipProvider>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <span>
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  className="size-7 text-destructive hover:text-destructive"
                                  disabled={!canManageKey}
                                  onClick={() => setDeleteTarget(item)}
                                >
                                  <Trash2 className="size-3.5" />
                                </Button>
                              </span>
                            </TooltipTrigger>
                            {!canManageKey && (
                              <TooltipContent>
                                <p>
                                  {t("spaceSettings.keys.adminOperateOnly")}
                                </p>
                              </TooltipContent>
                            )}
                          </Tooltip>
                        </TooltipProvider>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : null}
        </CardContent>
      </Card>

      {/* Create dialog */}
      <Dialog
        open={createOpen && canManageKey}
        onOpenChange={(open) => !open && handleCloseCreate()}
      >
        <DialogContent size="form">
          <DialogHeader>
            <DialogTitle>
              {createdKey
                ? t("spaceSettings.keys.keyCreated")
                : t("spaceSettings.keys.create")}
            </DialogTitle>
            <DialogDescription>
              {createdKey
                ? t("spaceSettings.keys.createdDescription")
                : t("spaceSettings.keys.createDescription")}
            </DialogDescription>
          </DialogHeader>

          {createdKey ? (
            <div className="space-y-3">
              <div className="rounded-lg border bg-muted/50 p-3">
                <pre className="font-mono text-sm break-all whitespace-pre-wrap">
                  {createdKey}
                </pre>
              </div>
              <Button
                variant="outline"
                className="w-full"
                onClick={handleCopyKey}
              >
                {copiedKey ? (
                  <Check className="mr-1 size-4 text-emerald-500" />
                ) : (
                  <Copy className="mr-1 size-4" />
                )}
                {copiedKey
                  ? t("common.copied")
                  : t("spaceSettings.keys.copyKey")}
              </Button>
            </div>
          ) : (
            <>
              <div>
                <label className="text-sm text-muted-foreground">
                  {t("spaceSettings.keys.optionalName")}
                </label>
                <Input
                  className="mt-1"
                  placeholder={t("spaceSettings.keys.namePlaceholder")}
                  value={keyName}
                  onChange={(e) => setKeyName(e.target.value)}
                  maxLength={128}
                />
              </div>
              <DialogFooter>
                <Button variant="outline" onClick={handleCloseCreate}>
                  {t("common.cancel")}
                </Button>
                <Button
                  onClick={handleCreate}
                  disabled={!canManageKey || createMutation.isPending}
                >
                  {createMutation.isPending
                    ? t("spaceSettings.keys.creating")
                    : t("spaceSelector.createAction")}
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>

      {/* Delete confirmation */}
      <AlertDialog
        open={!!deleteTarget && canManageKey}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("spaceSettings.keys.deleteTitle")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t("spaceSettings.keys.deleteDescription", {
                prefix: deleteTarget?.key_prefix,
              })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
            <AlertDialogAction
              className="text-destructive-foreground bg-destructive hover:bg-destructive/90"
              onClick={() =>
                canManageKey &&
                deleteTarget &&
                deleteMutation.mutate(deleteTarget.id)
              }
              disabled={!canManageKey || deleteMutation.isPending}
            >
              {deleteMutation.isPending
                ? t("spaceSettings.keys.deleting")
                : t("spaceSettings.keys.confirmDelete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
