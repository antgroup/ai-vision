import { useTranslation } from "react-i18next"
import { Link } from "react-router-dom"

import { Button } from "@/components/ui/button"
import { ErrorPage } from "@/components/error/ErrorPage"

export default function NotFoundPage() {
  const { t } = useTranslation()
  return (
    <ErrorPage
      code={404}
      title={t("error.notFoundTitle")}
      fullScreen
      actions={
        <Button asChild>
          <Link to="/">{t("error.backHome")}</Link>
        </Button>
      }
    />
  )
}
