import { useState } from "react"
import { useTranslation } from "react-i18next"
import { useMutation } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { DialogContent } from "@/components/dialogs/DialogContent"
import { createSandboxConnection, updateSandboxConnection } from "./api"
import type { SandboxConnection, SandboxConnectionInput } from "./types"

export function SandboxConnectionForm({
  spaceId,
  connection,
  prefill,
  onSaved,
  onCancel,
}: {
  spaceId: string
  connection?: SandboxConnection
  prefill?: { name: string; image: string }
  onSaved: () => void
  onCancel: () => void
}) {
  const { t } = useTranslation()
  const [input, setInput] = useState<SandboxConnectionInput>(() => ({
    name: connection?.name ?? prefill?.name ?? "",
    provider: "openshell",
    endpoint: connection?.endpoint ?? "",
    image: connection?.image ?? prefill?.image ?? "",
    cpu: connection?.cpu ?? 1,
    memory: connection?.memory ?? "512Mi",
    timeout_seconds: connection?.timeout_seconds ?? 5,
  }))
  const [secret, setSecret] = useState("")
  const mutation = useMutation({
    mutationFn: () => {
      const body = {
        ...input,
        name: input.name.trim(),
        endpoint: input.endpoint.trim(),
        image: input.image.trim(),
        ...(secret ? { api_key: secret } : {}),
      }
      return connection
        ? updateSandboxConnection(spaceId, connection.uuid, body)
        : createSandboxConnection(spaceId, body)
    },
    onSuccess: onSaved,
  })
  const valid =
    !!input.name.trim() &&
    /^https?:\/\//.test(input.endpoint.trim()) &&
    /^[A-Za-z0-9][A-Za-z0-9._/@:-]{0,511}$/.test(input.image.trim()) &&
    /^[1-9][0-9]{0,5}(Mi|Gi)$/.test(input.memory.trim()) &&
    Number.isInteger(input.cpu) &&
    input.cpu >= 1 &&
    input.cpu <= 64 &&
    Number.isInteger(input.timeout_seconds) &&
    input.timeout_seconds >= 1 &&
    input.timeout_seconds <= 300 &&
    (connection?.api_key_configured || !!secret)
  return (
    <DialogContent size="form" className="overflow-y-auto">
      <DialogHeader>
        <DialogTitle>
          {t(connection ? "sandbox.editConnection" : "sandbox.addConnection")}
        </DialogTitle>
        <DialogDescription>{t("sandbox.endpointHelp")}</DialogDescription>
      </DialogHeader>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          if (valid && !mutation.isPending) mutation.mutate()
        }}
      >
        {(["name", "endpoint", "image", "memory"] as const).map((field) => (
          <div key={field} className="space-y-1.5">
            <label className="text-sm font-medium" htmlFor={`sandbox-${field}`}>
              {t(`sandbox.${field}`)}
            </label>
            <Input
              id={`sandbox-${field}`}
              value={input[field]}
              required
              maxLength={field === "name" ? 128 : 1024}
              onChange={(event) =>
                setInput({ ...input, [field]: event.target.value })
              }
            />
          </div>
        ))}
        <p className="text-xs text-muted-foreground">
          {t("sandbox.imageHelp")}
        </p>
        <div className="space-y-1.5">
          <label className="text-sm font-medium" htmlFor="sandbox-api-key">
            {t(connection ? "sandbox.rotateKey" : "sandbox.apiKey")}
          </label>
          <Input
            id="sandbox-api-key"
            type="password"
            autoComplete="new-password"
            value={secret}
            required={!connection?.api_key_configured}
            maxLength={8192}
            onChange={(event) => setSecret(event.target.value)}
          />
          <p className="text-xs text-muted-foreground">
            {t(connection ? "sandbox.rotateKeyHelp" : "sandbox.apiKeyHelp")}
          </p>
        </div>
        <div className="grid grid-cols-2 gap-4">
          {(["cpu", "timeout_seconds"] as const).map((field) => (
            <div key={field} className="space-y-1.5">
              <label
                className="text-sm font-medium"
                htmlFor={`sandbox-${field}`}
              >
                {t(`sandbox.${field}`)}
              </label>
              <Input
                id={`sandbox-${field}`}
                type="number"
                min={1}
                max={field === "cpu" ? 64 : 300}
                step={1}
                required
                value={input[field]}
                onChange={(event) =>
                  setInput({ ...input, [field]: Number(event.target.value) })
                }
              />
            </div>
          ))}
        </div>
        {mutation.isError && (
          <p role="alert" className="text-sm text-destructive">
            {t("sandbox.saveError")}
          </p>
        )}
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={onCancel}
            disabled={mutation.isPending}
          >
            {t("common.cancel")}
          </Button>
          <Button type="submit" disabled={!valid || mutation.isPending}>
            {t(mutation.isPending ? "sandbox.saving" : "common.save")}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  )
}
