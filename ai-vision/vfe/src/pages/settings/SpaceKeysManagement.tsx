import { writeClipboardText } from "@/lib/clipboard"
import { useTranslation } from "react-i18next"
import { useState } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { Check, Copy, KeyRound, Plus, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { api } from "@/lib/api"
import { useSpace } from "@/contexts/SpaceContext"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
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
import { Skeleton } from "@/components/ui/skeleton"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"

export interface SpaceKeyItem {
  id: number
  key_prefix: string
  name: string
  enabled: boolean
  creator_name: string | null
  gmt_create: string
  last_used_at: string | null
}

export interface SpaceKeyListResponse {
  code: number
  data: {
    items: SpaceKeyItem[]
    total: number
    page: number
    pageSize: number
    role: number | null
  }
}

interface SpaceKeyCreateResponse {
  code: number
  data: {
    id: number
    key: string
    key_prefix: string
    name: string
  }
}

export default function SpaceKeysManagement() {
  const { t } = useTranslation()
  const { currentSpace } = useSpace()
  const queryClient = useQueryClient()
  const spaceId = currentSpace?.id ?? ""

  const [createOpen, setCreateOpen] = useState(false)
  const [keyName, setKeyName] = useState("")
  const [createdKey, setCreatedKey] = useState<string | null>(null)
  const [copiedKey, setCopiedKey] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<SpaceKeyItem | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ["space-keys", spaceId],
    queryFn: () =>
      api.get<SpaceKeyListResponse>(`/spaces/${spaceId}/space-keys`),
    enabled: !!spaceId,
  })

  const keys = data?.code === 0 ? (data.data?.items ?? []) : []
  const myRole = data?.code === 0 ? data.data?.role : null
  const isAdmin = myRole === 1

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
    onError: () => toast.error(t("spaceSettings.page.networkError")),
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
      toast.error(t("spaceSettings.keys.operationFailed"))
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
  })

  const handleCreate = () => {
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

  if (isLoading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-48 w-full" />
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-medium">
            {t("spaceSettings.keys.externalTitle")}
          </h3>
          <p className="text-sm text-muted-foreground">
            {t("spaceSettings.keys.externalDescription")}
          </p>
        </div>
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <span>
                <Button
                  size="sm"
                  disabled={!isAdmin}
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
            {!isAdmin && (
              <TooltipContent>
                <p>{t("spaceSettings.keys.adminCreateOnly")}</p>
              </TooltipContent>
            )}
          </Tooltip>
        </TooltipProvider>
      </div>

      <Card>
        <CardContent className="p-0">
          {keys.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-12 text-muted-foreground">
              <KeyRound className="size-8" />
              <p className="text-sm">{t("spaceSettings.keys.empty")}</p>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("spaceSettings.keys.key")}</TableHead>
                  <TableHead>{t("spaceSettings.keys.name")}</TableHead>
                  <TableHead>{t("spaceSettings.keys.status")}</TableHead>
                  <TableHead>{t("spaceSettings.keys.createdBy")}</TableHead>
                  <TableHead>{t("spaceSettings.keys.createdAt")}</TableHead>
                  <TableHead>{t("spaceSettings.keys.lastUsed")}</TableHead>
                  <TableHead className="text-right">
                    {t("spaceSettings.keys.actions")}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {keys.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell>
                      <code className="rounded bg-muted px-1.5 py-0.5 text-xs">
                        {item.key_prefix}********
                      </code>
                    </TableCell>
                    <TableCell className="text-sm">
                      {item.name || "-"}
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
                    <TableCell className="text-sm">
                      {item.creator_name || "-"}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {item.gmt_create}
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
                                  disabled={!isAdmin || isTogglingKey(item.id)}
                                  aria-busy={isTogglingKey(item.id)}
                                  onClick={() =>
                                    toggleMutation.mutate({
                                      id: item.id,
                                      enabled: !item.enabled,
                                    })
                                  }
                                >
                                  {item.enabled
                                    ? t("common.disableAction")
                                    : t("common.enableAction")}
                                </Button>
                              </span>
                            </TooltipTrigger>
                            {!isAdmin && (
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
                                  disabled={!isAdmin}
                                  onClick={() => setDeleteTarget(item)}
                                >
                                  <Trash2 className="size-3.5" />
                                </Button>
                              </span>
                            </TooltipTrigger>
                            {!isAdmin && (
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
          )}
        </CardContent>
      </Card>

      {/* Create dialog */}
      <Dialog
        open={createOpen}
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
                  disabled={createMutation.isPending}
                >
                  {createMutation.isPending
                    ? t("spaceSettings.keys.creating")
                    : t("spaceSettings.keys.create")}
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>

      {/* Delete confirmation */}
      <AlertDialog
        open={!!deleteTarget}
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
                deleteTarget && deleteMutation.mutate(deleteTarget.id)
              }
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending
                ? t("spaceSettings.keys.deleting")
                : t("spaceSettings.keys.confirmDelete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
