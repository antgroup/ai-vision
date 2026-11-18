import { useTranslation } from "react-i18next"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { ConnectionStatus } from "./ConnectionStatus"
import type { ModelConnection } from "./types"

/** Consumers own the binding and can opt into the existing diagnostic profile. */
export function ModelConnectionSelect({
  connections,
  value,
  onValueChange,
  placeholder,
  noneLabel,
  diagnosticValidation = false,
  disabled = false,
  isConnectionDisabled,
}: {
  connections: ModelConnection[]
  value: string
  onValueChange: (uuid: string) => void
  placeholder: string
  noneLabel: string
  diagnosticValidation?: boolean
  disabled?: boolean
  isConnectionDisabled?: (connection: ModelConnection) => boolean
}) {
  const { t } = useTranslation()
  return (
    <Select
      value={value || "__none__"}
      onValueChange={(uuid) => onValueChange(uuid === "__none__" ? "" : uuid)}
      disabled={disabled}
    >
      <SelectTrigger className="min-w-0 flex-1 text-xs">
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent position="popper">
        <SelectItem value="__none__" className="text-xs">
          {noneLabel}
        </SelectItem>
        {connections.map((connection) => (
          <SelectItem
            key={connection.uuid}
            value={connection.uuid}
            disabled={isConnectionDisabled?.(connection)}
            className="text-xs"
          >
            <span className="flex min-w-0 flex-wrap items-center gap-2">
              <span className="break-all">{connection.name}</span>
              <span className="font-mono break-all text-muted-foreground">
                ({connection.modelName})
              </span>
              <ConnectionStatus connection={connection} />
              {diagnosticValidation ? (
                <span className="text-muted-foreground">
                  {t("modelConnections.test")} ·{" "}
                  {t(
                    `modelConnections.validation.${connection.diagnosticValidation?.status ?? "unverified"}`
                  )}
                </span>
              ) : null}
            </span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
