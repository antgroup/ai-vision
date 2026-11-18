import { useTranslation } from "react-i18next"
import { TraceBadge } from "./shared/TraceBadge"
import type { ModelCallRole } from "./spanModelContext"

/** Purpose metadata is independent of success, quality and model identity. */
export function ModelCallRoleBadge({
  role,
}: {
  role: ModelCallRole | null | undefined
}) {
  const { t } = useTranslation()
  if (!role) return null
  return (
    <TraceBadge
      data-model-call-role={role.value}
      data-model-call-role-source={role.sourceSpanId}
      title={t("modelContext.roleSource", {
        spanId: role.sourceSpanId,
        fields: role.attributeKeys.join(", "),
      })}
    >
      {t(
        role.kind === "primary"
          ? "modelContext.rolePrimary"
          : role.task === "compression"
            ? "modelContext.roleCompression"
            : "modelContext.roleAuxiliary"
      )}
    </TraceBadge>
  )
}
