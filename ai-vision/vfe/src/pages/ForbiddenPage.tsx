import { useTranslation } from "react-i18next"
import { Link } from "react-router-dom"

import { Button } from "@/components/ui/button"
import { ErrorPage } from "@/components/error/ErrorPage"

export default function ForbiddenPage() {
  const { t } = useTranslation()
  return (
    <ErrorPage
      code={403}
      title={t("error.forbiddenTitle")}
      description={t("error.forbiddenDescription")}
      actions={
        <Button asChild>
          <Link to="/">{t("error.backHome")}</Link>
        </Button>
      }
    />
  )
}
