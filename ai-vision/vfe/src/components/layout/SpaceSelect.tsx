import { useState, type FormEvent } from "react"
import { useTranslation } from "react-i18next"
import { Building2, Check, ChevronDown, Plus, User } from "lucide-react"
import { useLocation, useNavigate } from "react-router-dom"
import { toast } from "sonner"

import { useSpace } from "@/contexts/SpaceContext"
import { spaceApi } from "@/lib/api"
import { SpaceType, type Space } from "@/lib/schemas/space"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { DialogContent } from "@/components/dialogs/DialogContent"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"

export function SpaceSelect() {
  const { t } = useTranslation()
  const { spaces, currentSpace, switchSpace, refreshSpaces, loading } =
    useSpace()
  const location = useLocation()
  const navigate = useNavigate()
  const [createOpen, setCreateOpen] = useState(false)
  const [spaceName, setSpaceName] = useState("")
  const [creating, setCreating] = useState(false)

  if (loading || spaces.length === 0) {
    return (
      <div className="h-9 w-64 min-w-0 animate-pulse rounded-full bg-muted" />
    )
  }

  const personalSpaces = spaces.filter((s) => s.type === SpaceType.Personal)
  const businessSpaces = spaces.filter((s) => s.type === SpaceType.Business)

  const selectSpace = (space: Space) => {
    switchSpace(space)

    const shouldSyncUrl =
      location.pathname === "/" ||
      location.pathname === "/dashboard" ||
      location.pathname === "/trace"
    if (shouldSyncUrl) {
      const params = new URLSearchParams(location.search)
      params.delete("agent")
      if (space.type === SpaceType.Business) {
        params.set("spaceId", space.id)
      } else {
        params.delete("spaceId")
      }
      const search = params.toString()
      navigate(search ? `${location.pathname}?${search}` : location.pathname, {
        replace: true,
      })
    }
  }

  const handleSelect = (id: string) => {
    const space = spaces.find((item) => item.id === id)
    if (space) selectSpace(space)
  }

  const handleCreate = async (event: FormEvent) => {
    event.preventDefault()
    const name = spaceName.trim()
    if (!name || creating) return

    setCreating(true)
    try {
      const response = await spaceApi.create(name)
      await refreshSpaces()
      selectSpace(response.data)
      setSpaceName("")
      setCreateOpen(false)
      toast.success(t("spaceSelector.created"))
    } catch (reason) {
      toast.error(
        reason instanceof Error
          ? reason.message
          : t("spaceSelector.createFailed")
      )
    } finally {
      setCreating(false)
    }
  }

  const renderSpaceItem = (space: (typeof spaces)[number]) => {
    const Icon = space.type === SpaceType.Business ? Building2 : User
    const selected = currentSpace?.id === space.id

    return (
      <DropdownMenuItem
        key={space.id}
        className="mx-1 flex min-h-9 items-center justify-between gap-3 rounded-lg px-3 text-sm font-normal"
        onSelect={() => handleSelect(space.id)}
      >
        <div className="flex min-w-0 items-center gap-2">
          <Icon className="size-4 shrink-0 text-muted-foreground" />
          <span className="truncate">{space.name}</span>
        </div>
        <Check
          className={cn(
            "size-4 shrink-0",
            selected ? "opacity-100" : "opacity-0"
          )}
        />
      </DropdownMenuItem>
    )
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="outline"
            size="sm"
            className="h-9 w-64 min-w-0 shrink justify-between gap-2 rounded-full px-3 font-normal"
            title={currentSpace?.name}
          >
            <span className="flex min-w-0 items-center gap-2">
              {currentSpace?.type === SpaceType.Business ? (
                <Building2 className="size-4 shrink-0 text-muted-foreground" />
              ) : (
                <User className="size-4 shrink-0 text-muted-foreground" />
              )}
              <span className="truncate text-sm leading-none font-normal text-foreground">
                {currentSpace?.name ?? t("spaceSelector.select")}
              </span>
            </span>
            <ChevronDown className="size-4 shrink-0 text-muted-foreground" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="start"
          className="max-h-[min(20rem,var(--radix-dropdown-menu-content-available-height))] w-[min(20rem,var(--radix-dropdown-menu-content-available-width))] min-w-0 overflow-y-auto rounded-xl p-0"
        >
          {personalSpaces.length > 0 && (
            <DropdownMenuGroup>
              <DropdownMenuLabel className="px-4 pt-3 text-xs font-normal text-muted-foreground">
                {t("spaceSelector.personal")}
              </DropdownMenuLabel>
              {personalSpaces.map(renderSpaceItem)}
            </DropdownMenuGroup>
          )}
          {personalSpaces.length > 0 && businessSpaces.length > 0 ? (
            <DropdownMenuSeparator />
          ) : null}
          {businessSpaces.length > 0 ? (
            <DropdownMenuGroup>
              <DropdownMenuLabel className="px-4 pt-3 text-xs font-normal text-muted-foreground">
                {t("spaceSelector.business")}
              </DropdownMenuLabel>
              {businessSpaces.map(renderSpaceItem)}
            </DropdownMenuGroup>
          ) : null}
          <DropdownMenuSeparator />
          <DropdownMenuItem
            className="mx-1 mb-1 min-h-9 rounded-lg px-3 text-sm"
            onSelect={() => setCreateOpen(true)}
          >
            <Plus className="size-4" />
            {t("spaceSelector.create")}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent size="compact">
          <form onSubmit={handleCreate}>
            <DialogHeader>
              <DialogTitle>{t("spaceSelector.create")}</DialogTitle>
              <DialogDescription>
                {t("spaceSelector.createDescription")}
              </DialogDescription>
            </DialogHeader>
            <div className="grid gap-2 py-5">
              <label
                htmlFor="business-space-name"
                className="text-sm font-medium"
              >
                {t("spaceSelector.name")}
              </label>
              <Input
                id="business-space-name"
                value={spaceName}
                maxLength={128}
                autoFocus
                placeholder={t("spaceSelector.namePlaceholder")}
                onChange={(event) => setSpaceName(event.target.value)}
              />
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setCreateOpen(false)}
                disabled={creating}
              >
                {t("common.cancel")}
              </Button>
              <Button type="submit" disabled={creating || !spaceName.trim()}>
                {creating
                  ? t("spaceSelector.creating")
                  : t("spaceSelector.createAction")}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  )
}
