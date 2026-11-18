import { useId, useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import {
  Dialog,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { DialogContent } from "@/components/dialogs/DialogContent"
import { builtinRuleThreshold } from "../builtinDiagnoserPolicy"
import { copyBuiltinDiagnoser, updateDiagnoser } from "../diagnoserApi"
import type { DiagnoserDTO } from "../types"

interface BuiltinRuleConfigurationDialogProps {
  mode: "copy" | "configure"
  target: DiagnoserDTO
  displayName: string
  onClose: () => void
  onSaved: (row: DiagnoserDTO) => void
}

export function BuiltinRuleConfigurationDialog({
  mode,
  target,
  displayName,
  onClose,
  onSaved,
}: BuiltinRuleConfigurationDialogProps) {
  const { t } = useTranslation()
  const fieldId = useId()
  const isCopyAction = mode === "copy"
  const isCustom = target.type === "custom"
  const threshold = builtinRuleThreshold(target.spec.executor)
  const storedValue = threshold ? target.spec[threshold.key] : undefined
  const initialValue =
    threshold && typeof storedValue === "number"
      ? String(
          threshold.multiplier === 100
            ? Number((storedValue * 100).toPrecision(15))
            : storedValue
        )
      : ""
  const [name, setName] = useState(() =>
    isCopyAction
      ? t("diagnose.diagnoser.ruleManagement.copyName", {
          name: displayName,
        }).slice(0, 128)
      : target.name
  )
  const [description, setDescription] = useState(target.description)
  const [value, setValue] = useState(initialValue)
  const nameChanged = isCustom && name.trim() !== target.name
  const descriptionChanged =
    isCustom && description.trim() !== target.description
  const valueChanged = value !== initialValue
  const numericValue = value === "" ? null : Number(value)
  const invalidValue =
    numericValue !== null &&
    (!Number.isFinite(numericValue) ||
      numericValue < 0 ||
      (threshold?.maximum !== undefined && numericValue > threshold.maximum))
  const title = t(
    isCopyAction
      ? "diagnose.diagnoser.ruleManagement.copy"
      : isCustom
        ? "diagnose.diagnoser.editConfig"
        : "diagnose.diagnoser.ruleManagement.editThreshold"
  )
  const mutation = useMutation({
    mutationFn: () => {
      if (isCopyAction) {
        return copyBuiltinDiagnoser(
          target.space_uuid,
          target.agent_uuid,
          target.uuid,
          name.trim()
        )
      }
      return updateDiagnoser(
        target.space_uuid,
        target.uuid,
        {
          ...(nameChanged && { name: name.trim() }),
          ...(descriptionChanged && { description: description.trim() }),
          ...(threshold &&
            valueChanged && {
              spec: {
                [threshold.key]:
                  numericValue === null
                    ? null
                    : numericValue / threshold.multiplier,
              },
            }),
        },
        target.agent_uuid
      )
    },
  })
  const disabled =
    mutation.isPending ||
    !name.trim() ||
    (!isCopyAction &&
      (invalidValue || (!nameChanged && !descriptionChanged && !valueChanged)))

  return (
    <Dialog
      open
      onOpenChange={(open) => !open && !mutation.isPending && onClose()}
    >
      <DialogContent size="form">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            {isCopyAction
              ? t("diagnose.diagnoser.ruleManagement.copyDescription")
              : displayName}
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (!disabled) mutation.mutate(undefined, { onSuccess: onSaved })
          }}
        >
          {(isCopyAction || isCustom) && (
            <div className="grid gap-2">
              <label
                className="text-sm font-medium"
                htmlFor={`${fieldId}-name`}
              >
                {t("diagnose.diagnoser.llm.name")}
              </label>
              <Input
                id={`${fieldId}-name`}
                maxLength={128}
                disabled={mutation.isPending}
                value={name}
                onChange={(event) => setName(event.target.value)}
                required
              />
            </div>
          )}
          {!isCopyAction && isCustom && (
            <div className="grid gap-2">
              <label
                className="text-sm font-medium"
                htmlFor={`${fieldId}-description`}
              >
                {t("diagnose.common.description")}
              </label>
              <Textarea
                id={`${fieldId}-description`}
                value={description}
                disabled={mutation.isPending}
                onChange={(event) => setDescription(event.target.value)}
              />
            </div>
          )}
          {!isCopyAction && threshold && (
            <div className="grid gap-2">
              <label
                className="text-sm font-medium"
                htmlFor={`${fieldId}-threshold`}
              >
                {t(`diagnose.diagnoser.detail.policy.${threshold.label}`)}{" "}
                {t(
                  threshold.multiplier === 100
                    ? "diagnose.diagnoser.ruleManagement.percentUnit"
                    : "diagnose.diagnoser.ruleManagement.millisecondsUnit"
                )}
              </label>
              <div className="flex flex-wrap items-center gap-2">
                <Input
                  id={`${fieldId}-threshold`}
                  className="min-w-24 flex-1"
                  type="number"
                  min={0}
                  max={threshold.maximum}
                  step="any"
                  value={value}
                  disabled={mutation.isPending}
                  placeholder={t(
                    "diagnose.diagnoser.ruleManagement.defaultValue"
                  )}
                  onChange={(event) => setValue(event.target.value)}
                />
                <Button
                  type="button"
                  variant="outline"
                  disabled={value === "" || mutation.isPending}
                  onClick={() => setValue("")}
                >
                  {t("diagnose.diagnoser.ruleManagement.reset")}
                </Button>
              </div>
              <p className="text-sm text-muted-foreground">
                {t(
                  threshold.multiplier === 100
                    ? "diagnose.diagnoser.detail.policy.defaultToolSuccess"
                    : "diagnose.diagnoser.detail.policy.defaultLatency"
                )}
              </p>
            </div>
          )}
          {mutation.error && (
            <p role="alert" className="text-sm text-destructive">
              {mutation.error.message}
            </p>
          )}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={mutation.isPending}
              onClick={onClose}
            >
              {t("common.cancel")}
            </Button>
            <Button type="submit" disabled={disabled}>
              {mutation.isPending
                ? t("diagnose.diagnoser.ruleManagement.saving")
                : isCopyAction
                  ? title
                  : t("common.save")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
